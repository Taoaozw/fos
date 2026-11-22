"""Ultra-lightweight AI-native `fos` CLI (<60ms cold start, fops-inspired ergonomics)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from fos import __version__
from fos.errors import ExitCode, FosError, GateFailedError, UsageError
from fos.output import dumps_json, write_error, write_json


class _StrictArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message, hint=f"run `{self.prog} --help` or `fos describe` for available flags")


def _detect_json_requested(argv: list[str]) -> bool:
    for i, tok in enumerate(argv):
        if tok in ("--json", "--json=true"):
            return True
        if tok == "--fields" and i + 1 < len(argv) and argv[i + 1]:
            return True
        if tok.startswith("--fields=") and tok.split("=", 1)[1]:
            return True
    return False


def _detect_git_project() -> str | None:
    """Walk upwards from cwd to find a .git entry; return its directory name as project scope."""
    cur = Path.cwd().resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ".git").exists():
            return candidate.name.lower()
    return None


def _print_result(data: Any, *, as_json: bool, command: str, fields: str | None = None) -> None:
    if as_json or fields:
        write_json(data, fields=fields)
        return

    if command == "boot":
        print(data.get("kernel_text", ""))
        print(
            f"\n[{data.get('mode', 'online')}] {data.get('count', 0)} entries, "
            f"{data.get('used_tokens', 0)}/{data.get('budget_tokens', 300)} tokens"
        )
        return

    if command == "recall":
        print(
            f"Recall ({data.get('mode', 'online')}, snap={data.get('snapshot_version')}, "
            f"tokens={data.get('used_tokens')}/{data.get('budget_tokens')}): {data.get('count', 0)} hits"
        )
        for item in data.get("results") or []:
            print(
                f"  - [{item['id']}|{item['severity']}|seen:{item['seen_count']}|{item['type']}|{item['scope']}] "
                f"{item['rule']} (score={item.get('score')})"
            )
            if item.get("rationale"):
                print(f"      rationale: {item['rationale']}")
        return

    if command == "mirror":
        print(f"Mirror ({data.get('mode', 'online')}, snap={data.get('snapshot_version')})")
        for section, label in (
            ("conflicts", "Conflicts"),
            ("lesson_warnings", "Lesson Warnings"),
            ("prior_decisions", "Prior Decisions"),
            ("blind_spots", "Blind Spots"),
        ):
            items = data.get(section) or []
            print(f"  {label} ({len(items)}):")
            for it in items:
                if "id" in it:
                    print(f"    - [{it['id']}] {it.get('rule', '')}")
                else:
                    print(f"    - [{it.get('kind', 'note')}] {it.get('detail', '')}")
        return

    if command == "status":
        brain = data.get("brain") or {}
        cache = data.get("cache") or {}
        skills = data.get("installed_skills") or []
        print(f"fos {data.get('version')} (mode={data.get('mode')}, device={data.get('device')})")
        if data.get("brain_url"):
            print(f"  Brain:       {data['brain_url']} (reachable={brain.get('reachable')})")
        else:
            print(f"  Brain:       local ({brain.get('data_dir')}, snap={brain.get('snapshot_version')}, nodes={brain.get('nodes')}, vec={brain.get('sqlite_vec')})")
        print(f"  Cache:       present={cache.get('present')}, snap={cache.get('snapshot_version')}, nodes={cache.get('nodes')}, outbox={cache.get('outbox_pending')}")
        upd_line = f"auto-update={data.get('auto_update_enabled')}, editable={data.get('editable_install')}"
        if data.get("last_update"):
            upd_line += f", last={data['last_update']}"
        print(f"  Automation:  {upd_line}")
        print(f"  AI Skills:   {len(skills)} installed" + (f" ({', '.join(skills)})" if skills else " (run `fos skill install`)"))
        return

    if command == "skill":
        installed = data.get("installed") or []
        if installed:
            for p in installed:
                print(f"installed {p}")
        else:
            print(dumps_json(data, pretty=True))
        return

    if command == "update":
        if data.get("updated"):
            print(f"fos updated: {data.get('local_version')} -> {data.get('remote_version')}")
        elif data.get("update_available"):
            print(f"update available: {data.get('local_version')} -> {data.get('remote_version')} (run `fos update`)")
        else:
            note = f" ({data['note']})" if data.get("note") else ""
            print(f"fos is up to date: {data.get('local_version')}{note}")
        return

    print(dumps_json(data, pretty=True))


def build_parser() -> _StrictArgumentParser:
    parser = _StrictArgumentParser(
        prog="fos",
        description="FlowOS (fos): AI-native cognitive compilation & memory engine (run `fos describe` for JSON catalog)",
    )
    parser.add_argument("--version", action="version", version=f"fos {__version__}")
    parser.add_argument("--home", type=str, default=None, help="Override FOS_HOME directory")
    parser.add_argument("--json", dest="global_json", action="store_true", help="Output JSON (compact when piped)")

    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Bind remote brain or switch to local mode")
    p_init.add_argument("--brain", type=str, default="", help="Brain URL, e.g. http://m3max.tailnet:7622")
    p_init.add_argument("--token", type=str, default="", help="Bearer token")
    p_init.add_argument("--local", action="store_true", help="Use local brain directly on this machine")
    p_init.add_argument("--json", action="store_true")

    # sync
    p_sync = sub.add_parser("sync", aliases=["s"], help="Flush offline outbox and pull latest compact snapshot")
    p_sync.add_argument("--json", action="store_true")

    # boot
    p_boot = sub.add_parser("boot", aliases=["b"], help="Emit <=300 token L0 wakeup kernel")
    p_boot.add_argument("--project", type=str, default=None)
    p_boot.add_argument("--all", dest="all_scopes", action="store_true", help="Include all scopes (skip cwd auto-detect)")
    p_boot.add_argument("--budget", type=int, default=300)
    p_boot.add_argument("--fields", type=str, default=None, help="Comma-separated JSON fields (implies --json)")
    p_boot.add_argument("--json", action="store_true")

    # recall
    p_recall = sub.add_parser("recall", aliases=["rec", "r", "search"], help="4-way hybrid recall within token budget")
    p_recall.add_argument("query", type=str)
    p_recall.add_argument("--kind", type=str, default=None)
    p_recall.add_argument("--scope", type=str, default=None)
    p_recall.add_argument("--as-of", dest="as_of", type=str, default=None)
    p_recall.add_argument("--budget", type=int, default=1200)
    p_recall.add_argument("--limit", type=int, default=20)
    p_recall.add_argument("--fields", type=str, default=None, help="Comma-separated JSON fields (implies --json)")
    p_recall.add_argument("--json", action="store_true")

    # get
    p_get = sub.add_parser("get", aliases=["g"], help="Fetch compiled node at L0/L1/L2 and optional graph")
    p_get.add_argument("node_id", type=str)
    p_get.add_argument("--tier", type=str, default="L1")
    p_get.add_argument("--graph", action="store_true")
    p_get.add_argument("--json", action="store_true")

    # mirror
    p_mirror = sub.add_parser("mirror", aliases=["mir", "check"], help="Pre-decision mirror review")
    p_mirror.add_argument("proposal", type=str)
    p_mirror.add_argument("--scope", type=str, default=None)
    p_mirror.add_argument("--json", action="store_true")

    # add
    p_add = sub.add_parser("add", aliases=["a"], help="Append a verbatim atom to the ledger")
    p_add.add_argument("content", type=str)
    p_add.add_argument("--kind", required=True, choices=["thought", "decision", "lesson", "outcome", "principle"])
    p_add.add_argument("--scope", type=str, default="global")
    p_add.add_argument("--supersedes", type=str, default=None)
    p_add.add_argument("--severity", type=str, default=None)
    p_add.add_argument("--entities", type=str, default=None, help="Comma-separated entity tags")
    p_add.add_argument("--trigger", action="append", default=None, help="Repeatable trigger_when phrase")
    p_add.add_argument("--evidence-ref", dest="evidence_ref", type=str, default=None)
    p_add.add_argument("--json", action="store_true")

    # ingest
    p_ingest = sub.add_parser("ingest", aliases=["ing"], help="Collect hindsight signals from local AI/tool traces")
    p_ingest.add_argument("--from", dest="source", required=True, choices=["antigravity", "claude", "codex", "pi", "fops", "all"])
    p_ingest.add_argument("--since", type=str, default="24h")
    p_ingest.add_argument("--root", type=str, default=None, help="Override trace root directory")
    p_ingest.add_argument("--limit", type=int, default=50)
    p_ingest.add_argument("--json", action="store_true")

    # export
    p_export = sub.add_parser("export", help="Export read-only human views")
    p_export.add_argument("--format", dest="fmt", default="md", choices=["md"])
    p_export.add_argument("--out", required=True, type=str)
    p_export.add_argument("--json", action="store_true")

    # dream
    p_dream = sub.add_parser("dream", aliases=["dr"], help="Run sleep compilation + 4-gate eval + atomic switch")
    p_dream.add_argument("--dry-run", action="store_true")
    p_dream.add_argument("--no-gate", action="store_true")
    p_dream.add_argument("--export-songe", type=str, default=None, help="Export cleaned experience stream for Songe MLX")
    p_dream.add_argument("--json", action="store_true")

    # eval
    p_eval = sub.add_parser("eval", aliases=["ev"], help="Run regression gates or pin a node into P0 golden suite")
    p_eval.add_argument("eval_args", nargs="*", help="Optional: pin <node_id>")
    p_eval.add_argument("--suite", type=str, default="all", choices=["all", "recall", "temporal", "fidelity", "behavior"])
    p_eval.add_argument("--json", action="store_true")

    # undo
    p_undo = sub.add_parser("undo", help="Atomically roll back to previous compiled snapshot")
    p_undo.add_argument("--json", action="store_true")

    # rebuild
    p_rebuild = sub.add_parser("rebuild", help="Rebuild SQLite snapshot from immutable ledger/*.jsonl")
    p_rebuild.add_argument("--json", action="store_true")

    # status
    p_status = sub.add_parser("status", aliases=["st"], help="Show CLI, brain connectivity, cache, update & AI skill status")
    p_status.add_argument("--offline", action="store_true", help="Skip network check")
    p_status.add_argument("--json", action="store_true")

    # describe
    p_desc = sub.add_parser("describe", aliases=["desc"], help="Emit machine-readable command catalog or command schema")
    p_desc.add_argument("target", nargs="?", default=None)
    p_desc.add_argument("--fields", type=str, default=None)
    p_desc.add_argument("--json", action="store_true")

    # skill
    p_skill = sub.add_parser("skill", aliases=["sk"], help="Install or refresh embedded AI agent skill bundle")
    p_skill.add_argument("action", nargs="?", default="install", choices=["install", "status"])
    p_skill.add_argument("--dir", dest="target_dir", type=str, default=None)
    p_skill.add_argument("--refresh-only", action="store_true")
    p_skill.add_argument("--json", action="store_true")

    # update
    p_upd = sub.add_parser("update", aliases=["up"], help="Self-update fos CLI from brain (SHA-256 verified) and refresh skills")
    p_upd.add_argument("--check", action="store_true", help="Check whether an update is available without installing")
    p_upd.add_argument("--background", action="store_true", help="Silent background auto-update mode")
    p_upd.add_argument("--json", action="store_true")

    return parser


def _check_unknown_subcommand(raw_argv: list[str]) -> None:
    """Provide Levenshtein 'did you mean?' hint before argparse emits a generic choice error."""
    # Find first positional token (skipping --home VALUE and global flags)
    i = 0
    sub_tok = None
    while i < len(raw_argv):
        tok = raw_argv[i]
        if tok in ("-h", "--help", "--version"):
            return
        if tok == "--home":
            i += 2
            continue
        if tok.startswith("--home=") or tok == "--json":
            i += 1
            continue
        if tok.startswith("-"):
            return
        sub_tok = tok
        break

    if not sub_tok:
        return

    from fos.describe import all_command_names, resolve_command_alias

    canonical = resolve_command_alias(sub_tok)
    valid = set(all_command_names())
    if canonical not in valid:
        hint = "run `fos --help` or `fos describe` to list commands"
        from fos.suggest import suggest_closest

        if close := suggest_closest(sub_tok, all_command_names()):
            hint = f'did you mean "{close}"? ({hint})'
        raise UsageError(f'unknown command "{sub_tok}"', hint=hint)


def run_cli(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    as_json = _detect_json_requested(raw_argv)

    try:
        _check_unknown_subcommand(raw_argv)
        parser = build_parser()
        args = parser.parse_args(raw_argv)
        fields = getattr(args, "fields", None)
        as_json = bool(getattr(args, "json", False) or getattr(args, "global_json", False) or fields)
        home = Path(args.home).expanduser().resolve() if args.home else None

        from fos.describe import resolve_command_alias

        cmd = resolve_command_alias(args.command)

        if cmd == "describe":
            from fos.describe import describe_payload

            result: Any = describe_payload(args.target)
            write_json(result, fields=fields)
            return int(ExitCode.OK)

        if cmd == "skill":
            from fos.skill import install_skill, list_installed_skills

            if args.action == "status":
                result = {"installed": list_installed_skills()}
            else:
                tdir = Path(args.target_dir) if args.target_dir else None
                result = install_skill(target_dir=tdir, refresh_only=args.refresh_only)
            _print_result(result, as_json=as_json, command=cmd)
            return int(ExitCode.OK)

        # Lazy import client only when needed
        from fos.client import FosClient
        from fos.config import load_config, save_config

        client = FosClient(home=home)

        if cmd == "init":
            if args.local:
                cfg = load_config(home)
                cfg.mode = "local"
                path = save_config(cfg, home)
                result = {"mode": "local", "config_path": str(path), "data_dir": str(cfg.resolved_data_dir(home))}
            else:
                if not args.brain:
                    raise UsageError("Either --brain <url> or --local is required.")
                result = client.init_remote(args.brain, args.token)
        elif cmd == "sync":
            result = client.sync()
        elif cmd == "status":
            result = client.status(offline=args.offline)
        elif cmd == "update":
            result = client.update(check_only=args.check, background=args.background)
            if args.background and not as_json:
                return int(ExitCode.OK)
        elif cmd == "boot":
            project = None if args.all_scopes else (args.project or _detect_git_project())
            result = client.boot(project=project, budget=args.budget)
        elif cmd == "recall":
            result = client.recall(
                args.query,
                kind=args.kind,
                scope=args.scope,
                as_of=args.as_of,
                budget=args.budget,
                limit=args.limit,
            )
        elif cmd == "get":
            result = client.get(args.node_id, tier=args.tier, include_graph=args.graph)
        elif cmd == "mirror":
            result = client.mirror(args.proposal, scope=args.scope)
        elif cmd == "add":
            entities = [e.strip() for e in args.entities.split(",") if e.strip()] if args.entities else None
            result = client.add(
                kind=args.kind,
                content=args.content,
                scope=args.scope,
                supersedes=args.supersedes,
                severity=args.severity,
                entities=entities,
                evidence_ref=args.evidence_ref,
                trigger_when=args.trigger,
            )
        elif cmd == "ingest":
            root = Path(args.root).expanduser().resolve() if args.root else None
            result = client.ingest(source=args.source, since=args.since, root_dir=root, limit=args.limit)
        elif cmd == "export":
            result = client.export_views(Path(args.out))
        elif cmd == "dream":
            songe_path = Path(args.export_songe) if args.export_songe else None
            result = client.dream(dry_run=args.dry_run, no_gate=args.no_gate, export_songe=songe_path)
        elif cmd == "eval":
            if args.eval_args:
                if len(args.eval_args) == 2 and args.eval_args[0] == "pin":
                    result = client.eval_pin(args.eval_args[1])
                else:
                    raise UsageError("Usage: fos eval [--suite ...] OR fos eval pin <node_id>")
            else:
                result = client.eval(suite=args.suite)
                _print_result(result, as_json=as_json, command=cmd)
                return int(ExitCode.OK if result.get("passed") else ExitCode.GATE_FAILED)
        elif cmd == "undo":
            result = client.undo()
        elif cmd == "rebuild":
            result = client.rebuild()
        else:
            raise UsageError(f"Unknown command: {cmd}")

        _print_result(result, as_json=as_json, command=cmd, fields=fields)
        return int(ExitCode.OK)

    except KeyboardInterrupt as exc:
        write_error(exc, as_json=as_json)
        return int(ExitCode.CANCELED)
    except GateFailedError as exc:
        write_error(exc, as_json=as_json)
        return int(exc.exit_code)
    except FosError as exc:
        write_error(exc, as_json=as_json)
        return int(exc.exit_code)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else int(ExitCode.ERROR)
    except Exception as exc:  # pragma: no cover
        write_error(exc, as_json=as_json)
        return int(ExitCode.ERROR)


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
