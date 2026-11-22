"""High-compounding Hindsight collectors: Git fix/revert commits + User Taste/Decision episodes.

First-principles design (modeled on fops LESSONS.md + Songe + Hindsight):
- `git`: mines verified `fix(...)` / `revert` / `perf(...)` commits across repos (with per-repo
  `reviewed_through` SHA cursor) — already verified root causes, zero chat chatter.
- `antigravity | claude | codex | pi`: focuses strictly on high-compounding human signals
  (user taste/workflow corrections & explicit architectural trade-offs) instead of scraping
  one-off HTTP 4xx/5xx firefighting tickets or transient tool errors.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fos.errors import UsageError
from fos.models import now_iso, parse_timestamp
from fos.redact import redact_secrets

VALID_SOURCES = ("git", "antigravity", "claude", "codex", "pi", "fops")
ALL_SOURCES_ALIAS = "all"

# High-compounding human correction / taste / principle signals (NOT one-off ops tickets)
_TASTE_CORRECTION_RE = re.compile(
    r"(?:不要用|不要做|不要再|不要\s*[a-zA-Z]{2,}|别用|禁止|严禁|以后不要|下次不要|我希望(?:他|它)?是|"
    r"偏好|第一性原理|过度抽象|太重了|参考.{1,15}(?:思路|做法|设计)|从.{1,10}出发|应该用|统一用|一律用|改用|换成|"
    r"never use\b|do not use\b|don't use\b|instead of\b|i prefer\b|stop doing\b|remember to\b|lesson learned|root cause)",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"(?:决定采用|最终选择|架构选型|放弃.{1,20}改用|改用.{1,20}替代|选型结论|设计原则|权衡|trade-?off|"
    r"decided to\b|we will use\b|architecture decision|chosen to\b)",
    re.IGNORECASE,
)
# Exclude one-off account/ticket operational chatter from chat sessions
_OPS_TICKET_NOISE_RE = re.compile(
    r"(?:帮我把|帮我查|帮我看下|这个账号|那个账号|上号失败|换成.{1,12}的账号|cookie\s*复用|报\s*40[034]|返回\s*40[034]|为什么一直返回)",
    re.IGNORECASE,
)

# Git commit prefixes that carry verified engineering rework / pitfall lessons (per fops LESSONS.md)
_GIT_LESSON_SUBJECT_RE = re.compile(
    r"^(?:fix|revert|perf)(?:\([^)]+\))?!?:\s+.{10,}",
    re.IGNORECASE,
)
_GIT_TRIVIAL_RE = re.compile(
    r"(?:typo|format|lint|whitespace|bump version|merge branch|wip\b)",
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
    if len(cleaned) > 2000:
        return ""
    return cleaned


def _classify_chat_signal(text: str, *, role: str) -> tuple[str, str] | None:
    """Classify only high-compounding user corrections, taste rules, and architectural decisions."""
    if role != "user":
        return None
    clean = _strip_boilerplate(text)
    if len(clean) < 12:
        return None
    if _OPS_TICKET_NOISE_RE.search(clean):
        return None
    if _DECISION_RE.search(clean):
        return ("decision", "P1")
    if _TASTE_CORRECTION_RE.search(clean):
        is_hard = any(w in clean.lower() for w in ("禁止", "严禁", "不要 mcp", "不要mcp", "never use", "第一性原理"))
        return ("principle" if is_hard else "lesson", "P0" if is_hard else "P1")
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


def _load_cursors(cursor_path: Path) -> dict[str, Any]:
    if not cursor_path.exists():
        return {}
    try:
        return json.loads(cursor_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cursors(cursor_path: Path, cursors: dict[str, Any]) -> None:
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cursor_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cursors, indent=2) + "\n", encoding="utf-8")
    tmp.replace(cursor_path)


def _find_resolution(turns: list[_Turn], idx: int) -> str:
    for j in range(idx + 1, min(len(turns), idx + 5)):
        t = turns[j]
        if t.role == "assistant" and not t.is_err and len(t.text) >= 24:
            return t.text[:400]
    return ""


def _find_prev_context(turns: list[_Turn], idx: int) -> str:
    for j in range(idx - 1, max(-1, idx - 3), -1):
        t = turns[j]
        if t.role in ("user", "assistant") and len(t.text) >= 12:
            return t.text[:220]
    return ""


# ---------------------------------------------------------------------------
# Git Fix/Revert Commit Collector (modeled on fops LESSONS.md reviewed-through)
# ---------------------------------------------------------------------------


def _discover_git_repos(root_dir: Path | None) -> list[Path]:
    if root_dir is not None:
        base = Path(root_dir).expanduser().resolve()
        if (base / ".git").exists():
            return [base]
        if base.is_dir():
            return sorted(p.parent for p in base.glob("*/.git") if p.parent.is_dir())
        return []

    # Default: current git repo if inside one, else scan ~/project/clive (or ~/project)
    cwd = Path.cwd().resolve()
    for cand in (cwd, *cwd.parents):
        if (cand / ".git").exists():
            return [cand]
    for fallback in (Path.home() / "project" / "clive", Path.home() / "projects" / "clive"):
        if fallback.is_dir():
            repos = sorted(p.parent for p in fallback.glob("*/.git") if p.parent.is_dir())
            if repos:
                return repos
    return []


def _collect_git_fix_events(
    *,
    since: str | None,
    root_dir: Path | None,
    cursor_path: Path | None,
    limit: int,
) -> list[dict[str, Any]]:
    repos = _discover_git_repos(root_dir)
    if not repos:
        return []

    cutoff = parse_since_duration(since)
    cursors = _load_cursors(cursor_path) if cursor_path else {}
    events: list[dict[str, Any]] = []
    sep = "\x1e"
    rec_sep = "\x1f"

    for repo in repos:
        if len(events) >= limit:
            break
        ckey = f"git:{repo.resolve()}"
        reviewed_sha = str(cursors.get(ckey) or "")

        cmd = [
            "git",
            "-C",
            str(repo),
            "log",
            "--no-merges",
            f"--format=%H{rec_sep}%cI{rec_sep}%s{rec_sep}%b{sep}",
            "-n",
            "80",
        ]
        if reviewed_sha:
            cmd.append(f"{reviewed_sha}..HEAD")
        elif cutoff is not None:
            cmd.append(f"--since={cutoff.isoformat()}")

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
            if proc.returncode != 0:
                continue
        except (OSError, subprocess.SubprocessError):
            continue

        head_proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        head_sha = head_proc.stdout.strip() if head_proc.returncode == 0 else ""

        records = [r.strip() for r in proc.stdout.split(sep) if r.strip()]
        scope = repo.name.lower()

        for rec in records:
            if len(events) >= limit:
                break
            parts = rec.split(rec_sep, 3)
            if len(parts) < 3:
                continue
            sha, ts, subject = parts[0].strip(), parts[1].strip(), parts[2].strip()
            body = parts[3].strip() if len(parts) > 3 else ""

            if not _GIT_LESSON_SUBJECT_RE.match(subject) or _GIT_TRIVIAL_RE.search(subject):
                continue

            # Fetch compact changed-file list for technical context
            files_proc = subprocess.run(
                ["git", "-C", str(repo), "show", "--stat", "--format=", sha],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            stat_lines = [l.strip() for l in files_proc.stdout.splitlines() if l.strip()][:6]

            text_parts = [f"Hindsight: {subject}"]
            if body:
                text_parts.append(f"Resolution: {body[:500]}")
            if stat_lines:
                text_parts.append(f"Context: touched files: {'; '.join(stat_lines)}")
            raw = "\n".join(text_parts)
            red = redact_secrets(raw)

            events.append(
                {
                    "kind": "lesson",
                    "scope": scope,
                    "source": f"hindsight:git:{repo.name}",
                    "raw": red.text,
                    "ts": ts or now_iso(),
                    "meta": {
                        "severity": "P1",
                        "collector": "git",
                        "evidence_ref": f"git:{sha[:7]}",
                        "commit_sha": sha,
                        "redacted_types": list(red.redacted_types),
                    },
                }
            )

        if head_sha:
            cursors[ckey] = head_sha

    if cursor_path is not None:
        _save_cursors(cursor_path, cursors)
    return events


# ---------------------------------------------------------------------------
# Main Collector Entrypoint
# ---------------------------------------------------------------------------


def collect_hindsight_events(
    source: str,
    *,
    since: str | None = "24h",
    root_dir: Path | None = None,
    cursor_path: Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Collect high-compounding hindsight signals from git fixes or AI agent sessions."""
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

    if source == "git":
        return _collect_git_fix_events(since=since, root_dir=root_dir, cursor_path=cursor_path, limit=limit)

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
        start_line = int(cursors.get(key, 0) or 0)
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

        last_emitted_idx = -10
        for idx, turn in enumerate(turns):
            if len(events) >= limit:
                break
            if idx - last_emitted_idx < 3:
                continue

            if source == "fops":
                if not turn.is_err:
                    continue
                kind, severity = ("lesson", "P1")
            else:
                verdict = _classify_chat_signal(turn.text, role=turn.role)
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
    if source == "antigravity":
        rtype = str(obj.get("type", ""))
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
            return ("assistant", content, session_scope, False, session_id)
        return None

    if source == "claude":
        rtype = str(obj.get("type", ""))
        session_id = str(obj.get("sessionId") or fpath.stem)
        scope = _infer_scope_from_path(str(obj.get("cwd") or ""))
        if rtype in ("user", "assistant"):
            return (rtype, _extract_text_from_message(obj.get("message")), scope, False, session_id)
        return None

    if source == "codex":
        rtype = str(obj.get("type", ""))
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
        session_id = str(payload.get("id") or session_id_hint)
        scope = _infer_scope_from_path(str(payload.get("cwd") or "")) if payload.get("cwd") else session_scope
        if rtype == "response_item":
            ptype = str(payload.get("type", ""))
            role = str(payload.get("role") or "")
            if ptype in ("message", "") and role in ("user", "assistant"):
                text = _extract_text_from_message(payload) or str(payload.get("text") or "")
                return (role, text, scope, False, session_id)
        elif rtype in ("event_msg", "message"):
            role = str(payload.get("role") or "user")
            if role in ("user", "assistant"):
                text = _extract_text_from_message(payload) or str(payload.get("text") or payload.get("message") or "")
                return (role, text, scope, False, session_id)
        return None

    if source == "pi":
        if obj.get("type") != "message":
            return None
        msg = obj.get("message")
        if not isinstance(msg, dict):
            return None
        role = str(msg.get("role") or "")
        if role in ("user", "assistant"):
            return (role, _extract_text_from_message(msg), session_scope, False, session_id_hint)
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
