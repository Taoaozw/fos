"""AI-native CLI output utilities: TTY-aware compact JSON, --fields projection, structured stderr errors."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from fos.errors import ExitCode, FosError


def is_stdout_tty() -> bool:
    if os.environ.get("FOS_PRETTY_JSON") == "1":
        return True
    if os.environ.get("FOS_COMPACT_JSON") == "1":
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


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
