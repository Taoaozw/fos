"""Tests for Harness deterministic guardrails (`fos guard`), Titans surprise gating, and PlugMem utility feedback."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from fos.cli import run_cli
from fos.dream import run_dream
from fos.engine import BrainEngine
from fos.guard import check_guard
from fos.proposals import scan_proposals
from fos.server import create_app


def test_deterministic_guard_diff_cmd_and_graduation(tmp_path: Path, capsys) -> None:
    home = tmp_path / "fos_home"
    engine = BrainEngine(home / "brain")

    # 1. Path-scoped TSX guard + SQL foreign-key guard + shell command guard
    ui_node = engine.add_atom(
        kind="decision",
        scope="ssh-center",
        content="前端 UI 必须使用 shadcn 组件，禁止在 components/ui 之外裸写原生 <button>/<input>。",
        meta={
            "severity": "P1",
            "guard_pattern": r"path:*.tsx !*components/ui/* :: <(button|input|select)\b",
        },
    )["node"]["id"]

    cmd_node = engine.add_atom(
        kind="lesson",
        scope="invoice-creator",
        content="禁止直接运行 mygo build -upload，必须通过 release.sh 发布到 R2。",
        meta={
            "severity": "P1",
            "guard_pattern": r"cmd:\bmygo\s+build\s+-upload\b",
        },
    )["node"]["id"]

    # Clean diff inside components/ui/ MUST pass
    clean_diff = (
        "+++ b/web/src/components/ui/button.tsx\n"
        "@@ -1,0 +1,2 @@\n"
        "+export function Button() { return <button className='btn' /> }\n"
    )
    res_ok = check_guard(engine, diff_text=clean_diff, scope="ssh-center")
    assert res_ok["passed"] is True
    assert res_ok["violations"] == []

    # Violating diff outside components/ui/ MUST block & record PlugMem guard_block utility!
    bad_diff = (
        "+++ b/web/src/pages/Settings.tsx\n"
        "@@ -10,0 +11,2 @@\n"
        "+export function Settings() { return <button>Save</button> }\n"
    )
    res_bad = check_guard(engine, diff_text=bad_diff, scope="ssh-center")
    assert res_bad["passed"] is False
    assert len(res_bad["violations"]) == 1
    assert res_bad["violations"][0]["node_id"] == ui_node
    assert res_bad["violations"][0]["file"] == "web/src/pages/Settings.tsx"

    # Command guard blocks `mygo build -upload` via CLI with ExitCode.GATE_FAILED (4)
    rc = run_cli(["--home", str(home), "guard", "--scope", "invoice-creator", "--cmd", "mygo build -upload", "--json"])
    assert rc == 4
    out = json.loads(capsys.readouterr().out)
    assert out["passed"] is False
    assert out["violations"][0]["node_id"] == cmd_node

    # Graduate ui_node -> excluded from L0 boot, STILL active in `fos guard`, survives rebuild!
    engine.graduate_node(ui_node, reason="Enforced by pre-commit fos guard")
    boot_after = engine.boot(project="ssh-center", budget=300)
    assert all(e["id"] != ui_node for e in boot_after["entries"])
    assert check_guard(engine, diff_text=bad_diff, scope="ssh-center")["passed"] is False

    # Survives rebuild_from_ledger & dream
    engine.rebuild_from_ledger()
    with engine.snapshots.open_current(read_only=True) as db:
        rebuilt = db.get_node(ui_node)
        assert rebuilt is not None
        assert rebuilt.graduated is True
        assert "components/ui" in rebuilt.guard_pattern

    # Dashboard reports guard_blocks & graduated_nodes
    dash = engine.dashboard_summary()
    assert dash["utility"]["total_guard_blocks"] >= 3
    assert dash["hygiene"]["graduated_nodes"] == 1
    assert dash["hygiene"]["guard_rules_count"] == 2


def test_titans_surprise_gate_reinforces_redundant_and_flags_conflict(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")

    base_id = engine.add_atom(
        kind="decision",
        scope="flowxy-gateway",
        source="manual",
        content="console 认证统一使用轻量 AuthWebFilter，禁止引入 Spring Security。",
        meta={"severity": "P1"},
    )["node"]["id"]

    # 1. Near-identical automated sighting -> low surprise ("redundant") -> reinforces seen_count instead of duplicating!
    dup = engine.add_atom(
        kind="decision",
        scope="flowxy-gateway",
        source="hindsight:docs:flowxy-gateway",
        content="console 认证统一使用轻量 AuthWebFilter，禁止引入 Spring Security。",
        meta={"severity": "P1"},
    )
    assert dup["surprise"]["classification"] == "redundant"
    assert dup["reinforced"] == base_id
    with engine.snapshots.open_current(read_only=True) as db:
        assert db.get_node(base_id).seen_count == 2  # type: ignore[union-attr]
        assert len(db.list_nodes(scope="flowxy-gateway", as_of=None)) == 1

    # 2. Opposite-polarity sighting -> maximum surprise ("conflict") -> attaches CONTRADICTS edge!
    conflict = engine.add_atom(
        kind="decision",
        scope="flowxy-gateway",
        source="manual",
        content="console 认证直接引入 Spring Security 与 AuthWebFilter 集成。",
        meta={"severity": "P1"},
    )
    assert conflict["surprise"]["classification"] == "conflict"
    assert conflict["surprise"]["closest_node_id"] == base_id
    new_id = conflict["node"]["id"]
    with engine.snapshots.open_current(read_only=True) as db:
        edges = db.get_edges(new_id)
        assert any(e.edge_type == "CONTRADICTS" and e.dst_id == base_id for e in edges)

    # Dream preserves reinforcement and CONTRADICTS edge cleanly
    res = run_dream(engine, no_gate=True)
    assert res["promoted"] is True


def test_plugmem_utility_feedback_reranks_and_surfaces_noisy_cards(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")
    good_id = engine.add_atom(
        kind="lesson",
        scope="demo",
        content="SQLite 并发写入必须开启 WAL 模式并配置 busy_timeout。",
    )["node"]["id"]
    noisy_id = engine.add_atom(
        kind="lesson",
        scope="demo",
        content="SQLite 并发写入建议检查日志目录权限和临时文件前缀。",
    )["node"]["id"]

    # Record strong positive feedback on good_id and negative noise feedback on noisy_id
    for _ in range(4):
        engine.feedback_node(good_id, "useful", context="Prevented SQLITE_BUSY")
    for _ in range(4):
        engine.feedback_node(noisy_id, "noise", context="Irrelevant to SQLite concurrency")

    # Recall favors higher-utility good_id over noisy_id
    rec = engine.recall("SQLite 并发写入", scope="demo")
    assert rec["results"][0]["id"] == good_id

    # Proposal Inbox automatically surfaces high-noise low-utility card as `retire_stale`
    scan = scan_proposals(engine, root_dir=tmp_path / "no_repos")
    assert any(
        p["kind"] == "retire_stale" and noisy_id in p["target_node_ids"]
        for p in scan["new_proposals"]
    )

    # HTTP /v1/nodes/{id}/feedback & /v1/guard/check endpoints work end-to-end
    app = create_app(engine=engine, token_hash="")
    tc = TestClient(app)
    r_fb = tc.post(f"/v1/nodes/{good_id}/feedback", json={"verdict": "useful", "context": "via ui"})
    assert r_fb.status_code == 200
    assert r_fb.json()["useful"] == 5
    assert r_fb.json()["utility_score"] > 0.8

    r_gd = tc.post("/v1/guard/check", json={"command": "echo ok", "scope": "demo"})
    assert r_gd.status_code == 200
    assert r_gd.json()["passed"] is True
