"""Sleep compilation (Quick Dream): Clean -> Distill -> Extract -> Consolidate -> Compile(vN+1) -> Regression Gate -> Atomic Switch."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fos.config import ProviderConfig
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
    VALID_KINDS,
    CompiledNode,
    GraphEdge,
    LedgerAtom,
    normalize_severity,
    now_iso,
)
from fos.providers import Reasoner, build_providers
from fos.store import SnapshotDB
from fos.tokens import extract_terms

_NOISE_PATTERNS = (
    re.compile(r"^(?:ok|okay|thanks|thx|got it|sure|好的|收到|谢谢|嗯|行)[\s.!！。]*$", re.IGNORECASE),
)
_PROMOTE_SEEN_THRESHOLD = 3
_DISTILL_BATCH_SIZE = 10

_DISTILL_SYSTEM_PROMPT = """You are FlowOS's First-Principles Cognitive Distiller (modeled on fops LESSONS.md + Songe + Hindsight).
Apply the STRICT **6-Month Half-Life Test**: "Will this knowledge still prevent a real engineering mistake or steer a real design decision 6 months from now?"

DROP (keep = false) IMMEDIATELY if the episode is ANY of:
1. Single-ticket ops firefighting, specific vendor/customer/account incidents, temporary third-party outages, or one-off metrics/row counts (e.g. "15 days exported 2,228 rows", "account #1928 returned 400", "OR_BACR2_59", "swap account A to B").
2. Transient tool/environment glitches or bare questions without a durable generalizable rule.
3. Trivial task requests that do not express a reusable preference, pitfall, or architectural trade-off.

KEEP (keep = true) ONLY high-compounding, settled knowledge in 3 categories:
1. `principle` (scope="global"): User's durable engineering taste, design philosophy, or workflow preference (e.g., "Prefer CLI + embedded Skill over MCP for local tools", "Use Guard Tests over verbal reminders").
2. `lesson`: Generalizable technical/shell/code/architecture pitfall with a first-principles root cause (like fops LESSONS.md: e.g., "Never rely on GNU rsync --mkpath on macOS openrsync; create remote dir via ssh mkdir -p first", "Use [f]ops in pkill -f inside sh -c to avoid killing own subshell").
3. `decision`: Deliberate architectural or stack trade-off (A chosen over B) with durable rationale.

