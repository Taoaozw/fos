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
