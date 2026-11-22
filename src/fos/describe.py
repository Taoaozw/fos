"""Machine-readable command catalog and schema (`fos describe [cmd]`), learned from fops."""

from __future__ import annotations

from typing import Any

from fos import __version__
from fos.errors import ExitCode, NotFoundError
from fos.suggest import suggest_closest

COMMAND_SPECS: list[dict[str, Any]] = [
    {
        "name": "boot",
        "aliases": ["b"],
        "readOnly": True,
        "summary": "Emit <=300 token L0 wakeup kernel (redlines, active project decisions, pitfall triggers)",
        "usage": "fos boot [--project P | --all] [--budget 300] [--fields F] [--json]",
        "flags": [
            {"name": "--project", "type": "string", "desc": "project/scope name (default: auto-detect from git repo)"},
            {"name": "--all", "type": "bool", "default": "false", "desc": "ignore cwd project and include all scopes"},
            {"name": "--budget", "type": "int", "default": "300", "desc": "strict token ceiling"},
            {"name": "--fields", "type": "string", "desc": "comma-separated JSON fields to keep (implies --json)"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output (compact when piped)"},
        ],
        "examples": [
            "fos boot --json",
            "fos boot --project fops --budget 300 --json",
            "fos boot --fields id,severity,l0",
        ],
    },
    {
        "name": "recall",
        "aliases": ["rec", "r", "search"],
        "readOnly": True,
        "summary": "4-way hybrid recall (FTS5 BM25 + sqlite-vec + trigger/graph + temporal) within token budget",
        "usage": 'fos recall "<query>" [--kind K] [--scope P] [--as-of YYYY-MM] [--budget 1200] [--limit 20] [--fields F] [--json]',
        "flags": [
            {"name": "--kind", "type": "string", "desc": "filter: thought|decision|lesson|outcome|principle"},
            {"name": "--scope", "type": "string", "desc": "project/domain scope filter"},
            {"name": "--as-of", "type": "string", "desc": "historical time-travel slice, e.g. 2026-08 or ISO8601"},
            {"name": "--budget", "type": "int", "default": "1200", "desc": "strict L1 token budget"},
            {"name": "--limit", "type": "int", "default": "20", "desc": "max cards to return"},
            {"name": "--fields", "type": "string", "desc": "comma-separated JSON fields to keep (implies --json)"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output (compact when piped)"},
        ],
        "examples": [
            'fos recall "macOS rsync deploy" --scope fops --json',
            'fos recall "storage engine" --as-of 2026-08 --json',
            'fos recall "rsync" --fields id,severity,rule',
        ],
    },
    {
        "name": "get",
        "aliases": ["g"],
        "readOnly": True,
        "summary": "Inspect a compiled node at L0/L1/L2 (verbatim ledger evidence) with optional bi-temporal graph",
        "usage": "fos get <ID> [--tier L0|L1|L2] [--graph] [--json]",
        "flags": [
            {"name": "--tier", "type": "string", "default": "L1", "desc": "detail level: L0|L1|L2"},
            {"name": "--graph", "type": "bool", "default": "false", "desc": "include bi-temporal graph edges"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
        "examples": [
            "fos get LSN-001 --tier L2 --graph --json",
        ],
        "notes": ["Exit code 3 = node not found."],
    },
    {
        "name": "mirror",
        "aliases": ["mir", "check"],
        "readOnly": True,
        "summary": "Pre-decision review: conflicts, recurring lesson warnings, prior decisions (incl. superseded), and blind spots",
        "usage": 'fos mirror "<proposal>" [--scope P] [--json]',
        "flags": [
            {"name": "--scope", "type": "string", "desc": "project/domain scope"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
        "examples": [
            'fos mirror "Use rsync --mkpath in macOS deploy script" --scope fops --json',
        ],
    },
    {
        "name": "add",
        "aliases": ["a"],
        "readOnly": False,
        "summary": "Append an immutable verbatim atom to the ledger (auto-redacts secrets; queues offline if brain unreachable)",
        "usage": 'fos add --kind thought|decision|lesson|outcome|principle [--scope P] "<content>" [--supersedes DEC-xxx] [--severity P0|P1|P2] [--json]',
        "flags": [
            {"name": "--kind", "type": "string", "desc": "required: thought|decision|lesson|outcome|principle"},
            {"name": "--scope", "type": "string", "default": "global", "desc": "project/domain scope"},
            {"name": "--supersedes", "type": "string", "desc": "old decision/node ID superseded by this entry"},
            {"name": "--severity", "type": "string", "desc": "P0|P1|P2"},
            {"name": "--entities", "type": "string", "desc": "comma-separated entity tags"},
            {"name": "--trigger", "type": "string", "desc": "repeatable trigger_when phrase"},
            {"name": "--evidence-ref", "type": "string", "desc": "reference pointer, e.g. git:955d311"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
        "examples": [
            'fos add --kind lesson --scope fops --severity P0 "Never use rsync --mkpath on macOS; run ssh mkdir -p first"',
            'fos add --kind decision --scope fos --supersedes DEC-001 "Switch to SQLite WAL + sqlite-vec"',
        ],
    },
    {
        "name": "correct",
        "aliases": ["cor", "fix"],
        "readOnly": False,
        "summary": "Self-correct a wrong/outdated node by superseding it with a refined rule (keeps kind & scope)",
        "usage": 'fos correct <ID> "<corrected rule>" [--reason "..."] [--severity P0|P1|P2] [--json]',
        "flags": [
            {"name": "--reason", "type": "string", "desc": "why the previous rule was wrong or refined"},
            {"name": "--severity", "type": "string", "desc": "optional override P0|P1|P2"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
        "examples": [
            'fos correct LSN-005 "Preserve decimal precision for upstream usage/quota fields" --reason "refined scope" --json',
        ],
        "notes": ["Exit code 3 = node not found."],
    },
    {
        "name": "archive",
        "aliases": ["arc", "retire"],
        "readOnly": False,
        "summary": "Retire an obsolete, expired, or already-guarded node (hides from active recall, preserves --as-of history)",
        "usage": 'fos archive <ID> [--reason "..."] [--json]',
        "flags": [
            {"name": "--reason", "type": "string", "desc": "reason for archiving (e.g. codified as Guard Test / feature removed)"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
        "examples": [
            'fos archive LSN-006 --reason "enforced by unit tests and schema validation" --json',
        ],
        "notes": ["Exit code 3 = node not found."],
    },
    {
        "name": "ingest",
        "aliases": ["ing"],
        "readOnly": False,
        "summary": "Collect high-compounding hindsight signals from git fix commits and AI agent sessions",
        "usage": "fos ingest --from git|antigravity|claude|codex|pi|fops|all [--since 24h] [--root DIR] [--limit 50] [--json]",
        "flags": [
            {"name": "--from", "type": "string", "desc": "required source: git|antigravity|claude|codex|pi|fops|all"},
            {"name": "--since", "type": "string", "default": "24h", "desc": "lookback duration or ISO timestamp"},
            {"name": "--root", "type": "string", "desc": "override trace directory"},
            {"name": "--limit", "type": "int", "default": "50", "desc": "max events to collect"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
    },
    {
        "name": "dream",
        "aliases": ["dr"],
        "readOnly": False,
        "summary": "Sleep compilation (Clean -> Extract -> Consolidate -> Compile vN+1) + 4-gate regression eval + atomic switch",
        "usage": "fos dream [--dry-run] [--no-gate] [--export-songe FILE] [--json]",
        "flags": [
            {"name": "--dry-run", "type": "bool", "default": "false", "desc": "evaluate candidate without promoting"},
            {"name": "--no-gate", "type": "bool", "default": "false", "desc": "skip regression gates"},
            {"name": "--reasoner", "type": "string", "desc": "distiller backend: auto|heuristic|cli|mlx|openai"},
            {"name": "--cli-cmd", "type": "string", "desc": "override CLI reasoner command (e.g. 'codex exec')"},
            {"name": "--export-songe", "type": "string", "desc": "export cleaned experience JSONL for Songe MLX"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
        "notes": ["Exit code 4 = regression gate failed (candidate rejected, current snapshot untouched)."],
    },
    {
        "name": "eval",
        "aliases": ["ev"],
        "readOnly": True,
        "summary": "Run 4-dimension regression gates or pin a node into permanent P0 golden suite",
        "usage": "fos eval [--suite all|recall|temporal|fidelity|behavior] [--json] | fos eval pin <ID> [--json]",
        "flags": [
            {"name": "--suite", "type": "string", "default": "all", "desc": "all|recall|temporal|fidelity|behavior"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
        "notes": ["Exit code 4 = one or more gates failed."],
    },
    {
        "name": "undo",
        "aliases": [],
        "readOnly": False,
        "summary": "Atomically roll back current snapshot pointer to previous compiled version",
        "usage": "fos undo [--json]",
        "notes": ["Exit code 3 = no previous snapshot available."],
    },
    {
        "name": "sync",
        "aliases": ["s"],
        "readOnly": False,
        "summary": "Flush offline outbox.jsonl to M3 Max brain, pull latest compact cache, and check CLI updates",
        "usage": "fos sync [--json]",
    },
    {
        "name": "init",
        "aliases": [],
        "readOnly": False,
        "summary": "Bind client to remote M3 Max brain (or switch back to --local mode)",
        "usage": "fos init --brain URL --token TOKEN [--json] | fos init --local [--json]",
    },
    {
        "name": "status",
        "aliases": ["st"],
        "readOnly": True,
        "summary": "Show CLI version, brain connectivity, snapshot, cache/outbox stats, auto-update & installed AI skills",
        "usage": "fos status [--offline] [--json]",
        "flags": [
            {"name": "--offline", "type": "bool", "default": "false", "desc": "skip network health check"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
    },
    {
        "name": "skill",
        "aliases": ["sk"],
        "readOnly": False,
        "summary": "Install or refresh the embedded AI agent skill bundle (SKILL.md + references/)",
        "usage": "fos skill install [--dir DIR] [--refresh-only] [--json] | fos skill status [--json]",
    },
    {
        "name": "update",
        "aliases": ["up"],
        "readOnly": False,
        "summary": "Self-upgrade fos CLI from the brain (SHA-256 verified) and auto-refresh installed AI skills",
        "usage": "fos update [--check] [--background] [--json]",
    },
    {
        "name": "export",
        "aliases": [],
        "readOnly": False,
        "summary": "Export read-only Markdown views from compiled snapshot",
        "usage": "fos export --format md --out DIR [--json]",
    },
    {
        "name": "rebuild",
        "aliases": [],
        "readOnly": False,
        "summary": "Rebuild SQLite snapshot from scratch solely from immutable ledger/*.jsonl",
        "usage": "fos rebuild [--json]",
    },
    {
        "name": "describe",
        "aliases": ["desc"],
        "readOnly": True,
        "summary": "Emit machine-readable command catalog or full flag schema for a specific command",
        "usage": "fos describe [COMMAND]",
    },
    {
        "name": "open",
        "aliases": ["ui", "browse"],
        "readOnly": True,
        "summary": "Open the built-in fosd Web Observability Console in default browser",
        "usage": "fos open [--print] [--port 7622] [--json]",
        "flags": [
            {"name": "--print", "type": "bool", "default": "false", "desc": "print resolved URL without opening browser"},
            {"name": "--port", "type": "int", "default": "7622", "desc": "local fosd port when running in local mode"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
    },
]


def resolve_command_alias(name: str) -> str:
    low = name.strip().lower()
    for spec in COMMAND_SPECS:
        if spec["name"] == low or low in spec.get("aliases", []):
            return spec["name"]
    return name


def all_command_names() -> list[str]:
    return [s["name"] for s in COMMAND_SPECS]


def describe_payload(target: str | None = None) -> dict[str, Any]:
    if not target:
        return {
            "tool": "fos",
            "version": __version__,
            "hint": "run `fos describe <command>` for flags & examples",
            "exitCodes": {
                "ok": int(ExitCode.OK),
                "error": int(ExitCode.ERROR),
                "usage": int(ExitCode.USAGE),
                "notFound": int(ExitCode.NOT_FOUND),
                "gateFailed": int(ExitCode.GATE_FAILED),
                "canceled": int(ExitCode.CANCELED),
            },
            "commands": [
                {
                    "name": s["name"],
                    "aliases": s.get("aliases", []),
                    "readOnly": s["readOnly"],
                    "summary": s["summary"],
                }
                for s in COMMAND_SPECS
            ],
        }

    canonical = resolve_command_alias(target)
    for spec in COMMAND_SPECS:
        if spec["name"] == canonical:
            return spec

    names = all_command_names()
    suggestion = suggest_closest(target, names)
    hint = f"available commands: {', '.join(names)}"
    if suggestion:
        hint = f'did you mean "{suggestion}"? ({hint})'
    raise NotFoundError(f'unknown command "{target}"', hint=hint)
