"""Passive Hindsight collectors for Antigravity, Claude Code, Codex, and fops traces."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fos.errors import UsageError
from fos.models import now_iso, parse_timestamp
from fos.redact import redact_secrets

VALID_SOURCES = ("antigravity", "claude", "codex", "fops")

# Hindsight signal patterns: corrections, errors, retries, outcomes, explicit decisions
_CORRECTION_RE = re.compile(
    r"(?:不对|不是这样|不要用|禁止|别用|搞错了|纠正|记住|以后|下次|踩坑|应该用|改用|换成|"
    r"actually|instead|wrong|don't use|never use|do not use|remember to|lesson learned|fix:|root cause)",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(
    r"(?:traceback|exception|error:|exit code [1-9]|exited with code [1-9]|failed|fatal:|unsupported|not found|permission denied|报错|失败|中断)",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"(?:决定|选择|定稿|采用|选型|架构方案|放弃.*改用|decided to|we will use|chosen|architecture decision)",
    re.IGNORECASE,
)


def parse_since_duration(since: str | None) -> datetime | None:
    """Parse durations like '24h', '7d', '30m', or ISO8601 timestamp into UTC datetime."""
    if not since:
        return None
    s = since.strip().lower()
    m = re.fullmatch(r"(\d+)\s*([smhdw])", s)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        delta = {
            "s": timedelta(seconds=val),
            "m": timedelta(minutes=val),
            "h": timedelta(hours=val),
            "d": timedelta(days=val),
            "w": timedelta(weeks=val),
        }[unit]
        return datetime.now(timezone.utc) - delta
    return parse_timestamp(since)


def _classify_hindsight(text: str, *, is_error_event: bool = False) -> tuple[str, str] | None:
    """Return (kind, severity) if text carries hindsight signal, else None (drops chatter)."""
    clean = text.strip()
    if len(clean) < 18:
        return None
    if is_error_event or _ERROR_RE.search(clean):
        return ("lesson", "P1")
    if _CORRECTION_RE.search(clean):
        return ("lesson", "P0" if any(w in clean.lower() for w in ("禁止", "严禁", "never", "must not")) else "P1")
    if _DECISION_RE.search(clean):
        return ("decision", "P1")
    return None


def _extract_text_from_message(msg: Any) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    if block.get("type") == "text" and block.get("text"):
                        parts.append(str(block["text"]))
                    elif block.get("type") == "tool_result" and block.get("is_error"):
                        parts.append(f"Tool error: {block.get('content', '')}")
            return "\n".join(parts)
    return ""


def _infer_scope_from_path(path_str: str) -> str:
    if not path_str:
        return "global"
    p = Path(path_str)
    name = p.name.strip()
    if name and name not in ("/", ".", "Users", "home"):
        return name.lower()
    return "global"


def _load_cursors(cursor_path: Path) -> dict[str, int]:
    if not cursor_path.exists():
        return {}
    try:
        return json.loads(cursor_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cursors(cursor_path: Path, cursors: dict[str, int]) -> None:
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cursor_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cursors, indent=2) + "\n", encoding="utf-8")
    tmp.replace(cursor_path)


def collect_hindsight_events(
    source: str,
    *,
    since: str | None = "24h",
    root_dir: Path | None = None,
    cursor_path: Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Scan local AI/tool traces and extract hindsight-bearing events (secret-redacted)."""
    if source not in VALID_SOURCES:
        raise UsageError(f"Invalid ingest source '{source}'. Must be one of: {', '.join(VALID_SOURCES)}")

    cutoff = parse_since_duration(since)
    cursors = _load_cursors(cursor_path) if cursor_path else {}
    events: list[dict[str, Any]] = []

    if source == "antigravity":
        base = root_dir or (Path.home() / ".gemini" / "antigravity" / "brain")
        files = sorted(base.glob("*/.system_generated/logs/transcript.jsonl")) if base.exists() else []
    elif source == "claude":
        base = root_dir or (Path.home() / ".claude" / "projects")
        files = sorted(base.glob("*/*.jsonl")) if base.exists() else []
    elif source == "codex":
        base = root_dir or (Path.home() / ".codex" / "sessions")
        files = sorted(base.glob("**/*.jsonl")) if base.exists() else []
    else:  # fops
        base = root_dir or (Path.home() / ".fops")
        files = sorted(base.glob("**/*.jsonl")) if base.exists() else []

    for fpath in files:
        if len(events) >= limit:
            break
        try:
            mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if cutoff is not None and mtime < cutoff:
            continue

        key = str(fpath.resolve())
        start_line = cursors.get(key, 0)
        last_line = start_line
        prev_context = ""

        try:
            with fpath.open("r", encoding="utf-8", errors="replace") as fh:
                for line_no, line in enumerate(fh, start=1):
                    if line_no <= start_line:
                        continue
                    last_line = line_no
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ts = str(obj.get("created_at") or obj.get("timestamp") or obj.get("ts") or now_iso())
                    ts_dt = parse_timestamp(ts)
                    if cutoff is not None and ts_dt is not None and ts_dt < cutoff:
                        continue

                    extracted = _extract_record_by_source(source, obj, fpath)
                    if extracted is None:
                        continue
                    role, text, scope, is_err, session_id = extracted
                    if not text:
                        continue

                    if role in ("assistant", "action"):
                        prev_context = text[:240]
                        if not is_err:
                            continue

                    verdict = _classify_hindsight(text, is_error_event=is_err)
                    if verdict is None:
                        if role == "user":
                            prev_context = text[:240]
                        continue

                    kind, severity = verdict
                    combined = f"Context: {prev_context}\nHindsight: {text[:600]}" if prev_context else text[:600]
                    red = redact_secrets(combined)
                    events.append(
                        {
                            "kind": kind,
                            "scope": scope,
                            "source": f"hindsight:{source}:{session_id}",
                            "raw": red.text,
                            "ts": ts,
                            "meta": {
                                "severity": severity,
                                "collector": source,
                                "session_id": session_id,
                                "evidence_ref": f"{fpath.name}:L{line_no}",
                                "redacted_types": list(red.redacted_types),
                            },
                        }
                    )
                    if len(events) >= limit:
                        break
        except OSError:
            continue

        cursors[key] = last_line

    if cursor_path is not None:
        _save_cursors(cursor_path, cursors)

    return events