When keep = true:
- `rule`: ONE crisp, timeless, actionable directive (no account IDs, no temporary counts, no one-off ticket numbers). Preserve ALL exact technical flags, negations (禁止/不要/必须/never/must), and durable technical constants!
- `rationale`: First-principles WHY (structural root cause, not ticket narrative).
- `guard_hint`: If this rule can be enforced mechanically via lint/unit-test/pre-commit/CI guard (fops LESSONS.md Rule #3), briefly state how; otherwise "".
- `scope`: Use "global" for cross-project principles/engineering laws; keep repo name ONLY if strictly repo-specific.
- `severity`: P0 only for universal redlines or explicit user principles; P1 for generalizable engineering lessons/decisions; P2 for narrow repo-only constraints.
- `trigger_when`: 2-3 crisp situations when future AI should recall this before acting.

Return STRICT JSON:
{"items": [{"id": "<atom_id>", "keep": true/false, "kind": "principle|lesson|decision", "scope": "global|<repo>", "severity": "P0|P1|P2", "rule": "...", "rationale": "...", "guard_hint": "...", "trigger_when": ["..."], "entities": ["..."], "drop_reason": "..."}]}"""


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


def _load_distill_cache(cache_path: Path) -> dict[str, dict[str, Any]]:
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_distill_cache(cache_path: Path, cache: dict[str, dict[str, Any]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, cache_path)


def _distill_hindsight_batch(
    atoms: list[LedgerAtom],
    reasoner: Reasoner,
    cache_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Batch-distill raw `hindsight:*` atoms via LLM Reasoner with incremental disk caching."""
    stats = {"distilled_kept": 0, "distilled_dropped": 0, "distill_cache_hits": 0}
    if reasoner.model_name == "heuristic":
        return {}, stats

    cache = _load_distill_cache(cache_path)
    results: dict[str, dict[str, Any]] = {}
    pending: list[LedgerAtom] = []

    for atom in atoms:
        if atom.source == "manual":
            continue
        ckey = f"{atom.id}@{reasoner.model_name}"
        if ckey in cache:
            results[atom.id] = cache[ckey]
            stats["distill_cache_hits"] += 1
            if cache[ckey].get("keep", True):
                stats["distilled_kept"] += 1
            else:
                stats["distilled_dropped"] += 1
        else:
            pending.append(atom)

    cache_dirty = False
    for start in range(0, len(pending), _DISTILL_BATCH_SIZE):
        chunk = pending[start : start + _DISTILL_BATCH_SIZE]
        payload = [
            {
                "id": a.id,
                "scope": a.scope,
                "kind": a.kind,
                "source": a.source,
                "raw": a.raw[:900],
            }
            for a in chunk
        ]
        prompt = (
            "Evaluate and distill these captured agent session episodes into reusable long-term memory cards "
            "(or set keep=false to drop transient/one-off noise):\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        resp = reasoner.complete_json(prompt, system=_DISTILL_SYSTEM_PROMPT)
        items = resp.get("items") if isinstance(resp, dict) and isinstance(resp.get("items"), list) else []
        by_id = {str(it.get("id")): it for it in items if isinstance(it, dict) and it.get("id")}

        for atom in chunk:
            verdict = by_id.get(atom.id)
            if verdict is None:
                continue
            entry = {
                "keep": bool(verdict.get("keep", False)),
                "kind": str(verdict.get("kind") or atom.kind),
                "scope": str(verdict.get("scope") or atom.scope or "global").strip().lower(),
                "severity": str(verdict.get("severity") or atom.meta.get("severity") or "P1"),
                "rule": str(verdict.get("rule") or "").strip(),
                "rationale": str(verdict.get("rationale") or "").strip(),
                "guard_hint": str(verdict.get("guard_hint") or "").strip(),
                "trigger_when": [str(x).strip() for x in (verdict.get("trigger_when") or []) if str(x).strip()][:4],
                "entities": [str(x).strip().lower() for x in (verdict.get("entities") or []) if str(x).strip()][:8],
                "drop_reason": str(verdict.get("drop_reason") or "").strip(),
            }
            if entry["keep"] and not entry["rule"]:
                entry["keep"] = False
            ckey = f"{atom.id}@{reasoner.model_name}"
            cache[ckey] = entry
            results[atom.id] = entry
            cache_dirty = True
            if entry["keep"]:
                stats["distilled_kept"] += 1
            else:
                stats["distilled_dropped"] += 1

    if cache_dirty:
        _save_distill_cache(cache_path, cache)
    return results, stats


def _compile_dream_nodes(
    engine: BrainEngine,
    atoms: list[LedgerAtom],
    *,
    reasoner: Reasoner | None = None,
) -> tuple[list[CompiledNode], list[GraphEdge], dict[str, Any]]:
    """Run Clean -> Distill -> Extract -> Consolidate over the immutable atom ledger."""
    active_reasoner = reasoner or engine.providers.reasoner

    cleaned: list[LedgerAtom] = []
    dropped_noise = 0
    for atom in atoms:
        if _is_low_signal_noise(atom):
            dropped_noise += 1
            continue
        cleaned.append(atom)

    distilled_map, distill_stats = _distill_hindsight_batch(
        cleaned,
        active_reasoner,
        engine.data_dir / "distill_cache.json",
    )

    counters: dict[str, int] = {}
    nodes_by_id: dict[str, CompiledNode] = {}
    node_order: list[str] = []
    edges: list[GraphEdge] = []
    merged_count = 0
    promoted_p0 = 0
    conflicts_found = 0

    for atom in cleaned:
        meta = dict(atom.meta)
        atom_kind = atom.kind
        atom_scope = atom.scope or "global"
        guard_hint = str(meta.get("guard_hint") or "")

        # Apply LLM sleep distillation verdict if available for automated hindsight atoms
        dist = distilled_map.get(atom.id)
        if dist is not None:
            if not dist.get("keep", False):
                dropped_noise += 1
                continue
            if dist.get("kind") in VALID_KINDS:
                atom_kind = dist["kind"]
            if dist.get("scope"):
                atom_scope = dist["scope"]
            if atom_kind == "principle":
                atom_scope = "global"
            rule = dist["rule"]
            rationale = dist.get("rationale", "")
            guard_hint = dist.get("guard_hint", "")
            if dist.get("trigger_when"):
                meta["trigger_when"] = dist["trigger_when"]
            if dist.get("entities"):
                meta["entities"] = dist["entities"]
            if dist.get("severity"):
                meta["severity"] = dist["severity"]
        else:
            rule, rationale = split_rule_and_rationale(atom.raw)
            if meta.get("rule"):
                rule = str(meta["rule"])
            if meta.get("rationale"):
                rationale = str(meta["rationale"])

        prefix = KIND_PREFIX.get(atom_kind, "MEM")
        entities = extract_entities(f"{rule} {rationale}", meta.get("entities"))
        triggers = extract_triggers(rule, atom_scope, entities, meta.get("trigger_when"))
        severity = normalize_severity(meta.get("severity"), kind=atom_kind)
        atom_date = atom.ts[:10] if len(atom.ts) >= 10 else ""

        # Consolidate: merge near-duplicate lessons/thoughts in same scope (unless explicitly superseding)
        merged_target: CompiledNode | None = None
        if atom_kind in ("lesson", "thought", "principle") and not meta.get("supersedes") and not meta.get("node_id"):
            cand_terms = extract_terms(rule)
            for nid in node_order:
                existing = nodes_by_id[nid]
                if existing.type == atom_kind and existing.scope == atom_scope and existing.status == "active":
                    if _jaccard(cand_terms, extract_terms(existing.rule)) >= 0.72:
                        merged_target = existing
                        break

        if merged_target is not None:
            merged_count += 1
            merged_target.seen_count += 1
            if atom_date and atom_date not in merged_target.seen_dates:
                merged_target.seen_dates.append(atom_date)
                merged_target.seen_dates.sort()
            if atom.id not in merged_target.evidence_ids:
                merged_target.evidence_ids.append(atom.id)
            if guard_hint and not merged_target.guard_hint:
                merged_target.guard_hint = guard_hint
            for t in triggers:
                if t not in merged_target.trigger_when and len(merged_target.trigger_when) < 5:
                    merged_target.trigger_when.append(t)
            for e in entities:
                if e not in merged_target.entities and len(merged_target.entities) < 12:
                    merged_target.entities.append(e)
            merged_target.confidence = min(0.99, merged_target.confidence + 0.05)
            if (
                merged_target.type == "lesson"
                and merged_target.seen_count >= _PROMOTE_SEEN_THRESHOLD
                and merged_target.severity != "P0"
            ):
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
            type=atom_kind,
            scope=atom_scope,
            status="active",
            severity=severity,
            valid_from=atom.ts,
            valid_until=None,
            superseded_by=None,
            seen_count=int(meta.get("seen_count", 1)),
            seen_dates=[atom_date] if atom_date else [],
            confidence=float(
                meta.get(
                    "confidence",
                    0.9 if dist is not None else (0.85 if atom.source == "manual" else 0.75),
                )
            ),
            trigger_when=triggers,
            rule=rule,
            rationale=rationale,
            guard_hint=guard_hint,
            entities=entities,
            contradicts=list(meta.get("contradicts") or []),
            evidence_ids=[atom.id],
            created_at=atom.ts,
            updated_at=atom.ts,
        )
        node.l0 = build_l0_line(node)
        nodes_by_id[node_id] = node
        node_order.append(node_id)

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
        "reasoner": active_reasoner.model_name,
        **distill_stats,
    }
    return [nodes_by_id[nid] for nid in node_order], edges, stats


