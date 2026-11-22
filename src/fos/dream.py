"""Sleep compilation (Quick Dream): Clean -> Extract -> Consolidate -> Compile(vN+1) -> Regression Gate -> Atomic Switch."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fos.engine import (
    BrainEngine,
    build_l0_line,
    extract_entities,
    extract_triggers,
    node_embedding_text,
    split_rule_and_rationale,
)
from fos.errors import GateFailedError
from fos.eval_gate import evaluate_snapshot
from fos.models import (
    KIND_PREFIX,
    CompiledNode,
    GraphEdge,
    LedgerAtom,
    normalize_severity,
    now_iso,
)
from fos.store import SnapshotDB
from fos.tokens import extract_terms

_NOISE_PATTERNS = (
    re.compile(r"^(?:ok|okay|thanks|thx|got it|sure|好的|收到|谢谢|嗯|行)[\s.!！。]*$", re.IGNORECASE),
)
_PROMOTE_SEEN_THRESHOLD = 3


def _is_low_signal_noise(atom: LedgerAtom) -> bool:
    """Clean stage: drop empty or pure pleasantry noise while preserving manual decisions/lessons."""
    raw = atom.raw.strip()
    if len(raw) < 4:
        return True
    if atom.source == "manual":
        return False
    return any(p.match(raw) for p in _NOISE_PATTERNS)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def _detect_contradiction(a: CompiledNode, b: CompiledNode) -> bool:
    """Detect likely conflict between two active nodes in same scope with opposing polarity."""
    if a.scope != b.scope or a.type != b.type:
        return False
    ta = extract_terms(a.rule)
    tb = extract_terms(b.rule)
    if _jaccard(ta, tb) < 0.35:
        return False
    neg = ("禁止", "不要", "不能", "放弃", "never", "avoid", "do not", "don't")
    a_neg = any(w in a.rule.lower() for w in neg)
    b_neg = any(w in b.rule.lower() for w in neg)
    return a_neg != b_neg


def _compile_dream_nodes(
    engine: BrainEngine,
    atoms: list[LedgerAtom],
) -> tuple[list[CompiledNode], list[GraphEdge], dict[str, Any]]:
    """Run Clean -> Extract -> Consolidate over the immutable atom ledger."""
    cleaned: list[LedgerAtom] = []
    dropped_noise = 0
    for atom in atoms:
        if _is_low_signal_noise(atom):
            dropped_noise += 1
            continue
        cleaned.append(atom)

    # Track deterministic sequence IDs matching initial compilation order so golden eval IDs stay stable
    counters: dict[str, int] = {}
    nodes_by_id: dict[str, CompiledNode] = {}
    node_order: list[str] = []
    edges: list[GraphEdge] = []
    merged_count = 0
    promoted_p0 = 0
    conflicts_found = 0

    for atom in cleaned:
        meta = atom.meta
        prefix = KIND_PREFIX.get(atom.kind, "MEM")
        rule, rationale = split_rule_and_rationale(atom.raw)
        if meta.get("rule"):
            rule = str(meta["rule"])
        if meta.get("rationale"):
            rationale = str(meta["rationale"])

        # Optional LLM extraction refinement when MLX / CLI / OpenAI reasoner is configured
        if engine.providers.reasoner.model_name != "heuristic" and len(atom.raw) > 120:
            refined = engine.providers.reasoner.complete_json(
                f"Extract a concise rule (keeping ALL negations, flags, and numbers verbatim!), "
                f"rationale, and up to 3 trigger_when phrases from this {atom.kind}:\n{atom.raw}\n"
                f'Return JSON: {{"rule": "...", "rationale": "...", "trigger_when": [...]}}',
                system="Never drop negations, CLI flags, numbers, or boundary exceptions.",
            )
            if refined and isinstance(refined.get("rule"), str) and refined["rule"].strip():
                rule = refined["rule"].strip()
                if isinstance(refined.get("rationale"), str):
                    rationale = refined["rationale"].strip()
                if isinstance(refined.get("trigger_when"), list):
                    meta = {**meta, "trigger_when": [str(x) for x in refined["trigger_when"] if x]}

        entities = extract_entities(atom.raw, meta.get("entities"))
        triggers = extract_triggers(atom.raw, atom.scope, entities, meta.get("trigger_when"))
        severity = normalize_severity(meta.get("severity"), kind=atom.kind)

        # Consolidate: merge near-duplicate lessons/thoughts in same scope (unless explicitly superseding)
        merged_target: CompiledNode | None = None
        if atom.kind in ("lesson", "thought") and not meta.get("supersedes") and not meta.get("node_id"):
            cand_terms = extract_terms(rule)
            for nid in node_order:
                existing = nodes_by_id[nid]
                if existing.type == atom.kind and existing.scope == atom.scope and existing.status == "active":
                    if _jaccard(cand_terms, extract_terms(existing.rule)) >= 0.72:
                        merged_target = existing
                        break

        if merged_target is not None:
            merged_count += 1
            merged_target.seen_count += 1
            if atom.id not in merged_target.evidence_ids:
                merged_target.evidence_ids.append(atom.id)
            for t in triggers:
                if t not in merged_target.trigger_when and len(merged_target.trigger_when) < 5:
                    merged_target.trigger_when.append(t)
            for e in entities:
                if e not in merged_target.entities and len(merged_target.entities) < 12:
                    merged_target.entities.append(e)
            merged_target.confidence = min(0.99, merged_target.confidence + 0.05)
            # Elevate recurring lessons seen >= 3 times to P0
            if merged_target.type == "lesson" and merged_target.seen_count >= _PROMOTE_SEEN_THRESHOLD and merged_target.severity != "P0":
                merged_target.severity = "P0"
                promoted_p0 += 1
            elif severity == "P0":
                merged_target.severity = "P0"
            merged_target.l0 = build_l0_line(merged_target)
            merged_target.updated_at = atom.ts
            continue

        # Allocate stable node id
        if meta.get("node_id"):
            node_id = str(meta["node_id"])
            parts = node_id.split("-")
            if len(parts) == 2 and parts[1].isdigit():
                counters[parts[0]] = max(counters.get(parts[0], 0), int(parts[1]))
        else:
            counters[prefix] = counters.get(prefix, 0) + 1
            node_id = f"{prefix}-{counters[prefix]:03d}"

        node = CompiledNode(
            id=node_id,
            type=atom.kind,
            scope=atom.scope or "global",
            status="active",
            severity=severity,
            valid_from=atom.ts,
            valid_until=None,
            superseded_by=None,
            seen_count=int(meta.get("seen_count", 1)),
            confidence=float(meta.get("confidence", 0.85 if atom.source == "manual" else 0.75)),
            trigger_when=triggers,
            rule=rule,
            rationale=rationale,
            entities=entities,
            contradicts=list(meta.get("contradicts") or []),
            evidence_ids=[atom.id],
            created_at=atom.ts,
            updated_at=atom.ts,
        )
        node.l0 = build_l0_line(node)
        nodes_by_id[node_id] = node
        node_order.append(node_id)

        # Handle supersession
        sup_id = meta.get("supersedes")
        if sup_id and str(sup_id) in nodes_by_id:
            old = nodes_by_id[str(sup_id)]
            old.status = "superseded"
            old.valid_until = atom.ts
            old.superseded_by = node.id
            edges.append(
                GraphEdge(
                    src_id=node.id,
                    dst_id=old.id,
                    edge_type="SUPERSEDES",
                    valid_from=atom.ts,
                    meta={"atom_id": atom.id},
                )
            )

        # Explicit links
        for rel_key, edge_type in (
            ("caused_by", "CAUSED_BY"),
            ("supports", "SUPPORTS"),
            ("contradicts", "CONTRADICTS"),
        ):
            refs = meta.get(rel_key)
            if isinstance(refs, str):
                refs = [refs]
            for target_id in refs or []:
                if target_id and str(target_id) in nodes_by_id:
                    edges.append(
                        GraphEdge(
                            src_id=node.id,
                            dst_id=str(target_id),
                            edge_type=edge_type,
                            valid_from=atom.ts,
                        )
                    )

        if node.scope and node.scope != "global":
            edges.append(
                GraphEdge(
                    src_id=node.id,
                    dst_id=f"scope:{node.scope}",
                    edge_type="APPLIES_TO",
                    valid_from=atom.ts,
                )
            )

    # Conflict detection pass among remaining active nodes
    active_nodes = [nodes_by_id[nid] for nid in node_order if nodes_by_id[nid].status == "active"]
    for i in range(len(active_nodes)):
        for j in range(i + 1, len(active_nodes)):
            na, nb = active_nodes[i], active_nodes[j]
            if _detect_contradiction(na, nb):
                conflicts_found += 1
                if nb.id not in na.contradicts:
                    na.contradicts.append(nb.id)
                if na.id not in nb.contradicts:
                    nb.contradicts.append(na.id)
                edges.append(
                    GraphEdge(
                        src_id=na.id,
                        dst_id=nb.id,
                        edge_type="CONTRADICTS",
                        valid_from=max(na.valid_from, nb.valid_from),
                    )
                )

    stats = {
        "input_atoms": len(atoms),
        "dropped_noise": dropped_noise,
        "compiled_nodes": len(node_order),
        "merged_duplicates": merged_count,
        "promoted_to_p0": promoted_p0,
        "conflicts_detected": conflicts_found,
        "edges": len(edges),
    }
    return [nodes_by_id[nid] for nid in node_order], edges, stats


def run_dream(
    engine: BrainEngine,
    *,
    dry_run: bool = False,
    no_gate: bool = False,
    export_songe_path: Path | None = None,
) -> dict[str, Any]:
    """Execute Quick Dream: Clean -> Extract -> Consolidate -> Compile(vN+1) -> Regression Gate -> Atomic Switch."""
    baseline_version = engine.snapshots.ensure_initialized()
    atoms = engine.ledger.load_all_atoms()

    try:
        nodes, edges, stats = _compile_dream_nodes(engine, atoms)
        cand_version, cand_path = engine.snapshots.create_candidate()

        # Batch-embed all compiled nodes
        texts = [node_embedding_text(n) for n in nodes]
        embeddings = engine.providers.embedder.embed(texts) if texts else []

        with SnapshotDB(cand_path, embedding_dim=engine.providers.embedder.dim) as cand_db:
            cand_db.set_meta("snapshot_version", cand_version)
            cand_db.set_meta("embedding_model", engine.providers.embedder.model_name)
            cand_db.set_meta("reasoner_model", engine.providers.reasoner.model_name)
            cand_db.set_meta("built_at", now_iso())
            cand_db.set_meta("atom_count", str(len(atoms)))
            for node, emb in zip(nodes, embeddings):
                cand_db.upsert_node(node, embedding=emb)
            for edge in edges:
                cand_db.add_edge(edge)

        # Evaluate candidate v(N+1) against baseline vN
        gate_report: dict[str, Any] | None = None
        if not no_gate:
            with (
                SnapshotDB(cand_path, embedding_dim=engine.providers.embedder.dim, read_only=True) as cand_ro,
                engine.snapshots.open_version(baseline_version, read_only=True) as base_ro,
            ):
                gate_report = evaluate_snapshot(
                    cand_ro,
                    engine.ledger,
                    engine.providers,
                    suite="all",
                    baseline_db=base_ro,
                )

        # Optional Deep Dream hook: export high-value hindsight stream for Songe MLX
        songe_exported = 0
        if export_songe_path is not None:
            songe_exported = export_songe_stream(nodes, engine, export_songe_path)

        if gate_report is not None and not gate_report["passed"]:
            rejected_path = engine.snapshots.discard(cand_version)
            payload = {
                "promoted": False,
                "dry_run": dry_run,
                "baseline_version": baseline_version,
                "candidate_version": cand_version,
                "rejected_snapshot": str(rejected_path) if rejected_path else None,
                "stats": stats,
                "gate": gate_report,
            }
            raise GateFailedError(
                f"Dream candidate {cand_version} failed regression gate; kept {baseline_version}.",
                details=payload,
            )

        if dry_run:
            engine.snapshots.discard(cand_version)
            return {
                "promoted": False,
                "dry_run": True,
                "baseline_version": baseline_version,
                "candidate_version": cand_version,
                "stats": stats,
                "gate": gate_report,
                "songe_exported": songe_exported,
            }

        state = engine.snapshots.promote(cand_version)
        return {
            "promoted": True,
            "dry_run": False,
            "baseline_version": baseline_version,
            "current_version": state["current"],
            "history": state["history"],
            "stats": stats,
            "gate": gate_report,
            "songe_exported": songe_exported,
        }
    finally:
        # Release peak MLX unified memory immediately after Dream completes
        engine.providers.unload_heavy()


def export_songe_stream(nodes: list[CompiledNode], engine: BrainEngine, out_path: Path) -> int:
    """Phase 3 hook: export cleaned high-value hindsight experience stream for Songe MLX Deep Dream."""
    out = Path(out_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out.open("w", encoding="utf-8") as fh:
        for node in nodes:
            if node.status != "active" or node.type not in ("lesson", "decision", "principle"):
                continue
            atoms = engine.ledger.get_atoms_by_ids(node.evidence_ids)
            record = {
                "id": node.id,
                "type": node.type,
                "scope": node.scope,
                "severity": node.severity,
                "seen_count": node.seen_count,
                "trigger_when": node.trigger_when,
                "rule": node.rule,
                "rationale": node.rationale,
                "evidence_raw": [a.raw for a in atoms],
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count
