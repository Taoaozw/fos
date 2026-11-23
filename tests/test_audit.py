"""Tests for read-only codebase-grounded memory auditing (`fos audit`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fos.audit import audit_repo_memories, build_audit_argv
from fos.engine import BrainEngine
from fos.proposals import accept_proposal, list_proposals


def test_build_audit_argv_uses_read_only_sandbox_and_model_effort(tmp_path: Path) -> None:
    out_file = tmp_path / "out.txt"
    argv = build_audit_argv(
        cli_command="codex exec",
        repo_path=tmp_path,
        out_file=out_file,
        prompt="audit prompt",
        model="gpt-6.2-sol",
        effort="medium",
    )
    assert argv[:2] == ["codex", "exec"]
    assert "-m" in argv and "gpt-6.2-sol" in argv
    assert "-c" in argv and "model_reasoning_effort=medium" in argv
    assert "-s" in argv and "read-only" in argv
    assert "-C" in argv and str(tmp_path) in argv
    assert "--ephemeral" in argv


def test_audit_repo_memories_dry_run_and_proposal_accept_lifecycle(tmp_path: Path) -> None:
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    engine = BrainEngine(tmp_path / "brain")
    n_ok = engine.add_atom(
        kind="decision",
        scope="demo-repo",
        content="所有 API 错误必须返回结构化可枚举错误码。",
        meta={"severity": "P1", "verified_at": "2025-01-01"},
    )["node"]["id"]
    n_drift = engine.add_atom(
        kind="lesson",
        scope="demo-repo",
        content="默认向量模型使用 E5-large，查询必须加 query: 前缀。",
        meta={"severity": "P1", "source_ref": str(repo / "src/embed.py")},
    )["node"]["id"]
    n_obs = engine.add_atom(
        kind="decision",
        scope="demo-repo",
        content="保留旧版 XML RPC 兼容层。",
        meta={"severity": "P2"},
    )["node"]["id"]

    def _fake_runner(**kwargs: Any) -> dict[str, Any]:
        return {
            "findings": [
                {
                    "id": n_ok,
                    "verdict": "verified",
                    "evidence": "src/errors.py:12 defines ExitCode & FosError",
                    "reason": "代码现状与规则完全一致",
                    "confidence": 0.95,
                },
                {
                    "id": n_drift,
                    "verdict": "drifted",
                    "evidence": "src/embed.py switched default to Qwen3-Embedding-4B; E5 prefix kept only as fallback",
                    "reason": "默认模型已切换为 Qwen3，旧规则描述已漂移",
                    "suggested_rule": "默认向量模型为 Qwen3-Embedding-4B，仅在模型名包含 e5 时附加 query:/passage: 前缀。",
                    "confidence": 0.92,
                },
                {
                    "id": n_obs,
                    "verdict": "obsolete",
                    "evidence": "rg 'xmlrpc' returned 0 matches",
                    "reason": "XML RPC 兼容层已彻底移除",
                    "confidence": 0.98,
                },
            ]
        }

    # 1. Dry-run produces report with zero mutations
    dry = audit_repo_memories(
        engine,
        scope="demo-repo",
        repo_path=repo,
        model="gpt-6.2-sol",
        effort="medium",
        dry_run=True,
        runner=_fake_runner,
    )
    assert dry["audited_count"] == 3
    assert dry["summary"] == {"verified": 1, "drifted": 1, "reclassify": 0, "obsolete": 1, "misscoped": 0}
    assert dry["verified_refreshed"] == 0
    assert dry["created_proposals"] == []
    assert list_proposals(engine, status="pending")["counts"]["pending"] == 0

    # 2. Live audit refreshes verified_at and enqueues fix_drifted + retire_stale proposals
    live = audit_repo_memories(
        engine,
        scope="demo-repo",
        repo_path=repo,
        model="gpt-6.2-sol",
        effort="medium",
        dry_run=False,
        runner=_fake_runner,
    )
    assert live["verified_refreshed"] == 1
    assert len(live["created_proposals"]) == 2
    assert engine.get(n_ok)["node"]["verified_at"] != "2025-01-01"

    # Re-running audit creates 0 duplicate proposals
    live2 = audit_repo_memories(
        engine,
        scope="demo-repo",
        repo_path=repo,
        dry_run=False,
        runner=_fake_runner,
    )
    assert live2["created_proposals"] == []

    # 3. Accepting proposals executes real immutable supersede (fix_drifted) and archive (retire_stale)
    props = {p["kind"]: p for p in list_proposals(engine, status="pending")["proposals"]}
    assert set(props.keys()) == {"fix_drifted", "retire_stale"}

    fix_res = accept_proposal(engine, props["fix_drifted"]["id"])
    new_id = fix_res["result_node_id"]
    assert new_id is not None
    assert engine.get(n_drift)["node"]["status"] == "superseded"
    assert engine.get(n_drift)["node"]["superseded_by"] == new_id
    assert "Qwen3-Embedding-4B" in engine.get(new_id)["node"]["rule"]

    ret_res = accept_proposal(engine, props["retire_stale"]["id"])
    assert ret_res["result_node_id"] == n_obs
    assert engine.get(n_obs)["node"]["status"] == "archived"


def test_audit_reclassify_changes_priority_type_and_migrates_misscoped(tmp_path: Path) -> None:
    repo = tmp_path / "repo-a"
    repo.mkdir()
    (repo / ".git").mkdir()

    engine = BrainEngine(tmp_path / "brain")
    n_reclass = engine.add_atom(
        kind="decision",
        scope="repo-a",
        content="某个低频管理页表格每页默认 20 行。",
        meta={"severity": "P1"},
    )["node"]["id"]
    n_migrate = engine.add_atom(
        kind="lesson",
        scope="repo-a",
        content="SSH Center 下发受管区块必须做字符白名单校验。",
        meta={"severity": "P1"},
    )["node"]["id"]

    def _runner(**kwargs: Any) -> dict[str, Any]:
        return {
            "findings": [
                {
                    "id": n_reclass,
                    "verdict": "reclassify",
                    "evidence": "web/pages/admin.tsx",
                    "reason": "低频局部细节不应占用 P1 L0 预算，降为 P2 lesson",
                    "suggested_severity": "P2",
                    "suggested_type": "lesson",
                    "suggested_rule": "管理页列表默认 pageSize=20。",
                    "confidence": 0.9,
                },
                {
                    "id": n_migrate,
                    "verdict": "misscoped",
                    "evidence": "rg 'ssh-center' returned 0 matches",
                    "reason": "属于 ssh-center 仓库而非 repo-a",
                    "suggested_scope": "ssh-center",
                    "confidence": 0.95,
                },
            ]
        }

    res = audit_repo_memories(engine, scope="repo-a", repo_path=repo, dry_run=False, runner=_runner)
    assert res["summary"]["reclassify"] == 1
    assert res["summary"]["misscoped"] == 1
    assert len(res["created_proposals"]) == 2

    by_target = {p["target_node_ids"][0]: p for p in list_proposals(engine, status="pending")["proposals"]}
    # Accept reclassify -> supersedes with new severity P2 + type lesson + distilled rule
    r1 = accept_proposal(engine, by_target[n_reclass]["id"])
    new1 = engine.get(r1["result_node_id"])["node"]
    assert engine.get(n_reclass)["node"]["status"] == "superseded"
    assert new1["severity"] == "P2"
    assert new1["type"] == "lesson"
    assert new1["rule"] == "管理页列表默认 pageSize=20。"

    # Accept misscoped migration -> supersedes old repo-a node with new ssh-center node
    r2 = accept_proposal(engine, by_target[n_migrate]["id"])
    new2 = engine.get(r2["result_node_id"])["node"]
    assert engine.get(n_migrate)["node"]["status"] == "superseded"
    assert new2["scope"] == "ssh-center"


def test_audit_all_filters_domain_scopes_handles_repo_prefix_and_isolates_failures(tmp_path: Path) -> None:
    from fos.audit import audit_all_repo_memories, list_auditable_repo_scopes

    git_root = tmp_path / "repos"
    git_root.mkdir()
    for name in ("alpha", "beta"):
        rdir = git_root / name
        rdir.mkdir()
        (rdir / ".git").mkdir()

    engine = BrainEngine(tmp_path / "brain")
    # Global & domain scopes MUST NOT be treated as local git repos
    engine.add_atom(kind="principle", scope="global", content="保持最小改动", meta={"severity": "P0"})
    engine.add_atom(kind="decision", scope="eng/frontend/shadcn", content="前端统一使用 shadcn Base UI", meta={"severity": "P1"})
    # Repo with `repo/` prefix + plain repo + missing local checkout repo
    n_alpha = engine.add_atom(kind="lesson", scope="repo/alpha", content="alpha 接口必须带幂等键", meta={"severity": "P1"})["node"]["id"]
    engine.add_atom(kind="lesson", scope="beta", content="beta 队列消费者必须显式 ack", meta={"severity": "P1"})
    engine.add_atom(kind="lesson", scope="ghost-repo", content="ghost 仓库本地未检出", meta={"severity": "P1"})

    assert list_auditable_repo_scopes(engine) == ["alpha", "beta", "ghost-repo"]

    seen_repos: list[str] = []

    def _runner(*, repo_path: Path, **kwargs: Any) -> dict[str, Any]:
        seen_repos.append(repo_path.name)
        if repo_path.name == "beta":
            raise RuntimeError("simulated codex timeout on beta")
        return {
            "findings": [
                {
                    "id": n_alpha,
                    "verdict": "verified",
                    "evidence": "src/api.py",
                    "reason": "幂等键校验存在",
                    "confidence": 0.95,
                }
            ]
        }

    report = audit_all_repo_memories(
        engine,
        root_dir=git_root,
        concurrency=2,
        stagger_s=0.0,
        dry_run=False,
        runner=_runner,
    )
    assert sorted(seen_repos) == ["alpha", "beta"]
    assert report["audited_repos"] == 1
    assert report["failed_repos"] == 1
    assert report["skipped_repos"] == 1
    assert report["skipped"][0]["scope"] == "ghost-repo"
    assert report["skipped"][0]["reason"] == "repo_not_found"
    assert report["failed"][0]["scope"] == "beta"
    assert report["verified_refreshed"] == 1
    assert engine.get(n_alpha)["node"]["verified_at"] != ""
