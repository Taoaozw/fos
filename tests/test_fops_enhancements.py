"""Tests for fops-inspired CLI ergonomics, embedded skill installer, and brain-hosted auto-update."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fos.cli import run_cli
from fos.client import FosClient
from fos.config import hash_token
from fos.engine import BrainEngine
from fos.errors import ExitCode, FosError
from fos.output import dumps_json, project_fields
from fos.server import create_app
from fos.skill import install_skill, list_installed_skills, refresh_installed_skills
from fos.suggest import suggest_closest
from fos.update import read_update_state, should_auto_update


def _asgi_sync_transport(app) -> httpx.BaseTransport:
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


def test_describe_aliases_typo_suggestion_and_fields_projection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = str(tmp_path / "fos_home")

    # 1. Typo suggestion on unknown command with structured JSON stderr envelope
    rc = run_cli(["--home", home, "recal", "--json"])
    assert rc == int(ExitCode.USAGE)
    err_payload = json.loads(capsys.readouterr().err)
    assert err_payload["ok"] is False
    assert err_payload["code"] == "invalid_usage"
    assert "recall" in err_payload["hint"]

    # 2. Machine-readable describe catalog & command schema (via alias `desc`)
    assert run_cli(["--home", home, "desc"]) == int(ExitCode.OK)
    catalog = json.loads(capsys.readouterr().out)
    assert catalog["tool"] == "fos"
    assert any(c["name"] == "recall" for c in catalog["commands"])

    assert run_cli(["--home", home, "desc", "rec"]) == int(ExitCode.OK)
    schema = json.loads(capsys.readouterr().out)
    assert schema["name"] == "recall"
    assert any(f["name"] == "--as-of" for f in schema["flags"])

    # 3. Unknown command in describe -> exit 3 with Levenshtein hint
    assert run_cli(["--home", home, "desc", "miror", "--json"]) == int(ExitCode.NOT_FOUND)
    err2 = json.loads(capsys.readouterr().err)
    assert "mirror" in err2["hint"]

    # 4. Short aliases (`a`, `r`) + `--fields` projection implies compact JSON
    assert (
        run_cli(["--home", home, "a", "--kind", "lesson", "--scope", "fops", "--severity", "P0", "禁止使用 rsync --mkpath"])
        == int(ExitCode.OK)
    )
    capsys.readouterr()

    assert run_cli(["--home", home, "r", "rsync", "--fields", "id,severity,rule"]) == int(ExitCode.OK)
    raw_out = capsys.readouterr().out.strip()
    # Non-TTY piped output is single-line compact JSON
    assert "\n" not in raw_out
    projected = json.loads(raw_out)
    assert projected == [{"id": "LSN-001", "severity": "P0", "rule": "禁止使用 rsync --mkpath"}]

    # Direct helper checks
    assert suggest_closest("miror", ["boot", "recall", "mirror"]) == "mirror"
    assert "\n" not in dumps_json({"a": 1}, pretty=False)
    assert "\n" in dumps_json({"a": 1}, pretty=True)
    assert project_fields({"results": [{"id": "1", "x": 2}]}, "id") == [{"id": "1"}]


def test_embedded_skill_install_and_refresh_only(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".codex").mkdir(parents=True)
    (fake_home / ".gemini" / "config").mkdir(parents=True)

    # Refresh-only before any install installs nothing
    empty = refresh_installed_skills(home=fake_home)
    assert empty["installed"] == []

    # Auto-detect installs into all detected tool roots (SKILL.md + references/commands.md)
    res = install_skill(home=fake_home)
    assert len(res["installed"]) == 3
    for root in (
        fake_home / ".claude" / "skills" / "fos",
        fake_home / ".codex" / "skills" / "fos",
        fake_home / ".gemini" / "config" / "skills" / "fos",
    ):
        assert (root / "SKILL.md").exists()
        assert (root / "references" / "commands.md").exists()

    # Adding a new tool dir later: refresh_only must ONLY touch existing installs
    (fake_home / ".cursor").mkdir(parents=True)
    refreshed = refresh_installed_skills(home=fake_home)
    assert len(refreshed["installed"]) == 3
    assert not (fake_home / ".cursor" / "skills" / "fos").exists()
    assert len(list_installed_skills(home=fake_home)) == 3


def test_auto_update_version_rules_download_sha_and_install_sh(tmp_path: Path) -> None:
    # Version progression guardrails
    assert should_auto_update("0.1.0", "0.2.0") is True
    assert should_auto_update("0.2.0", "0.1.0") is False
    assert should_auto_update("0.1.0", "0.1.0") is False
    assert should_auto_update("dev", "0.2.0") is False
    assert should_auto_update("0.1.0+dirty", "0.2.0") is False
    assert should_auto_update("2026.11.20-1000", "2026.11.22-1400") is True
    assert should_auto_update("2026.11.22-1400", "2026.11.20-1000") is False

    # Setup brain with a published fake wheel (v0.2.0)
    brain_dir = tmp_path / "brain"
    dist_dir = brain_dir / "dist"
    dist_dir.mkdir(parents=True)
    fake_wheel_bytes = b"PK\x03\x04" + b"wheel-payload-data" * 100
    wheel_file = dist_dir / "fos-0.2.0-py3-none-any.whl"
    wheel_file.write_bytes(fake_wheel_bytes)
    (dist_dir / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    expected_sha = hashlib.sha256(fake_wheel_bytes).hexdigest()

    token = "fos_update_token_test"
    engine = BrainEngine(brain_dir)
    app = create_app(engine=engine, token_hash=hash_token(token))
    transport = _asgi_sync_transport(app)

    # 1. Public /install.sh renders brain URL and PEP-427 wheel filename cleanly
    tc = TestClient(app)
    r_sh = tc.get("/install.sh", headers={"host": "m3max.tailnet:7622"})
    assert r_sh.status_code == 200
    assert "BRAIN='http://m3max.tailnet:7622'" in r_sh.text
    assert 'WHEEL="$TMP_DIR/fos-0.2.0-py3-none-any.whl"' in r_sh.text
    assert "fos-latest" not in r_sh.text
    assert "/v1/cli/download" in r_sh.text

    # 2. Authenticated /v1/cli/version & /v1/cli/download advertise version + SHA-256
    r_ver = tc.get("/v1/cli/version", headers={"Authorization": f"Bearer {token}"})
    assert r_ver.status_code == 200
    assert r_ver.json()["version"] == "0.2.0"
    assert r_ver.json()["sha256"] == expected_sha
    assert r_ver.headers["X-Fos-Version"] == "0.2.0"

    # 3. Client check + verified update via mock installer_fn
    client_home = tmp_path / "client_home"
    client = FosClient(home=client_home, http_transport=transport)
    client.init_remote("http://m3max.tailnet:7622", token)

    chk = client.update(check_only=True)
    assert chk["update_available"] is True
    assert chk["remote_version"] == "0.2.0"

    installed_paths: list[Path] = []

    def _fake_installer(p: Path) -> str:
        installed_paths.append(p)
        assert p.read_bytes() == fake_wheel_bytes
        return "mock-uv"

    upd = client.update(installer_fn=_fake_installer)
    assert upd["updated"] is True
    assert upd["sha256"] == expected_sha
    assert len(installed_paths) == 1

    st = read_update_state(client.cache_dir / "update_state.json")
    assert st.updated_to == "0.2.0"
    assert st.failed_version == ""

    from fos import __version__

    # 4. Status diagnostic report works online and offline
    st_on = client.status(offline=False)
    assert st_on["brain"]["reachable"] is True
    assert st_on["last_update"] == f"{__version__} -> 0.2.0"
    st_off = client.status(offline=True)
    assert st_off["brain"]["skipped"] == "offline"

    # 5. Failing installer records failure cooldown state
    def _broken_installer(_p: Path) -> str:
        raise FosError("simulated install error")

    with pytest.raises(FosError):
        client.update(installer_fn=_broken_installer)
    st_fail = read_update_state(client.cache_dir / "update_state.json")
    assert st_fail.failed_version == "0.2.0"
    assert "simulated" in st_fail.last_error
