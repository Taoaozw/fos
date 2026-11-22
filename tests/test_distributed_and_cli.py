"""End-to-end tests for fosd auth, remote client + offline cache/outbox sync, and CLI contract & cold start."""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from fos.cli import run_cli
from fos.client import FosClient
from fos.config import hash_token
from fos.engine import BrainEngine
from fos.errors import AuthError, ExitCode
from fos.server import create_app


class _UnreachableTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Simulated offline airplane mode")


def _asgi_sync_transport(app) -> httpx.BaseTransport:
    """Wrap FastAPI app in a sync httpx transport via Starlette TestClient."""
    from fastapi.testclient import TestClient

    tc = TestClient(app)

    class _Bridge(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            resp = tc.request(
                method=request.method,
                url=str(request.url.raw_path.decode("utf-8")),
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(
                status_code=resp.status_code,
                headers=resp.headers,
                content=resp.content,
                request=request,
            )

    return _Bridge()


def test_remote_auth_offline_cache_and_outbox_sync(tmp_path: Path) -> None:
    brain_dir = tmp_path / "m3max_brain"
    client_home = tmp_path / "macbook_home"
    secret_token = "fos_test_secret_token_123456"

    engine = BrainEngine(brain_dir, device="m3max")
    engine.add_atom(
        kind="lesson",
        scope="fops",
        content="禁止依赖 rsync --mkpath，必须先 ssh mkdir -p，因为 macOS openrsync 不支持",
        meta={"severity": "P0"},
    )
    app = create_app(engine=engine, token_hash=hash_token(secret_token))
    online_transport = _asgi_sync_transport(app)

    # Wrong token is rejected with AuthError
    bad_client = FosClient(home=client_home, http_transport=online_transport)
    with pytest.raises(AuthError):
        bad_client.init_remote("http://m3max.tailnet:7622", "wrong-token")

    # Valid token binds & downloads compact L0/L1 cache
    client = FosClient(home=client_home, http_transport=online_transport)
    init_res = client.init_remote("http://m3max.tailnet:7622", secret_token)
    assert init_res["cached_nodes"] == 1

    # Online recall
    online_rec = client.recall("rsync --mkpath", scope="fops")
    assert online_rec["mode"] == "online"
    assert online_rec["results"][0]["id"] == "LSN-001"

    # Switch to offline airplane mode -> reads degrade cleanly to offline_cache, writes queue to outbox.jsonl
    client.http_transport = _UnreachableTransport()
    off_boot = client.boot(project="fops", budget=300)
    assert off_boot["mode"] == "offline_cache"
    assert off_boot["count"] == 1

    off_rec = client.recall("rsync --mkpath", scope="fops")
    assert off_rec["mode"] == "offline_cache"
    assert off_rec["results"][0]["id"] == "LSN-001"

    off_mirror = client.mirror("部署直接用 rsync --mkpath", scope="fops")
    assert off_mirror["mode"] == "offline_cache"
    assert len(off_mirror["lesson_warnings"]) == 1

    off_add = client.add(
        kind="decision",
        scope="fops",
        content="离线期间决定增加心跳健康检查接口",
    )
    assert off_add["mode"] == "offline_outbox"
    assert off_add["queued"] is True
    assert client.outbox_path.exists()

    # Come back online -> fos sync flushes outbox and pulls refreshed snapshot
    client.http_transport = online_transport
    sync_res = client.sync()
    assert sync_res["flushed_outbox"] == 1
    assert sync_res["remaining_outbox"] == 0
    assert sync_res["cached_nodes"] == 2
    assert not client.outbox_path.exists()

    # Verify flushed decision is now live on M3 Max brain
    verify = client.recall("心跳健康检查", scope="fops")
    assert verify["mode"] == "online"
    assert any("心跳健康检查" in r["rule"] for r in verify["results"])


def test_cli_contract_exit_codes_and_cold_start(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = str(tmp_path / "fos_home")

    # 1. Usage error -> exit code 2
    assert run_cli(["--home", home, "add", "--kind", "invalid", "x"]) == int(ExitCode.USAGE)
    capsys.readouterr()

    # 2. Add + boot + recall + get + dream + eval + undo -> exit code 0
    assert (
        run_cli(
            [
                "--home",
                home,
                "add",
                "--kind",
                "lesson",
                "--scope",
                "fops",
                "--severity",
                "P0",
                "禁止依赖 rsync --mkpath，必须先 ssh mkdir -p",
                "--json",
            ]
        )
        == int(ExitCode.OK)
    )
    out = json.loads(capsys.readouterr().out)
    node_id = out["node"]["id"]

    assert run_cli(["--home", home, "boot", "--project", "fops", "--budget", "300", "--json"]) == int(ExitCode.OK)
    boot_data = json.loads(capsys.readouterr().out)
    assert boot_data["used_tokens"] <= 300

    assert run_cli(["--home", home, "get", node_id, "--tier", "L2", "--graph", "--json"]) == int(ExitCode.OK)
    capsys.readouterr()

    # 3. Missing node -> exit code 3
    assert run_cli(["--home", home, "get", "LSN-404", "--json"]) == int(ExitCode.NOT_FOUND)
    capsys.readouterr()

    # 4. Dream + eval + undo
    assert run_cli(["--home", home, "dream", "--json"]) == int(ExitCode.OK)
    capsys.readouterr()
    assert run_cli(["--home", home, "eval", "--suite", "all", "--json"]) == int(ExitCode.OK)
    capsys.readouterr()
    assert run_cli(["--home", home, "undo", "--json"]) == int(ExitCode.OK)
    capsys.readouterr()

    # 5. CLI cold-start check (fos --version)
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, "-m", "fos.cli", "--version"], capture_output=True, text=True, check=True)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert "fos 0.1.0" in proc.stdout
    assert elapsed_ms < 250
