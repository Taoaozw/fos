"""Codebase-grounded read-only memory auditor (inspired by stalebrain + Letta Sleep-time Compute).

Runs a read-only coding agent (`codex exec -s read-only -C <repo_path>`) against a single repository
to verify each active memory card against live source code, docs, and git history.
Never mutates repository files or silently deletes memories; drift/obsolete findings are routed
into the existing Proposal Inbox for human review.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fos.engine import BrainEngine
from fos.errors import FosError, UsageError
from fos.models import CompiledNode, now_iso
from fos.proposals import (
    Proposal,
    ProposalStore,
    _is_similar_proposal,
    _proposal_fingerprint,
)
from fos.providers import _extract_json_object
from fos.scopes import canonical_repo_name, scope_kind
from fos.ulid import new_ulid

AuditVerdict = Literal["verified", "drifted", "reclassify", "obsolete", "misscoped"]
VALID_AUDIT_VERDICTS: tuple[str, ...] = ("verified", "drifted", "reclassify", "obsolete", "misscoped")

_AUDIT_SYSTEM_PROMPT = """You are FlowOS's Read-Only Codebase Memory Auditor (modeled on stalebrain + Letta Sleep-time Compute).
You are running INSIDE the real target repository in read-only sandbox mode.
Your job is to audit EVERY active FlowOS memory card below against the ACTUAL codebase, docs, linter/CI guards, and git history.

MANDATORY VERIFICATION PROTOCOL:
- DO NOT guess from memory! Inspect the real files in the working directory (use rg, ls, read files, or git log) to verify each claim.
- Check `source_ref` if provided, and also check the actual implementation code and lint/test guards (docs themselves can be stale!).
- For each card ID in the input list, emit EXACTLY ONE verdict:
  1. `verified`: The rule, scope, type, and severity accurately reflect the current codebase and provide durable non-redundant engineering value.
  2. `drifted`: The underlying implementation/symbol/config/boundary has changed, OR the rule is verbose/contradictory with another card and should be distilled into a sharper constraint. You MUST provide a crisp `suggested_rule` (1-2 Simplified Chinese sentences, <= 75 tokens, preserving exact technical symbols/flags), and optionally `suggested_type` (`decision`|`lesson`) / `suggested_severity` (`P1`|`P2`).
  3. `reclassify`: The rule's substance is valid, BUT its severity (`P1` vs `P2`), type (`decision` vs `lesson`), or wording should be tuned (e.g., narrow low-frequency detail occupying `P1` L0 budget should be downgraded to `P2`, or high-impact pitfall promoted to `P1`). Provide `suggested_severity` and/or `suggested_type` (and optional tightened `suggested_rule`). Never suggest `P0` for repo-scoped cards.
  4. `obsolete`: The referenced feature/module/workaround was deleted or replaced, OR the card is strictly duplicated/contradicted by a better card in the list, OR already mechanically enforced by ESLint / CI / Guard Test, OR low-value aesthetic/boilerplate/etiquette fluff failing the 6-month half-life test.
  5. `misscoped`: The card clearly belongs to a DIFFERENT repository or a reusable domain/stack scope (e.g. `eng/frontend`, `eng/backend/python`, `eng/ops`). Provide `suggested_scope` (lowercase repo name or <=3-level domain path) if clearly identifiable so it can be migrated, else "" to retire it.

