"""Tests for passive hindsight collectors: antigravity, claude, codex, fops."""

import json
from pathlib import Path

from fos.ingest import collect_hindsight_events
from fos.models import now_iso


def test_ingest_collectors_filter_chatter_redact_and_track_cursors(tmp_path: Path) -> None:
    ts = now_iso()
    cursor_path = tmp_path / "cursors.json"

    # 1. Antigravity transcript.jsonl
    agy_log = tmp_path / "agy" / "conv-1" / ".system_generated" / "logs" / "transcript.jsonl"
    agy_log.parent.mkdir(parents=True)
    agy_log.write_text(
        "\n".join(
            [
                json.dumps({"type": "USER_INPUT", "content": "好的", "created_at": ts}),
                json.dumps({"type": "PLANNER_RESPONSE", "content": "Ran rsync --mkpath on macOS", "status": "DONE", "created_at": ts}),
                json.dumps(
                    {
                        "type": "USER_INPUT",
                        "content": "不对，不要用 rsync --mkpath，macOS openrsync 不支持，顺便 key=sk-proj-1234567890abcdefghijklmnop",
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
    assert agy_events[0]["kind"] == "lesson"
    assert "sk-proj-" not in agy_events[0]["raw"]
    assert "[REDACTED:OPENAI_KEY]" in agy_events[0]["raw"]

    # Re-running with same cursor yields 0 duplicates
    assert collect_hindsight_events("antigravity", since="24h", root_dir=tmp_path / "agy", cursor_path=cursor_path) == []

    # 2. Claude Code session jsonl
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
                        "message": {"content": "我们最终决定采用 SQLite WAL + sqlite-vec 作为默认检索架构"},
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

    # 3. Codex session jsonl
    cx_log = tmp_path / "codex" / "2026" / "11" / "22" / "rollout.jsonl"
    cx_log.parent.mkdir(parents=True)
    cx_log.write_text(
        json.dumps(
            {
                "type": "response_item",
                "timestamp": ts,
                "payload": {
                    "id": "cx1",
                    "cwd": "/Users/clive/project/fos",
                    "role": "user",
                    "text": "Actually never use bare pip install; always use uv sync in this repo.",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cx_events = collect_hindsight_events("codex", since="24h", root_dir=tmp_path / "codex", cursor_path=cursor_path)
    assert len(cx_events) == 1
    assert cx_events[0]["scope"] == "fos"

    # 4. pi session jsonl (transient noise filtered, real episode captures forward Resolution)
    pi_log = tmp_path / "pi" / "proj" / "sess.jsonl"
    pi_log.parent.mkdir(parents=True)
    pi_log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "id": "pi-1", "cwd": "/Users/clive/project/zhijiang", "timestamp": ts}),
                # Transient edit mismatch -> must be filtered out
                json.dumps({"type": "message", "timestamp": ts, "message": {"role": "toolResult", "isError": True, "content": [{"type": "text", "text": "Found 2 occurrences of edits[2] in index.html. Each oldText must be unique."}]}}),
                json.dumps({"type": "message", "timestamp": ts, "message": {"role": "assistant", "content": [{"type": "text", "text": "Opening mirror page with Playwright"}]}}),
                json.dumps({"type": "message", "timestamp": ts, "message": {"role": "toolResult", "isError": True, "content": [{"type": "text", "text": "PageNavigationTimeoutError: page.goto timed out after 15000ms waiting for load"}]}}),
                json.dumps({"type": "message", "timestamp": ts, "message": {"role": "assistant", "content": [{"type": "text", "text": "根因是第三方追踪脚本阻塞了 load 事件，改用 wait_until='domcontentloaded' 后解决。"}]}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    pi_events = collect_hindsight_events("pi", since="24h", root_dir=tmp_path / "pi", cursor_path=cursor_path)
    assert len(pi_events) == 1
    assert pi_events[0]["scope"] == "zhijiang"
    assert pi_events[0]["meta"]["has_resolution"] is True
    assert "Resolution:" in pi_events[0]["raw"]
    assert "domcontentloaded" in pi_events[0]["raw"]

    # 5. fops trajectory jsonl
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
