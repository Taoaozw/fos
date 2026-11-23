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
        "usage": "fos boot [--project|--scope P | --all] [--scopes S1,S2] [--budget 300] [--fields F] [--json]",
        "flags": [
            {"name": "--project", "type": "string", "desc": "project or hierarchical domain scope (alias: --scope; default: auto-detect git repo + stacks)"},
            {"name": "--scopes", "type": "string", "desc": "comma-separated extra domain/stack scopes to mount (e.g. eng/frontend,product)"},
            {"name": "--all", "type": "bool", "default": "false", "desc": "ignore cwd project and include all scopes"},
            {"name": "--budget", "type": "int", "default": "300", "desc": "strict token ceiling"},
            {"name": "--fields", "type": "string", "desc": "comma-separated JSON fields to keep (implies --json)"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output (compact when piped)"},
        ],
        "examples": [
            "fos boot --json",
            "fos boot --scope eng/frontend --budget 300 --json",
            "fos boot --project fops --scopes eng/ops --budget 300 --json",
            "fos boot --fields id,severity,l0",
        ],
    },
    {
        "name": "recall",
        "aliases": ["rec", "r", "search"],
        "readOnly": True,
        "summary": "4-way hybrid recall (FTS5 BM25 + sqlite-vec + trigger/graph + temporal) within token budget",
        "usage": 'fos recall "<query>" [--kind K] [--scope P] [--scopes S1,S2] [--as-of YYYY-MM] [--budget 1200] [--limit 20] [--exclude IDs] [--fields F] [--json]',
        "flags": [
            {"name": "--kind", "type": "string", "desc": "filter: thought|decision|lesson|outcome|principle"},
            {"name": "--scope", "type": "string", "desc": "project or hierarchical domain scope (<=3 levels, e.g. fos, eng/frontend/react, product)"},
            {"name": "--scopes", "type": "string", "desc": "comma-separated mounted domain/stack scopes"},
            {"name": "--as-of", "type": "string", "desc": "historical time-travel slice, e.g. 2026-08 or ISO8601"},
            {"name": "--budget", "type": "int", "default": "1200", "desc": "strict L1 token budget"},
            {"name": "--limit", "type": "int", "default": "20", "desc": "max cards to return"},
            {"name": "--exclude", "type": "string", "desc": "comma-separated node IDs to skip (e.g. already loaded in L0 boot)"},
            {"name": "--fields", "type": "string", "desc": "comma-separated JSON fields to keep (implies --json)"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output (compact in agent/piped envs)"},
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
        "usage": 'fos mirror "<proposal>" [--scope P] [--scopes S1,S2] [--json]',
        "flags": [
            {"name": "--scope", "type": "string", "desc": "project or hierarchical domain scope"},
            {"name": "--scopes", "type": "string", "desc": "comma-separated mounted domain/stack scopes"},
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
            {"name": "--scope", "type": "string", "default": "global", "desc": "hierarchical scope (<=3 levels): global, domain/specialty/stack (e.g. eng/frontend/react, eng/ops, product), or repo name"},
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
        "summary": "Collect high-compounding hindsight signals from global/repo rule docs, git fix commits & AI sessions",
        "usage": "fos ingest --from docs|git|antigravity|claude|codex|pi|fops|all [--since 24h] [--root DIR] [--limit 50] [--json]",
        "flags": [
            {"name": "--from", "type": "string", "desc": "required source: docs|git|antigravity|claude|codex|pi|fops|all"},
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
    {
        "name": "proposals",
        "aliases": ["prop", "inbox"],
        "readOnly": False,
        "summary": "Scan, list, accept, or dismiss cross-project architectural & governance proposals",
        "usage": "fos proposals [list|scan|accept|dismiss] [ID] [--status pending|all] [--root DIR] [--reasoner cli] [--severity P0|P1] [--scope SCOPE] [--reason TEXT] [--force] [--json]",
        "flags": [
            {"name": "--status", "type": "string", "default": "pending", "desc": "filter for list: pending|accepted|dismissed|expired|all"},
            {"name": "--root", "type": "string", "default": "", "desc": "root directory of owned git repos for scan"},
            {"name": "--reasoner", "type": "string", "default": "", "desc": "optional reasoner override for scan (e.g. cli)"},
            {"name": "--severity", "type": "string", "default": "", "desc": "override severity (P0/P1) on accept"},
            {"name": "--scope", "type": "string", "default": "", "desc": "override target scope (e.g. eng/frontend, eng/ops, global) on accept"},
            {"name": "--rule", "type": "string", "default": "", "desc": "override rule text on accept"},
            {"name": "--reason", "type": "string", "default": "", "desc": "dismissal reason or rationale override"},
            {"name": "--force", "type": "bool", "default": "false", "desc": "force rescan (bypass cache) or bypass L0 budget guard on accept"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
    },
    {
        "name": "guard",
        "aliases": ["gd"],
        "readOnly": False,
        "summary": "Run deterministic 0-LLM static guardrail checks on git diffs or shell commands, or graduate a rule to mechanical guard",
        "usage": "fos guard [--staged | --diff [FILE] | --cmd CMD] [--scope SCOPE] [--json] | fos guard graduate <ID> [--pattern REGEX] [--reason TEXT]",
        "flags": [
            {"name": "--staged", "type": "bool", "default": "false", "desc": "check git diff --cached in current repo"},
            {"name": "--diff", "type": "string", "default": "", "desc": "diff file path or '-' for stdin"},
            {"name": "--cmd", "type": "string", "default": "", "desc": "shell command to check against cmd: guard patterns"},
            {"name": "--scope", "type": "string", "default": "", "desc": "project scope (auto-detected from cwd if omitted)"},
            {"name": "--pattern", "type": "string", "default": "", "desc": "static guard pattern when graduating a rule"},
            {"name": "--reason", "type": "string", "default": "", "desc": "graduation reason"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
    },
    {
        "name": "feedback",
        "aliases": ["fb"],
        "readOnly": False,
        "summary": "Record PlugMem utility feedback (useful/noise) for a recalled memory node",
        "usage": "fos feedback <NODE_ID> (--useful | --noise) [--context TEXT] [--json]",
        "flags": [
            {"name": "--useful", "type": "bool", "default": "false", "desc": "mark node as helpful in retrieval"},
            {"name": "--noise", "type": "bool", "default": "false", "desc": "mark node as noisy/irrelevant in retrieval"},
            {"name": "--context", "type": "string", "default": "", "desc": "optional short feedback note"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
    },
    {
        "name": "pattern",
        "aliases": ["pat"],
        "readOnly": False,
        "summary": "Store, list, or evolve timeless high-performance code patterns (L1 invariant + symbol-anchored L2 skeleton + SemVer compat)",
        "usage": 'fos pattern add <file[::symbol]> "<invariant>" [--why "..."] [--bench "..."] [--supersedes ID] [--json] | fos pattern sync <ID> [--rule "..."] [--why "..."] [--json] | fos pattern list [--scope P] [--json]',
        "flags": [
            {"name": "--rule", "type": "string", "desc": "updated invariant text when running sync"},
            {"name": "--why", "type": "string", "default": "", "desc": "first-principles rationale / performance benefit / fix summary"},
            {"name": "--bench", "type": "string", "default": "", "desc": "verification benchmark or test command"},
            {"name": "--supersedes", "type": "string", "desc": "old pattern node ID superseded by this evolved v2 pattern"},
            {"name": "--scope", "type": "string", "desc": "project scope (auto-detected from target file repo if omitted)"},
            {"name": "--severity", "type": "string", "default": "P1", "desc": "P0|P1|P2 (default P1, keeps L0 boot lean)"},
            {"name": "--kind", "type": "string", "default": "decision", "desc": "decision|lesson|principle"},
            {"name": "--stack", "type": "string", "desc": "override/add SemVer stack versions (e.g. kotlin=2.4,spring-boot=4.1)"},
            {"name": "--max-lines", "type": "int", "default": "45", "desc": "max skeleton lines stored in L2 ledger"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
        "examples": [
            'fos pattern add path/to/DetachableBody.kt::DetachableBody "WebFlux streaming relay uses lock-free BaseSubscriber<DataBuffer> zero-copy pass-through with upstream drain on disconnect" --why "Zero heap copy + accurate billing on client abort" --bench "./gradlew :services:gateway:test" --json',
            'fos pattern sync DEC-047 --why "Fixed rare race on client cancel + reduced allocation" --json',
            "fos pattern list --json",
        ],
    },
    {
        "name": "audit",
        "aliases": ["au"],
        "readOnly": False,
        "summary": "Verify active repository memories against live codebase via read-only coding agent (codex exec) and enqueue drift/obsolete proposals",
        "usage": "fos audit [--scope REPO] [--repo PATH] [--model MODEL] [--effort low|medium|high|xhigh] [--dry-run] [--json]",
        "flags": [
            {"name": "--scope", "type": "string", "desc": "repository scope to audit (auto-detected from cwd)"},
            {"name": "--repo", "type": "string", "desc": "explicit local repository path"},
            {"name": "--model", "type": "string", "desc": "override codex model (e.g. gpt-6.2-sol)"},
            {"name": "--effort", "type": "string", "desc": "reasoning effort: low|medium|high|xhigh"},
            {"name": "--cli-cmd", "type": "string", "desc": "override CLI agent command"},
            {"name": "--dry-run", "type": "bool", "default": "false", "desc": "report verdicts without creating proposals or updating verified_at"},
            {"name": "--timeout", "type": "int", "default": "240", "desc": "audit subprocess timeout in seconds"},
            {"name": "--json", "type": "bool", "default": "false", "desc": "JSON output"},
        ],
        "examples": [
            "fos audit --scope fos --model gpt-6.2-sol --effort medium --dry-run",
            "fos audit --scope flowxy-gateway --model gpt-6.2-sol --effort medium --json",
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
