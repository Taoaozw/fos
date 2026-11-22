"""Core FlowOS Brain Engine: compile, rebuild, boot, 4-way hybrid recall, get, mirror, export."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fos.config import FosConfig, ProviderConfig
from fos.errors import NotFoundError, UsageError
from fos.ledger import LedgerStore
from fos.models import (
    KIND_PREFIX,
    VALID_KINDS,
    CompiledNode,
    GraphEdge,
    LedgerAtom,
    is_valid_at,
    normalize_severity,
    now_iso,
    parse_timestamp,
)
from fos.providers import ProviderBundle, build_providers
from fos.store import SnapshotDB, SnapshotManager
from fos.tokens import (
    estimate_json_tokens,
    estimate_tokens,
    extract_terms,
    truncate_to_tokens,
)
from fos.ulid import new_atom_id

_TECH_ENTITY_RE = re.compile(r"\b(?:[a-zA-Z][a-zA-Z0-9_.-]{2,}|--[a-z0-9-]+)\b")
_STOP_ENTITIES = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when", "then",
    "use", "using", "not", "must", "should", "avoid", "never", "always", "all",
    "can", "will", "are", "was", "were", "have", "has", "had", "but", "you",
}
_ASSUMPTION_CUES = (
    "假设", "应该", "大概", "默认", "估计", "预计", "认为", "可能", "直接用", "无需",
    "assume", "assuming", "probably", "should work", "expect", "guess", "default", "simply",
)
_NEGATION_WORDS = (
    "禁止", "不要", "不能", "不支持", "严禁", "避免", "放弃", "废弃", "替代", "改用",
    "never", "avoid", "disable", "deprecated", "replace", "instead of", "do not", "don't",
)


def extract_entities(text: str, extra: list[str] | None = None) -> list[str]:
    found: list[str] = []
    for e in extra or []:
        s = str(e).strip().lower()
        if s and s not in found:
            found.append(s)
    for m in _TECH_ENTITY_RE.finditer(text):
        tok = m.group(0).strip(".-").lower()
        if len(tok) >= 3 and tok not in _STOP_ENTITIES and tok not in found:
            found.append(tok)
        if len(found) >= 10:
            break
    return found


def build_l0_line(node: CompiledNode) -> str:
    """Build ultra-compact L0 wakeup kernel line (~15-25 tokens)."""
    summary = truncate_to_tokens(node.rule.replace("\n", " ").strip(), 22)
    return f"[{node.id}|{node.severity}|seen:{node.seen_count}] {summary}"


def split_rule_and_rationale(raw: str) -> tuple[str, str]:
    """Split a raw statement into a crisp rule/claim and rationale if separable."""
    # For hindsight-collected episodes ("Context: ...\nHindsight: ...\nResolution: ...")
    if "Hindsight:" in raw:
        before, after = raw.split("Hindsight:", 1)
        ctx = before.replace("Context:", "", 1).strip()
        if "Resolution:" in after:
            hind_part, res_part = after.split("Resolution:", 1)
            hind = " ".join(line.strip() for line in hind_part.strip().splitlines() if line.strip())
            res = " ".join(line.strip() for line in res_part.strip().splitlines() if line.strip())
            if res and hind:
                return truncate_to_tokens(f"{hind} -> {res}", 96), truncate_to_tokens(ctx, 50)
        hind = " ".join(line.strip() for line in after.strip().splitlines() if line.strip())
        if hind:
            return truncate_to_tokens(hind, 90), truncate_to_tokens(ctx, 60)

    clean = " ".join(line.strip() for line in raw.strip().splitlines() if line.strip())
    for sep in ("因为", "原因:", "原因：", "because ", "rationale:", "理由:", "理由："):
        idx = clean.lower().find(sep.lower())
        if idx > 8:
            rule = clean[:idx].strip(" ,;，；。")
            rationale = clean[idx:].strip()
            return rule, rationale
    return truncate_to_tokens(clean, 80), ""


def extract_triggers(raw: str, scope: str, entities: list[str], meta_triggers: list[str] | None = None) -> list[str]:
    triggers: list[str] = []
    for t in meta_triggers or []:
        s = str(t).strip()
        if s and s not in triggers:
            triggers.append(s)
    if triggers:
        return triggers[:5]
    # Extract conditional prefix ("在...时", "when ...", "编写...", "部署...")
    for pat in (
        r"(?:当|在)([^，,。；;→\-]{4,28})(?:时|中|下)",
        r"(?i)when\s+([^,.;\n]{4,40})",
        r"(?i)before\s+([^,.;\n]{4,40})",
    ):
        for m in re.finditer(pat, raw):
            cand = m.group(1).strip()
            if cand and cand not in triggers:
                triggers.append(cand)
    if not triggers:
        # Derive compact trigger from scope + top entities or leading clause
        head = re.split(r"[→=>，,。；;\n]", raw.strip())[0].strip()
        if head:
            triggers.append(truncate_to_tokens(head, 16))
        if scope and scope != "global" and entities:
            triggers.append(f"{scope} {' '.join(entities[:3])}")
    return triggers[:4]


def node_embedding_text(node: CompiledNode) -> str:
    return " | ".join(
        part
        for part in (
            node.scope,
            node.type,
            node.rule,
            node.rationale,
            " ".join(node.trigger_when),
            " ".join(node.entities),
        )
        if part
    )


def severity_weight(severity: str) -> float:
    return {"P0": 1.6, "P1": 1.2, "P2": 1.0}.get(severity, 1.0)


def recency_weight(valid_from: str, reference: datetime | None = None) -> float:
    ref = reference or datetime.now(timezone.utc)
    dt = parse_timestamp(valid_from)
    if dt is None:
        return 1.0
    age_days = max(0.0, (ref - dt).total_seconds() / 86400.0)
    # Gentle decay: 1.0 today -> ~0.75 at 90 days -> floor 0.5
    return max(0.5, 1.0 / (1.0 + age_days / 270.0))


def query_snapshot_boot(
    db: SnapshotDB,
    *,
    project: str | None = None,
    budget: int = 300,
    mode: str = "online",
) -> dict[str, Any]:
    """Build <= budget token L0 wakeup kernel from any SnapshotDB (brain or offline cache)."""
    active = db.list_nodes(scope=project, as_of=None)

    def _boot_rank(n: CompiledNode) -> tuple[int, int, int, str]:
        # Priority: P0 principles/lessons > project-specific decisions > high-seen lessons > others
        is_proj = 1 if (project and n.scope == project) else 0
        tier = 0
        if n.type == "principle" or n.severity == "P0":
            tier = 4
        elif n.type == "decision" and is_proj:
            tier = 3
        elif n.type == "lesson" and (n.seen_count >= 2 or n.severity == "P1"):
            tier = 2
        elif n.type == "decision":
            tier = 1
        return (tier, is_proj, n.seen_count, n.valid_from)

    active.sort(key=_boot_rank, reverse=True)

    header = f"# FOS L0 Kernel (scope={project or 'all'}, snap={db.get_meta('snapshot_version', 'unknown')})"
    used = estimate_tokens(header)
    lines = [header]
    entries: list[dict[str, Any]] = []

    for node in active:
        line = node.l0 or build_l0_line(node)
        if node.scope and node.scope != "global" and f"({node.scope})" not in line:
            line = f"{line} ({node.scope})"
        cost = estimate_tokens(line) + 1
        if used + cost > budget:
            if not entries and budget > used + 8:
                line = truncate_to_tokens(line, budget - used - 1)
                cost = estimate_tokens(line) + 1
            else:
                continue
        lines.append(line)
        used += cost
        entries.append(
            {
                "id": node.id,
                "type": node.type,
                "scope": node.scope,
                "severity": node.severity,
                "seen_count": node.seen_count,
                "l0": line,
            }
        )

    return {
        "mode": mode,
        "snapshot_version": db.get_meta("snapshot_version", "unknown"),
        "project": project,
        "budget_tokens": budget,
        "used_tokens": used,
        "count": len(entries),
        "kernel_text": "\n".join(lines),
        "entries": entries,
    }


def query_snapshot_recall(
    db: SnapshotDB,
    query: str,
    *,
    embedder: Any | None = None,
    reranker: Any | None = None,
    kind: str | None = None,
    scope: str | None = None,
    as_of: str | None = None,
    budget: int = 1200,
    limit: int = 20,
    mode: str = "online",
) -> dict[str, Any]:
    """Execute 4-way hybrid recall + RRF + Rerank + strict L1 token budget packing."""
    if kind and kind not in VALID_KINDS:
        raise UsageError(f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}")

    # Candidate universe filtered by scope, kind, and bi-temporal validity
    valid_nodes = db.list_nodes(scope=scope, kind=kind, as_of=as_of)
    valid_map: dict[str, CompiledNode] = {n.id: n for n in valid_nodes}
    if not valid_map:
        return {
            "mode": mode,
            "snapshot_version": db.get_meta("snapshot_version", "unknown"),
            "query": query,
            "scope": scope,
            "kind": kind,
            "as_of": as_of,
            "budget_tokens": budget,
            "used_tokens": 0,
            "count": 0,
            "results": [],
        }

    # Way 1: FTS5 BM25
    fts_hits = [(nid, sc) for nid, sc in db.search_fts(query, limit=40) if nid in valid_map]

    # Way 2: Vector KNN (sqlite-vec)
    vec_hits: list[tuple[str, float]] = []
    if embedder is not None:
        q_vecs = embedder.embed([query])
        if q_vecs:
            vec_hits = [(nid, sc) for nid, sc in db.search_vec(q_vecs[0], limit=40) if nid in valid_map]

    # Way 3: Trigger & Entity direct match + 1-2 Hop Graph Expansion
    q_terms = extract_terms(query)
    trigger_hits: list[tuple[str, float]] = []
    for nid, node in valid_map.items():
        trig_terms = extract_terms(" ".join(node.trigger_when + node.entities))
        if not trig_terms or not q_terms:
            continue
        overlap = len(q_terms & trig_terms)
        if overlap > 0:
            score = overlap / max(1, min(len(q_terms), len(trig_terms)))
            trigger_hits.append((nid, score))
    trigger_hits.sort(key=lambda x: x[1], reverse=True)

    # Seed graph expansion with top hits from FTS + Vec + Trigger
    seed_ids = list(
        dict.fromkeys(
            [nid for nid, _ in fts_hits[:5]]
            + [nid for nid, _ in vec_hits[:5]]
            + [nid for nid, _ in trigger_hits[:5]]
        )
    )
    graph_hops = db.expand_graph(seed_ids, hops=2, as_of=as_of) if seed_ids else {}
    graph_hits = [
        (nid, 1.0 / hop)
        for nid, hop in sorted(graph_hops.items(), key=lambda kv: kv[1])
        if nid in valid_map
    ]

    # RRF Fusion across active channels
    rrf_k = 60.0
    rrf_scores: dict[str, float] = {}
    channels: dict[str, list[str]] = {}

    def _add_channel(name: str, ranked: list[tuple[str, float]], weight: float = 1.0) -> None:
        for idx, (nid, _raw) in enumerate(ranked):
            rrf_scores[nid] = rrf_scores.get(nid, 0.0) + weight / (rrf_k + idx + 1.0)
            channels.setdefault(nid, [])
            if name not in channels[nid]:
                channels[nid].append(name)

    _add_channel("fts", fts_hits, weight=1.2)
    _add_channel("vec", vec_hits, weight=1.0)
    _add_channel("trigger", trigger_hits[:30], weight=1.15)
    _add_channel("graph", graph_hits[:20], weight=0.6)

    # If all channels returned nothing (e.g. ultra-short query), fall back to lexical scan over valid nodes
    if not rrf_scores:
        for nid in list(valid_map.keys())[:limit]:
            rrf_scores[nid] = 0.001
            channels[nid] = ["fallback"]

    # Pre-select top candidates for Cross-Encoder / Lexical Rerank
    top_ids = sorted(rrf_scores, key=lambda nid: rrf_scores[nid], reverse=True)[: max(limit * 2, 24)]

    rerank_scores: dict[str, float] = {}
    if reranker is not None and top_ids:
        docs = [(nid, node_embedding_text(valid_map[nid])) for nid in top_ids]
        rerank_scores = reranker.rerank(query, docs)

    # Way 4: Temporal & Salience Weighting: severity * log(1 + seen_count) * recency
    ref_time = parse_timestamp(as_of) if as_of else datetime.now(timezone.utc)
    final_ranked: list[tuple[str, float]] = []
    max_rrf = max((rrf_scores[nid] for nid in top_ids), default=1.0) or 1.0

    for nid in top_ids:
        node = valid_map[nid]
        norm_rrf = rrf_scores[nid] / max_rrf
        rr = rerank_scores.get(nid, norm_rrf)
        salience = severity_weight(node.severity) * (1.0 + math.log(1.0 + max(1, node.seen_count))) * recency_weight(node.valid_from, ref_time)
        # Scope boost for exact project match over 'global'
        scope_boost = 1.15 if (scope and node.scope == scope) else 1.0
        combined = (0.55 * rr + 0.45 * norm_rrf) * salience * scope_boost
        final_ranked.append((nid, combined))

    final_ranked.sort(key=lambda x: x[1], reverse=True)

    # Strict Token Budget Packing (L1 structured cards)
    used_tokens = 0
    results: list[dict[str, Any]] = []
    for nid, score in final_ranked:
        if len(results) >= limit:
            break
        node = valid_map[nid]
        card = node.to_l1_dict()
        card["score"] = round(score, 4)
        card["channels"] = channels.get(nid, [])
        cost = estimate_json_tokens(card)
        if used_tokens + cost > budget:
            if not results and budget >= 40:
                # Guarantee at least top-1 trimmed to budget
                card["rationale"] = ""
                card["rule"] = truncate_to_tokens(card["rule"], max(16, budget // 3))
                cost = estimate_json_tokens(card)
            else:
                continue
        results.append(card)
        used_tokens += cost

    return {
        "mode": mode,
        "snapshot_version": db.get_meta("snapshot_version", "unknown"),
        "query": query,
        "scope": scope,
        "kind": kind,
        "as_of": as_of,
        "budget_tokens": budget,
        "used_tokens": used_tokens,
        "count": len(results),
        "results": results,
    }


def query_snapshot_mirror(
    db: SnapshotDB,
    proposal: str,
    *,
    embedder: Any | None = None,
    reranker: Any | None = None,
    reasoner: Any | None = None,
    scope: str | None = None,
    mode: str = "online",
) -> dict[str, Any]:
    """Pre-decision AI-native mirror: 4 structured checks over principles, lessons, decisions, blind spots."""
    recalled = query_snapshot_recall(
        db,
        proposal,
        embedder=embedder,
        reranker=reranker,
        scope=scope,
        budget=2400,
        limit=16,
        mode=mode,
    )
    prop_terms = extract_terms(proposal)

    conflicts: list[dict[str, Any]] = []
    lesson_warnings: list[dict[str, Any]] = []
    prior_decisions: list[dict[str, Any]] = []
    blind_spots: list[dict[str, Any]] = []

    seen_decision_ids: set[str] = set()

    for card in recalled["results"]:
        nid = card["id"]
        ntype = card["type"]
        rule_text = card["rule"]
        trig_text = " ".join(card.get("trigger_when") or [])
        overlap = prop_terms & extract_terms(rule_text + " " + trig_text)
        if not overlap and card["score"] < 0.25:
            continue

        # 1. Redline check (principles & P0 rules)
        if ntype == "principle" or card["severity"] == "P0":
            has_prohibition = any(w in rule_text.lower() for w in _NEGATION_WORDS)
            if has_prohibition or ntype == "principle":
                conflicts.append(
                    {
                        "id": nid,
                        "type": ntype,
                        "severity": card["severity"],
                        "reason": "Triggered redline/principle constraint",
                        "rule": rule_text,
                        "matched_terms": sorted(overlap)[:6],
                    }
                )

        # 2. Lesson recurrence warning (seen >= 1)
        if ntype == "lesson" and int(card.get("seen_count", 1)) >= 1:
            lesson_warnings.append(
                {
                    "id": nid,
                    "severity": card["severity"],
                    "seen_count": card["seen_count"],
                    "rule": rule_text,
                    "rationale": card.get("rationale", ""),
                    "trigger_when": card.get("trigger_when", []),
                }
            )

        # 3. Same-domain historical decisions (active)
        if ntype == "decision":
            seen_decision_ids.add(nid)
            prior_decisions.append(
                {
                    "id": nid,
                    "status": card["status"],
                    "valid_from": card["valid_from"],
                    "rule": rule_text,
                    "rationale": card.get("rationale", ""),
                }
            )

    # Also surface superseded historical decisions in same scope so rejected options are visible
    for node in db.list_nodes(scope=scope, kind="decision", include_inactive=True):
        if node.status == "superseded" and node.id not in seen_decision_ids:
            node_terms = extract_terms(node.rule + " " + " ".join(node.trigger_when + node.entities))
            if len(prop_terms & node_terms) >= 2:
                prior_decisions.append(
                    {
                        "id": node.id,
                        "status": node.status,
                        "valid_from": node.valid_from,
                        "valid_until": node.valid_until,
                        "superseded_by": node.superseded_by,
                        "rule": node.rule,
                        "rationale": node.rationale,
                    }
                )

    # 4. Blind spots: detect unverified assumptions in proposal or unvalidated thoughts
    prop_low = proposal.lower()
    matched_cues = [cue for cue in _ASSUMPTION_CUES if cue in prop_low]
    if matched_cues:
        blind_spots.append(
            {
                "kind": "unverified_assumption",
                "detail": f"Proposal contains assumption cues ({', '.join(matched_cues[:4])}); verify empirically before committing.",
            }
        )
    has_outcome_support = any(c["type"] == "outcome" for c in recalled["results"])
    if not has_outcome_support and not prior_decisions:
        blind_spots.append(
            {
                "kind": "no_prior_outcome",
                "detail": "No prior recorded outcome or decision in this domain; treat key premises as unvalidated hypotheses.",
            }
        )

    # Optional LLM enrichment when a non-heuristic reasoner is configured
    if reasoner is not None:
        extra = reasoner.complete_json(
            f"Proposal: {proposal}\nConflicts: {conflicts}\nLessons: {lesson_warnings}\n"
            f"Prior decisions: {prior_decisions}\n"
            f"Return JSON with optional additional 'blind_spots': [{{\"kind\": ..., \"detail\": ...}}].",
            system="You are a concise technical risk reviewer.",
        )
        if extra and isinstance(extra.get("blind_spots"), list):
            for item in extra["blind_spots"][:3]:
                if isinstance(item, dict) and item.get("detail"):
                    blind_spots.append({"kind": str(item.get("kind", "llm_review")), "detail": str(item["detail"])})

    return {
        "mode": mode,
        "snapshot_version": db.get_meta("snapshot_version", "unknown"),
        "proposal": proposal,
        "scope": scope,
        "conflicts": conflicts,
        "lesson_warnings": lesson_warnings,
        "prior_decisions": prior_decisions,
        "blind_spots": blind_spots,
    }


class BrainEngine:
    """High-level coordinator operating on a brain data_dir (LedgerStore + SnapshotManager + Providers)."""

    def __init__(
        self,
        data_dir: Path,
        *,
        provider_cfg: ProviderConfig | None = None,
        providers: ProviderBundle | None = None,
        device: str = "local",
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.device = device
        self.providers = providers or build_providers(provider_cfg or ProviderConfig())
        self.ledger = LedgerStore(self.data_dir)
        self.snapshots = SnapshotManager(self.data_dir, embedding_dim=self.providers.embedder.dim)
        self.snapshots.ensure_initialized()

    @classmethod
    def from_config(cls, cfg: FosConfig, *, home: Path | None = None) -> BrainEngine:
        return cls(
            cfg.resolved_data_dir(home),
            provider_cfg=cfg.providers,
            device=cfg.device,
        )

    def _compile_atom_into_db(
        self,
        db: SnapshotDB,
        atom: LedgerAtom,
        *,
        synthesize_eval: bool = True,
    ) -> tuple[CompiledNode, CompiledNode | None]:
        """Compile a single LedgerAtom into a CompiledNode + edges inside `db`."""
        meta = atom.meta
        prefix = KIND_PREFIX.get(atom.kind, "MEM")
        node_id = str(meta.get("node_id") or db.next_sequence_id(prefix))
        severity = normalize_severity(meta.get("severity"), kind=atom.kind)
        rule, rationale = split_rule_and_rationale(atom.raw)
        if meta.get("rule"):
            rule = str(meta["rule"])
        if meta.get("rationale"):
            rationale = str(meta["rationale"])
        entities = extract_entities(atom.raw, meta.get("entities"))
        triggers = extract_triggers(atom.raw, atom.scope, entities, meta.get("trigger_when"))

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
            confidence=float(meta.get("confidence", 0.85 if atom.source == "manual" else 0.72)),
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

        emb = self.providers.embedder.embed([node_embedding_text(node)])[0]
        db.upsert_node(node, embedding=emb)

        # Handle explicit supersession (--supersedes DEC-xxx)
        superseded_node: CompiledNode | None = None
        sup_id = meta.get("supersedes")
        if sup_id:
            old = db.get_node(str(sup_id))
            if old is not None:
                old.status = "superseded"
                old.valid_until = atom.ts
                old.superseded_by = node.id
                old_emb = self.providers.embedder.embed([node_embedding_text(old)])[0]
                db.upsert_node(old, embedding=old_emb)
                db.add_edge(
                    GraphEdge(
                        src_id=node.id,
                        dst_id=old.id,
                        edge_type="SUPERSEDES",
                        valid_from=atom.ts,
                        meta={"atom_id": atom.id},
                    )
                )
                superseded_node = old

        # Optional explicit relation links from meta
        for rel_key, edge_type in (
            ("caused_by", "CAUSED_BY"),
            ("supports", "SUPPORTS"),
            ("contradicts", "CONTRADICTS"),
        ):
            refs = meta.get(rel_key)
            if isinstance(refs, str):
                refs = [refs]
            for target_id in refs or []:
                if target_id and db.get_node(str(target_id)) is not None:
                    db.add_edge(
                        GraphEdge(
                            src_id=node.id,
                            dst_id=str(target_id),
                            edge_type=edge_type,
                            valid_from=atom.ts,
                        )
                    )

        # Scope application edge
        if node.scope and node.scope != "global":
            db.add_edge(
                GraphEdge(
                    src_id=node.id,
                    dst_id=f"scope:{node.scope}",
                    edge_type="APPLIES_TO",
                    valid_from=atom.ts,
                )
            )

        if synthesize_eval:
            self.ledger.synthesize_cases_for_node(node, superseded_node=superseded_node, source=atom.source)

        return node, superseded_node

    def add_atom(
        self,
        *,
        kind: str,
        content: str,
        scope: str = "global",
        source: str = "manual",
        device: str | None = None,
        meta: dict[str, Any] | None = None,
        atom_id: str | None = None,
        ts: str | None = None,
    ) -> dict[str, Any]:
        if kind not in VALID_KINDS:
            raise UsageError(f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}")
        if not content or not content.strip():
            raise UsageError("Content must be non-empty.")

        meta_clean = dict(meta or {})
        atom = LedgerAtom(
            id=atom_id or new_atom_id(),
            ts=ts or now_iso(),
            device=device or self.device,
            kind=kind,
            scope=scope or "global",
            source=source,
            raw=content.strip(),
            meta=meta_clean,
        )

        # Persist to append-only ledger first (source of truth)
        persisted = self.ledger.append_atom(atom)

        # Index immediately into current active snapshot
        with self.snapshots.open_current() as db:
            # Verify supersedes target exists if provided
            sup_id = persisted.meta.get("supersedes")
            if sup_id and db.get_node(str(sup_id)) is None:
                raise NotFoundError(f"Supersedes target node not found: {sup_id}")
            node, superseded = self._compile_atom_into_db(db, persisted, synthesize_eval=True)
            db.set_meta("atom_count", str(len(self.ledger.load_all_atoms())))
            snap_ver = db.get_meta("snapshot_version", self.snapshots.current_version() or "v0001")

        return {
            "atom": persisted.to_dict(),
            "node": node.to_dict(),
            "superseded": superseded.id if superseded else None,
            "redacted": bool(persisted.meta.get("redacted_types")),
            "snapshot_version": snap_ver,
        }

    def rebuild_from_ledger(self) -> dict[str, Any]:
        """Rebuild a clean SQLite snapshot solely from immutable ledger/*.jsonl files."""
        atoms = self.ledger.load_all_atoms()
        version, db_path = self.snapshots.create_candidate()
        with SnapshotDB(db_path, embedding_dim=self.providers.embedder.dim) as db:
            db.set_meta("snapshot_version", version)
            db.set_meta("embedding_model", self.providers.embedder.model_name)
            db.set_meta("built_at", now_iso())
            for atom in atoms:
                self._compile_atom_into_db(db, atom, synthesize_eval=False)
            db.set_meta("atom_count", str(len(atoms)))
            node_count = db.count_nodes()
        self.snapshots.promote(version)
        return {
            "snapshot_version": version,
            "atom_count": len(atoms),
            "node_count": node_count,
        }

    def boot(self, *, project: str | None = None, budget: int = 300) -> dict[str, Any]:
        with self.snapshots.open_current(read_only=True) as db:
            return query_snapshot_boot(db, project=project, budget=budget, mode="online")

    def recall(
        self,
        query: str,
        *,
        kind: str | None = None,
        scope: str | None = None,
        as_of: str | None = None,
        budget: int = 1200,
        limit: int = 20,
    ) -> dict[str, Any]:
        with self.snapshots.open_current(read_only=True) as db:
            return query_snapshot_recall(
                db,
                query,
                embedder=self.providers.embedder,
                reranker=self.providers.reranker,
                kind=kind,
                scope=scope,
                as_of=as_of,
                budget=budget,
                limit=limit,
                mode="online",
            )

    def get(
        self,
        node_id: str,
        *,
        tier: str = "L1",
        include_graph: bool = False,
    ) -> dict[str, Any]:
        tier_up = tier.upper()
        if tier_up not in ("L0", "L1", "L2"):
            raise UsageError(f"Invalid tier '{tier}'. Must be L0, L1, or L2.")

        with self.snapshots.open_current(read_only=True) as db:
            node = db.get_node(node_id)
            if node is None:
                raise NotFoundError(f"Node not found: {node_id}")

            result: dict[str, Any] = {
                "mode": "online",
                "snapshot_version": db.get_meta("snapshot_version", "unknown"),
                "tier": tier_up,
                "id": node.id,
            }
            if tier_up == "L0":
                result["l0"] = node.l0
            else:
                result["node"] = node.to_l1_dict()
                result["l0"] = node.l0
            if tier_up == "L2":
                atoms = self.ledger.get_atoms_by_ids(node.evidence_ids)
                result["evidence"] = [a.to_dict() for a in atoms]
            if include_graph:
                edges = db.get_edges(node.id, include_inactive=True)
                result["graph"] = [e.to_dict() for e in edges]
            return result

    def mirror(self, proposal: str, *, scope: str | None = None) -> dict[str, Any]:
        if not proposal or not proposal.strip():
            raise UsageError("Proposal must be non-empty.")
        with self.snapshots.open_current(read_only=True) as db:
            return query_snapshot_mirror(
                db,
                proposal.strip(),
                embedder=self.providers.embedder,
                reranker=self.providers.reranker,
                reasoner=self.providers.reasoner,
                scope=scope,
                mode="online",
            )

    def export_compact_snapshot(self) -> dict[str, Any]:
        with self.snapshots.open_current(read_only=True) as db:
            return db.export_compact_bundle()

    def export_markdown(self, out_dir: Path) -> dict[str, Any]:
        """Generate read-only human-browsable Markdown projection from compiled snapshot."""
        out = Path(out_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)
        with self.snapshots.open_current(read_only=True) as db:
            nodes = db.list_nodes(include_inactive=True)
            edges = db.all_edges()
            snap_ver = db.get_meta("snapshot_version", "unknown")

        by_type: dict[str, list[CompiledNode]] = {k: [] for k in VALID_KINDS}
        for n in nodes:
            by_type.setdefault(n.type, []).append(n)

        written: list[str] = []
        index_lines = [
            f"# FlowOS Cognitive Views (`{snap_ver}`)",
            "",
            f"- Generated at: `{now_iso()}`",
            f"- Total nodes: `{len(nodes)}` | Total edges: `{len(edges)}`",
            "",
            "| Kind | Active | Total | File |",
            "|---|---:|---:|---|",
        ]
        for kind in VALID_KINDS:
            items = by_type.get(kind, [])
            active_cnt = sum(1 for n in items if n.status == "active")
            fname = f"{kind}s.md"
            index_lines.append(f"| `{kind}` | {active_cnt} | {len(items)} | [{fname}](./{fname}) |")

            lines = [f"# {kind.title()}s (`{snap_ver}`)", ""]
            for n in items:
                badge = f"`{n.status}` · `{n.severity}` · `seen:{n.seen_count}` · `{n.scope}`"
                lines.append(f"## {n.id} — {badge}")
                lines.append("")
                lines.append(f"- **L0**: {n.l0}")
                lines.append(f"- **Rule**: {n.rule}")
                if n.rationale:
                    lines.append(f"- **Rationale**: {n.rationale}")
                if n.trigger_when:
                    lines.append(f"- **Trigger When**: {', '.join(n.trigger_when)}")
                lines.append(f"- **Valid**: `{n.valid_from}` → `{n.valid_until or 'present'}`")
                if n.superseded_by:
                    lines.append(f"- **Superseded By**: `{n.superseded_by}`")
                if n.evidence_ids:
                    lines.append(f"- **Evidence**: {', '.join(f'`{e}`' for e in n.evidence_ids)}")
                lines.append("")
            fpath = out / fname
            fpath.write_text("\n".join(lines), encoding="utf-8")
            written.append(str(fpath))

        readme = out / "README.md"
        readme.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
        written.append(str(readme))
        return {"snapshot_version": snap_ver, "out_dir": str(out), "files": written, "node_count": len(nodes)}