def run_dream(
    engine: BrainEngine,
    *,
    dry_run: bool = False,
    no_gate: bool = False,
    export_songe_path: Path | None = None,
    reasoner_override: str | None = None,
    cli_command: str | None = None,
) -> dict[str, Any]:
    """Execute Quick Dream: Clean -> Distill -> Extract -> Consolidate -> Compile(vN+1) -> Regression Gate -> Atomic Switch."""
    baseline_version = engine.snapshots.ensure_initialized()
    atoms = engine.ledger.load_all_atoms()

    active_reasoner: Reasoner = engine.providers.reasoner
    override_bundle = None
    if reasoner_override and reasoner_override != "auto":
        pcfg = ProviderConfig(reasoner=reasoner_override)
        if cli_command:
            pcfg.cli_command = cli_command
        override_bundle = build_providers(pcfg)
        active_reasoner = override_bundle.reasoner

    try:
        nodes, edges, stats = _compile_dream_nodes(engine, atoms, reasoner=active_reasoner)
        cand_version, cand_path = engine.snapshots.create_candidate()

        texts = [node_embedding_text(n) for n in nodes]
        embeddings = engine.providers.embedder.embed(texts) if texts else []

        with SnapshotDB(cand_path, embedding_dim=engine.providers.embedder.dim) as cand_db:
            cand_db.set_meta("snapshot_version", cand_version)
            cand_db.set_meta("embedding_model", engine.providers.embedder.model_name)
            cand_db.set_meta("reasoner_model", active_reasoner.model_name)
            cand_db.set_meta("built_at", now_iso())
            cand_db.set_meta("atom_count", str(len(atoms)))
            for node, emb in zip(nodes, embeddings):
                cand_db.upsert_node(node, embedding=emb)
            for edge in edges:
                cand_db.add_edge(edge)

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
        if override_bundle is not None:
            override_bundle.unload_heavy()
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
