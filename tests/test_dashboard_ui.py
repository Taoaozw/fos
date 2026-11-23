"""Tests for the React+TS Web Observability Console served by fosd, /v1/dashboard, /v1/ledger, and `fos open`."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from fos.cli import run_cli
from fos.config import hash_token
from fos.dream import run_dream
from fos.engine import BrainEngine
from fos.errors import ExitCode
from fos.server import create_app


def test_web_ui_dashboard_and_ledger_endpoints(tmp_path: Path) -> None:
    token = "fos_ui_test_secret_token"
    engine = BrainEngine(tmp_path / "brain")
    engine.add_atom(kind="principle", scope="global", content="纯 CLI 交付，禁止 MCP", meta={"severity": "P0"})
    l1 = engine.add_atom(kind="lesson", scope="goto", content="渠道用量必须保留小数精度", meta={"severity": "P1"})["node"]["id"]
    engine.correct_node(l1, "渠道用量与配额必须保留小数精度，禁止整数截断", reason="补充配额场景")

    # Serve a mock built React dist directory (keeps unit test independent of Vite build artifacts)
    fake_dist = tmp_path / "web_dist"
    (fake_dist / "assets").mkdir(parents=True)
    (fake_dist / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div><script src="/assets/app.js"></script></body></html>',
        encoding="utf-8",
    )
    (fake_dist / "assets" / "app.js").write_text("console.log('fos');", encoding="utf-8")

    app = create_app(engine=engine, token_hash=hash_token(token), web_dist=fake_dist)
    tc = TestClient(app)

    # 1. React SPA index.html & static /assets/* served cleanly with zero leaked secrets
    r_ui = tc.get("/")
    assert r_ui.status_code == 200
    assert "text/html" in r_ui.headers["content-type"]
    assert 'id="root"' in r_ui.text
    assert token not in r_ui.text
    assert "渠道用量" not in r_ui.text

    r_js = tc.get("/assets/app.js")
    assert r_js.status_code == 200
    assert "console.log('fos')" in r_js.text

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


def test_edit_node_api_ledger_replay_and_dream(tmp_path: Path) -> None:
    token = "fos_edit_test_token"
    engine = BrainEngine(tmp_path / "brain")
    created = engine.add_atom(
        kind="lesson",
        scope="eng/frontend",
        content="旧规则文本",
        meta={"severity": "P2", "rationale": "旧原因", "guard_hint": "旧守卫"},
    )
    node_id = created["node"]["id"]

    app = create_app(engine=engine, token_hash=hash_token(token))
    tc = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Auth enforced
    assert tc.patch(f"/v1/nodes/{node_id}", json={"rule": "x"}).status_code == 401

    # 2. Validation errors -> 400, missing node -> 404
    assert tc.patch(f"/v1/nodes/{node_id}", headers=headers, json={"rule": "   "}).status_code == 400
    assert tc.patch(f"/v1/nodes/{node_id}", headers=headers, json={"severity": "P9"}).status_code == 400
    assert tc.patch(f"/v1/nodes/{node_id}", headers=headers, json={"type": "invalid"}).status_code == 400
    assert tc.patch("/v1/nodes/LSN-999", headers=headers, json={"rule": "missing"}).status_code == 404

    # 3. Full structured in-place edit via PATCH
    resp = tc.patch(
        f"/v1/nodes/{node_id}",
        headers=headers,
        json={
            "rule": "画布直接更新视口时必须取消排队 rAF",
            "rationale": "避免旧帧覆盖新视口",
            "scope": "eng/frontend/react",
            "severity": "P0",
            "type": "decision",
            "guard_hint": "单测验证 pointercancel 后旧帧不触发",
            "guard_pattern": "path:*.tsx :: requestAnimationFrame",
            "source_ref": "web/src/lib/graph-viewport.ts::createViewportScheduler",
            "trigger_when": ["画布缩放", "pointercancel"],
            "entities": ["canvas", "raf"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["edited"] == node_id
    assert body["node"]["id"] == node_id
    assert body["node"]["rule"] == "画布直接更新视口时必须取消排队 rAF"
    assert body["node"]["rationale"] == "避免旧帧覆盖新视口"
    assert body["node"]["scope"] == "eng/frontend/react"
    assert body["node"]["severity"] == "P0"
    assert body["node"]["type"] == "decision"
    assert body["node"]["guard_pattern"] == "path:*.tsx :: requestAnimationFrame"
    assert body["node"]["trigger_when"] == ["画布缩放", "pointercancel"]
    assert body["node"]["entities"] == ["canvas", "raf"]
    assert set(body["changed_fields"]) >= {"rule", "rationale", "scope", "severity", "type"}

    # 4. No-op edit does not append a duplicate ledger atom
    atoms_before = len(engine.ledger.load_all_atoms())
    noop = tc.patch(
        f"/v1/nodes/{node_id}",
        headers=headers,
        json={"rule": "画布直接更新视口时必须取消排队 rAF", "severity": "P0"},
    )
    assert noop.status_code == 200
    assert noop.json()["changed_fields"] == []
    assert len(engine.ledger.load_all_atoms()) == atoms_before

    # 5. Clearing optional fields explicitly works
    cleared = tc.patch(
        f"/v1/nodes/{node_id}",
        headers=headers,
        json={"guard_hint": "", "entities": []},
    )
    assert cleared.status_code == 200
    assert cleared.json()["node"]["guard_hint"] == ""
    assert cleared.json()["node"]["entities"] == []

    # 6. Updated text & scope are immediately searchable and APPLIES_TO edge reflects new scope
    recalled = engine.recall("pointercancel 画布缩放", scope="eng/frontend/react")
    assert any(r["id"] == node_id for r in recalled["results"])
    detail = tc.get(f"/v1/nodes/{node_id}?tier=L2&graph=true", headers=headers).json()
    active_scope_edges = [
        e for e in detail["graph"] if e["edge_type"] == "APPLIES_TO" and e["valid_until"] is None
    ]
    assert [e["dst_id"] for e in active_scope_edges] == ["scope:eng/frontend/react"]
    assert len(detail["evidence"]) >= 2  # Original atom + edit audit atoms

    # 7. Rebuild from ledger & dream replay both preserve all edits identically
    engine.rebuild_from_ledger()
    rebuilt = engine.get(node_id, tier="L1")["node"]
    assert rebuilt["rule"] == "画布直接更新视口时必须取消排队 rAF"
    assert rebuilt["scope"] == "eng/frontend/react"
    assert rebuilt["severity"] == "P0"
    assert rebuilt["type"] == "decision"
    assert rebuilt["guard_hint"] == ""
    assert rebuilt["entities"] == []

    dream_res = run_dream(engine, no_gate=True)
    assert dream_res["promoted"] is True
    after_dream = engine.get(node_id, tier="L1")["node"]
    assert after_dream["rule"] == "画布直接更新视口时必须取消排队 rAF"
    assert after_dream["scope"] == "eng/frontend/react"
    assert after_dream["severity"] == "P0"
    assert after_dream["type"] == "decision"
    assert after_dream["guard_hint"] == ""
    assert after_dream["entities"] == []

