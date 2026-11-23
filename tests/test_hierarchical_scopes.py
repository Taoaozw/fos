"""Tests for hierarchical scopes (<=3 levels), multi-branch repo profiles, LUB promotion, and AGENTS.md L0 dedup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fos.cli import run_cli
from fos.dream import run_dream
from fos.engine import BrainEngine
from fos.errors import UsageError
from fos.freshness import compute_doc_digest
from fos.guard import check_guard
from fos.proposals import accept_proposal, scan_proposals
from fos.scopes import (
    detect_repo_scopes,
    expand_scope_ancestors,
    infer_lub_scope,
    is_repo_root_ambient_doc,
    normalize_scope,
    resolve_active_scopes,
    scope_depth,
    scope_kind,
)
from fos.server import create_app


def test_scope_normalization_depth_guard_and_ancestors() -> None:
    assert normalize_scope(None) == "global"
    assert normalize_scope("  ENG/Frontend/React/ ") == "eng/frontend/react"
    assert normalize_scope("repo/FOS") == "repo/fos"
    assert normalize_scope("fos") == "fos"

    # Strict validation rejects >3 taxonomy levels and invalid global child segments
    with pytest.raises(UsageError):
        normalize_scope("eng/frontend/react/hooks", strict=True)
    with pytest.raises(UsageError):
        normalize_scope("global/eng", strict=True)
    # Non-strict safely clamps LLM outputs to <= 3 segments
    assert normalize_scope("eng/frontend/react/hooks", strict=False) == "eng/frontend/react"

    # Classification & monotonic specificity depth
    assert scope_kind("global") == "global"
    assert scope_kind("eng") == "domain"
    assert scope_kind("product") == "domain"
    assert scope_kind("eng/frontend/react") == "domain"
    assert scope_kind("music/piano") == "domain"  # extensible 360-industry root
    assert scope_kind("fos") == "repo"
    assert scope_kind("repo/fos") == "repo"

    assert (
        scope_depth("global")
        < scope_depth("eng")
        < scope_depth("eng/frontend")
        < scope_depth("eng/frontend/react")
        < scope_depth("fos")
    )

    assert expand_scope_ancestors("eng/frontend/react") == [
        "eng/frontend/react",
        "eng/frontend",
        "eng",
        "global",
    ]
    assert expand_scope_ancestors("product") == ["product", "global"]
    assert expand_scope_ancestors("fos") == ["fos", "repo/fos", "global"]
    assert expand_scope_ancestors("repo/fos") == ["repo/fos", "fos", "global"]


def test_multi_branch_repo_profile_and_subdir_focus(tmp_path: Path) -> None:
    repo = tmp_path / "fullstack-app"
    (repo / ".git").mkdir(parents=True)
    (repo / "deploy").mkdir()
    (repo / "web").mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "fullstack"\nrequires-python = ">=3.12"\ndependencies = ["fastapi>=0.115"]\n',
        encoding="utf-8",
    )
    (repo / "web" / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^19.0.0", "tailwindcss": "^4.0.0"}}),
        encoding="utf-8",
    )
    (repo / "web" / "components.json").write_text('{"style":"default"}', encoding="utf-8")

    # At repo root: mounts Python backend + React/CSS/shadcn frontend + ops
    root_scopes = detect_repo_scopes(repo)
    assert "eng" in root_scopes
    assert "eng/backend/python" in root_scopes
    assert "eng/frontend/react" in root_scopes
    assert "eng/frontend/shadcn" in root_scopes
    assert "eng/ops" in root_scopes

    # Inside `web/` sub-package: focuses strictly on frontend branch (no backend/ops bleed)
    web_scopes = detect_repo_scopes(repo / "web")
    assert "eng/frontend/react" in web_scopes
    assert "eng/frontend/shadcn" in web_scopes
    assert "eng/frontend" in web_scopes
    assert "eng/backend/python" not in web_scopes
    assert "eng/ops" not in web_scopes

    # Non-code domain (`product` / `writing`) stays completely isolated even if cwd is a code repo
    prod_active = resolve_active_scopes("product", cwd=repo)
    assert prod_active == ["product", "global"]


def test_boot_hierarchical_isolation_and_repo_root_agents_md_dedup(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")

    # Create real repo docs to test ambient AGENTS.md L0 deduplication vs non-ambient docs
    repo_dir = tmp_path / "my-web-app"
    (repo_dir / "docs").mkdir(parents=True)
    agents_md = repo_dir / "AGENTS.md"
    agents_md.write_text("# AGENTS\nAlways run bun test before commit.\n", encoding="utf-8")
    design_md = repo_dir / "docs" / "DESIGN.md"
    design_md.write_text("# Design\nCheckout state transitions must remain explicit.\n", encoding="utf-8")
    nested_agents = repo_dir / "web" / "AGENTS.md"
    nested_agents.parent.mkdir(parents=True)
    nested_agents.write_text("# Web AGENTS\nSubfolder rule.\n", encoding="utf-8")

    assert is_repo_root_ambient_doc("my-web-app", f"{agents_md.resolve()}#section") is True
    assert is_repo_root_ambient_doc("my-web-app", f"{design_md.resolve()}#section") is False
    assert is_repo_root_ambient_doc("my-web-app", f"{nested_agents.resolve()}#section") is False

    # Seed multi-tier rules
    n_global_p0 = engine.add_atom(
        kind="principle",
        scope="global",
        content="保持最小局部改动，禁止猜测。",
        meta={"severity": "P0"},
    )["node"]["id"]
    n_eng_p0 = engine.add_atom(
        kind="principle",
        scope="eng",
        content="所有关键变更必须补齐回归测试验证。",
        meta={"severity": "P0"},
    )["node"]["id"]
    n_fe_p0 = engine.add_atom(
        kind="principle",
        scope="eng/frontend",
        content="前端禁止硬编码十六进制色值，必须使用语义主题 Token。",
        meta={"severity": "P0"},
    )["node"]["id"]
    n_shadcn_p1 = engine.add_atom(
        kind="decision",
        scope="eng/frontend/shadcn",
        content="表单控件优先复用 components/ui 共享组件。",
        meta={"severity": "P1"},
    )["node"]["id"]
    n_go_p0 = engine.add_atom(
        kind="principle",
        scope="eng/backend/go",
        content="Go 单机工具禁止引入 CGO 依赖。",
        meta={"severity": "P0"},
    )["node"]["id"]
    n_prod_p0 = engine.add_atom(
        kind="principle",
        scope="product",
        content="PRD 必须先写用户痛点与成功指标，再写交互细节。",
        meta={"severity": "P0"},
    )["node"]["id"]

    # Repo-root AGENTS.md node (should be excluded from `my-web-app` L0 boot to prevent double-injection)
    n_repo_agents = engine.add_atom(
        kind="decision",
        scope="my-web-app",
        source="hindsight:docs:my-web-app",
        content="提交前必须运行 bun test 检查。",
        meta={
            "severity": "P1",
            "source_ref": f"{agents_md.resolve()}#commands",
            "source_hash": compute_doc_digest(agents_md),
        },
    )["node"]["id"]
    # Non-ambient docs/DESIGN.md node (SHOULD remain in L0 boot)
    n_repo_design = engine.add_atom(
        kind="decision",
        scope="my-web-app",
        source="hindsight:docs:my-web-app",
        content="结账状态转换必须显式枚举，禁止隐式回退。",
        meta={
            "severity": "P1",
            "source_ref": f"{design_md.resolve()}#state",
            "source_hash": compute_doc_digest(design_md),
        },
    )["node"]["id"]

    # 1. Boot frontend repo: gets global P0 + eng P0 + eng/frontend P0 + DESIGN.md;
    #    excludes AGENTS.md (ambient dedup), shadcn P1 (0t L0 contract), Go P0, and product P0!
    fe_boot = engine.boot(project="my-web-app", scopes=["eng/frontend/shadcn"], budget=400)
    fe_ids = {e["id"] for e in fe_boot["entries"]}
    assert n_global_p0 in fe_ids
    assert n_eng_p0 in fe_ids
    assert n_fe_p0 in fe_ids
    assert n_repo_design in fe_ids
    assert n_repo_agents not in fe_ids
    assert n_shadcn_p1 not in fe_ids
    assert n_go_p0 not in fe_ids
    assert n_prod_p0 not in fe_ids
    assert "(eng/frontend)" in fe_boot["kernel_text"]

    # 2. Boot Go backend repo: gets global P0 + eng P0 + eng/backend/go P0; NEVER frontend or product!
    go_boot = engine.boot(project="order-go", scopes=["eng/backend/go"], budget=400)
    go_ids = {e["id"] for e in go_boot["entries"]}
    assert {n_global_p0, n_eng_p0, n_go_p0}.issubset(go_ids)
    assert n_fe_p0 not in go_ids
    assert n_prod_p0 not in go_ids

    # 3. Boot pure non-code domain (`product`): gets global P0 + product P0; ZERO engineering rules!
    prod_boot = engine.boot(project="product", budget=400)
    prod_ids = {e["id"] for e in prod_boot["entries"]}
    assert {n_global_p0, n_prod_p0}.issubset(prod_ids)
    assert n_eng_p0 not in prod_ids
    assert n_fe_p0 not in prod_ids
    assert n_go_p0 not in prod_ids


def test_recall_mirror_and_guard_with_hierarchical_scopes(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")

    n_shadcn = engine.add_atom(
        kind="decision",
        scope="eng/frontend/shadcn",
        content="禁止在页面中裸写原生 <button>，必须复用 components/ui 共享 Button 组件。",
        meta={
            "severity": "P1",
            "guard_pattern": r"path:*.tsx !components/ui :: <button\b",
            "trigger_when": ["前端按钮", "shadcn 组件"],
        },
    )["node"]["id"]
    n_go = engine.add_atom(
        kind="lesson",
        scope="eng/backend/go",
        content="禁止在 Go 单机二进制里引入 CGO sqlite 驱动，避免交叉编译失败。",
        meta={"severity": "P1"},
    )["node"]["id"]
    n_writing = engine.add_atom(
        kind="principle",
        scope="writing",
        content="技术文章开头先抛核心结论与量化证据，禁止冗长铺垫。",
        meta={"severity": "P1"},
    )["node"]["id"]

    # Recall in frontend repo with mounted shadcn branch finds shadcn P1 and isolates Go & writing
    rec_fe = engine.recall("页面按钮 原生 button shadcn 组件", scope="my-web-app", scopes=["eng/frontend/shadcn"])
    fe_hit_ids = [r["id"] for r in rec_fe["results"]]
    assert n_shadcn in fe_hit_ids
    assert n_go not in fe_hit_ids
    assert n_writing not in fe_hit_ids

    # Recall in `writing` domain isolates all engineering cards
    rec_wr = engine.recall("文章开头 结论 按钮 sqlite", scope="writing")
    wr_hit_ids = [r["id"] for r in rec_wr["results"]]
    assert n_writing in wr_hit_ids
    assert n_shadcn not in wr_hit_ids
    assert n_go not in wr_hit_ids

    # Mirror in frontend repo surfaces domain-level shadcn conflict
    mir = engine.mirror("在页面中直接手写原生 <button> 提交表单", scope="my-web-app", scopes=["eng/frontend/shadcn"])
    assert any(c["id"] == n_shadcn for c in mir["conflicts"])

    # Static guard inherits mounted `eng/frontend/shadcn` guard pattern across any frontend repo
    diff = "+++ b/src/pages/Login.tsx\n@@ -1,1 +1,2 @@\n+<button onClick={go}>Login</button>\n"
    gd = check_guard(engine, diff_text=diff, scope="my-web-app", scopes=["my-web-app", "eng/frontend/shadcn", "eng", "global"])
    assert gd["passed"] is False
    assert gd["violations"][0]["node_id"] == n_shadcn


def test_dream_and_proposals_lub_scope_promotion_and_http_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 1. Pure LUB inference tests (with word-boundary safety: "domain" must NOT match "dom")
    assert infer_lub_scope([{"rule": "前端统一用 shadcn components/ui 共享组件"}]) == "eng/frontend/shadcn"
    assert infer_lub_scope([{"rule": "PostgreSQL 大表 DDL 必须并发建索引"}]) == "eng/backend/postgres"
    assert infer_lub_scope([{"rule": "禁止依赖 rsync --mkpath，必须先 ssh mkdir -p"}]) == "eng/ops"
    assert infer_lub_scope([{"rule": "Ensure clean domain boundary and minimal storage state"}]) == "eng"

    # 2. Dream cross-repo heuristic consolidation elevates to LUB (`eng/ops`) instead of bloating `global`
    home = tmp_path / "fos_home"
    engine = BrainEngine(home / "brain")
    engine.add_atom(kind="lesson", scope="fops", source="hindsight:git:fops", content="禁止依赖 rsync --mkpath，必须先 ssh mkdir -p")
    engine.add_atom(kind="lesson", scope="goto", source="hindsight:git:goto", content="禁止依赖 rsync --mkpath，必须先 ssh mkdir -p")
    dr = run_dream(engine, no_gate=True)
    assert dr["promoted"] is True
    assert engine.get("LSN-001", tier="L1")["node"]["scope"] == "eng/ops"

    # 3. Heuristic proposal scan across 2 frontend repos proposes LUB scope `eng/frontend/shadcn`
    engine.add_atom(
        kind="decision",
        scope="app-one",
        content="应用页面必须复用 shadcn components/ui 共享组件，禁止手写原生控件。",
    )
    engine.add_atom(
        kind="decision",
        scope="app-two",
        content="应用页面必须复用 shadcn components/ui 共享组件，禁止直接手写原生控件。",
    )
    scan_res = scan_proposals(engine, root_dir=tmp_path / "no_git_root")
    fe_props = [p for p in scan_res["new_proposals"] if p["kind"] == "promote_global"]
    assert len(fe_props) == 1
    assert fe_props[0]["proposed_scope"] == "eng/frontend/shadcn"
    pid = fe_props[0]["id"]

    # 4. Accept proposal with explicit CLI `--scope` override (`eng/frontend`)
    rc = run_cli(["--home", str(home), "proposals", "accept", pid, "--scope", "eng/frontend", "--json"])
    assert rc == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["proposal"]["proposed_scope"] == "eng/frontend"
    promoted_node = engine.get(accepted["result_node_id"], tier="L1")["node"]
    assert promoted_node["scope"] == "eng/frontend"

    # 5. CLI `fos add` enforces <=3 scope depth strictly
    rc_bad = run_cli(["--home", str(home), "add", "--kind", "lesson", "--scope", "eng/frontend/react/hooks", "too deep", "--json"])
    assert rc_bad != 0
    capsys.readouterr()

    # 6. HTTP API `/v1/boot`, `/v1/recall`, `/v1/mirror` honor hierarchical `scopes`
    engine.add_atom(
        kind="principle",
        scope="eng/frontend",
        content="所有前端颜色必须来自语义主题变量。",
        meta={"severity": "P0"},
    )
    app = create_app(engine=engine, token_hash="")
    tc = TestClient(app)

    r_boot = tc.get("/v1/boot", params={"project": "brand-new-ui", "scopes": "eng/frontend/react"})
    assert r_boot.status_code == 200
    boot_data = r_boot.json()
    assert "eng/frontend" in boot_data["active_scopes"]
    assert any(e["scope"] == "eng/frontend" for e in boot_data["entries"])

    r_rec = tc.get("/v1/recall", params={"q": "shadcn 共享组件", "scope": "brand-new-ui", "scopes": "eng/frontend/shadcn"})
    assert r_rec.status_code == 200
    assert any(r["scope"] == "eng/frontend" for r in r_rec.json()["results"])

    r_mir = tc.post("/v1/mirror", json={"proposal": "在页面手写原生控件", "scope": "brand-new-ui", "scopes": ["eng/frontend/shadcn"]})
    assert r_mir.status_code == 200
    assert "eng/frontend" in r_mir.json()["active_scopes"]

    # 7. Narrowing a global P0 proposal to a domain scope via `accept_proposal(..., scope="eng/frontend")` bypasses global L0 ceiling
    for i in range(12):
        engine.add_atom(
            kind="principle",
            scope="global",
            content=f"全局核心红线 {i}：任何关键变更必须具备可观测性、回归测试与显式状态机定义。",
            meta={"severity": "P0"},
        )
    from fos.proposals import Proposal, ProposalStore

    pstore = ProposalStore(engine.data_dir)
    overflow_prop = Proposal(
        id="prp_test_narrow_scope",
        kind="promote_global",
        status="pending",
        fingerprint="fp_narrow_scope",
        title="测试收窄作用域绕开全局 L0 溢出",
        proposed_type="principle",
        proposed_scope="global",
        proposed_severity="P0",
        proposed_rule="所有前端组件必须使用语义主题变量，禁止写死颜色。",
        proposed_rationale="仅影响前端项目。",
        evidence_repos=["app-one", "app-two"],
    )
    pstore.save(overflow_prop)
    # Narrowing scope to `eng/frontend` succeeds without hitting L0_BUDGET_EXCEEDED!
    narrowed = accept_proposal(engine, "prp_test_narrow_scope", scope="eng/frontend")
    assert narrowed["proposal"]["proposed_scope"] == "eng/frontend"
    assert engine.get(narrowed["result_node_id"], tier="L1")["node"]["scope"] == "eng/frontend"
