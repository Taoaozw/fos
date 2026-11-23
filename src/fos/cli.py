"""Ultra-lightweight AI-native `fos` CLI (<60ms cold start, fops-inspired ergonomics)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from fos import __version__
from fos.errors import ExitCode, FosError, GateFailedError, UsageError
from fos.output import compact_source_ref, dumps_json, write_error, write_json


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
        return

    if command == "boot_hook":
        if data.get("count", 0) > 0:
            print(data.get("kernel_text", ""))
            print("(fos: run `fos mirror` before risky changes; `fos correct <ID>` / `fos archive <ID>` if a rule is wrong/obsolete)")
        return

    if command == "recall":
        req_scope = data.get("scope") or "all"
        hits = data.get("results") or []
        print(f"# FOS L1 (scope={req_scope}, {len(hits)} hits, {data.get('snapshot_version')})")
        for item in hits:
            seen = int(item.get("seen_count", 1))
            seen_s = f"|{seen}x" if seen > 1 else ""
            sc = item.get("scope", "")
            scope_s = f"|{sc}" if (sc and sc != req_scope and not (sc == "global" and item.get("type") == "principle")) else ""
            fresh = item.get("freshness", "")
            fresh_s = f"|{fresh}" if fresh in ("drifted", "broken_ref") else ""
            print(f"[{item['id']}|{item['severity']}{seen_s}{scope_s}{fresh_s}] {item['rule']}")
            meta_parts: list[str] = []
            if item.get("rationale"):
                meta_parts.append(f"why: {item['rationale']}")
            if item.get("source_ref"):
                meta_parts.append(f"ref: {compact_source_ref(item['source_ref'])}")
            if meta_parts:
                print("  " + " | ".join(meta_parts))
            cc = item.get("compat_check")
            if isinstance(cc, dict) and cc.get("status") == "major_drift":
                drifts = ", ".join(cc.get("drifts") or [])
                print(f"  ⚠ compat major_drift ({drifts}): {cc.get('note', '')}")
        return

    if command == "mirror":
        sections = (
            ("conflicts", "Conflicts"),
            ("lesson_warnings", "Warnings"),
            ("prior_decisions", "Prior"),
            ("blind_spots", "BlindSpots"),
        )
        non_empty = [(sec, lbl, data.get(sec) or []) for sec, lbl in sections if data.get(sec)]
        if not non_empty:
            print(f"# FOS Mirror ({data.get('snapshot_version')}): OK (0 conflicts, 0 warnings)")
            return
        print(f"# FOS Mirror ({data.get('snapshot_version')})")
        for _sec, label, items in non_empty:
            print(f"{label} ({len(items)}):")
            for it in items:
                if "id" in it:
                    sup = f" -> superseded_by:{it['superseded_by']}" if it.get("superseded_by") else ""
                    print(f"  [{it['id']}{sup}] {it.get('rule', '')}")
                else:
                    print(f"  [{it.get('kind', 'note')}] {it.get('detail', '')}")
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

    if command == "proposals" and "proposals" in data:
        budget = data.get("l0_budget") or {}
        counts = data.get("counts") or {}
        print(
            f"Proposals (pending={counts.get('pending', 0)}, accepted={counts.get('accepted', 0)}, "
            f"dismissed={counts.get('dismissed', 0)} | L0={budget.get('used_tokens', 0)}/{budget.get('limit_tokens', 300)}t)"
        )
        for p in data.get("proposals") or []:
            repos = ",".join(p.get("evidence_repos") or [])
            delta = p.get("estimated_l0_delta", 0)
            delta_s = f"+{delta}t" if delta > 0 else f"{delta}t"
            pscope = p.get("proposed_scope") or "global"
            print(
                f"  - [{p['id']}|{p['kind']}|{pscope}|{p['proposed_severity']}|L0:{delta_s}] {p.get('title', '')}"
                + (f" ({repos})" if repos else "")
            )
            print(f"      {p.get('proposed_rule', '')}")
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

    if command == "audit" and "findings" in data:
        sm = data.get("summary") or {}
        print(
            f"Codebase Audit (scope={data.get('scope')}, repo={data.get('repo_path')}, "
            f"audited={data.get('audited_count', 0)}/{data.get('total_repo_nodes', 0)}, dry_run={data.get('dry_run')})"
        )
        print(
            f"  Summary: verified={sm.get('verified', 0)}, drifted={sm.get('drifted', 0)}, "
            f"reclassify={sm.get('reclassify', 0)}, obsolete={sm.get('obsolete', 0)}, misscoped={sm.get('misscoped', 0)} | "
            f"proposals_created={len(data.get('created_proposals') or [])}, verified_refreshed={data.get('verified_refreshed', 0)}"
        )
        for f in data.get("findings") or []:
            tag = str(f.get("verdict", "")).upper()
            print(f"  - [{tag}|{f.get('id')}|{f.get('severity')}] {f.get('reason', '')}")
            if f.get("evidence"):
                print(f"      evidence: {f['evidence']}")
            if f.get("suggested_rule"):
                print(f"      suggested: {f['suggested_rule']}")
        return

    if command == "pattern" and "patterns" in data:
        print(f"Code Patterns (snap={data.get('snapshot_version')}, count={data.get('count', 0)})")
        for it in data.get("patterns") or []:
            compat = it.get("compat") or {}
            stack = compat.get("stack") or {}
            stack_s = ", ".join(f"{k}={v}" for k, v in sorted(stack.items()))
            print(f"  - [{it['id']}|{it['severity']}|{it['scope']}|{it.get('freshness', 'untracked')}] {it['rule']}")
            if it.get("source_ref"):
                print(f"      source: {it['source_ref']}" + (f" ({stack_s})" if stack_s else ""))
            if compat.get("bench_cmd"):
                print(f"      verify: {compat['bench_cmd']}")
            cc = it.get("compat_check")
            if isinstance(cc, dict) and cc.get("status") in ("major_drift", "minor_drift"):
                drifts = ", ".join(cc.get("drifts") or [])
                print(f"      compat: {cc['status']} ({drifts})")
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
    p_boot.add_argument("--project", "--scope", dest="project", type=str, default=None, help="Project or domain scope (default: auto-detect git repo)")
    p_boot.add_argument("--scopes", type=str, default=None, help="Comma-separated extra domain/stack scopes to mount (e.g. eng/frontend,product)")
    p_boot.add_argument("--all", dest="all_scopes", action="store_true", help="Include all scopes (skip cwd auto-detect)")
    p_boot.add_argument("--budget", type=int, default=300)
    p_boot.add_argument("--hook", action="store_true", help="SessionStart hook mode (compact kernel only; silent when empty)")
    p_boot.add_argument("--fields", type=str, default=None, help="Comma-separated JSON fields (implies --json)")
    p_boot.add_argument("--json", action="store_true")

    # recall
    p_recall = sub.add_parser("recall", aliases=["rec", "r", "search"], help="4-way hybrid recall within token budget")
    p_recall.add_argument("query", type=str)
    p_recall.add_argument("--kind", type=str, default=None)
    p_recall.add_argument("--scope", type=str, default=None, help="Project or hierarchical domain scope (e.g. fos, eng/frontend, product)")
    p_recall.add_argument("--scopes", type=str, default=None, help="Comma-separated mounted domain/stack scopes")
    p_recall.add_argument("--as-of", dest="as_of", type=str, default=None)
    p_recall.add_argument("--budget", type=int, default=1200)
    p_recall.add_argument("--limit", type=int, default=20)
    p_recall.add_argument("--exclude", type=str, default=None, help="Comma-separated node IDs to omit (e.g. already in L0)")
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
    p_mirror.add_argument("--scope", type=str, default=None, help="Project or hierarchical domain scope")
    p_mirror.add_argument("--scopes", type=str, default=None, help="Comma-separated mounted domain/stack scopes")
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
    p_add.add_argument("--guard-pattern", dest="guard_pattern", type=str, default=None, help="Deterministic static guard regex (e.g. 'cmd:...' or 'path:*.tsx :: ...')")
    p_add.add_argument("--json", action="store_true")

    # correct
    p_cor = sub.add_parser("correct", aliases=["cor", "fix"], help="Self-correct an existing node by superseding it with a refined rule")
    p_cor.add_argument("node_id", type=str)
    p_cor.add_argument("content", type=str)
    p_cor.add_argument("--reason", type=str, default="", help="Why the previous rule was wrong or refined")
    p_cor.add_argument("--severity", type=str, default=None)
    p_cor.add_argument("--json", action="store_true")

    # archive
    p_arc = sub.add_parser("archive", aliases=["arc", "retire"], help="Retire an obsolete, expired, or already-guarded node (preserves --as-of history)")
    p_arc.add_argument("node_id", type=str)
    p_arc.add_argument("--reason", type=str, default="", help="Why this node is being archived")
    p_arc.add_argument("--json", action="store_true")

    # ingest
    p_ingest = sub.add_parser("ingest", aliases=["ing"], help="Collect high-compounding hindsight signals from docs/rules, git fixes & AI sessions")
    p_ingest.add_argument("--from", dest="source", required=True, choices=["docs", "git", "antigravity", "claude", "codex", "pi", "fops", "all"])
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
    p_dream.add_argument("--reasoner", type=str, default=None, choices=["auto", "heuristic", "cli", "mlx", "openai"], help="Override dream distiller backend")
    p_dream.add_argument("--cli-cmd", dest="cli_cmd", type=str, default=None, help="Override CLI reasoner command (e.g. 'codex exec' or 'claude -p')")
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

    # open
    p_open = sub.add_parser("open", aliases=["ui", "browse"], help="Open fosd Web Observability Console in default browser")
    p_open.add_argument("--print", dest="print_only", action="store_true", help="Print URL without opening browser")
    p_open.add_argument("--port", type=int, default=7622, help="Local fosd port when in local mode")
    p_open.add_argument("--json", action="store_true")

    # proposals
    p_prop = sub.add_parser("proposals", aliases=["prop", "inbox"], help="Scan, list, accept, or dismiss cross-project governance proposals")
    p_prop.add_argument("action", nargs="?", default="list", choices=["list", "scan", "accept", "dismiss"])
    p_prop.add_argument("proposal_id", nargs="?", default=None, help="Proposal ID (prp_...) for accept/dismiss")
    p_prop.add_argument("--status", default="pending", choices=["pending", "accepted", "dismissed", "expired", "all"])
    p_prop.add_argument("--root", type=str, default=None, help="Owned git repos root dir for scan")
    p_prop.add_argument("--reasoner", type=str, default=None, choices=["heuristic", "cli"], help="Reasoner override for scan")
    p_prop.add_argument("--cli-cmd", type=str, default=None, help="CLI reasoner command override")
    p_prop.add_argument("--severity", type=str, default=None, choices=["P0", "P1", "P2"], help="Severity override on accept")
    p_prop.add_argument("--scope", type=str, default=None, help="Target scope override on accept (e.g. eng/frontend, eng/ops, global)")
    p_prop.add_argument("--rule", type=str, default=None, help="Rule override on accept")
    p_prop.add_argument("--reason", type=str, default="", help="Dismiss reason or rationale override")
    p_prop.add_argument("--force", action="store_true", help="Bypass scan cache or L0 budget guard")
    p_prop.add_argument("--json", action="store_true")

    # guard
    p_guard = sub.add_parser("guard", aliases=["gd"], help="Run deterministic 0-LLM static guardrail checks or graduate a rule")
    p_guard.add_argument("guard_args", nargs="*", help="Optional: 'graduate <NODE_ID>'")
    p_guard.add_argument("--staged", action="store_true", help="Check staged git diff (--cached)")
    p_guard.add_argument("--diff", dest="diff_source", type=str, default=None, help="Diff file path or '-' for stdin")
    p_guard.add_argument("--cmd", dest="check_cmd", type=str, default=None, help="Shell command string to check against cmd: rules")
    p_guard.add_argument("--scope", type=str, default=None, help="Project scope (auto-detected from cwd if omitted)")
    p_guard.add_argument("--pattern", type=str, default=None, help="Guard regex pattern when graduating a node")
    p_guard.add_argument("--reason", type=str, default="", help="Reason when graduating a node")
    p_guard.add_argument("--json", action="store_true")

    # feedback
    p_fb = sub.add_parser("feedback", aliases=["fb"], help="Record PlugMem utility feedback (useful/noise) for a node")
    p_fb.add_argument("node_id", type=str)
    fb_grp = p_fb.add_mutually_exclusive_group(required=True)
    fb_grp.add_argument("--useful", action="store_true", help="Mark recalled node as helpful")
    fb_grp.add_argument("--noise", action="store_true", help="Mark recalled node as noise/irrelevant")
    p_fb.add_argument("--context", type=str, default="", help="Optional feedback context note")
    p_fb.add_argument("--json", action="store_true")

    # pattern (code exemplar memory)
    p_pat = sub.add_parser(
        "pattern",
        aliases=["pat"],
        help="Store, list, or evolve timeless high-performance code patterns (L1 invariant + symbol-anchored L2 + SemVer compat)",
    )
    p_pat.add_argument("action", nargs="?", default="list", choices=["add", "list", "sync"])
    p_pat.add_argument("target", nargs="?", default=None, help="File[::Symbol] (for add) or NODE_ID (for sync)")
    p_pat.add_argument("rule", nargs="?", default=None, help="Concise timeless design invariant / optimization essence (for add)")
    p_pat.add_argument("--rule", dest="rule_override", type=str, default=None, help="Updated invariant/rule when running sync")
    p_pat.add_argument("--why", dest="rationale", type=str, default=None, help="First-principles rationale / what improved or was fixed")
    p_pat.add_argument("--bench", dest="bench_cmd", type=str, default=None, help="Verification test/benchmark command")
    p_pat.add_argument("--supersedes", type=str, default=None, help="Old pattern node ID superseded by this improved v2 pattern")
    p_pat.add_argument("--scope", type=str, default=None, help="Project scope (auto-detected from target repo if omitted)")
    p_pat.add_argument("--severity", type=str, default="P1", choices=["P0", "P1", "P2"])
    p_pat.add_argument("--kind", type=str, default="decision", choices=["decision", "lesson", "principle"])
    p_pat.add_argument("--stack", type=str, default=None, help="Override/add compat stack versions, e.g. kotlin=2.4,spring-boot=4.1")
    p_pat.add_argument("--entities", type=str, default=None, help="Comma-separated entity tags")
    p_pat.add_argument("--trigger", action="append", default=None, help="Repeatable trigger_when phrase")
    p_pat.add_argument("--max-lines", dest="max_lines", type=int, default=45, help="Max skeleton lines stored in L2 ledger")
    p_pat.add_argument("--json", action="store_true")

    # audit (codebase-grounded read-only memory verification)
    p_audit = sub.add_parser(
        "audit",
        aliases=["au"],
        help="Verify active repository memories against live source code via read-only coding agent (codex exec)",
    )
    p_audit.add_argument("--scope", type=str, default=None, help="Repository scope to audit (auto-detected from cwd)")
    p_audit.add_argument("--repo", type=str, default=None, help="Explicit local repository path")
    p_audit.add_argument("--model", type=str, default=None, help="Override coding agent model (e.g. gpt-6.2-sol)")
    p_audit.add_argument(
        "--effort",
        type=str,
        default=None,
        choices=["low", "medium", "high", "xhigh"],
        help="Reasoning effort for codex exec",
    )
    p_audit.add_argument("--cli-cmd", type=str, default=None, help="Override CLI agent command (default from config)")
    p_audit.add_argument("--dry-run", action="store_true", help="Report findings only without writing proposals or timestamps")
    p_audit.add_argument("--timeout", type=int, default=240, help="Subprocess timeout in seconds")
    p_audit.add_argument("--json", action="store_true")

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
            from fos.scopes import canonical_repo_name, detect_repo_scopes, parse_scopes_param

            explicit_scopes = parse_scopes_param(args.scopes)
            git_proj = _detect_git_project()
            if args.all_scopes:
                project = None
                mounted_scopes = explicit_scopes or None
            else:
                project = args.project if args.project is not None else (None if explicit_scopes else git_proj)
                auto_scopes: list[str] = []
                if project and git_proj and canonical_repo_name(project) == git_proj:
                    auto_scopes = detect_repo_scopes(Path.cwd())
                mounted_scopes = list(dict.fromkeys([*auto_scopes, *explicit_scopes])) or None
            result = client.boot(project=project, scopes=mounted_scopes, budget=args.budget)
            if args.hook and not as_json and not fields:
                _print_result(result, as_json=False, command="boot_hook")
                return int(ExitCode.OK)
        elif cmd == "recall":
            from fos.code_pattern import detect_repo_stack_versions
            from fos.scopes import canonical_repo_name, detect_repo_scopes, parse_scopes_param

            cwd_stack, _ = detect_repo_stack_versions(Path.cwd())
            explicit_scopes = parse_scopes_param(args.scopes)
            git_proj = _detect_git_project()
            auto_scopes: list[str] = []
            if args.scope and git_proj and canonical_repo_name(args.scope) == git_proj:
                auto_scopes = detect_repo_scopes(Path.cwd(), cwd_stack=cwd_stack or None)
            mounted_scopes = list(dict.fromkeys([*auto_scopes, *explicit_scopes])) or None
            excl = [x.strip() for x in args.exclude.split(",") if x.strip()] if args.exclude else None
            result = client.recall(
                args.query,
                kind=args.kind,
                scope=args.scope,
                scopes=mounted_scopes,
                as_of=args.as_of,
                budget=args.budget,
                limit=args.limit,
                cwd_stack=cwd_stack or None,
                exclude_ids=excl,
            )
        elif cmd == "get":
            result = client.get(args.node_id, tier=args.tier, include_graph=args.graph)
        elif cmd == "mirror":
            from fos.scopes import canonical_repo_name, detect_repo_scopes, parse_scopes_param

            explicit_scopes = parse_scopes_param(args.scopes)
            git_proj = _detect_git_project()
            auto_scopes = (
                detect_repo_scopes(Path.cwd())
                if (args.scope and git_proj and canonical_repo_name(args.scope) == git_proj)
                else []
            )
            mounted_scopes = list(dict.fromkeys([*auto_scopes, *explicit_scopes])) or None
            result = client.mirror(args.proposal, scope=args.scope, scopes=mounted_scopes)
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
                guard_pattern=args.guard_pattern,
            )
        elif cmd == "correct":
            result = client.correct(
                args.node_id,
                args.content,
                reason=args.reason,
                severity=args.severity,
            )
        elif cmd == "archive":
            result = client.archive(args.node_id, reason=args.reason)
        elif cmd == "ingest":
            root = Path(args.root).expanduser().resolve() if args.root else None
            result = client.ingest(source=args.source, since=args.since, root_dir=root, limit=args.limit)
        elif cmd == "export":
            result = client.export_views(Path(args.out))
        elif cmd == "dream":
            songe_path = Path(args.export_songe) if args.export_songe else None
            result = client.dream(
                dry_run=args.dry_run,
                no_gate=args.no_gate,
                export_songe=songe_path,
                reasoner=args.reasoner,
                cli_command=args.cli_cmd,
            )
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
        elif cmd == "open":
            import webbrowser

            url = client.cfg.brain_url.rstrip("/") + "/" if client.is_remote else f"http://127.0.0.1:{args.port}/"
            opened = False
            if not args.print_only:
                opened = bool(webbrowser.open(url))
            result = {"url": url, "opened": opened, "mode": client.cfg.mode}
            if not as_json:
                print(url)
                return int(ExitCode.OK)
        elif cmd == "proposals":
            if args.action == "list":
                result = client.list_proposals(status=args.status)
            elif args.action == "scan":
                rdir = Path(args.root).expanduser().resolve() if args.root else None
                result = client.scan_proposals(
                    root_dir=rdir,
                    reasoner=args.reasoner,
                    cli_command=args.cli_cmd,
                    force=args.force,
                )
            elif args.action == "accept":
                if not args.proposal_id:
                    raise UsageError("Usage: fos proposals accept <proposal_id> [--severity P0|P1] [--rule TEXT]")
                result = client.accept_proposal(
                    args.proposal_id,
                    rule=args.rule,
                    rationale=args.reason or None,
                    severity=args.severity,
                    scope=args.scope,
                    force=args.force,
                )
            elif args.action == "dismiss":
                if not args.proposal_id:
                    raise UsageError("Usage: fos proposals dismiss <proposal_id> [--reason TEXT]")
                result = client.dismiss_proposal(args.proposal_id, reason=args.reason)
            else:
                raise UsageError(f"Unknown proposals action: {args.action}")
        elif cmd == "guard":
            if args.guard_args:
                if len(args.guard_args) == 2 and args.guard_args[0] == "graduate":
                    result = client.graduate(
                        args.guard_args[1],
                        guard_pattern=args.pattern,
                        reason=args.reason,
                    )
                else:
                    raise UsageError("Usage: fos guard [--staged|--diff FILE|--cmd CMD] OR fos guard graduate <NODE_ID>")
            else:
                from fos.guard import load_git_diff
                from fos.scopes import canonical_repo_name, detect_repo_scopes, resolve_active_scopes

                git_proj = _detect_git_project()
                scope = args.scope or git_proj
                guard_scopes: list[str] | None = None
                if scope and git_proj and canonical_repo_name(scope) == git_proj:
                    guard_scopes = resolve_active_scopes(scope, extra_scopes=detect_repo_scopes(Path.cwd()))
                diff_text: str | None = None
                if args.diff_source == "-":
                    diff_text = sys.stdin.read()
                elif args.diff_source:
                    diff_text = Path(args.diff_source).read_text(encoding="utf-8", errors="replace")
                elif args.staged or not args.check_cmd:
                    diff_text = load_git_diff(staged=bool(args.staged))
                result = client.guard_check(diff_text=diff_text, command=args.check_cmd, scope=scope, scopes=guard_scopes)
                _print_result(result, as_json=as_json, command=cmd)
                return int(ExitCode.OK if result.get("passed") else ExitCode.GATE_FAILED)
        elif cmd == "feedback":
            verdict = "useful" if args.useful else "noise"
            result = client.feedback(args.node_id, verdict, context=args.context)
        elif cmd == "audit":
            scope = args.scope or _detect_git_project()
            if not scope:
                raise UsageError("Specify `--scope <repo>` or run `fos audit` inside a git repository.")
            rpath = Path(args.repo).expanduser().resolve() if args.repo else None
            result = client.audit(
                scope=scope,
                repo_path=rpath,
                cli_command=args.cli_cmd,
                model=args.model,
                effort=args.effort,
                dry_run=args.dry_run,
                timeout_s=args.timeout,
            )
        elif cmd == "pattern":
            from fos.code_pattern import detect_repo_stack_versions, parse_stack_param

            if args.action == "list":
                cwd_stack, _ = detect_repo_stack_versions(Path.cwd())
                result = client.list_patterns(scope=args.scope, cwd_stack=cwd_stack or None)
            elif args.action == "add":
                if not args.target or not args.rule:
                    raise UsageError(
                        'Usage: fos pattern add <file[::symbol]> "<timeless invariant>" [--why "..."] [--bench "..."] [--supersedes ID]'
                    )
                entities = [e.strip() for e in args.entities.split(",") if e.strip()] if args.entities else None
                stack_override = parse_stack_param(args.stack) if args.stack else None
                result = client.add_pattern(
                    args.target,
                    args.rule,
                    rationale=args.rationale or "",
                    bench_cmd=args.bench_cmd or "",
                    scope=args.scope,
                    severity=args.severity,
                    kind=args.kind,
                    supersedes=args.supersedes,
                    entities=entities,
                    trigger_when=args.trigger,
                    stack_override=stack_override,
                    max_lines=args.max_lines,
                )
            elif args.action == "sync":
                if not args.target:
                    raise UsageError(
                        'Usage: fos pattern sync <NODE_ID> [--rule "..."] [--why "..."] [--bench "..."] [--json]'
                    )
                stack_override = parse_stack_param(args.stack) if args.stack else None
                result = client.sync_pattern(
                    args.target,
                    rule=args.rule_override or args.rule,
                    rationale=args.rationale,
                    bench_cmd=args.bench_cmd,
                    stack_override=stack_override,
                    max_lines=args.max_lines,
                )
            else:
                raise UsageError(f"Unknown pattern action: {args.action}")
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
