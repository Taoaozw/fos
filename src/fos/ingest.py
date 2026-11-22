"""Passive Episode-level Hindsight collectors for Antigravity, Claude Code, Codex, Pi, and fops traces.

Follows SOTA sleep-time memory practices (Hindsight / Songe / Claude Auto Dream):
- Filters transient tool glitches upfront (edit uniqueness mismatches, bare Exit code 1, interrupted calls).
- Captures full (Context + Problem -> Resolution) episodes by looking forward 1-5 turns for root cause & fix.
- Deduplicates within debugging bursts and tracks incremental per-file line cursors.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fos.errors import UsageError
from fos.models import now_iso, parse_timestamp
from fos.redact import redact_secrets

VALID_SOURCES = ("antigravity", "claude", "codex", "pi", "fops")
ALL_SOURCES_ALIAS = "all"

# Hindsight signal patterns: corrections, concrete errors, explicit decisions
_CORRECTION_RE = re.compile(
    r"(?:不对|不是这样|不要用|禁止|别用|搞错了|纠正|记住|以后不要|下次不要|踩坑|应该用|改用|换成|不要再|"
    r"actually\b|instead of\b|wrong\b|don't use\b|never use\b|do not use\b|remember to\b|lesson learned|root cause)",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(
    r"(?:traceback \(most recent|exception:|exit code [1-9]|exited with code [1-9]|fatal:|"
    r"unrecognized option|unknown option|permission denied|command failed|报错|失败中断|400|403|404|500|502|503)",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"(?:决定|最终选择|选型|架构方案|放弃.*改用|改用.*替代|统一用|一律用|"
    r"decided to\b|we will use\b|architecture decision|chosen to\b)",
    re.IGNORECASE,
)

# Wrapper / injected boilerplate stripping
_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*([\s\S]*?)\s*</USER_REQUEST>", re.IGNORECASE)
_BOILERPLATE_BLOCK_RE = re.compile(
    r"<(?:system-reminder|environment_context|permissions instructions|ADDITIONAL_METADATA|SYSTEM_MESSAGE|"
    r"USER_SETTINGS_CHANGE|command-message|local-command-stdout|local-command-caveat|INSTRUCTIONS)[^>]*>[\s\S]*?</[^>]+>",
    re.IGNORECASE,
)
_NOISE_PREFIXES = (
    "[image:",
    "# agents.md instructions",
    "<environment_context>",
    "another claude session",
    "base directory for this skill:",
    "created at:",
)
_NOISE_SUBSTRINGS = (
    "encountered error in step execution: file does not exist",
    "the user doesn't want to proceed with this tool use",
    "the tool call was rejected by the user",
)

# Transient tool glitches that have zero cross-session reusable value
_TRANSIENT_TOOL_PATTERNS = (
    re.compile(r"oldtext must be unique|the text must be unique|found \d+ occurrences of", re.IGNORECASE),
    re.compile(r"not a git repository", re.IGNORECASE),
    re.compile(r"^ls: .*: no such file or directory", re.IGNORECASE),
    re.compile(r"tool call was interrupted before a result was received", re.IGNORECASE),
    re.compile(r"is temporarily unavailable \(timed out\)", re.IGNORECASE),
    re.compile(r"^tool error:\s*exit code \d+\s*$", re.IGNORECASE),
    re.compile(r"^tool error:\s*<tool_use_error>error: no such tool available:", re.IGNORECASE),
)


@dataclass
class _Turn:
    line_no: int
    ts: str
    role: str  # user | assistant | tool | action
    text: str
    scope: str
    is_err: bool
    session_id: str


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


def _strip_boilerplate(text: str) -> str:
    if not text:
        return ""
    cleaned = _BOILERPLATE_BLOCK_RE.sub("", text).strip()
    low = cleaned.lower()
    for pref in _NOISE_PREFIXES:
        if low.startswith(pref):
            return ""
    for sub in _NOISE_SUBSTRINGS:
        if sub in low:
            return ""
    if len(cleaned) > 3000:
        return ""
    return cleaned


def _is_transient_tool_noise(text: str, *, role: str, is_err: bool) -> bool:
    """Filter fleeting tool/environment glitches so they never pollute long-term memory."""
    compact = " ".join(text.strip().split())
    if not compact:
        return True
    for pat in _TRANSIENT_TOOL_PATTERNS:
        if pat.search(compact):
            return True
    # Bare shell exit codes with no informative message
    if is_err and role in ("tool", "action") and len(compact) < 28:
        return True
    return False


def _classify_hindsight(text: str, *, role: str = "user", is_error_event: bool = False) -> tuple[str, str] | None:
    """Return (kind, severity) if turn carries hindsight signal, else None."""
    clean = _strip_boilerplate(text)
    if len(clean) < 16:
        return None
    if _is_transient_tool_noise(clean, role=role, is_err=is_error_event):
        return None
    if is_error_event or _ERROR_RE.search(clean):
        return ("lesson", "P1")
    if _CORRECTION_RE.search(clean):
        sev = "P0" if any(w in clean.lower() for w in ("禁止", "严禁", "never use", "must not")) else "P1"
        return ("lesson", sev)
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
                    btype = block.get("type")
                    if btype in ("text", "input_text", "output_text") and block.get("text"):
                        parts.append(str(block["text"]))
                    elif btype == "tool_result" and block.get("is_error"):
                        parts.append(f"Tool error: {block.get('content', '')}")
            return "\n".join(parts)
    return ""


def _infer_scope_from_path(path_str: str) -> str:
    if not path_str:
        return "global"
    p = Path(path_str)
    name = p.name.strip()
    if name and name not in ("/", ".", "Users", "home", "clive"):
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


def _find_resolution(turns: list[_Turn], idx: int) -> str:
    """Look ahead 1..5 turns after a problem/correction to capture the root cause or verified fix."""
    candidates: list[str] = []
    for j in range(idx + 1, min(len(turns), idx + 6)):
        t = turns[j]
        if t.role == "assistant" and not t.is_err and len(t.text) >= 24:
            candidates.append(t.text)
            # Prefer a substantive explanation turn (diagnosis/fix) over short "Checking..." preambles
            if len(t.text) >= 80 or any(
                cue in t.text.lower()
                for cue in ("原因", "因为", "根因", "修复", "解决", "改用", "root cause", "fixed", "resolved", "because")
            ):
                return t.text[:450]
    return candidates[-1][:450] if candidates else ""


def _find_prev_context(turns: list[_Turn], idx: int) -> str:
    for j in range(idx - 1, max(-1, idx - 3), -1):
        t = turns[j]
        if t.role in ("user", "assistant") and len(t.text) >= 12:
            return t.text[:220]
    return ""


def collect_hindsight_events(
    source: str,
    *,
    since: str | None = "24h",
    root_dir: Path | None = None,
    cursor_path: Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Scan local AI/tool traces and extract high-signal (Context + Problem -> Resolution) episodes."""
    if source == ALL_SOURCES_ALIAS:
        all_events: list[dict[str, Any]] = []
        per_src_limit = max(1, limit)
        for src in VALID_SOURCES:
            all_events.extend(
                collect_hindsight_events(
                    src,
                    since=since,
                    root_dir=None,
                    cursor_path=cursor_path,
                    limit=per_src_limit,
                )
            )
        return all_events

    if source not in VALID_SOURCES:
        raise UsageError(
            f"Invalid ingest source '{source}'. Must be one of: {', '.join(VALID_SOURCES)}, all"
        )

    cutoff = parse_since_duration(since)
    cursors = _load_cursors(cursor_path) if cursor_path else {}
    events: list[dict[str, Any]] = []

    if source == "antigravity":
        base = root_dir or (Path.home() / ".gemini" / "antigravity" / "brain")
        files = sorted(base.glob("*/.system_generated/logs/transcript.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
    elif source == "claude":
        base = root_dir or (Path.home() / ".claude" / "projects")
        files = sorted(base.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
    elif source == "codex":
        base = root_dir or (Path.home() / ".codex" / "sessions")
        files = sorted(base.glob("**/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
    elif source == "pi":
        base = root_dir or (Path.home() / ".pi" / "agent" / "sessions")
        files = sorted(base.glob("**/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
    else:  # fops
        base = root_dir or (Path.home() / ".fops")
        files = sorted(base.glob("**/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []

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
        session_scope = "global"
        session_id_hint = fpath.stem
        turns: list[_Turn] = []

        try:
            with fpath.open("r", encoding="utf-8", errors="replace") as fh:
                for line_no, line in enumerate(fh, start=1):
                    line_s = line.strip()
                    if not line_s:
                        continue
                    try:
                        obj = json.loads(line_s)
                    except json.JSONDecodeError:
                        continue

                    if source == "codex" and obj.get("type") in ("session_meta", "turn_context"):
                        p = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                        if p.get("cwd"):
                            session_scope = _infer_scope_from_path(str(p["cwd"]))
                        if p.get("id"):
                            session_id_hint = str(p["id"])
                    elif source == "pi" and obj.get("type") == "session":
                        if obj.get("cwd"):
                            session_scope = _infer_scope_from_path(str(obj["cwd"]))
                        if obj.get("id"):
                            session_id_hint = str(obj["id"])

                    if line_no <= start_line:
                        continue
                    last_line = line_no

                    ts = str(obj.get("created_at") or obj.get("timestamp") or obj.get("ts") or now_iso())
                    ts_dt = parse_timestamp(ts)
                    if cutoff is not None and ts_dt is not None and ts_dt < cutoff:
                        continue

                    extracted = _extract_record_by_source(source, obj, fpath, session_scope, session_id_hint)
                    if extracted is None:
                        continue
                    role, text, scope, is_err, session_id = extracted
                    clean_text = _strip_boilerplate(text)
                    if not clean_text:
                        continue
                    turns.append(
                        _Turn(
                            line_no=line_no,
                            ts=ts,
                            role=role,
                            text=clean_text,
                            scope=scope,
                            is_err=is_err,
                            session_id=session_id,
                        )
                    )
        except OSError:
            continue

        # Extract episodes with burst cooldown (skip overlapping signals within 3 turns)
        last_emitted_idx = -10
        for idx, turn in enumerate(turns):
            if len(events) >= limit:
                break
            if idx - last_emitted_idx < 3:
                continue
            if turn.role == "assistant" and not turn.is_err:
                continue

            verdict = _classify_hindsight(turn.text, role=turn.role, is_error_event=turn.is_err)
            if verdict is None:
                continue

            kind, severity = verdict
            prev_ctx = _find_prev_context(turns, idx)
            resolution = _find_resolution(turns, idx)

            parts: list[str] = []
            if prev_ctx:
                parts.append(f"Context: {prev_ctx}")
            parts.append(f"Hindsight: {turn.text[:450]}")
            if resolution:
                parts.append(f"Resolution: {resolution}")
            combined = "\n".join(parts)

            red = redact_secrets(combined)
            events.append(
                {
                    "kind": kind,
                    "scope": turn.scope,
                    "source": f"hindsight:{source}:{turn.session_id}",
                    "raw": red.text,
                    "ts": turn.ts,
                    "meta": {
                        "severity": severity,
                        "collector": source,
                        "session_id": turn.session_id,
                        "has_resolution": bool(resolution),
                        "evidence_ref": f"{fpath.name}:L{turn.line_no}",
                        "redacted_types": list(red.redacted_types),
                    },
                }
            )
            last_emitted_idx = idx

        cursors[key] = last_line

    if cursor_path is not None:
        _save_cursors(cursor_path, cursors)

    return events


def _extract_record_by_source(
    source: str,
    obj: dict[str, Any],
    fpath: Path,
    session_scope: str,
    session_id_hint: str,
) -> tuple[str, str, str, bool, str] | None:
    """Normalize a single log line into (role, text, scope, is_error, session_id)."""
    if source == "antigravity":
        rtype = str(obj.get("type", ""))
        status = str(obj.get("status", ""))
        content = str(obj.get("content") or "")
        session_id = fpath.parent.parent.parent.name
        if rtype == "USER_INPUT":
            if "<USER_REQUEST>" in content:
                m = _USER_REQUEST_RE.search(content)
                content = m.group(1).strip() if m else ""
            elif "<SYSTEM_MESSAGE>" in content:
                return None
            return ("user", content, session_scope, False, session_id)
        if rtype == "PLANNER_RESPONSE":
            return ("assistant", content, session_scope, status == "ERROR", session_id)
        if status == "ERROR" and content:
            return ("tool", content, session_scope, True, session_id)
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
        session_id = str(payload.get("id") or session_id_hint)
        scope = _infer_scope_from_path(str(payload.get("cwd") or "")) if payload.get("cwd") else session_scope
        if rtype == "response_item":
            ptype = str(payload.get("type", ""))
            role = str(payload.get("role") or "")
            if ptype == "message" and role in ("user", "assistant"):
                text = _extract_text_from_message(payload) or str(payload.get("text") or "")
                return (role, text, scope, False, session_id)
            if ptype == "" and role in ("user", "assistant"):
                text = _extract_text_from_message(payload) or str(payload.get("text") or "")
                is_err = bool(payload.get("exit_code") not in (None, 0) or payload.get("is_error"))
                return (role, text, scope, is_err, session_id)
        elif rtype in ("event_msg", "message"):
            role = str(payload.get("role") or "user")
            if role == "developer":
                return None
            text = _extract_text_from_message(payload) or str(payload.get("text") or payload.get("message") or "")
            is_err = bool(payload.get("exit_code") not in (None, 0) or payload.get("is_error"))
            return (role, text, scope, is_err, session_id)
        return None

    if source == "pi":
        if obj.get("type") != "message":
            return None
        msg = obj.get("message")
        if not isinstance(msg, dict):
            return None
        role = str(msg.get("role") or "")
        if role == "system":
            return None
        text = _extract_text_from_message(msg)
        if role == "toolResult":
            if not msg.get("isError"):
                return None
            return ("tool", text, session_scope, True, session_id_hint)
        if role in ("user", "assistant"):
            return (role, text, session_scope, False, session_id_hint)
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
