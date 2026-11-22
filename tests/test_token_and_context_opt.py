"""Tests for token-efficiency & context-quality optimizations (TOON/RTK/Aider/QMD-inspired)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fos.cli import run_cli
from fos.engine import BrainEngine, build_l0_line, split_rule_and_rationale
from fos.models import CompiledNode
from fos.output import compact_source_ref, dumps_json, is_stdout_tty
from fos.tokens import estimate_tokens, extract_stemmed_terms, stem_en


def test_agent_pty_compact_json_and_compact_source_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Simulate an interactive PTY inside an AI agent harness -> must emit single-line compact JSON
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("FOS_PRETTY_JSON", raising=False)
    monkeypatch.delenv("FOS_COMPACT_JSON", raising=False)
    monkeypatch.setenv("CODEX_SANDBOX", "read-only")
    assert is_stdout_tty() is False
    assert "\n" not in dumps_json({"id": "LSN-001", "rule": "ok"})

    # Explicit FOS_PRETTY_JSON=1 still overrides for human debugging
    monkeypatch.setenv("FOS_PRETTY_JSON", "1")
    assert is_stdout_tty() is True
    assert "\n" in dumps_json({"id": "LSN-001", "rule": "ok"})

    # Repo-relative source_ref display inside repo vs ~/ outside
    repo = tmp_path / "my-repo"
    (repo / ".git").mkdir(parents=True)
    doc = repo / "docs" / "ARCHITECTURE.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Arch\n", encoding="utf-8")
    assert compact_source_ref(f"{doc.resolve()}#热路径", cwd=repo / "docs") == "docs/ARCHITECTURE.md#热路径"


def test_salient_l0_compression_and_project_first_boot(tmp_path: Path) -> None:
    # 1. Salient clause extraction preserves imperative action and strips doc filler + default seen:1
    n1 = CompiledNode(
        id="LSN-005",
        type="lesson",
        scope="goto",
        severity="P1",
        seen_count=1,
        rule="按 DESIGN.md，上游用量或配额允许小数时，必须在适配器中保留小数精度，禁止截断。详见 DESIGN.md。",
    )
    l0_1 = build_l0_line(n1)
    assert "seen:1" not in l0_1
    assert "DESIGN.md" not in l0_1
    assert "保留小数精度" in l0_1

    n2 = CompiledNode(
        id="LSN-009",
        type="lesson",
        scope="goto",
        severity="P0",
        seen_count=3,
        rule="禁止直接删除渠道记录",
    )
    assert "|3x]" in build_l0_line(n2)

    # 2. Project-first boot reserves budget for repo lessons/decisions despite 10 ambient global rules
    engine = BrainEngine(tmp_path / "brain")
    for i in range(10):
        engine.add_atom(
            kind="principle",
            scope="global",
            content=f"全局环境规则 #{i}：所有常规改动必须先阅读上下文再执行最小局部修改并验证测试。",
            meta={"severity": "P0", "source_ref": f"/Users/demo/.codex/AGENTS.md#sec{i}"},
        )
    p_lsn = engine.add_atom(
        kind="lesson",
        scope="my-service",
        content="网关转发热路径严禁直连 PostgreSQL，必须读取内存快照。",
        meta={"severity": "P1"},
    )["node"]["id"]
    p_dec = engine.add_atom(
        kind="decision",
        scope="my-service",
        content="控制台认证统一采用 AuthWebFilter，禁止引入 Spring Security。",
        meta={"severity": "P1"},
    )["node"]["id"]

    boot = engine.boot(project="my-service", budget=300)
    assert boot["used_tokens"] <= 300
    boot_ids = [e["id"] for e in boot["entries"]]
    assert p_lsn in boot_ids[:2]
    assert p_dec in boot_ids[:2]


def test_recall_tail_cutoff_exclude_ids_and_lean_cards(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "fos_home"
    engine = BrainEngine(home / "brain")

    hit_id = engine.add_atom(
        kind="decision",
        scope="gw",
        content="gateway 禁止连接业务库，必须通过 Redpanda compacted topic 加载配置快照。",
        meta={"severity": "P1", "guard_hint": "模块依赖单测检查"},
    )["node"]["id"]
    for i in range(6):
        engine.add_atom(
            kind="lesson",
            scope="gw",
            content=f"前端表格渲染优化备忘 #{i}：避免在大列表中创建内联闭包与重复排序。",
            meta={"severity": "P1", "guard_hint": "React profiler 检查"},
        )

    rec = engine.recall("gateway 连接业务库 Redpanda 配置快照", scope="gw", budget=1200)
    assert rec["results"][0]["id"] == hit_id
    # Anti-context-rot cutoff drops unrelated frontend lessons instead of filling 1200t budget
    assert rec["count"] <= 2
    # Lean L1 card strips guard_hint and graduated=False
    assert "guard_hint" not in rec["results"][0]
    assert "graduated" not in rec["results"][0]

    # --exclude omits specified IDs cleanly via CLI, and default CLI text output stays ultra-compact
    assert run_cli(["--home", str(home), "recall", "gateway Redpanda", "--scope", "gw"]) == 0
    txt_out = capsys.readouterr().out
    assert "# FOS L1" in txt_out
    assert hit_id in txt_out
    assert estimate_tokens(txt_out) < 120

    assert (
        run_cli(["--home", str(home), "recall", "gateway Redpanda", "--scope", "gw", "--exclude", hit_id, "--json"])
        == 0
    )
    excl_out = json.loads(capsys.readouterr().out)
    assert all(r["id"] != hit_id for r in excl_out["results"])


def test_mirror_catches_repo_prohibitions_and_preserves_triggers(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")
    # Unrelated global P0 principle must NOT spam conflicts
    engine.add_atom(
        kind="principle",
        scope="global",
        content="本地 Agent 工具采用 CLI + Skill 交付，禁止接入 MCP。",
        meta={"severity": "P0"},
    )
    # Repo P1 & P2 prohibitions MUST be caught as conflicts
    d_db = engine.add_atom(
        kind="decision",
        scope="gw",
        content="gateway 禁止连接业务数据库，配置从 Redpanda 加载。",
        meta={"severity": "P1", "trigger_when": ["修改 gateway 数据访问"]},
    )["node"]["id"]
    d_fk = engine.add_atom(
        kind="decision",
        scope="gw",
        content="数据库表结构禁止使用外键约束，关联一致性在应用层维护。",
        meta={"severity": "P2"},
    )["node"]["id"]
    l_sec = engine.add_atom(
        kind="lesson",
        scope="gw",
        content="console 禁止引入 Spring Security，统一由 AuthWebFilter 认证。",
        meta={"severity": "P1", "trigger_when": ["console 登录认证"]},
    )["node"]["id"]

    rep = engine.mirror("在 gw 里让 gateway 直连业务数据库并添加外键约束，同时 console 引入 Spring Security", scope="gw")
    conflict_ids = {c["id"] for c in rep["conflicts"]}
    assert {d_db, d_fk, l_sec} <= conflict_ids
    assert "PRN-001" not in conflict_ids
    # Non-stripped trigger_when is preserved on lesson_warnings
    warn_map = {w["id"]: w for w in rep["lesson_warnings"]}
    assert warn_map[l_sec]["trigger_when"] == ["console 登录认证"]


def test_split_rule_overflow_preservation_and_en_stemming() -> None:
    assert stem_en("numbers") == stem_en("numeric")
    assert stem_en("bikes") == stem_en("bike")
    assert "relocat" in extract_stemmed_terms("recent relocation")
    assert "relocat" in extract_stemmed_terms("she relocated last month")

    long_doc = (
        "网关转发层必须保持全异步零拷贝内存流转，禁止在热路径反序列化完整请求体；"
        "上游断开或客户端取消时必须在后台排空剩余缓冲并记录精确计费事件，详见架构规范。" * 2
    )
    rule, rationale = split_rule_and_rationale(long_doc)
    assert estimate_tokens(rule) <= 84
    assert len(rationale) > 10
