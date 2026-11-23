"""Tests for high-compounding hindsight collectors: git fix commits + user taste/decision sessions + fops."""

import json
import subprocess
from pathlib import Path

from fos.ingest import collect_hindsight_events
from fos.models import now_iso


def test_ingest_git_and_high_signal_chat_collectors(tmp_path: Path) -> None:
    ts = now_iso()
    cursor_path = tmp_path / "cursors.json"

    # 1. Git fix(...) commit collector (modeled on fops LESSONS.md reviewed-through)
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "deploy.sh").write_text("rsync dist/ host:/srv\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "chore: initial commit"], check=True)

    (repo / "deploy.sh").write_text("ssh host mkdir -p /srv && rsync dist/ host:/srv\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "-q",
            "-m",
            "fix(deploy): create remote dir via ssh instead of --mkpath for macOS openrsync",
        ],
        check=True,
    )
    git_events = collect_hindsight_events("git", since="7d", root_dir=repo, cursor_path=cursor_path)
    assert len(git_events) == 1
    assert git_events[0]["scope"] == "demo-repo"
    assert git_events[0]["meta"]["evidence_ref"].startswith("git:")
    assert "--mkpath" in git_events[0]["raw"]
    # Cursor reviewed_through prevents re-collecting same commit
    assert collect_hindsight_events("git", since="7d", root_dir=repo, cursor_path=cursor_path) == []

    # 2. Antigravity transcript: captures user taste correction & redacts secrets; drops chatter & ops tickets
    agy_log = tmp_path / "agy" / "conv-1" / ".system_generated" / "logs" / "transcript.jsonl"
    agy_log.parent.mkdir(parents=True)
    agy_log.write_text(
        "\n".join(
            [
                json.dumps({"type": "USER_INPUT", "content": "<USER_REQUEST>\n好的\n</USER_REQUEST>", "created_at": ts}),
                json.dumps({"type": "USER_INPUT", "content": "<USER_REQUEST>\n为什么一直返回 400.你看下具体的报错信息\n</USER_REQUEST>", "created_at": ts}),
                json.dumps(
                    {
                        "type": "USER_INPUT",
                        "content": "<USER_REQUEST>\n不要 mcp，我希望他是纯 cli 形式的，不要过度抽象 (key=sk-proj-1234567890abcdefghijklmnop)\n</USER_REQUEST>",
                        "created_at": ts,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    agy_events = collect_hindsight_events("antigravity", since="24h", root_dir=tmp_path / "agy", cursor_path=cursor_path)
    assert len(agy_events) == 1
    assert agy_events[0]["kind"] == "principle"
    assert agy_events[0]["meta"]["severity"] == "P0"
    assert "sk-proj-" not in agy_events[0]["raw"]
    assert "[REDACTED:OPENAI_KEY]" in agy_events[0]["raw"]
    assert collect_hindsight_events("antigravity", since="24h", root_dir=tmp_path / "agy", cursor_path=cursor_path) == []

    # 3. Claude Code session: captures explicit architectural decision
    cc_log = tmp_path / "claude" / "-Users-clive-project-fops" / "sess1.jsonl"
    cc_log.parent.mkdir(parents=True)
    cc_log.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "cwd": "/Users/clive/project/fops", "sessionId": "sess1", "timestamp": ts, "message": {"content": "hello"}}),
                json.dumps(
                    {
                        "type": "user",
                        "cwd": "/Users/clive/project/fops",
                        "sessionId": "sess1",
                        "timestamp": ts,
                        "message": {"content": "我们决定采用 SQLite WAL + sqlite-vec 作为默认检索架构"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cc_events = collect_hindsight_events("claude", since="24h", root_dir=tmp_path / "claude", cursor_path=cursor_path)
    assert len(cc_events) == 1
    assert cc_events[0]["kind"] == "decision"
    assert cc_events[0]["scope"] == "fops"

    # 4. Codex session: captures user correction with input_text block
    cx_log = tmp_path / "codex" / "2026" / "11" / "22" / "rollout.jsonl"
    cx_log.parent.mkdir(parents=True)
    cx_log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "timestamp": ts, "payload": {"id": "cx1", "cwd": "/Users/clive/project/fos"}}),
                json.dumps(
                    {
                        "type": "response_item",
                        "timestamp": ts,
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Never use bare pip install; always use uv sync in this repo."}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cx_events = collect_hindsight_events("codex", since="24h", root_dir=tmp_path / "codex", cursor_path=cursor_path)
    assert len(cx_events) == 1
    assert cx_events[0]["scope"] == "fos"

    # 5. Pi session: ignores raw toolResult noise, captures user taste/correction with Resolution
    pi_log = tmp_path / "pi" / "proj" / "sess.jsonl"
    pi_log.parent.mkdir(parents=True)
    pi_log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "id": "pi-1", "cwd": "/Users/clive/project/zhijiang", "timestamp": ts}),
                json.dumps({"type": "message", "timestamp": ts, "message": {"role": "toolResult", "isError": True, "content": [{"type": "text", "text": "Found 2 occurrences of edits[2]"}]}}),
                json.dumps({"type": "message", "timestamp": ts, "message": {"role": "user", "content": [{"type": "text", "text": "不要靠口头文档提醒，改用静态 Guard Test 在 CI 里自动拦截。"}]}}),
                json.dumps({"type": "message", "timestamp": ts, "message": {"role": "assistant", "content": [{"type": "text", "text": "收到，已把该规范固化为自动化 Guard Test 纳入 make check。"}]}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    pi_events = collect_hindsight_events("pi", since="24h", root_dir=tmp_path / "pi", cursor_path=cursor_path)
    assert len(pi_events) == 1
    assert pi_events[0]["scope"] == "zhijiang"
    assert "Resolution:" in pi_events[0]["raw"]

    # 6. fops trajectory jsonl
    fops_log = tmp_path / "fops" / "runs.jsonl"
    fops_log.parent.mkdir(parents=True)
    fops_log.write_text(
        json.dumps(
            {
                "run_id": "r-42",
                "project": "fops",
                "ts": ts,
                "command": "rsync --mkpath dist/ host:/srv/app",
                "exit_code": 1,
                "stderr": "rsync: --mkpath: unknown option",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    fops_events = collect_hindsight_events("fops", since="24h", root_dir=tmp_path / "fops", cursor_path=cursor_path)
    assert len(fops_events) == 1
    assert fops_events[0]["kind"] == "lesson"
    assert fops_events[0]["scope"] == "fops"

    # 7. docs collector (LESSONS.md / docs/agents/*.md with context7 boilerplate stripped & SHA cursor;
    #    repo-root AGENTS.md / CLAUDE.md skipped so local agent wrappers never pollute memory)
    doc_dir = tmp_path / "design-repo"
    (doc_dir / "docs" / "agents").mkdir(parents=True)
    (doc_dir / "AGENTS.md").write_text(
        "# Project instructions\n- Reply in Simplified Chinese. Use English for code comments.\n",
        encoding="utf-8",
    )
    (doc_dir / "LESSONS.md").write_text(
        "# 代码规范\n\n## 前端规范\n所有 UI 一律使用 shadcn Base UI 组件和语义主题 Token，禁止写死十六进制颜色或 Tailwind 调色板颜色。\n"
        "<!-- context7 -->\nUse ctx7 CLI boilerplate\n<!-- context7 -->\n",
        encoding="utf-8",
    )
    (doc_dir / "docs" / "agents" / "frontend-conventions.md").write_text(
        "# 前端设计规范\n\n"
        "## 字体规则\n" + ("按钮、徽章和 Tab 控件统一使用 font-normal，避免中文字重发虚。" * 25) + "\n\n"
        "## 状态边界\n" + ("表单验证必须返回可枚举的错误码，不得静默吞掉错误。" * 25) + "\n",
        encoding="utf-8",
    )
    # Limit=2 takes LESSONS.md (1 section) + first section of frontend-conventions.md;
    # AGENTS.md is skipped; frontend-conventions.md must NOT be marked complete in cursor until fully emitted
    partial = collect_hindsight_events("docs", root_dir=doc_dir, cursor_path=cursor_path, limit=2)
    assert len(partial) == 2
    assert all("AGENTS.md" not in e["raw"] for e in partial)
    assert all("ctx7 CLI boilerplate" not in e["raw"] for e in partial)
    doc_events = collect_hindsight_events("docs", root_dir=doc_dir, cursor_path=cursor_path, limit=10)
    assert len(doc_events) == 2
    assert any("font-normal" in e["raw"] for e in doc_events)
    assert any("可枚举的错误码" in e["raw"] for e in doc_events)
    # Unchanged docs produce 0 duplicates via SHA-256 cursor
    assert collect_hindsight_events("docs", root_dir=doc_dir, cursor_path=cursor_path) == []


def test_automated_hindsight_p0_does_not_mint_raw_golden_eval_cases(tmp_path: Path) -> None:
    from fos.engine import BrainEngine

    engine = BrainEngine(tmp_path / "brain")
    engine.add_atom(
        kind="principle",
        scope="global",
        source="hindsight:docs:global:gemini",
        content="Hindsight: [GEMINI.md · Overview]\nYou MUST inspect before guessing when uncertain.",
        meta={"severity": "P0"},
    )
    # Automated ingest must NOT prematurely freeze raw text as pinned golden recall cases
    assert engine.ledger.load_eval_cases() == []

    # Manual capture MUST continue to synthesize golden recall cases
    engine.add_atom(
        kind="principle",
        scope="global",
        source="manual",
        content="本地 Agent 工具采用 CLI + Skill 交付，禁止接入 MCP。",
        meta={"severity": "P0"},
    )
    cases = engine.ledger.load_eval_cases()
    assert len(cases) == 1
    assert cases[0].pinned is True


def test_antigravity_scope_inference_and_non_redline_first_principles_demotion(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cursor_path = tmp_path / "cursors.json"
    agy_log = tmp_path / "agy" / "conv-fos" / ".system_generated" / "logs" / "transcript.jsonl"
    agy_log.parent.mkdir(parents=True)
    agy_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "PLANNER_RESPONSE",
                        "content": "Checking /Users/demo/project/demo/fos/src/fos/dream.py",
                        "created_at": ts,
                    }
                ),
                json.dumps(
                    {
                        "type": "USER_INPUT",
                        "content": "<USER_REQUEST>\n从第一性原理出发，现在新的这套长期记忆还要优化，大内容用 source_ref 指向原文\n</USER_REQUEST>",
                        "created_at": ts,
                    }
                ),
                json.dumps({"type": "PLANNER_RESPONSE", "content": "好的", "created_at": ts}),
                json.dumps({"type": "PLANNER_RESPONSE", "content": "ok", "created_at": ts}),
                json.dumps({"type": "PLANNER_RESPONSE", "content": "done", "created_at": ts}),
                json.dumps(
                    {
                        "type": "USER_INPUT",
                        "content": "<USER_REQUEST>\n[LSN-005] 禁止按整数截断。这些我感觉也是项目级别的啊？\n</USER_REQUEST>",
                        "created_at": ts,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    events = collect_hindsight_events("antigravity", since="24h", root_dir=tmp_path / "agy", cursor_path=cursor_path)
    assert len(events) == 2
    for ev in events:
        assert ev["scope"] == "fos"
        assert ev["kind"] != "principle"
        assert ev["meta"]["severity"] != "P0"


def test_ingest_multi_repo_workspace_skips_third_party_clones(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    monkeypatch.setattr("fos.proposals._current_git_identity", lambda: ("dev@example.com", "mydev"))

    def _mk_repo(path: Path, author: str, email: str, doc_text: str) -> None:
        path.mkdir(parents=True)
        subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(path), "config", "user.name", author], check=True)
        subprocess.run(["git", "-C", str(path), "config", "user.email", email], check=True)
        (path / "LESSONS.md").write_text(doc_text, encoding="utf-8")
        subprocess.run(["git", "-C", str(path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)

    _mk_repo(
        tmp_path / "my-app",
        "mydev",
        "dev@example.com",
        "## Core Boundary\nAll API handlers MUST return typed enumerable error codes and never leak stack traces.\n",
    )
    _mk_repo(
        tmp_path / "third-party-litellm",
        "upstream-oss",
        "oss@litellm.ai",
        "## Proxy Rules\nThird party upstream instructions that should never enter personal memory.\n",
    )

    events = collect_hindsight_events("docs", root_dir=tmp_path, cursor_path=tmp_path / "c.json", limit=20)
    scopes = {e["scope"] for e in events}
    assert "my-app" in scopes
    assert "third-party-litellm" not in scopes


def test_cross_device_evidence_ref_dedup_and_stage_only_ingest(tmp_path: Path) -> None:
    from fos.engine import BrainEngine

    engine = BrainEngine(tmp_path / "brain")
    r1 = engine.add_atom(
        kind="lesson",
        scope="fops",
        source="hindsight:git:fops",
        device="m3-max",
        content="Hindsight: fix(mw): bypass pgx local DNS lookup for SSH-tunneled PG hosts",
        meta={"evidence_ref": "git:3b9d4d7", "commit_sha": "3b9d4d7abcdef"},
        stage_only=True,
    )
    assert r1["staged"] is True
    assert r1["node"] is None
    # Active snapshot remains unpolluted until `fos dream`
    with engine.snapshots.open_current(read_only=True) as db:
        assert db.list_nodes(scope="fops") == []

    # Second device reporting the exact same commit SHA / evidence_ref is deduplicated
    r2 = engine.add_atom(
        kind="lesson",
        scope="fops",
        source="hindsight:git:fops",
        device="macbook-pro",
        content="Hindsight: fix(mw): bypass pgx local DNS lookup for SSH-tunneled PG hosts",
        meta={"evidence_ref": "git:3b9d4d7", "commit_sha": "3b9d4d7abcdef"},
        stage_only=True,
    )
    assert r2["deduplicated"] is True
    assert len(engine.ledger.load_all_atoms()) == 1


def test_codex_patrol_regressions_dream_ids_rebuild_etiquette_and_outbox(tmp_path: Path) -> None:
    import pytest
    from fos.dream import run_dream
    from fos.engine import BrainEngine
    from fos.errors import NotFoundError

    # 1. Staged doc + live manual decision + archive manual decision -> dream must archive the manual decision, NOT the staged doc
    engine = BrainEngine(tmp_path / "brain")
    engine.add_atom(
        kind="decision",
        scope="fops",
        source="hindsight:docs:fops",
        content="Hindsight: [ARCHITECTURE.md · Cache] Use SQLite WAL for offline cache persistence.",
        meta={"evidence_ref": "doc:docs/ARCHITECTURE.md#Cache:abc123"},
        stage_only=True,
    )
    manual = engine.add_atom(
        kind="decision",
        scope="fops",
        source="manual",
        content="Use fixed port 7622 for internal daemon testing only.",
    )
    manual_id = manual["node"]["id"]
    assert manual_id == "DEC-001"

    # Invalid archive target must raise BEFORE writing a poison atom into ledger_atoms
    before_count = len(engine.ledger.load_all_atoms())
    with pytest.raises(NotFoundError):
        engine.add_atom(
            kind="outcome",
            scope="fops",
            source="manual:archive",
            content="Archive DEC-999",
            meta={"archives": "DEC-999"},
        )
    assert len(engine.ledger.load_all_atoms()) == before_count

    engine.archive_node(manual_id, reason="no longer needed")

    # rebuild_from_ledger must NOT leak staged raw atoms into active snapshot
    engine.rebuild_from_ledger()
    with engine.snapshots.open_current(read_only=True) as db:
        active_after_rebuild = db.list_nodes(scope="fops")
        assert all("ARCHITECTURE.md" not in n.rule for n in active_after_rebuild)
        assert db.get_node(manual_id).status == "archived"

    # Dream must keep manual_id archived (not archive the staged doc instead)
    run_dream(engine, no_gate=True)
    with engine.snapshots.open_current(read_only=True) as db:
        m_node = db.get_node(manual_id)
        assert m_node is not None and m_node.status == "archived"
        assert "7622" in m_node.rule

    # 2. Line-level etiquette stripping preserves concrete technical constraint in the same section
    doc_dir = tmp_path / "repo-mixed"
    doc_dir.mkdir()
    (doc_dir / "LESSONS.md").write_text(
        "## Rules\n"
        "- Reply in Simplified Chinese.\n"
        "- Verify changes before concluding.\n"
        "- Every lease renewal MUST carry a monotonic fencing token validated by the storage backend.\n",
        encoding="utf-8",
    )
    events = collect_hindsight_events("docs", root_dir=doc_dir, cursor_path=tmp_path / "c2.json", limit=10)
    assert len(events) == 1
    assert "fencing token" in events[0]["raw"]
    assert "Simplified Chinese" not in events[0]["raw"]
    first_ref = events[0]["meta"]["evidence_ref"]

    # Updating the doc section produces a new evidence_ref hash so updates are not silently dropped
    (doc_dir / "LESSONS.md").write_text(
        "## Rules\n"
        "- Every lease renewal MUST carry a monotonic 64-bit fencing token validated atomically by PostgreSQL.\n",
        encoding="utf-8",
    )
    events2 = collect_hindsight_events("docs", root_dir=doc_dir, cursor_path=tmp_path / "c2.json", limit=10)
    assert len(events2) == 1
    assert events2[0]["meta"]["evidence_ref"] != first_ref
