"""Four-dimension Regression Gate (Recall, Temporal, Fidelity, Behavior) for FlowOS."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fos.engine import BrainEngine, query_snapshot_mirror, query_snapshot_recall
from fos.errors import NotFoundError, UsageError
from fos.ledger import LedgerStore
from fos.models import EvalCase, parse_timestamp
from fos.providers import ProviderBundle
from fos.store import SnapshotDB

VALID_SUITES = ("all", "recall", "temporal", "fidelity", "behavior")

_CRITICAL_TOKEN_RE = re.compile(
    r"(?:--[a-z][a-z0-9-]*|\b\d+(?:\.\d+)?(?:ms|s|gb|mb|kb|%|x)\b)",
    re.IGNORECASE,
)
_NEGATION_POLARITY_RE = re.compile(
    r"(?:禁止|不要|严禁|不能|不支持|不得|避免|无需|不必|切勿|不添|不改|不臆测|never|must not|avoid|do not|don't)",
    re.IGNORECASE,
)
_OBLIGATION_POLARITY_RE = re.compile(
    r"(?:必须|务必|应当|仅|只有|always|must\b|require)",
    re.IGNORECASE,
)


def _build_evidence_to_active_node(db: SnapshotDB) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for node in db.list_nodes(include_inactive=True):
        target = node
        while target.superseded_by and db.get_node(target.superseded_by):
            target = db.get_node(target.superseded_by)  # type: ignore[assignment]
        for aid in node.evidence_ids:
            mapping[aid] = target.id
    return mapping


def _resolve_expected_ids(
    db: SnapshotDB,
    case: EvalCase,
    ev_to_node: dict[str, str],
) -> list[str]:
    # Permanent LedgerAtom evidence_ids take precedence so cases stay invariant under backfill/consolidation
    if case.evidence_ids:
        mapped: list[str] = []
        for aid in case.evidence_ids:
            if aid in ev_to_node and ev_to_node[aid] not in mapped:
                mapped.append(ev_to_node[aid])
        if mapped:
            return mapped

    resolved: list[str] = []
    for eid in case.expected_ids:
        node = db.get_node(eid)
        if node is not None and node.status == "active":
            resolved.append(eid)
        elif node is not None and node.superseded_by:
            cur = node
            while cur.superseded_by and db.get_node(cur.superseded_by):
                cur = db.get_node(cur.superseded_by)  # type: ignore[assignment]
            resolved.append(cur.id)
        else:
            resolved.append(eid)
    return resolved


def _eval_recall(
    db: SnapshotDB,
    cases: list[EvalCase],
    providers: ProviderBundle,
) -> dict[str, Any]:
    recall_cases = [c for c in cases if c.suite == "recall" and c.expected_ids]
    if not recall_cases:
        # Synthesize ephemeral checks from active P0/P1 lessons & decisions if golden has none yet
        for n in db.list_nodes(as_of=None):
            if n.type in ("lesson", "decision", "principle") and n.severity in ("P0", "P1"):
                q = " ".join(n.trigger_when[:2]) or n.rule[:64]
                recall_cases.append(
                    EvalCase(
                        id=f"auto_{n.id}",
                        suite="recall",
                        query=q,
                        scope=n.scope if n.scope != "global" else None,
                        expected_ids=[n.id],
                        evidence_ids=list(n.evidence_ids),
                        node_id=n.id,
                        pinned=(n.severity == "P0"),
                    )
                )

    if not recall_cases:
        return {"passed": True, "cases": 0, "recall_at_5": 1.0, "mrr": 1.0, "pinned_top3_rate": 1.0, "failures": []}

    ev_to_node = _build_evidence_to_active_node(db)
    hits_at_5 = 0
    rr_sum = 0.0
    pinned_total = 0
    pinned_top3 = 0
    failures: list[dict[str, Any]] = []

    for case in recall_cases:
        active_expected = _resolve_expected_ids(db, case, ev_to_node)

        res = query_snapshot_recall(
            db,
            case.query,
            embedder=providers.embedder,
            reranker=providers.reranker,
            scope=case.scope,
            budget=2400,
            limit=10,
        )
        returned_ids = [r["id"] for r in res["results"]]
        top5 = returned_ids[:5]
        top3 = returned_ids[:3]

        best_rank = None
        for idx, rid in enumerate(returned_ids, start=1):
            if rid in active_expected:
                best_rank = idx
                break

        hit5 = any(eid in top5 for eid in active_expected)
        hit3 = any(eid in top3 for eid in active_expected)
        if hit5:
            hits_at_5 += 1
        if best_rank is not None:
            rr_sum += 1.0 / best_rank

        if case.pinned:
            pinned_total += 1
            if hit3:
                pinned_top3 += 1
            else:
                failures.append(
                    {
                        "case_id": case.id,
                        "query": case.query,
                        "expected": active_expected,
                        "got_top3": top3,
                        "reason": "Pinned case missed Top-3",
                    }
                )
        elif not hit5:
            failures.append(
                {
                    "case_id": case.id,
                    "query": case.query,
                    "expected": active_expected,
                    "got_top5": top5,
                    "reason": "Missed Recall@5",
                }
            )

    n = len(recall_cases)
    recall_at_5 = hits_at_5 / n
    mrr = rr_sum / n
    pinned_rate = (pinned_top3 / pinned_total) if pinned_total else 1.0
    passed = pinned_rate == 1.0 and recall_at_5 >= 0.8
    return {
        "passed": passed,
        "cases": n,
        "recall_at_5": round(recall_at_5, 4),
        "mrr": round(mrr, 4),
        "pinned_top3_rate": round(pinned_rate, 4),
        "failures": failures,
    }


def _eval_temporal(
    db: SnapshotDB,
    cases: list[EvalCase],
    providers: ProviderBundle,
) -> dict[str, Any]:
    temporal_cases = [c for c in cases if c.suite == "temporal"]
    # Also inspect every SUPERSEDES edge directly in the snapshot
    edges = [e for e in db.all_edges() if e.edge_type == "SUPERSEDES"]

    total = 0
    stale_leaks = 0
    as_of_restored = 0
    as_of_total = 0
    failures: list[dict[str, Any]] = []

    for case in temporal_cases:
        total += 1
        res = query_snapshot_recall(
            db,
            case.query,
            embedder=providers.embedder,
            reranker=providers.reranker,
            scope=case.scope,
            as_of=case.as_of,
            budget=2400,
            limit=10,
        )
        returned = [r["id"] for r in res["results"]]
        leaked = [fid for fid in case.forbidden_ids if fid in returned]
        if leaked:
            stale_leaks += 1
            failures.append(
                {
                    "case_id": case.id,
                    "query": case.query,
                    "leaked_forbidden": leaked,
                    "reason": "Superseded/forbidden node leaked into active recall",
                }
            )

    for edge in edges:
        old_node = db.get_node(edge.dst_id)
        new_node = db.get_node(edge.src_id)
        if not old_node or not new_node:
            continue
        total += 1
        query = " ".join((old_node.trigger_when or new_node.trigger_when)[:2]) or old_node.rule[:64]

        # 1. Default query must NOT return superseded old_node
        cur_res = query_snapshot_recall(
            db,
            query,
            embedder=providers.embedder,
            reranker=providers.reranker,
            scope=old_node.scope if old_node.scope != "global" else None,
            as_of=None,
            budget=2400,
            limit=10,
        )
        cur_ids = [r["id"] for r in cur_res["results"]]
        if old_node.id in cur_ids:
            stale_leaks += 1
            failures.append(
                {
                    "edge": f"{new_node.id}->{old_node.id}",
                    "query": query,
                    "reason": f"Superseded node {old_node.id} leaked into default recall",
                }
            )

        # 2. Historical --as-of slice (1 second before supersession) MUST restore old_node
        end_dt = parse_timestamp(old_node.valid_until)
        start_dt = parse_timestamp(old_node.valid_from)
        if end_dt is not None and (start_dt is None or end_dt > start_dt):
            as_of_total += 1
            probe_dt = end_dt - timedelta(seconds=1)
            if start_dt is not None and probe_dt < start_dt:
                probe_dt = start_dt
            hist_res = query_snapshot_recall(
                db,
                query,
                embedder=providers.embedder,
                reranker=providers.reranker,
                scope=old_node.scope if old_node.scope != "global" else None,
                as_of=probe_dt.isoformat(),
                budget=2400,
                limit=10,
            )
            hist_ids = [r["id"] for r in hist_res["results"]]
            if old_node.id in hist_ids and new_node.id not in hist_ids:
                as_of_restored += 1
            else:
                failures.append(
                    {
                        "edge": f"{new_node.id}->{old_node.id}",
                        "as_of": probe_dt.isoformat(),
                        "got": hist_ids[:5],
                        "reason": f"Historical --as-of failed to restore {old_node.id} cleanly",
                    }
                )

    as_of_rate = (as_of_restored / as_of_total) if as_of_total else 1.0
    passed = stale_leaks == 0 and as_of_rate == 1.0
    return {
        "passed": passed,
        "cases": total,
        "stale_leaks": stale_leaks,
        "as_of_restore_rate": round(as_of_rate, 4),
        "failures": failures,
    }


def _eval_fidelity(
    db: SnapshotDB,
    ledger: LedgerStore,
    cases: list[EvalCase],
) -> dict[str, Any]:
    """Verify L1 compressed cards preserve critical constraints (negations, flags, numbers) from L2 raw ledger."""
    nodes = db.list_nodes(as_of=None)
    checked = 0
    preserved_total = 0
    required_total = 0
    failures: list[dict[str, Any]] = []

    # 1. Explicit must_preserve_terms from golden cases
    case_terms_by_node: dict[str, list[str]] = {}
    for c in cases:
        if c.node_id and c.must_preserve_terms:
            case_terms_by_node.setdefault(c.node_id, []).extend(c.must_preserve_terms)

    for node in nodes:
        # Fidelity gate enforces strict constraint preservation on manual/P0/pinned nodes
        if node.severity != "P0" and node.id not in case_terms_by_node:
            continue
        l1_text = f"{node.rule} {node.rationale} {' '.join(node.trigger_when)}".lower()
        atoms = ledger.get_atoms_by_ids(node.evidence_ids)
        raw_blob = " ".join(a.raw for a in atoms)

        constraints = list(dict.fromkeys(m.group(0).lower() for m in _CRITICAL_TOKEN_RE.finditer(raw_blob)))
        for explicit in case_terms_by_node.get(node.id, []):
            tok = explicit.lower()
            if tok not in constraints:
                constraints.append(tok)

        has_neg_raw = bool(_NEGATION_POLARITY_RE.search(raw_blob))
        has_obl_raw = bool(_OBLIGATION_POLARITY_RE.search(raw_blob))
        if not constraints and not has_neg_raw and not has_obl_raw:
            continue
        checked += 1
        missing = [c for c in constraints if c not in l1_text]
        if has_neg_raw and not (_NEGATION_POLARITY_RE.search(l1_text) or _OBLIGATION_POLARITY_RE.search(l1_text)):
            missing.append("<negation-polarity>")
        required_total += len(constraints) + (1 if has_neg_raw else 0)
        preserved_total += (len(constraints) + (1 if has_neg_raw else 0)) - len(missing)
        if missing:
            failures.append(
                {
                    "node_id": node.id,
                    "severity": node.severity,
                    "missing_constraints": missing,
                    "rule": node.rule,
                }
            )

    retention_rate = (preserved_total / required_total) if required_total else 1.0
    # Strict 100% constraint retention per PRD fidelity gate
    passed = len(failures) == 0
    return {
        "passed": passed,
        "checked_nodes": checked,
        "retention_rate": round(retention_rate, 4),
        "failures": failures,
    }


def _eval_behavior(
    db: SnapshotDB,
    cases: list[EvalCase],
    providers: ProviderBundle,
) -> dict[str, Any]:
    """Verify mistake-prevention behavior on held-out scenarios via mirror + optional LLM Judge."""
    beh_cases = [c for c in cases if c.suite == "behavior"]
    # Also derive held-out mistake checks from pinned recall cases on lessons/principles
    for c in cases:
        if c.suite == "recall" and c.pinned and c.node_id:
            node = db.get_node(c.node_id)
            if node and node.type in ("lesson", "principle") and node.status == "active":
                beh_cases.append(c)

    if not beh_cases:
        return {"passed": True, "cases": 0, "guard_hit_rate": 1.0, "failures": []}

    ev_to_node = _build_evidence_to_active_node(db)
    passed_count = 0
    failures: list[dict[str, Any]] = []

    for case in beh_cases:
        report = query_snapshot_mirror(
            db,
            case.query,
            embedder=providers.embedder,
            reranker=providers.reranker,
            reasoner=None,
            scope=case.scope,
        )
        warned_ids = {
            item["id"]
            for item in (report["conflicts"] + report["lesson_warnings"] + report["prior_decisions"])
        }
        expected = set(_resolve_expected_ids(db, case, ev_to_node))
        if not expected or (expected & warned_ids):
            # Optional LLM judge confirmation when non-heuristic reasoner is active
            judge_ok = True
            if providers.reasoner.model_name != "heuristic":
                verdict = providers.reasoner.complete_json(
                    f"Scenario: {case.query}\nMirror Warnings: {report['lesson_warnings'] + report['conflicts']}\n"
                    f"Does the retrieved memory prevent repeating the mistake? Return {{\"prevented\": true/false, \"reason\": \"...\"}}.",
                    system="You are a strict regression test judge.",
                )
                if verdict is not None and verdict.get("prevented") is False:
                    judge_ok = False
            if judge_ok:
                passed_count += 1
            else:
                failures.append({"case_id": case.id, "query": case.query, "reason": "LLM judge rejected prevention"})
        else:
            failures.append(
                {
                    "case_id": case.id,
                    "query": case.query,
                    "expected_guard_ids": sorted(expected),
                    "fired_ids": sorted(warned_ids),
                    "reason": "Behavior guard did not trigger for mistake scenario",
                }
            )

    rate = passed_count / len(beh_cases)
    return {
        "passed": len(failures) == 0,
        "cases": len(beh_cases),
        "guard_hit_rate": round(rate, 4),
        "failures": failures,
    }


def evaluate_snapshot(
    db: SnapshotDB,
    ledger: LedgerStore,
    providers: ProviderBundle,
    *,
    suite: str = "all",
    baseline_db: SnapshotDB | None = None,
) -> dict[str, Any]:
    """Run requested regression suite(s) on `db` and optionally compare non-regression vs `baseline_db`."""
    if suite not in VALID_SUITES:
        raise UsageError(f"Invalid suite '{suite}'. Must be one of: {', '.join(VALID_SUITES)}")

    cases = ledger.load_eval_cases()
    suites_to_run = ["recall", "temporal", "fidelity", "behavior"] if suite == "all" else [suite]
    results: dict[str, Any] = {}
    regressions: list[str] = []

    if "recall" in suites_to_run:
        has_golden_recall = any(c.suite == "recall" and c.expected_ids for c in cases)
        results["recall"] = _eval_recall(db, cases, providers)
        if baseline_db is not None and has_golden_recall:
            base_rec = _eval_recall(baseline_db, cases, providers)
            results["recall"]["baseline_recall_at_5"] = base_rec["recall_at_5"]
            results["recall"]["baseline_mrr"] = base_rec["mrr"]
            if results["recall"]["recall_at_5"] + 1e-6 < base_rec["recall_at_5"]:
                results["recall"]["passed"] = False
                regressions.append(
                    f"Recall@5 regressed: {results['recall']['recall_at_5']} < baseline {base_rec['recall_at_5']}"
                )
            if results["recall"]["cases"] >= 5 and results["recall"]["mrr"] + 0.05 < base_rec["mrr"]:
                results["recall"]["passed"] = False
                regressions.append(
                    f"MRR regressed: {results['recall']['mrr']} < baseline {base_rec['mrr']}"
                )

    if "temporal" in suites_to_run:
        results["temporal"] = _eval_temporal(db, cases, providers)

    if "fidelity" in suites_to_run:
        results["fidelity"] = _eval_fidelity(db, ledger, cases)

    if "behavior" in suites_to_run:
        results["behavior"] = _eval_behavior(db, cases, providers)
        if baseline_db is not None:
            base_beh = _eval_behavior(baseline_db, cases, providers)
            results["behavior"]["baseline_guard_hit_rate"] = base_beh["guard_hit_rate"]
            if results["behavior"]["guard_hit_rate"] + 1e-6 < base_beh["guard_hit_rate"]:
                results["behavior"]["passed"] = False
                regressions.append(
                    f"Behavior guard regressed: {results['behavior']['guard_hit_rate']} < baseline {base_beh['guard_hit_rate']}"
                )

    overall_passed = all(r["passed"] for r in results.values()) and not regressions
    return {
        "passed": overall_passed,
        "snapshot_version": db.get_meta("snapshot_version", "unknown"),
        "suite": suite,
        "golden_cases": len(cases),
        "regressions": regressions,
        "gates": results,
    }


def run_eval_on_engine(engine: BrainEngine, *, suite: str = "all") -> dict[str, Any]:
    with engine.snapshots.open_current(read_only=True) as db:
        return evaluate_snapshot(db, engine.ledger, engine.providers, suite=suite)


def pin_node_in_engine(engine: BrainEngine, node_id: str) -> dict[str, Any]:
    with engine.snapshots.open_current(read_only=True) as db:
        node = db.get_node(node_id)
        if node is None:
            raise NotFoundError(f"Node not found: {node_id}")
    cases = engine.ledger.pin_node(node)
    return {
        "pinned_node": node.id,
        "cases": [c.to_dict() for c in cases],
    }