STRICT OUTPUT FORMAT (return STRICT JSON ONLY, no markdown fences, no prose):
{
  "findings": [
    {
      "id": "<NODE_ID>",
      "verdict": "verified" | "drifted" | "reclassify" | "obsolete" | "misscoped",
      "evidence": "Concrete file path(s), symbol(s), linter/test rule, or git fact observed in this repo (<= 160 chars)",
      "reason": "Concise Simplified Chinese explanation of why this verdict holds",
      "suggested_rule": "Required when verdict == 'drifted'; optional distilled rule for 'reclassify'; else empty string",
      "suggested_type": "Optional 'decision' | 'lesson' when changing type; else empty string",
      "suggested_severity": "Optional 'P1' | 'P2' when changing priority; else empty string",
      "suggested_scope": "Optional target repo name when verdict == 'misscoped'; else empty string",
      "confidence": 0.9
    }
  ]
}"""


def resolve_repo_path(scope: str, explicit_repo: Path | None = None) -> Path:
    """Resolve the local checkout path for `scope`."""
    if explicit_repo is not None:
        p = Path(explicit_repo).expanduser().resolve()
        if not p.is_dir():
            raise UsageError(f"Repository path does not exist: {p}")
        return p

    from fos.scopes import canonical_repo_name, scope_kind

    if not scope or scope_kind(scope) != "repo":
        raise UsageError(
            f"Codebase audit requires a concrete repository scope (e.g. `--scope fos`), not `{scope or 'global'}`.",
            hint="Run inside a git repository or pass `--scope <repo> --repo <path>`.",
        )
    target = canonical_repo_name(scope)

    # 1. Check current working directory git root
    cwd = Path.cwd().resolve()
    for cand in (cwd, *cwd.parents):
        if (cand / ".git").exists():
            if cand.name.lower() == target:
                return cand
            break

    # 2. Search standard local workspace roots
    search_roots = [
        cwd.parent,
        Path.home() / "project" / "clive",
        Path.home() / "projects" / "clive",
        Path.home() / "project",
        Path.home() / "projects",
    ]
    for root in search_roots:
        cand = root / target
        if cand.is_dir():
            return cand.resolve()

    raise UsageError(
        f"Cannot locate local repository checkout for scope '{scope}'.",
        hint=f"Pass `--repo /path/to/{scope}` explicitly.",
    )


def build_audit_argv(
    *,
    cli_command: str,
    repo_path: Path,
    out_file: Path,
    prompt: str,
    model: str | None = None,
    effort: str | None = None,
) -> list[str]:
    """Build read-only CLI invocation for codebase-grounded auditing."""
    tokens = shlex.split(cli_command or "codex exec")
    if not tokens:
        tokens = ["codex", "exec"]

    is_codex = len(tokens) >= 2 and Path(tokens[0]).name == "codex" and tokens[1] == "exec"
    if is_codex:
        argv = list(tokens)
        if model and "-m" not in argv and "--model" not in argv:
            argv.extend(["-m", model])
        if effort and not any("model_reasoning_effort" in t for t in argv):
            argv.extend(["-c", f"model_reasoning_effort={effort}"])
        argv.extend(
            [
                "-s",
                "read-only",
                "-C",
                str(repo_path),
                "--skip-git-repo-check",
                "--ephemeral",
                "-o",
                str(out_file),
                prompt,
            ]
        )
        return argv

    return list(tokens) + [prompt]


def _default_cli_runner(
    *,
    repo_path: Path,
    prompt: str,
    cli_command: str,
    model: str | None,
    effort: str | None,
    timeout_s: int,
) -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory(prefix="fos-audit-") as tmp_dir:
        out_file = Path(tmp_dir) / "audit_out.txt"
        argv = build_audit_argv(
            cli_command=cli_command,
            repo_path=repo_path,
            out_file=out_file,
            prompt=prompt,
            model=model,
            effort=effort,
        )
        try:
            proc = subprocess.run(
                argv,
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FosError(
                f"Codebase audit timed out after {timeout_s}s.",
                hint="Increase `--timeout` or audit a smaller scope.",
            ) from exc
        except OSError as exc:
            raise FosError(
                f"Failed to execute audit CLI `{cli_command}`: {exc}",
                hint="Ensure `codex` is installed and on PATH, or pass `--cli-cmd`.",
            ) from exc

        raw_out = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else proc.stdout
        parsed = _extract_json_object(raw_out)
        if parsed is None:
            stderr_tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise FosError(
                f"Audit agent returned non-JSON output (exit={proc.returncode}).",
                hint=stderr_tail or "Verify model name and CLI login state.",
            )
        return parsed


def _refresh_verified_timestamps(engine: BrainEngine, verified_ids: list[str]) -> int:
    if not verified_ids:
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    refreshed = 0
    with engine.snapshots.open_current(read_only=False) as db:
        for nid in verified_ids:
            node = db.get_node(nid)
            if node is None or node.status != "active":
                continue
            node.verified_at = today
            node.updated_at = now_iso()
            db.upsert_node(node)
            refreshed += 1
    return refreshed


def _enqueue_audit_proposals(
    engine: BrainEngine,
    *,
    scope: str,
    node_map: dict[str, CompiledNode],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Route drifted/obsolete/misscoped findings into ProposalStore with dedup & cooldown."""
    store = ProposalStore(engine.data_dir)
    existing_all = store.load_all()
    pending_list = [p for p in existing_all if p.status == "pending"]
    dismissed_list = [p for p in existing_all if p.status == "dismissed"]
    accepted_list = [p for p in existing_all if p.status == "accepted"]
    pending_by_fp = {p.fingerprint: p for p in pending_list}
    dismissed_by_fp = {p.fingerprint: p for p in dismissed_list}
    accepted_fps = {p.fingerprint for p in accepted_list}

    created: list[dict[str, Any]] = []
    for f in findings:
        verdict = f["verdict"]
        nid = f["id"]
        node = node_map.get(nid)
        if node is None or verdict == "verified":
            continue

        evidence = f.get("evidence") or ""
        reason = f.get("reason") or ""
        full_rationale = f"{reason}（核验证据：{evidence}）".strip("（）") if evidence else reason

        target_type = str(f.get("suggested_type") or "").strip().lower()
        if target_type not in ("decision", "lesson", "principle"):
            target_type = node.type
        target_sev = str(f.get("suggested_severity") or "").strip().upper()
        if target_sev not in ("P1", "P2"):
            target_sev = node.severity
        target_scope = node.scope

        if verdict == "drifted" and f.get("suggested_rule"):
            kind = "fix_drifted"
            title = f"代码漂移提炼 {nid}（{scope}）"
            proposed_rule = str(f["suggested_rule"]).strip()
            topic_key = f"audit-drifted-{nid.lower()}"
        elif verdict == "reclassify":
            kind = "fix_drifted"
            proposed_rule = str(f.get("suggested_rule") or node.rule).strip()
            changes = []
            if target_sev != node.severity:
                changes.append(f"{node.severity}→{target_sev}")
            if target_type != node.type:
                changes.append(f"{node.type}→{target_type}")
            if proposed_rule != node.rule:
                changes.append("提炼")
            if not changes:
                continue
            title = f"重分类/调优 {nid}（{' / '.join(changes)}）"
            topic_key = f"audit-reclassify-{nid.lower()}-{target_sev.lower()}-{target_type}"
        elif verdict == "misscoped" and f.get("suggested_scope") and f["suggested_scope"] not in ("global", scope):
            from fos.scopes import normalize_scope

            kind = "fix_drifted"
            target_scope = normalize_scope(str(f["suggested_scope"]), strict=False)
            proposed_rule = str(f.get("suggested_rule") or node.rule).strip()
            title = f"迁移作用域 {nid}（{scope}→{target_scope}）"
            topic_key = f"audit-migrate-{nid.lower()}-{target_scope}"
        else:
            kind = "retire_stale"
            label = "作用域错位" if verdict == "misscoped" else "无效/过时规则"
            title = f"{label}，建议归档 {nid}"
            proposed_rule = node.rule
            topic_key = f"audit-{verdict}-{nid.lower()}"

        fp = _proposal_fingerprint(kind, topic_key, [nid])
        triggers = list(node.trigger_when)
        if (
            fp in pending_by_fp
            or fp in accepted_fps
            or fp in dismissed_by_fp
            or any(
                _is_similar_proposal(
                    kind=kind,
                    title=title,
                    rule=proposed_rule,
                    triggers=triggers,
                    target_ids=[nid],
                    existing=prev,
                )
                for prev in (pending_list + accepted_list + dismissed_list)
            )
        ):
            continue

        prop = Proposal(
            id=f"prp_{new_ulid()}",
            kind=kind,
            status="pending",
            fingerprint=fp,
            title=title[:60],
            proposed_type=target_type,
            proposed_scope=target_scope,
            proposed_severity=target_sev,
            proposed_rule=proposed_rule,
            proposed_rationale=full_rationale,
            proposed_guard_hint=node.guard_hint,
            proposed_trigger_when=triggers,
            evidence_repos=[scope],
            evidence_nodes=[
                {
                    "id": node.id,
                    "scope": node.scope,
                    "type": node.type,
                    "severity": node.severity,
                    "rule": node.rule[:220],
                    "audit_verdict": verdict,
                    "audit_evidence": evidence[:200],
                }
            ],
            evidence_manifests=[],
            target_node_ids=[nid],
            estimated_l0_delta=0,
            confidence=min(1.0, max(0.5, float(f.get("confidence", 0.85)))),
        )
        store.save(prop)
        pending_by_fp[fp] = prop
        pending_list.append(prop)
        created.append(prop.to_dict())

    return created