def _extract_record_by_source(
    source: str,
    obj: dict[str, Any],
    fpath: Path,
) -> tuple[str, str, str, bool, str] | None:
    """Normalize a single log line into (role, text, scope, is_error, session_id)."""
    if source == "antigravity":
        rtype = str(obj.get("type", ""))
        status = str(obj.get("status", ""))
        content = str(obj.get("content") or "")
        session_id = fpath.parent.parent.parent.name
        if rtype == "USER_INPUT":
            return ("user", content, "global", False, session_id)
        if rtype == "PLANNER_RESPONSE":
            return ("assistant", content, "global", status == "ERROR", session_id)
        if status == "ERROR" and content:
            return ("tool", content, "global", True, session_id)
        return None

    if source == "claude":
        rtype = str(obj.get("type", ""))
        session_id = str(obj.get("sessionId") or fpath.stem)
        scope = _infer_scope_from_path(str(obj.get("cwd") or ""))
        if rtype == "user":
            text = _extract_text_from_message(obj.get("message"))
            is_err = "Tool error:" in text
            return ("user", text, scope, is_err, session_id)
        if rtype == "assistant":
            text = _extract_text_from_message(obj.get("message"))
            return ("assistant", text, scope, False, session_id)
        return None

    if source == "codex":
        rtype = str(obj.get("type", ""))
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
        session_id = str(payload.get("id") or fpath.stem)
        scope = _infer_scope_from_path(str(payload.get("cwd") or ""))
        if rtype in ("response_item", "event_msg", "message"):
            role = str(payload.get("role") or "user")
            text = _extract_text_from_message(payload) or str(payload.get("text") or payload.get("message") or "")
            is_err = bool(payload.get("exit_code") not in (None, 0) or payload.get("is_error"))
            return (role, text, scope, is_err, session_id)
        return None

    # fops structured trajectory jsonl
    session_id = str(obj.get("run_id") or obj.get("session_id") or fpath.stem)
    scope = str(obj.get("project") or obj.get("scope") or "fops")
    exit_code = obj.get("exit_code")
    is_err = exit_code is not None and exit_code != 0
    cmd = str(obj.get("command") or obj.get("action") or "")
    err_out = str(obj.get("stderr") or obj.get("error") or obj.get("outcome") or obj.get("message") or "")
    text = f"{cmd} -> {err_out}".strip(" ->")
    if not text:
        return None
    return ("action", text, scope, is_err, session_id)
