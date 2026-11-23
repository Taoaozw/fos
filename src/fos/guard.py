r"""Deterministic static guardrails (`fos guard`) inspired by OpenAI Harness Engineering.

Turns settled FlowOS rules into zero-LLM, millisecond mechanical checks for:
- `git diff --cached` / PR diffs (`fos guard --staged` / `fos guard --diff`)
- Pre-Tool shell commands (`fos guard --cmd "..."` in Claude Code / Codex PreToolUse hooks)

Pattern syntax (`CompiledNode.guard_pattern`, semicolon-separated rules supported):
- `cmd:<regex>` — matches a shell command string (e.g. `cmd:mygo\s+build\s+-upload`)
- `path:<glob> [!exclude_glob] :: <regex>` — matches added diff lines in matching file paths
  (e.g. `path:*.tsx !*components/ui/* :: <(button|input|select|textarea)\b`)
- `<regex>` — matches added diff lines in any file (e.g. `(?i)\bFOREIGN\s+KEY\b`)
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fos.engine import BrainEngine
from fos.freshness import record_utility_feedback
from fos.models import CompiledNode


@dataclass(frozen=True)
class _ParsedGuard:
    mode: str  # "diff" | "cmd"
    include_globs: tuple[str, ...]
    exclude_globs: tuple[str, ...]
    regex: re.Pattern[str]
    raw: str


def _parse_guard_patterns(raw_pattern: str) -> list[_ParsedGuard]:
    parsed: list[_ParsedGuard] = []
    for chunk in (raw_pattern or "").split(";;"):
        spec = chunk.strip()
        if not spec:
            continue
        if spec.lower().startswith("cmd:"):
            expr = spec[4:].strip()
            if not expr:
                continue
            try:
                rx = re.compile(expr, re.IGNORECASE)
            except re.error:
                continue
            parsed.append(_ParsedGuard("cmd", (), (), rx, spec))
            continue

        inc: list[str] = []
        exc: list[str] = []
        expr = spec
        if "::" in spec and spec.lower().startswith("path:"):
            path_part, expr = spec.split("::", 1)
            expr = expr.strip()
            tokens = path_part[5:].strip().split()
            for tok in tokens:
                if tok.startswith("!"):
                    exc.append(tok[1:])
                elif tok:
                    inc.append(tok)
        if not expr:
            continue
        try:
            rx = re.compile(expr)
        except re.error:
            continue
        parsed.append(_ParsedGuard("diff", tuple(inc), tuple(exc), rx, spec))
    return parsed


def _path_matches(file_path: str, inc: tuple[str, ...], exc: tuple[str, ...]) -> bool:
    norm = file_path.lstrip("./")
    base = Path(norm).name
    if inc:
        matched_inc = any(fnmatch.fnmatch(norm, g) or fnmatch.fnmatch(base, g) for g in inc)
        if not matched_inc:
            return False
    if exc:
        if any(fnmatch.fnmatch(norm, g) or fnmatch.fnmatch(base, g) for g in exc):
            return False
    return True


def parse_unified_diff_added_lines(diff_text: str) -> list[tuple[str, int, str]]:
    """Return `(file_path, new_line_no, added_line_text)` for all `+` lines in a unified diff."""
    added: list[tuple[str, int, str]] = []
    cur_file = "<diff>"
    cur_line = 0
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ "):
            target = raw_line[4:].strip().split("\t", 1)[0]
            if target.startswith("b/"):
                target = target[2:]
            cur_file = target
            continue
        m = hunk_re.match(raw_line)
        if m:
            cur_line = int(m.group(1))
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added.append((cur_file, cur_line, raw_line[1:]))
            cur_line += 1
        elif not raw_line.startswith("-"):
            cur_line += 1
    return added


def load_git_diff(*, staged: bool = True, repo_dir: Path | None = None) -> str:
    """Capture `git diff --cached` (or working tree `git diff`) from `repo_dir`."""
    cwd = str(repo_dir or Path.cwd())
    cmd = ["git", "-C", cwd, "diff", "--unified=0"]
    if staged:
        cmd.append("--cached")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    return proc.stdout if proc.returncode == 0 else ""


def check_guard(
    engine: BrainEngine,
    *,
    diff_text: str | None = None,
    command: str | None = None,
    scope: str | None = None,
    scopes: list[str] | None = None,
    record_utility: bool = True,
) -> dict[str, Any]:
    """Run deterministic 0-LLM static guardrail checks against active FlowOS nodes."""
    t0 = time.perf_counter()
    with engine.snapshots.open_current(read_only=True) as db:
        candidates: list[CompiledNode] = db.list_nodes(scope=None if scopes else scope, scopes=scopes, as_of=None)

    guard_nodes = [n for n in candidates if n.guard_pattern.strip()]
    added_lines = parse_unified_diff_added_lines(diff_text) if diff_text else []
    cmd_str = (command or "").strip()

    violations: list[dict[str, Any]] = []
    triggered_ids: set[str] = set()
    checked_rules = 0

    for node in guard_nodes:
        specs = _parse_guard_patterns(node.guard_pattern)
        for spec in specs:
            if spec.mode == "cmd":
                if not cmd_str:
                    continue
                checked_rules += 1
                m = spec.regex.search(cmd_str)
                if m:
                    triggered_ids.add(node.id)
                    violations.append(
                        {
                            "node_id": node.id,
                            "scope": node.scope,
                            "severity": node.severity,
                            "graduated": node.graduated,
                            "mode": "cmd",
                            "rule": node.rule,
                            "guard_pattern": spec.raw,
                            "source_ref": node.source_ref,
                            "match": m.group(0)[:120],
                            "file": None,
                            "line": None,
                        }
                    )
            else:
                if not added_lines:
                    continue
                checked_rules += 1
                for fpath, lineno, text in added_lines:
                    if not _path_matches(fpath, spec.include_globs, spec.exclude_globs):
                        continue
                    m = spec.regex.search(text)
                    if m:
                        triggered_ids.add(node.id)
                        violations.append(
                            {
                                "node_id": node.id,
                                "scope": node.scope,
                                "severity": node.severity,
                                "graduated": node.graduated,
                                "mode": "diff",
                                "rule": node.rule,
                                "guard_pattern": spec.raw,
                                "source_ref": node.source_ref,
                                "match": m.group(0)[:120],
                                "file": fpath,
                                "line": lineno,
                            }
                        )
                        break  # One violation per rule per diff check keeps output crisp

    # Implicit PlugMem positive utility signal whenever a guard catches a real violation!
    if record_utility and triggered_ids:
        for nid in sorted(triggered_ids):
            record_utility_feedback(
                engine.data_dir,
                nid,
                "guard_block",
                context=f"Blocked violation in {'cmd' if cmd_str else 'diff'}",
            )

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {
        "passed": len(violations) == 0,
        "scope": scope or "global",
        "guard_nodes": len(guard_nodes),
        "checked_rules": checked_rules,
        "elapsed_ms": elapsed_ms,
        "violations": violations,
    }