def _evaluate_repo_memories_readonly(
    *,
    clean_scope: str,
    resolved_repo: Path,
    snap_ver: str,
    nodes: list[CompiledNode],
    cli_command: str,
    model: str | None,
    effort: str | None,
    dry_run: bool,
    timeout_s: int,
    runner: Callable[..., dict[str, Any] | None] | None,
) -> tuple[dict[str, Any], dict[str, CompiledNode]]:
    """Run read-only codebase audit agent and parse findings without mutating BrainEngine state."""
    if not nodes:
        return (
            {
                "scope": clean_scope,
                "repo_path": str(resolved_repo),
                "snapshot_version": snap_ver,
                "cli_command": cli_command,
                "model": model or "",
                "effort": effort or "",
                "dry_run": dry_run,
                "audited_count": 0,
                "total_repo_nodes": 0,
                "summary": {v: 0 for v in VALID_AUDIT_VERDICTS},
                "findings": [],
                "verified_refreshed": 0,
                "created_proposals": [],
            },
            {},
        )

    node_map = {n.id: n for n in nodes}
    cards_payload = [
        {
            "id": n.id,
            "type": n.type,
            "severity": n.severity,
            "rule": n.rule,
            "rationale": n.rationale[:200],
            "source_ref": n.source_ref,
            "verified_at": n.verified_at,
        }
        for n in sorted(nodes, key=lambda x: (x.severity, x.id))
    ]
    prompt = (
        f"{_AUDIT_SYSTEM_PROMPT}\n\n"
        f"Target repository scope: `{clean_scope}`\n"
        f"Repository working directory: `{resolved_repo}`\n"
        f"Active memory cards to audit ({len(cards_payload)}):\n"
        + json.dumps(cards_payload, ensure_ascii=False, indent=2)
    )

    exec_fn = runner or _default_cli_runner
    resp = exec_fn(
        repo_path=resolved_repo,
        prompt=prompt,
        cli_command=cli_command,
        model=model,
        effort=effort,
        timeout_s=timeout_s,
    )
    raw_findings = resp.get("findings") if isinstance(resp, dict) and isinstance(resp.get("findings"), list) else []

    seen_ids: set[str] = set()
    findings: list[dict[str, Any]] = []
    summary: dict[str, int] = {v: 0 for v in VALID_AUDIT_VERDICTS}

    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        nid = str(item.get("id") or "").strip()
        if nid not in node_map or nid in seen_ids:
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in VALID_AUDIT_VERDICTS:
            continue
        seen_ids.add(nid)
        summary[verdict] += 1
        s_type = str(item.get("suggested_type") or "").strip().lower()
        if s_type not in ("decision", "lesson"):
            s_type = ""
        s_sev = str(item.get("suggested_severity") or "").strip().upper()
        if s_sev not in ("P1", "P2"):
            s_sev = ""
        findings.append(
            {
                "id": nid,
                "severity": node_map[nid].severity,
                "type": node_map[nid].type,
                "current_rule": node_map[nid].rule,
                "verdict": verdict,
                "evidence": str(item.get("evidence") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
                "suggested_rule": str(item.get("suggested_rule") or "").strip() if verdict in ("drifted", "reclassify") else "",
                "suggested_type": s_type if verdict in ("drifted", "reclassify") else "",
                "suggested_severity": s_sev if verdict in ("drifted", "reclassify") else "",
                "suggested_scope": str(item.get("suggested_scope") or "").strip().lower() if verdict == "misscoped" else "",
                "confidence": min(1.0, max(0.0, float(item.get("confidence", 0.85)))),
            }
        )

    return (
        {
            "scope": clean_scope,
            "repo_path": str(resolved_repo),
            "snapshot_version": snap_ver,
            "cli_command": cli_command,
            "model": model or "",
            "effort": effort or "",
            "dry_run": dry_run,
            "audited_count": len(findings),
            "total_repo_nodes": len(nodes),
            "summary": summary,
            "findings": findings,
            "verified_refreshed": 0,
            "created_proposals": [],
        },
        node_map,
    )


def _commit_repo_audit_findings(
    engine: BrainEngine,
    *,
    clean_scope: str,
    node_map: dict[str, CompiledNode],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Persist verified timestamps and proposals on the calling thread (zero SQLite write contention)."""
    findings = report.get("findings") or []
    if not findings or not node_map:
        return report
    verified_ids = [f["id"] for f in findings if f["verdict"] == "verified"]
    report["verified_refreshed"] = _refresh_verified_timestamps(engine, verified_ids)
    report["created_proposals"] = _enqueue_audit_proposals(
        engine,
        scope=clean_scope,
        node_map=node_map,
        findings=findings,
    )
    return report


def _load_active_repo_nodes(engine: BrainEngine, target_repo: str) -> tuple[str, list[CompiledNode]]:
    canon = canonical_repo_name(target_repo)
    with engine.snapshots.open_current(read_only=True) as db:
        snap_ver = db.get_meta("snapshot_version", "unknown")
        nodes = [
            n
            for n in db.list_nodes(as_of=None)
            if n.status == "active"
            and scope_kind(n.scope) == "repo"
            and canonical_repo_name(n.scope) == canon
        ]
    return snap_ver, nodes


def list_auditable_repo_scopes(engine: BrainEngine) -> list[str]:
    """Return sorted canonical repository names that have active memories (excluding global & domain scopes)."""
    repos: set[str] = set()
    with engine.snapshots.open_current(read_only=True) as db:
        for node in db.list_nodes(as_of=None):
            if node.status != "active":
                continue
            if scope_kind(node.scope) != "repo":
                continue
            canon = canonical_repo_name(node.scope)
            if canon:
                repos.add(canon)
    return sorted(repos)


def _repo_has_recent_commits(repo_dir: Path, since_hours: int) -> bool:
    """Return True if the git repository has at least one commit within `since_hours`."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "log", f"--since={int(since_hours)} hours ago", "-n", "1", "--oneline"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:
        return True


def audit_repo_memories(
    engine: BrainEngine,
    *,
    scope: str,
    repo_path: Path | None = None,
    cli_command: str = "codex exec",
    model: str | None = None,
    effort: str | None = None,
    dry_run: bool = False,
    timeout_s: int = 240,
    runner: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Audit all active memories of a single repository against its live codebase."""
    clean_scope = scope.strip().lower()
    resolved_repo = resolve_repo_path(clean_scope, repo_path)
    canon_scope = canonical_repo_name(clean_scope)
    snap_ver, nodes = _load_active_repo_nodes(engine, canon_scope)

    report, node_map = _evaluate_repo_memories_readonly(
        clean_scope=canon_scope,
        resolved_repo=resolved_repo,
        snap_ver=snap_ver,
        nodes=nodes,
        cli_command=cli_command,
        model=model,
        effort=effort,
        dry_run=dry_run,
        timeout_s=timeout_s,
        runner=runner,
    )
    if not dry_run:
        _commit_repo_audit_findings(engine, clean_scope=canon_scope, node_map=node_map, report=report)
    return report


def audit_all_repo_memories(
    engine: BrainEngine,
    *,
    root_dir: Path | None = None,
    since_hours: int | None = None,
    concurrency: int = 2,
    stagger_s: float = 0.5,
    cli_command: str = "codex exec",
    model: str | None = None,
    effort: str | None = None,
    dry_run: bool = False,
    timeout_s: int = 240,
    runner: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Audit all active repository scopes (skipping global & domain scopes) with bounded staggered concurrency."""
    effective_concurrency = max(1, min(6, int(concurrency or 1)))
    repo_scopes = list_auditable_repo_scopes(engine)
    root_resolved = Path(root_dir).expanduser().resolve() if root_dir is not None else None

    targets: list[tuple[str, Path, str, list[CompiledNode]]] = []
    skipped: list[dict[str, str]] = []
    for repo_name in repo_scopes:
        resolved_repo: Path | None = None
        if root_resolved is not None:
            cand = root_resolved / repo_name
            if (cand / ".git").exists():
                resolved_repo = cand.resolve()
        else:
            try:
                resolved_repo = resolve_repo_path(repo_name, None)
            except UsageError:
                resolved_repo = None

        if resolved_repo is None or not (resolved_repo / ".git").exists():
            skipped.append({"scope": repo_name, "reason": "repo_not_found"})
            continue

        if since_hours is not None and since_hours > 0 and not _repo_has_recent_commits(resolved_repo, since_hours):
            skipped.append({"scope": repo_name, "repo_path": str(resolved_repo), "reason": "no_recent_commits"})
            continue

        snap_ver, nodes = _load_active_repo_nodes(engine, repo_name)
        if not nodes:
            skipped.append({"scope": repo_name, "repo_path": str(resolved_repo), "reason": "no_active_nodes"})
            continue
        targets.append((repo_name, resolved_repo, snap_ver, nodes))

    audited_reports: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    total_summary: dict[str, int] = {v: 0 for v in VALID_AUDIT_VERDICTS}
    total_verified_refreshed = 0
    total_proposals = 0

    def _run_one(idx: int, repo_name: str, resolved_repo: Path, snap_ver: str, nodes: list[CompiledNode]) -> tuple[str, Path, dict[str, Any], dict[str, CompiledNode]]:
        if stagger_s > 0.0 and effective_concurrency > 1 and idx > 0:
            time.sleep(stagger_s * min(idx, effective_concurrency - 1))
        rep, nmap = _evaluate_repo_memories_readonly(
            clean_scope=repo_name,
            resolved_repo=resolved_repo,
            snap_ver=snap_ver,
            nodes=nodes,
            cli_command=cli_command,
            model=model,
            effort=effort,
            dry_run=dry_run,
            timeout_s=timeout_s,
            runner=runner,
        )
        return repo_name, resolved_repo, rep, nmap

    if effective_concurrency == 1 or len(targets) <= 1:
        for idx, (repo_name, resolved_repo, snap_ver, nodes) in enumerate(targets):
            try:
                _, _, rep, nmap = _run_one(idx, repo_name, resolved_repo, snap_ver, nodes)
                if not dry_run:
                    _commit_repo_audit_findings(engine, clean_scope=repo_name, node_map=nmap, report=rep)
                audited_reports.append(rep)
            except Exception as exc:
                failed.append({"scope": repo_name, "repo_path": str(resolved_repo), "error": str(exc)})
    else:
        with ThreadPoolExecutor(max_workers=effective_concurrency) as pool:
            future_meta = {
                pool.submit(_run_one, idx, repo_name, resolved_repo, snap_ver, nodes): (repo_name, resolved_repo)
                for idx, (repo_name, resolved_repo, snap_ver, nodes) in enumerate(targets)
            }
            for fut in as_completed(future_meta):
                repo_name, resolved_repo = future_meta[fut]
                try:
                    _, _, rep, nmap = fut.result()
                    if not dry_run:
                        _commit_repo_audit_findings(engine, clean_scope=repo_name, node_map=nmap, report=rep)
                    audited_reports.append(rep)
                except Exception as exc:
                    failed.append({"scope": repo_name, "repo_path": str(resolved_repo), "error": str(exc)})

    audited_reports.sort(key=lambda r: str(r.get("scope") or ""))
    failed.sort(key=lambda r: str(r.get("scope") or ""))
    for rep in audited_reports:
        for k in VALID_AUDIT_VERDICTS:
            total_summary[k] += int((rep.get("summary") or {}).get(k, 0))
        total_verified_refreshed += int(rep.get("verified_refreshed") or 0)
        total_proposals += len(rep.get("created_proposals") or [])

    return {
        "mode": "all",
        "root_dir": str(root_resolved) if root_resolved is not None else "",
        "since_hours": since_hours,
        "concurrency": effective_concurrency,
        "dry_run": dry_run,
        "audited_repos": len(audited_reports),
        "skipped_repos": len(skipped),
        "failed_repos": len(failed),
        "summary": total_summary,
        "verified_refreshed": total_verified_refreshed,
        "created_proposals_count": total_proposals,
        "repos": audited_reports,
        "skipped": skipped,
        "failed": failed,
    }
