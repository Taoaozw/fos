"""Tests for Cross-Project Preference & Governance Proposal Inbox."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fos.cli import run_cli
from fos.engine import BrainEngine
from fos.proposals import (
    ProposalStateError,
    accept_proposal,
    discover_owned_repos,
    dismiss_proposal,
    list_proposals,
    scan_proposals,
)
from fos.server import create_app


class _FakeProposalReasoner:
    name = "fake:proposal"

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.calls = 0

    def complete_json(self, prompt: str, *, system: str = "") -> dict[str, Any] | None:
        self.calls += 1
        return {"proposals": self.items}


def _init_git_repo(path: Path, *, author_name: str, author_email: str, origin: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", author_name], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", author_email], check=True)
    if origin:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    (path / "README.md").write_text(f"# {path.name}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


def test_owned_repo_discovery_excludes_third_party_and_scans_manifests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fos.proposals._current_git_identity", lambda: ("38341571+taoaozw@users.noreply.github.com", "tao"))

    # Two owned repos with React + TypeScript + shadcn
    r1 = tmp_path / "repo-a"
    _init_git_repo(r1, author_name="TAO", author_email="38341571+Taoaozw@users.noreply.github.com")
    (r1 / "web").mkdir()
    (r1 / "web" / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^19.0.0", "tailwindcss": "^4.0.0"}, "devDependencies": {"typescript": "^5.8.0", "vite": "^6.0.0"}}),
        encoding="utf-8",
    )
    (r1 / "web" / "components.json").write_text(json.dumps({"style": "base-vega"}), encoding="utf-8")

    r2 = tmp_path / "repo-b"
    _init_git_repo(r2, author_name="TAO", author_email="38341571+Taoaozw@users.noreply.github.com")
    (r2 / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^19.0.0", "tailwindcss": "^4.1.0"}, "devDependencies": {"typescript": "^5.8.0", "vite": "^6.0.0"}}),
        encoding="utf-8",
    )
    (r2 / "components.json").write_text(json.dumps({"style": "base-vega"}), encoding="utf-8")

    # Third-party clone (different author & remote) MUST be excluded
    third = tmp_path / "third-party-oss"
    _init_git_repo(
        third,
        author_name="Random Maintainer",
        author_email="oss@example.org",
        origin="https://github.com/someone-else/oss.git",
    )
    (third / "Cargo.toml").write_text("[package]\nname='oss'\n", encoding="utf-8")

    owned = discover_owned_repos(root_dir=tmp_path)
    assert [p.name for p in owned] == ["repo-a", "repo-b"]

    engine = BrainEngine(tmp_path / "brain")
    res = scan_proposals(engine, root_dir=tmp_path)
    assert res["owned_repos"] == ["repo-a", "repo-b"]
    assert "React" in res["recurring_stack_tags"]
    assert "shadcn Base UI" in res["recurring_stack_tags"]
    # Bare manifest stack overlap alone MUST NOT generate noisy stack-selection promote_global proposals
    assert res["created"] == 0

    # Once >=2 repos have real recurring enforceable constraint nodes, promote_global P1 IS proposed
    engine.add_atom(
        kind="decision",
        scope="repo-a",
        content="应用页面交互控件必须复用 components/ui 共享组件，禁止直接手写原生控件。",
    )
    engine.add_atom(
        kind="decision",
        scope="repo-b",
        content="应用页面交互控件必须复用 components/ui 共享组件，禁止手写原生控件。",
    )
    res2 = scan_proposals(engine, root_dir=tmp_path, force=True)
    assert res2["created"] >= 1
    assert any(p["kind"] == "promote_global" and p["proposed_severity"] == "P1" for p in res2["new_proposals"])


def test_proposal_lifecycle_accept_merge_cooldown_and_rebuild(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")

    # Two overlapping global P0 principles -> merge_dedup target
    p1 = engine.add_atom(
        kind="principle",
        scope="global",
        content="必须采用最小可行局部改动，保持统一抽象与少量正交原语，状态转换显式最少。",
        meta={"severity": "P0"},
    )["node"]["id"]
    p2 = engine.add_atom(
        kind="principle",
        scope="global",
        content="必须以最小正确改动解决问题，使用少量正交原语与统一抽象，避免隐藏副作用。",
        meta={"severity": "P0"},
    )["node"]["id"]

    # Two repo-level decisions with same UI preference -> promote_global target
    d1 = engine.add_atom(
        kind="decision",
        scope="flowxy-gateway",
        content="前端交互一律使用 shadcn components/ui 共享组件与语义主题 Token，禁止手写原生 button 和 input。",
    )["node"]["id"]
    d2 = engine.add_atom(
        kind="decision",
        scope="ssh-center",
        content="所有前端 UI 必须使用 shadcn components/ui 组件与语义主题 Token，禁止裸写原生 button 或 input。",
    )["node"]["id"]

    fake = _FakeProposalReasoner(
        [
            {
                "kind": "promote_global",
                "topic_key": "frontend-shadcn-semantic-tokens",
                "title": "前端统一使用 shadcn 组件与语义主题 Token",
                "proposed_type": "principle",
                "proposed_severity": "P1",
                "proposed_rule": "所有前端界面统一使用 shadcn 共享组件与语义主题 Token，禁止裸写原生交互控件或硬编码色板。",
                "proposed_rationale": "在 flowxy-gateway 与 ssh-center 两个项目中独立重复沉淀。",
                "proposed_guard_hint": "ESLint 禁止在 components/ui 外使用原生 button/input/select。",
                "proposed_trigger_when": ["新建前端", "shadcn UI", "Tailwind 样式"],
                "evidence_repos": ["flowxy-gateway", "ssh-center"],
                "target_node_ids": [d1, d2],
                "confidence": 0.92,
            },
            {
                "kind": "merge_dedup",
                "topic_key": "merge-core-engineering-prns",
                "title": "合并重叠的最小改动与正交原语全局原则",
                "proposed_type": "principle",
                "proposed_severity": "P0",
                "proposed_rule": "采用最小可行局部改动，以统一抽象与少量正交原语构建显式最少状态转换，禁止隐藏副作用。",
                "proposed_rationale": "合并重叠 P0 原则以节省 L0 唤醒内核 Token。",
                "proposed_guard_hint": "",
                "proposed_trigger_when": ["代码改动", "架构设计"],
                "evidence_repos": ["global"],
                "target_node_ids": [p1, p2],
                "confidence": 0.9,
            },
        ]
    )

    scan1 = scan_proposals(engine, root_dir=tmp_path / "empty_repos", reasoner=fake)
    assert scan1["created"] == 2
    assert fake.calls == 1

    # Second scan with unchanged signals hits cache and creates 0 duplicates
    scan2 = scan_proposals(engine, root_dir=tmp_path / "empty_repos", reasoner=fake)
    assert scan2["cache_hit"] is True
    assert scan2["created"] == 0
    assert fake.calls == 1

    props = list_proposals(engine, status="pending")["proposals"]
    by_kind = {p["kind"]: p for p in props}
    promote_id = by_kind["promote_global"]["id"]
    merge_id = by_kind["merge_dedup"]["id"]

    # 1. Accept merge_dedup -> creates new global P0 and supersedes BOTH p1 and p2
    m_res = accept_proposal(engine, merge_id)
    merged_id = m_res["result_node_id"]
    assert merged_id is not None
    with engine.snapshots.open_current(read_only=True) as db:
        assert db.get_node(p1).status == "superseded"  # type: ignore[union-attr]
        assert db.get_node(p2).status == "superseded"  # type: ignore[union-attr]
        assert db.get_node(p2).superseded_by == merged_id  # type: ignore[union-attr]

    # 2. Re-accepting already resolved proposal raises PROPOSAL_ALREADY_RESOLVED
    with pytest.raises(ProposalStateError) as exc_info:
        accept_proposal(engine, merge_id)
    assert exc_info.value.error_code == "PROPOSAL_ALREADY_RESOLVED"

    # 3. Dismiss promote_global -> cooldown suppresses same 2-repo evidence on rescan
    dismiss_proposal(engine, promote_id, reason="暂不需要")
    scan3 = scan_proposals(engine, root_dir=tmp_path / "empty_repos", reasoner=fake, force=True)
    assert scan3["created"] == 0
    assert scan3["skipped_cooldown"] == 1

    # 4. Adding a THIRD corroborating repo wakes up the dismissed proposal
    d3 = engine.add_atom(
        kind="decision",
        scope="goto",
        content="前端共享组件禁止裸写原生按钮和输入框。",
    )["node"]["id"]
    fake.items[0]["evidence_repos"] = ["flowxy-gateway", "goto", "ssh-center"]
    fake.items[0]["target_node_ids"] = [d1, d2, d3]
    scan4 = scan_proposals(engine, root_dir=tmp_path / "empty_repos", reasoner=fake, force=True)
    assert scan4["created"] == 1
    new_promote_id = scan4["new_proposals"][0]["id"]

    # 5. Accept woken promote_global -> attaches SUPPORTS edges & survives rebuild_from_ledger()
    acc = accept_proposal(engine, new_promote_id)
    promoted_id = acc["result_node_id"]
    assert promoted_id is not None
    engine.rebuild_from_ledger()
    with engine.snapshots.open_current(read_only=True) as db:
        assert db.get_node(p1).status == "superseded"  # type: ignore[union-attr]
        assert db.get_node(p2).status == "superseded"  # type: ignore[union-attr]
        edges = db.get_edges(promoted_id)
        support_srcs = {e.src_id for e in edges if e.edge_type == "SUPPORTS" and e.dst_id == promoted_id}
        assert {d1, d2, d3}.issubset(support_srcs)


def test_l0_budget_guard_and_http_cli_proposals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "fos_home"
    engine = BrainEngine(home / "brain")

    # Fill global L0 close to 300 tokens (~11 P0 rules)
    for i in range(11):
        engine.add_atom(
            kind="principle",
            scope="global",
            content=f"全局工程红线编号 {i}：所有关键接口变更都必须具备完整的契约测试、错误码枚举以及回滚验证机制。",
            meta={"severity": "P0"},
        )
    d1 = engine.add_atom(kind="decision", scope="app-a", content="使用 shadcn Base UI 组件库构建管理界面。")["node"]["id"]
    d2 = engine.add_atom(kind="decision", scope="app-b", content="使用 shadcn Base UI 组件库构建控制台界面。")["node"]["id"]

    fake = _FakeProposalReasoner(
        [
            {
                "kind": "promote_global",
                "topic_key": "ui-p0-overflow-test",
                "title": "全局 P0 预算溢出守卫测试",
                "proposed_type": "principle",
                "proposed_severity": "P0",
                "proposed_rule": "所有前端项目强制采用 React + TypeScript + Vite + Tailwind CSS v4 + shadcn Base UI 与语义主题 Token，严禁硬编码颜色。",
                "proposed_rationale": "跨仓统一 UI。",
                "proposed_guard_hint": "",
                "proposed_trigger_when": ["前端 UI", "shadcn"],
                "evidence_repos": ["app-a", "app-b"],
                "target_node_ids": [d1, d2],
                "confidence": 0.9,
            }
        ]
    )
    scan_res = scan_proposals(engine, root_dir=tmp_path / "none", reasoner=fake)
    pid = scan_res["new_proposals"][0]["id"]

    app = create_app(engine=engine, token_hash="")
    tc = TestClient(app)

    # GET /v1/proposals returns pending proposal and L0 budget
    r_list = tc.get("/v1/proposals?status=pending")
    assert r_list.status_code == 200
    assert r_list.json()["counts"]["pending"] == 1

    # Accepting as P0 hits HTTP 422 L0_BUDGET_EXCEEDED
    r_fail = tc.post(f"/v1/proposals/{pid}/accept", json={})
    assert r_fail.status_code == 422
    assert r_fail.json()["detail"]["error_code"] == "L0_BUDGET_EXCEEDED"

    # Downgrading to P1 via CLI succeeds cleanly!
    rc = run_cli(["--home", str(home), "proposals", "accept", pid, "--severity", "P1", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["proposal"]["proposed_severity"] == "P1"
    assert payload["result_node_id"] is not None


def test_proposals_blocks_manifest_only_and_rephrased_dismissed(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")
    d1 = engine.add_atom(kind="decision", scope="fops", content="单机 Go 工具嵌入式存储优先使用无 CGO 的 modernc.org/sqlite。")["node"]["id"]
    d2 = engine.add_atom(kind="decision", scope="ssh-center", content="Go 单机工具需要关系库时使用无 CGO 的 modernc.org/sqlite。")["node"]["id"]

    # 1. Bare manifest-only proposal (target_node_ids=[]) MUST be rejected by hard invariant
    fake_stack = _FakeProposalReasoner(
        [
            {
                "kind": "promote_global",
                "topic_key": "frontend-default-stack",
                "title": "前端默认栈",
                "proposed_type": "decision",
                "proposed_severity": "P1",
                "proposed_rule": "新建管理端默认使用 React、TypeScript、Vite、Bun、Tailwind CSS v4。",
                "evidence_repos": ["fops", "ssh-center", "fos"],
                "target_node_ids": [],
                "confidence": 0.95,
            },
            {
                "kind": "promote_global",
                "topic_key": "go-pure-sqlite",
                "title": "本地 Go 工具优先用纯 Go SQLite",
                "proposed_type": "decision",
                "proposed_severity": "P1",
                "proposed_rule": "单机 Go 工具需要嵌入式关系数据库时默认使用无 CGO 的 modernc.org/sqlite。",
                "proposed_trigger_when": ["Go 工具", "SQLite", "modernc"],
                "evidence_repos": ["fops", "ssh-center"],
                "target_node_ids": [d1, d2],
                "confidence": 0.9,
            },
        ]
    )
    scan1 = scan_proposals(engine, root_dir=tmp_path / "none", reasoner=fake_stack)
    assert scan1["created"] == 1
    pid = scan1["new_proposals"][0]["id"]

    # 2. Dismiss it, then have LLM rephrase title & invent a brand-new topic_key with same repos
    dismiss_proposal(engine, pid, reason="不需要全局技术栈选型")
    fake_rephrased = _FakeProposalReasoner(
        [
            {
                "kind": "promote_global",
                "topic_key": "local-cli-default-golang-sqlite",
                "title": "本地工具默认采用 Go 与 SQLite",
                "proposed_type": "decision",
                "proposed_severity": "P1",
                "proposed_rule": "单用户单机 Go 工具需要嵌入式关系库时默认使用无 CGO 的 modernc.org/sqlite。",
                "proposed_trigger_when": ["本地工具", "Go", "SQLite"],
                "evidence_repos": ["fops", "ssh-center"],
                "target_node_ids": [d1, d2],
                "confidence": 0.9,
            }
        ]
    )
    scan2 = scan_proposals(engine, root_dir=tmp_path / "none", reasoner=fake_rephrased, force=True)
    assert scan2["created"] == 0
    assert scan2["skipped_cooldown"] == 1
