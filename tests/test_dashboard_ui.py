"""Tests for the built-in fosd Web Observability Console, /v1/dashboard, /v1/ledger, and `fos open`."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from fos.cli import run_cli
from fos.config import hash_token
from fos.engine import BrainEngine
from fos.errors import ExitCode
from fos.server import create_app


def test_web_ui_dashboard_and_ledger_endpoints(tmp_path: Path) -> None:
    token = "fos_ui_test_secret_token"
    engine = BrainEngine(tmp_path / "brain")
    engine.add_atom(kind="principle", scope="global", content="纯 CLI 交付，禁止 MCP", meta={"severity": "P0"})
    l1 = engine.add_atom(kind="lesson", scope="goto", content="渠道用量必须保留小数精度", meta={"severity": "P1"})["node"]["id"]
    engine.correct_node(l1, "渠道用量与配额必须保留小数精度，禁止整数截断", reason="补充配额场景")

    app = create_app(engine=engine, token_hash=hash_token(token))
    tc = TestClient(app)

    # 1. Static HTML shell is served at / with zero embedded secrets or data
    r_ui = tc.get("/")
    assert r_ui.status_code == 200
    assert "text/html" in r_ui.headers["content-type"]
    assert "FlowOS" in r_ui.text
    assert token not in r_ui.text
    assert "渠道用量" not in r_ui.text

    # 2. /v1/dashboard requires Bearer auth
    assert tc.get("/v1/dashboard").status_code == 401
    r_dash = tc.get("/v1/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert r_dash.status_code == 200
    dash = r_dash.json()
    assert dash["counts"]["active_nodes"] == 2
    assert dash["counts"]["superseded_nodes"] == 1
    assert dash["counts"]["ledger_atoms"] == 3
    assert "global" in dash["scopes"] and "goto" in dash["scopes"]
    assert any(e["edge_type"] == "SUPERSEDES" for e in dash["edges"])

    # 3. /v1/ledger returns recent verbatim atoms
    r_led = tc.get("/v1/ledger", headers={"Authorization": f"Bearer {token}"})
    assert r_led.status_code == 200
    assert r_led.json()["total"] == 3


def test_fos_open_print_only(tmp_path: Path, capsys) -> None:
    rc = run_cli(["--home", str(tmp_path), "open", "--print", "--json"])
    assert rc == int(ExitCode.OK)
    data = json.loads(capsys.readouterr().out)
    assert data["url"] == "http://127.0.0.1:7622/"
    assert data["opened"] is False
