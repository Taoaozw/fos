"""AI-native CLI output utilities: TTY-aware compact JSON, --fields projection, structured stderr errors."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from fos.errors import ExitCode, FosError

_AGENT_ENV_KEYS: tuple[str, ...] = (
    "ANTIGRAVITY_AGENT",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CODEX_SANDBOX",
    "CODEX_CI",
    "CODEX_THREAD_ID",
    "CURSOR_AGENT",
    "CURSOR_TRACE_ID",
    "AIDER_MODEL",
    "PI_CODING_AGENT",
    "CI",
)


def is_agent_env() -> bool:
    """Return True when invoked inside an AI coding agent shell/PTY harness or CI."""
    return any(bool(os.environ.get(k)) for k in _AGENT_ENV_KEYS)


def is_stdout_tty() -> bool:
    if os.environ.get("FOS_PRETTY_JSON") == "1":
        return True
    if os.environ.get("FOS_COMPACT_JSON") == "1" or is_agent_env():
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def compact_source_ref(source_ref: str, *, cwd: Path | None = None) -> str:
    """Shorten absolute `source_ref` for human/LLM text display without mutating stored paths."""
    ref = (source_ref or "").strip()
    if not ref or ref.startswith(("git:", "http://", "https://", "ledger:")):
        return ref
    sep = "::" if "::" in ref else ("#" if "#" in ref else "")
    path_part, _, anchor = ref.partition(sep) if sep else (ref, "", "")
    p = Path(path_part)
    if not p.is_absolute():
        return ref
    base = (cwd or Path.cwd()).resolve()
    # Find enclosing git repo root if inside one
    repo_root = base
    for cand in (base, *base.parents):
        if (cand / ".git").exists():
            repo_root = cand
            break
    resolved = p.resolve()
    try:
        rel = str(resolved.relative_to(repo_root))
    except ValueError:
        home = Path.home().resolve()
        try:
            rel = "~/" + str(resolved.relative_to(home))
        except ValueError:
            rel = str(p)
    return f"{rel}{sep}{anchor}" if (sep and anchor) else rel


def dumps_json(data: Any, *, pretty: bool | None = None) -> str:
    """Emit pretty JSON on interactive TTYs and compact single-line JSON when piped/captured (~30% fewer tokens)."""
    use_pretty = is_stdout_tty() if pretty is None else pretty
    if use_pretty:
        return json.dumps(data, ensure_ascii=False, indent=2)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def project_fields(data: Any, fields_csv: str | None) -> Any:
    """Filter list-of-dicts or dict (including `results`/`entries` wrappers) down to comma-separated fields."""
    if not fields_csv or not fields_csv.strip():
        return data
    want = {f.strip().lower() for f in fields_csv.split(",") if f.strip()}
    if not want:
        return data

    def _proj_dict(d: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in d.items() if k.lower() in want}

    if isinstance(data, list):
        return [_proj_dict(item) if isinstance(item, dict) else item for item in data]
    if isinstance(data, dict):
        # For envelope payloads like recall/boot, project the item array when requested fields target items
        for list_key in ("results", "entries", "commands"):
            if isinstance(data.get(list_key), list):
                items = data[list_key]
                if items and isinstance(items[0], dict):
                    item_keys = {k.lower() for k in items[0].keys()}
                    if want & item_keys:
                        return [_proj_dict(item) if isinstance(item, dict) else item for item in items]
                elif not items:
                    return []
        return _proj_dict(data)
    return data


def write_json(data: Any, *, fields: str | None = None, pretty: bool | None = None) -> None:
    projected = project_fields(data, fields)
    print(dumps_json(projected, pretty=pretty))


def write_error(exc: Exception, *, as_json: bool) -> None:
    """Emit structured single-line JSON error to stderr in --json mode, or concise text + hint otherwise."""
    if isinstance(exc, FosError):
        code_str = exc.error_code
        msg = str(exc)
        hint = exc.hint
        details = exc.details
    elif isinstance(exc, KeyboardInterrupt):
        code_str = "canceled"
        msg = "interrupted"
        hint = ""
        details = {}
    else:
        code_str = "error"
        msg = str(exc)
        hint = ""
        details = {}

    if as_json:
        payload: dict[str, Any] = {
            "ok": False,
            "code": code_str,
            "error": msg,
        }
        if hint:
            payload["hint"] = hint
        if details:
            payload["details"] = details
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return

    print(f"error: {msg}", file=sys.stderr)
    if hint:
        print(f"hint: {hint}", file=sys.stderr)
    if details and isinstance(exc, FosError) and exc.exit_code == ExitCode.GATE_FAILED:
        print(dumps_json(details, pretty=True), file=sys.stderr)
