"""Core FlowOS Brain Engine: compile, rebuild, boot, 4-way hybrid recall, get, mirror, export."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fos.code_pattern import check_compat_drift
from fos.config import FosConfig, ProviderConfig
from fos.errors import NotFoundError, UsageError
from fos.freshness import (
    check_source_freshness,
    evaluate_surprise,
    freshness_multiplier,
    load_usage_stats,
    node_utility_score,
    record_recall_hits,
    record_utility_feedback,
    resolve_legacy_source_ref,
    summarize_utility_analytics,
    utility_multiplier,
)
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
from fos.redact import redact_secrets
from fos.scopes import (
    canonical_repo_name,
    is_repo_root_ambient_doc,
    normalize_scope,
    resolve_active_scopes,
    scope_depth,
    scope_kind,
)
from fos.store import SnapshotDB, SnapshotManager
from fos.tokens import (
    estimate_json_tokens,
    estimate_tokens,
    extract_stemmed_terms,
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
    "禁止", "不要", "不能", "不支持", "严禁", "避免", "放弃", "废弃", "替代", "改用", "不得", "不可", "切勿",
    "never", "avoid", "disable", "deprecated", "replace", "instead of", "do not", "don't", "must not", "forbid",
)
_ACTION_CLAUSE_RE = re.compile(
    r"(?:禁止|严禁|不得|不要|不可|切勿|避免|必须|务必|统一|改用|采用|优先|保留|\b(?:never|must|avoid|always|prefer|use)\b)",
    re.IGNORECASE,
)
_DOC_PREFIX_RE = re.compile(
    r"^(?:(?:按|根据|参见|详见|遵循)\s*[A-Za-z0-9_./-]+\.md\s*[，,：:\s]*|遵守仓库红线[：:\s]*)",
    re.IGNORECASE,
)
_DOC_SUFFIX_RE = re.compile(r"[；;。,\s]*(?:详见|参见)\s*[^。；;\n]+[。.]?\s*$")
_AMBIENT_DOC_NAMES = ("agents.md", "claude.md", "gemini.md")
_CURRENT_STATE_RE = re.compile(
    r"(?:\b(?:currently|current|now|recent|recently|latest|still|anymore|today)\b|现在|目前|当前|最新|最近)",
    re.IGNORECASE,
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


def _condense_l0_summary(rule: str, max_tokens: int = 26) -> str:
    """Extract salient imperative/prohibition summary without mid-clause cliffhangers."""
    cleaned = rule.replace("\n", " ").strip()
    cleaned = _DOC_PREFIX_RE.sub("", cleaned)
    cleaned = _DOC_SUFFIX_RE.sub("", cleaned).strip(" 。；;，,")
    if not cleaned:
        cleaned = rule.replace("\n", " ").strip()
    if estimate_tokens(cleaned) <= max_tokens:
        return cleaned

    # Split into clauses; if leading setup clause lacks actionable polarity while next clause has it, preserve action!
    clauses = [c.strip() for c in re.split(r"[；;。]|(?<=[^A-Za-z0-9])[，,]", cleaned) if c.strip()]
    if len(clauses) >= 2 and not _ACTION_CLAUSE_RE.search(clauses[0]):
        for sub in clauses[1:]:
            if _ACTION_CLAUSE_RE.search(sub):
                if estimate_tokens(clauses[0]) <= 11:
                    merged = f"{clauses[0]}：{sub}"
                else:
                    merged = sub
                return truncate_to_tokens(merged, max_tokens)
    return truncate_to_tokens(cleaned, max_tokens)


EDITABLE_NODE_FIELDS: tuple[str, ...] = (
    "rule",
    "rationale",
    "scope",
    "severity",
    "type",
    "guard_hint",
    "guard_pattern",
    "source_ref",
    "trigger_when",
    "entities",
)


def _clean_string_list(items: list[str] | None, *, redact: bool = False) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items or []:
        val = str(raw).strip()
        if redact and val:
            val = redact_secrets(val).text.strip()
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    return out


def apply_node_edit_patch(
    target: CompiledNode,
    meta: dict[str, Any],
    *,
    ts: str,
    atom_id: str,
) -> tuple[bool, str | None]:
    """Apply an immutable `manual:edit` patch to `target` in place.

    Shared by live compilation (`BrainEngine._compile_atom_into_db`) and sleep
    replay (`dream._compile_dream_nodes`) so edited cards survive dream rebuilds.
    Returns `(scope_changed, old_scope)`.
    """
    raw_fields = meta.get("edit_fields")
    fields = set(str(f) for f in raw_fields) if isinstance(raw_fields, list) else {
        f for f in EDITABLE_NODE_FIELDS if f in meta
    }
    old_scope = target.scope

    if "type" in fields:
        new_type = str(meta.get("type") or "").strip()
        if new_type in VALID_KINDS:
            target.type = new_type
    if "scope" in fields:
        new_scope = str(meta.get("scope") or "").strip()
        if new_scope:
            target.scope = normalize_scope(new_scope, strict=False)
    if "severity" in fields:
        new_sev = str(meta.get("severity") or "").strip()
        if new_sev:
            target.severity = normalize_severity(new_sev, kind=target.type)
    if "rule" in fields:
        new_rule = str(meta.get("rule") or "").strip()
        if new_rule:
            target.rule = new_rule
    if "rationale" in fields:
        target.rationale = str(meta.get("rationale") or "").strip()
    if "guard_hint" in fields:
        target.guard_hint = str(meta.get("guard_hint") or "").strip()
    if "guard_pattern" in fields:
        target.guard_pattern = str(meta.get("guard_pattern") or "").strip()
    if "source_ref" in fields:
        new_ref = str(meta.get("source_ref") or "").strip()
        if new_ref != target.source_ref:
            target.source_ref = new_ref
            target.source_hash = ""
            target.verified_at = ts[:10] if (new_ref and len(ts) >= 10) else ""
    if "trigger_when" in fields:
        target.trigger_when = _clean_string_list(list(meta.get("trigger_when") or []))
    if "entities" in fields:
        target.entities = _clean_string_list(list(meta.get("entities") or []))

    if atom_id and atom_id not in target.evidence_ids:
        target.evidence_ids.append(atom_id)
    target.l0 = build_l0_line(target)
    target.updated_at = ts
    return target.scope != old_scope, old_scope


def build_l0_line(node: CompiledNode) -> str:
    """Build ultra-compact L0 wakeup kernel line (~15-26 tokens, zero default noise)."""
    summary = _condense_l0_summary(node.rule, 26)
    seen_tag = f"|{node.seen_count}x" if node.seen_count > 1 else ""
    return f"[{node.id}|{node.severity}{seen_tag}] {summary}"


def split_rule_and_rationale(raw: str) -> tuple[str, str]:
    """Split a raw statement into a crisp rule/claim and rationale without dropping trailing context."""
    # For hindsight-collected episodes ("Context: ...\nHindsight: ...\nResolution: ...")
    if "Hindsight:" in raw:
        before, after = raw.split("Hindsight:", 1)
        ctx = before.replace("Context:", "", 1).strip()
        if "Resolution:" in after:
            hind_part, res_part = after.split("Resolution:", 1)
            hind = " ".join(line.strip() for line in hind_part.strip().splitlines() if line.strip())
            res = " ".join(line.strip() for line in res_part.strip().splitlines() if line.strip())
            if res and hind:
                return truncate_to_tokens(f"{hind} -> {res}", 94), truncate_to_tokens(ctx, 50)
        hind = " ".join(line.strip() for line in after.strip().splitlines() if line.strip())
        if hind:
            return truncate_to_tokens(hind, 90), truncate_to_tokens(ctx, 60)

    clean = " ".join(line.strip() for line in raw.strip().splitlines() if line.strip())
    for sep in ("因为", "原因:", "原因：", "because ", "rationale:", "理由:", "理由："):
        idx = clean.lower().find(sep.lower())
        if idx > 8:
            rule = truncate_to_tokens(clean[:idx].strip(" ,;，；。"), 92)
            rationale = truncate_to_tokens(clean[idx:].strip(), 85)
            return rule, rationale

    if estimate_tokens(clean) <= 84:
        return clean, ""

    # Split cleanly at first major boundary so `rule` stays focused and `rationale` keeps a concise summary
    lead = truncate_to_tokens(clean, 84).rstrip("…").strip()
    split_pos = len(lead)
    for m in re.finditer(r"(?:[。；;!！?？]|\|\|\s*)", lead):
        if m.end() >= int(len(lead) * 0.45):
            split_pos = m.end()
    rule_part = lead[:split_pos].strip(" ,;，；")
    rest_part = clean[split_pos:].strip(" ,;，；|")
    # Only populate rationale when an explicit CJK/pipe clause separator existed (avoids diluting code/QA embeddings)
    if split_pos < len(lead) and rest_part:
        return (rule_part or truncate_to_tokens(clean, 84)), truncate_to_tokens(rest_part, 48)
    return truncate_to_tokens(clean, 84), ""


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
    compat_parts: list[str] = []
    if node.compat:
        if node.compat.get("lang"):
            compat_parts.append(str(node.compat["lang"]))
        stack = node.compat.get("stack")
        if isinstance(stack, dict):
            compat_parts.extend(str(k) for k in stack.keys())
    return " | ".join(
        part
        for part in (
            node.scope,
            node.type,
            node.rule,
            node.rationale,
            " ".join(node.trigger_when),
            " ".join(node.entities),
            " ".join(compat_parts),
        )
        if part
    )


def severity_weight(severity: str) -> float:
    return {"P0": 1.6, "P1": 1.2, "P2": 1.0}.get(severity, 1.0)


def recency_weight(valid_from: str, reference: datetime | None = None, *, half_life_days: float = 270.0) -> float:
    ref = reference or datetime.now(timezone.utc)
    dt = parse_timestamp(valid_from)
    if dt is None:
        return 1.0
    age_days = max(0.0, (ref - dt).total_seconds() / 86400.0)
    return max(0.5, 1.0 / (1.0 + age_days / max(1.0, half_life_days)))


def _is_ambient_global_doc(node: CompiledNode) -> bool:
    """Return True if a global node merely mirrors an ambient prompt doc (`AGENTS.md`/`GEMINI.md`/`CLAUDE.md`)."""
    if node.scope != "global" or not node.source_ref:
        return False
    doc_path = node.source_ref.lower().split("#", 1)[0].split("::", 1)[0]
    return any(doc_path.endswith("/" + name) or doc_path == name for name in _AMBIENT_DOC_NAMES)


def query_snapshot_boot(
    db: SnapshotDB,
    *,
    project: str | None = None,
    scopes: list[str] | None = None,
    budget: int = 300,
    mode: str = "online",
) -> dict[str, Any]:
    """Build <= budget token L0 wakeup kernel with hierarchical scope inheritance & ambient-prompt deduplication."""
    norm_target = normalize_scope(project, strict=False) if project and project.strip() else None
    active_scopes = resolve_active_scopes(norm_target, extra_scopes=scopes)
    raw_active = db.list_nodes(scopes=active_scopes or None, as_of=None)

    def _is_target_scope(n: CompiledNode) -> bool:
        if not norm_target:
            return False
        n_norm = normalize_scope(n.scope, strict=False)
        if scope_kind(norm_target) == "repo":
            return scope_kind(n_norm) == "repo" and canonical_repo_name(n_norm) == canonical_repo_name(norm_target)
        return n_norm == norm_target

    # Exclude low-priority P2 trivia, global & inherited-domain P1 on-demand preferences (0t L0 contract),
    # repo-root ambient AGENTS.md/CLAUDE.md/GEMINI.md duplicates (already auto-loaded by coding agents),
    # graduated mechanical guards, non-P0 code exemplars, and broken-ref cards from L0 wakeup kernel.
    active = [
        n
        for n in raw_active
        if (n.severity != "P2" or n.type == "principle")
        and not (n.scope == "global" and n.severity != "P0")
        and not (scope_kind(n.scope) == "domain" and not _is_target_scope(n) and n.severity != "P0")
        and not (
            norm_target
            and scope_kind(norm_target) == "repo"
            and _is_target_scope(n)
            and is_repo_root_ambient_doc(n.scope, n.source_ref)
        )
        and not n.graduated
        and not (n.compat.get("pattern") == "code_exemplar" and n.severity != "P0")
        and check_source_freshness(n.source_ref, n.source_hash) != "broken_ref"
    ]
    if norm_target is None and not active_scopes:
        global_or_p0 = [n for n in active if n.scope == "global" or n.severity == "P0" or n.type == "principle"]
        if global_or_p0:
            active = global_or_p0

    def _inherited_rank(n: CompiledNode) -> tuple[int, int, int, int, int, str]:
        non_ambient = 0 if _is_ambient_global_doc(n) else 1
        # Prefer more specific mounted domain redlines over generic global when both are P0
        spec_depth = scope_depth(n.scope) if (norm_target or active_scopes) else 0
        tier = 4 if (n.type == "principle" or n.severity == "P0") else (3 if n.type == "decision" else 2)
        return (non_ambient, spec_depth, tier, len(n.seen_dates), n.seen_count, n.valid_from)

    def _proj_rank(n: CompiledNode) -> tuple[int, int, int, int, str]:
        is_p0 = 1 if n.severity == "P0" else 0
        # Recurring/explicit project lessons first, then active project decisions
        kind_pri = 3 if n.type == "lesson" else (2 if n.type == "decision" else 1)
        return (is_p0, kind_pri, len(n.seen_dates), n.seen_count, n.valid_from)

    snap_ver = db.get_meta("snapshot_version", "unknown")
    header_scope = norm_target or (",".join(scopes) if scopes else "all")
    header = f"# FOS L0 (scope={header_scope}, {snap_ver})"
    used = estimate_tokens(header)
    lines = [header]
    entries: list[dict[str, Any]] = []

    def _format_boot_line(node: CompiledNode) -> str:
        base = build_l0_line(node) if node.rule else (node.l0 or build_l0_line(node))
        if norm_target is None:
            if node.scope and node.scope != "global" and f"({node.scope})" not in base:
                return f"{base} ({node.scope})"
            return base
        # When scoped to a target project/domain, header already states scope=<target>.
        # Tag inherited domain/stack redlines and non-principle global items for clarity.
        if not _is_target_scope(node):
            if node.scope == "global":
                if node.type != "principle" and "(global)" not in base:
                    return f"{base} (global)"
            elif node.scope and f"({node.scope})" not in base:
                return f"{base} ({node.scope})"
        return base

    def _try_pack(candidates: list[CompiledNode], ceiling: int) -> list[CompiledNode]:
        nonlocal used
        skipped: list[CompiledNode] = []
        for node in candidates:
            line = _format_boot_line(node)
            cost = estimate_tokens(line) + 1
            if used + cost > ceiling:
                if not entries and ceiling > used + 8:
                    line = truncate_to_tokens(line, ceiling - used - 1)
                    cost = estimate_tokens(line) + 1
                else:
                    skipped.append(node)
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
        return skipped

    if norm_target:
        proj_nodes = sorted([n for n in active if _is_target_scope(n)], key=_proj_rank, reverse=True)
        inherited_nodes = sorted([n for n in active if not _is_target_scope(n)], key=_inherited_rank, reverse=True)
        # Reserve up to 60% of available budget for target-specific memories first, then inherited P0 redlines, then slack
        proj_ceiling = budget if not inherited_nodes else (used + int(max(0, budget - used) * 0.60))
        overflow_proj = _try_pack(proj_nodes, min(budget, proj_ceiling))
        _try_pack(inherited_nodes, budget)
        if overflow_proj and used < budget:
            _try_pack(overflow_proj, budget)
    else:
        ordered = sorted(active, key=_inherited_rank, reverse=True)
        _try_pack(ordered, budget)

    return {
        "mode": mode,
        "snapshot_version": snap_ver,
        "project": project,
        "active_scopes": active_scopes,
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
    scopes: list[str] | None = None,
    as_of: str | None = None,
    budget: int = 1200,
    limit: int = 20,
    mode: str = "online",
    usage_stats: dict[str, dict[str, Any]] | None = None,
    cwd_stack: dict[str, str] | None = None,
    exclude_ids: set[str] | list[str] | None = None,
) -> dict[str, Any]:
    """Execute 4-way hybrid recall + semantic-gated RRF/Rerank + anti-rot token budget packing."""
    if kind and kind not in VALID_KINDS:
        raise UsageError(f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}")

    excluded = {str(x).strip() for x in (exclude_ids or ()) if str(x).strip()}

    # Only filter candidate scopes when explicit `scope` or `scopes` is provided;
    # keep `scope=None, scopes=None` unfiltered for cross-repo recall (`cwd_stack` remains orthogonal for SemVer compat).
    active_scopes = resolve_active_scopes(scope, extra_scopes=scopes) if (scope or scopes) else []

    # Candidate universe filtered by hierarchical active scopes, kind, and bi-temporal validity
    valid_nodes = db.list_nodes(scopes=active_scopes or None, kind=kind, as_of=as_of)
    valid_map: dict[str, CompiledNode] = {n.id: n for n in valid_nodes}
    if not valid_map:
        return {
            "mode": mode,
            "snapshot_version": db.get_meta("snapshot_version", "unknown"),
            "query": query,
            "scope": scope,
            "active_scopes": active_scopes,
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
        q_vec: list[float] | None = None
        if hasattr(embedder, "embed_query"):
            q_vec = embedder.embed_query(query)
        else:
            q_vecs = embedder.embed([query])
            q_vec = q_vecs[0] if q_vecs else None
        if q_vec:
            vec_hits = [(nid, sc) for nid, sc in db.search_vec(q_vec, limit=40) if nid in valid_map]

    # Way 3: Trigger & Entity direct match + 1-2 Hop Graph Expansion
    q_terms = extract_stemmed_terms(query)
    trigger_hits: list[tuple[str, float]] = []
    min_trig_overlap = 2 if len(q_terms) >= 3 else 1
    for nid, node in valid_map.items():
        trig_terms = extract_stemmed_terms(" ".join(node.trigger_when + node.entities))
        if not trig_terms or not q_terms:
            continue
        overlap = len(q_terms & trig_terms)
        if overlap >= min_trig_overlap:
            score = overlap / math.sqrt(len(q_terms) * len(trig_terms))
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
    has_neural = embedder is not None and not str(getattr(embedder, "model_name", "")).startswith("local_hash")
    rrf_k = 25.0 if has_neural else 60.0
    rrf_scores: dict[str, float] = {}
    channels: dict[str, list[str]] = {}
    raw_vec_map: dict[str, float] = {nid: float(sc) for nid, sc in vec_hits}
    # Min-max normalize vector similarities across retrieved candidates to remove anisotropic embedding-cone compression
    if raw_vec_map:
        v_min = min(raw_vec_map.values())
        v_max = max(raw_vec_map.values())
        if v_max - v_min > 1e-6:
            vec_sim_map: dict[str, float] = {k: (v - v_min) / (v_max - v_min) for k, v in raw_vec_map.items()}
        else:
            vec_sim_map = {k: 1.0 for k in raw_vec_map}
    else:
        vec_sim_map = {}

    def _add_channel(name: str, ranked: list[tuple[str, float]], weight: float = 1.0) -> None:
        for idx, (nid, _raw) in enumerate(ranked):
            rrf_scores[nid] = rrf_scores.get(nid, 0.0) + weight / (rrf_k + idx + 1.0)
            channels.setdefault(nid, [])
            if name not in channels[nid]:
                channels[nid].append(name)

    if has_neural:
        _add_channel("vec", vec_hits, weight=2.2)
        _add_channel("fts", fts_hits, weight=0.90)
        _add_channel("trigger", trigger_hits[:30], weight=0.45)
        _add_channel("graph", graph_hits[:20], weight=0.40)
    else:
        _add_channel("fts", fts_hits, weight=1.2)
        _add_channel("vec", vec_hits, weight=1.0)
        _add_channel("trigger", trigger_hits[:30], weight=1.15)
        _add_channel("graph", graph_hits[:20], weight=0.6)

    if not rrf_scores:
        for nid in list(valid_map.keys())[:limit]:
            rrf_scores[nid] = 0.001
            channels[nid] = ["fallback"]

    # Pre-select top candidates for Cross-Encoder / Lexical Rerank (always preserve top neural vector hits)
    pool_size = max(limit * 2, 24)
    top_ids = sorted(rrf_scores, key=lambda nid: rrf_scores[nid], reverse=True)[:pool_size]
    if has_neural and vec_hits:
        top_set = set(top_ids)
        for nid, _ in vec_hits[:15]:
            if nid not in top_set:
                top_ids.append(nid)
                top_set.add(nid)

    rerank_scores: dict[str, float] = {}
    if reranker is not None and top_ids:
        docs = [(nid, node_embedding_text(valid_map[nid])) for nid in top_ids]
        rerank_scores = reranker.rerank(query, docs)

    # Way 4: Temporal, Salience, Anti-Rot Freshness & PlugMem Utility Weighting
    is_current_state_q = bool(_CURRENT_STATE_RE.search(query))
    ref_time = parse_timestamp(as_of) if as_of else datetime.now(timezone.utc)
    if as_of is None and is_current_state_q:
        # On current-state queries ("currently", "now", "最新"), anchor recency to latest candidate so updated facts win
        cand_dts = [dt for nid in top_ids if (dt := parse_timestamp(valid_map[nid].valid_from)) is not None]
        if cand_dts:
            ref_time = min(ref_time, max(cand_dts))
    half_life = 150.0 if is_current_state_q else 270.0
    u_stats = usage_stats or {}
    final_ranked: list[tuple[str, float, str]] = []
    max_rrf = max((rrf_scores[nid] for nid in top_ids), default=1.0) or 1.0

    for nid in top_ids:
        if nid in excluded:
            continue
        node = valid_map[nid]
        norm_rrf = rrf_scores[nid] / max_rrf
        lex_rr = rerank_scores.get(nid, 0.0)
        sem_rr = vec_sim_map.get(nid, 0.0)
        if has_neural:
            # Semantic-gated hybrid fusion: lexical & RRF signals amplify semantically plausible candidates
            # without rescuing semantically orthogonal false-friend keyword matches
            lex_sig = 0.52 * lex_rr + 0.48 * norm_rrf
            rel = 0.80 * sem_rr + 0.20 * lex_sig * (0.35 + 0.65 * sem_rr)
        else:
            rr = lex_rr if lex_rr > 0 else sem_rr
            rel = 0.55 * rr + 0.45 * norm_rrf
        salience = (
            severity_weight(node.severity)
            * (1.0 + math.log(1.0 + max(1, node.seen_count)))
            * recency_weight(node.valid_from, ref_time, half_life_days=half_life)
        )
        # Calibrated hierarchical scope prior:
        # exact target (1.40) > stack depth-3 (1.24) > specialty depth-2 (1.16) > domain depth-1 (1.08) > global (1.0/0.78)
        norm_req_scope = normalize_scope(scope, strict=False) if scope else None
        if norm_req_scope and norm_req_scope != "global":
            node_norm = normalize_scope(node.scope, strict=False)
            is_exact = node_norm == norm_req_scope or (
                scope_kind(norm_req_scope) == "repo"
                and scope_kind(node_norm) == "repo"
                and canonical_repo_name(node_norm) == canonical_repo_name(norm_req_scope)
            )
            if is_exact:
                scope_boost = 1.40
            elif scope_kind(node_norm) == "domain":
                d = scope_depth(node_norm)
                scope_boost = 1.24 if d >= 3 else (1.16 if d == 2 else 1.08)
            else:
                direct_hits = len(q_terms & extract_stemmed_terms(node.rule))
                scope_boost = 1.0 if direct_hits >= 2 else 0.78
        elif scopes:
            d = scope_depth(node.scope)
            scope_boost = 1.28 if d >= 3 else (1.18 if d == 2 else (1.10 if d == 1 else 1.0))
        else:
            scope_boost = 1.08 if node.scope == "global" else 1.0
        fresh = check_source_freshness(node.source_ref, node.source_hash)
        util_mult = utility_multiplier(u_stats.get(nid))
        combined = rel * salience * scope_boost * freshness_multiplier(fresh) * util_mult
        final_ranked.append((nid, combined, fresh))

    final_ranked.sort(key=lambda x: x[1], reverse=True)
    top_score = final_ranked[0][1] if final_ranked else 0.0
    prune_tail = budget <= 1600

    # Strict Token Budget Packing with Submodular Query-Term Coverage + Card-to-Card MMR + Anti-Context-Rot Tail Cutoff
    used_tokens = 0
    results: list[dict[str, Any]] = []
    q_terms_pack = extract_stemmed_terms(query)
    covered_terms: set[str] = set()
    selected_term_sets: list[set[str]] = []
    term_cache: dict[str, set[str]] = {
        nid: extract_stemmed_terms(valid_map[nid].rule + " " + " ".join(valid_map[nid].entities))
        for nid, _, _ in final_ranked
    }
    remaining = list(final_ranked)

    def _max_selected_jaccard(terms: set[str]) -> float:
        if not terms or not selected_term_sets:
            return 0.0
        best_sim = 0.0
        for sel in selected_term_sets:
            if not sel:
                continue
            inter = len(terms & sel)
            if inter:
                sim = inter / len(terms | sel)
                if sim > best_sim:
                    best_sim = sim
        return best_sim

    while remaining and len(results) < limit:
        uncovered = q_terms_pack - covered_terms
        if not results:
            best_idx = 0
        else:
            best_idx = 0
            best_val = -1.0
            for idx, (cand_id, base_score, _cf) in enumerate(remaining[:12]):
                cand_terms = term_cache[cand_id]
                if uncovered:
                    novel = uncovered & cand_terms
                    novel_boost = 1.0 + 0.6 * (len(novel) / max(1, len(uncovered)))
                else:
                    novel_boost = 1.0
                max_sim = _max_selected_jaccard(cand_terms)
                # MMR diversity multiplier: penalize cross-scope near-duplicates without clamping raw score
                div_mult = max(0.25, 1.0 - 0.55 * max_sim) if max_sim > 0.15 else 1.0
                val = base_score * novel_boost * div_mult
                if val > best_val:
                    best_val = val
                    best_idx = idx

        nid, score, fresh = remaining.pop(best_idx)
        node = valid_map[nid]
        cand_terms = term_cache[nid]
        cand_novel = uncovered & cand_terms
        cand_max_sim = _max_selected_jaccard(cand_terms)

        # Anti-context-rot cutoff: stop stuffing low-relevance / redundant tail noise once core query is answered
        if prune_tail and results and top_score > 0:
            if cand_max_sim >= 0.72 and not cand_novel:
                continue
            if score < 0.24 * top_score:
                continue
            if not cand_novel and score < 0.42 * top_score:
                continue
            if not cand_novel and len(results) >= 3 and score < 0.55 * top_score:
                continue

        raw_card = node.to_l1_dict()
        # Strip pure-indexing / L2-only metadata and false/empty defaults so L1 recall stays ultra-lean
        _omit_in_recall = (
            "source_hash",
            "verified_at",
            "trigger_when",
            "entities",
            "seen_dates",
            "evidence_ids",
            "confidence",
            "valid_from",
            "status",
            "guard_hint",
        )
        card: dict[str, Any] = {
            k: v
            for k, v in raw_card.items()
            if k not in _omit_in_recall
            and v is not None
            and v != ""
            and v != []
            and v != {}
            and not (k == "graduated" and v is False)
        }
        if fresh != "untracked":
            card["freshness"] = fresh
        if node.compat and cwd_stack:
            cc = check_compat_drift(node.compat, cwd_stack)
            if cc is not None:
                card["compat_check"] = cc
        card["score"] = round(score, 4)
        card["channels"] = channels.get(nid, [])
        cost = estimate_json_tokens(card)
        if used_tokens + cost > budget:
            if not results and budget >= 40:
                card.pop("rationale", None)
                card["rule"] = truncate_to_tokens(str(card.get("rule", "")), max(16, budget // 3))
                cost = estimate_json_tokens(card)
            else:
                continue
        results.append(card)
        used_tokens += cost
        selected_term_sets.append(cand_terms)
        if q_terms_pack:
            covered_terms |= q_terms_pack & cand_terms

    return {
        "mode": mode,
        "snapshot_version": db.get_meta("snapshot_version", "unknown"),
        "query": query,
        "scope": scope,
        "active_scopes": active_scopes,
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
    scopes: list[str] | None = None,
    cwd_stack: dict[str, str] | None = None,
    mode: str = "online",
) -> dict[str, Any]:
    """Pre-decision AI-native mirror: 4 structured checks over principles, lessons, decisions, blind spots."""
    recalled = query_snapshot_recall(
        db,
        proposal,
        embedder=embedder,
        reranker=reranker,
        scope=scope,
        scopes=scopes,
        cwd_stack=cwd_stack,
        budget=2400,
        limit=12,
        mode=mode,
    )
    prop_terms = extract_terms(proposal)
    prop_stem_terms = extract_stemmed_terms(proposal)
    recalled_cards = recalled["results"]
    top_score = float(recalled_cards[0]["score"]) if recalled_cards else 1.0

    # Precompute term document-frequency across recalled pool so ubiquitous repo nouns
    # (e.g. "gateway", "spring") alone don't trigger unrelated prohibition conflicts
    card_blobs: dict[str, tuple[CompiledNode | None, set[str], set[str]]] = {}
    pool_df: dict[str, int] = {}
    for card in recalled_cards:
        nid = card["id"]
        fn = db.get_node(nid)
        t_list = list(fn.trigger_when) if fn else []
        e_list = list(fn.entities) if fn else []
        blob = f"{card['rule']} {' '.join(t_list)} {' '.join(e_list)}"
        ex_set = extract_terms(blob)
        st_set = extract_stemmed_terms(blob)
        card_blobs[nid] = (fn, ex_set, st_set)
        for tok in st_set:
            pool_df[tok] = pool_df.get(tok, 0) + 1
    ubiquitous_cutoff = max(3, (len(recalled_cards) + 1) // 2)

    conflicts: list[dict[str, Any]] = []
    lesson_warnings: list[dict[str, Any]] = []
    prior_decisions: list[dict[str, Any]] = []
    blind_spots: list[dict[str, Any]] = []

    conflict_ids: set[str] = set()
    seen_decision_ids: set[str] = set()

    for card in recalled_cards:
        nid = card["id"]
        ntype = card["type"]
        rule_text = card["rule"]
        full_node, ex_set, st_set = card_blobs[nid]
        trig_list = list(full_node.trigger_when) if full_node else []
        valid_from = full_node.valid_from if full_node else ""

        overlap = prop_terms & ex_set
        stem_overlap = prop_stem_terms & st_set
        specific_overlap = {t for t in stem_overlap if pool_df.get(t, 0) <= ubiquitous_cutoff}
        score = float(card.get("score", 0.0))

        if not stem_overlap and score < max(0.55, 0.50 * top_score):
            continue

        has_prohibition = any(w in rule_text.lower() for w in _NEGATION_WORDS)

        # 1. Conflict / Redline check: catches BOTH P0 redlines AND repo-scoped P1/P2 prohibitions,
        # requiring at least one discriminative topic term (or strong top-rank score) to avoid domain-noun noise.
        is_prohibition_hit = has_prohibition and (len(specific_overlap) >= 1 or score >= 0.70 * top_score)
        is_principle_hit = card["severity"] == "P0" and (
            len(specific_overlap) >= 2 or score >= 0.70 * top_score
        )
        if is_prohibition_hit or is_principle_hit:
            conflict_ids.add(nid)
            conflicts.append(
                {
                    "id": nid,
                    "type": ntype,
                    "severity": card["severity"],
                    "reason": "Violates active prohibition" if has_prohibition else "Triggered redline/principle constraint",
                    "rule": rule_text,
                    "matched_terms": sorted(overlap or stem_overlap)[:6],
                }
            )

        # 2. Lesson recurrence warning (seen >= 1)
        if ntype == "lesson" and int(card.get("seen_count", 1)) >= 1 and (len(stem_overlap) >= 1 or score >= 0.50 * top_score):
            lesson_warnings.append(
                {
                    "id": nid,
                    "severity": card["severity"],
                    "seen_count": int(card.get("seen_count", 1)),
                    "rule": rule_text,
                    "rationale": card.get("rationale", ""),
                    "trigger_when": trig_list,
                }
            )

        # 3. Same-domain historical decisions (active, non-redundant with conflicts)
        if ntype == "decision":
            seen_decision_ids.add(nid)
            if nid not in conflict_ids and score >= 0.42 * top_score:
                prior_decisions.append(
                    {
                        "id": nid,
                        "status": full_node.status if full_node else "active",
                        "valid_from": valid_from,
                        "rule": rule_text,
                        "rationale": card.get("rationale", ""),
                    }
                )

    # Also surface superseded historical decisions in same active scope set so rejected options are visible
    mirror_active_scopes = recalled.get("active_scopes") or None
    for node in db.list_nodes(scope=None if mirror_active_scopes else scope, scopes=mirror_active_scopes, kind="decision", include_inactive=True):
        if node.status == "superseded" and node.id not in seen_decision_ids:
            node_terms = extract_stemmed_terms(node.rule + " " + " ".join(node.trigger_when + node.entities))
            if len(prop_stem_terms & node_terms) >= 2:
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
    if not has_outcome_support and not prior_decisions and not conflicts:
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
        "active_scopes": recalled.get("active_scopes") or [],
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
    ) -> tuple[CompiledNode | None, CompiledNode | None]:
        """Compile a single LedgerAtom into a CompiledNode + edges inside `db` (or apply archive transition)."""
        meta = atom.meta

        # Pure lifecycle archive transition (does not create a clutter node)
        archive_target_id = meta.get("archives")
        if archive_target_id:
            target = db.get_node(str(archive_target_id))
            if target is not None:
                target.status = "archived"
                target.valid_until = atom.ts
                if meta.get("archive_reason"):
                    reason = str(meta["archive_reason"]).strip()
                    target.rationale = (
                        f"{target.rationale} [archived: {reason}]".strip()
                        if target.rationale
                        else f"[archived: {reason}]"
                    )
                target.updated_at = atom.ts
                emb = self.providers.embedder.embed([node_embedding_text(target)])[0]
                db.upsert_node(target, embedding=emb)
            return None, target

        # Pure lifecycle graduation transition (rule now mechanically enforced via fos guard / CI)
        grad_target_id = meta.get("graduates")
        if grad_target_id:
            target = db.get_node(str(grad_target_id))
            if target is not None:
                target.graduated = True
                if meta.get("guard_pattern"):
                    target.guard_pattern = str(meta["guard_pattern"]).strip()
                if meta.get("graduate_reason"):
                    reason = str(meta["graduate_reason"]).strip()
                    target.rationale = (
                        f"{target.rationale} [graduated: {reason}]".strip()
                        if target.rationale
                        else f"[graduated: {reason}]"
                    )
                target.updated_at = atom.ts
                emb = self.providers.embedder.embed([node_embedding_text(target)])[0]
                db.upsert_node(target, embedding=emb)
            return None, target

        # Pure in-place edit transition recorded as an immutable ledger event
        edit_target_id = meta.get("edits")
        if edit_target_id:
            target = db.get_node(str(edit_target_id))
            if target is not None:
                scope_changed, _old_scope = apply_node_edit_patch(
                    target, meta, ts=atom.ts, atom_id=atom.id
                )
                emb = self.providers.embedder.embed([node_embedding_text(target)])[0]
                db.upsert_node(target, embedding=emb)
                if scope_changed:
                    db.expire_scope_edges(target.id, atom.ts)
                    if target.scope and target.scope != "global":
                        db.add_edge(
                            GraphEdge(
                                src_id=target.id,
                                dst_id=f"scope:{target.scope}",
                                edge_type="APPLIES_TO",
                                valid_from=atom.ts,
                            )
                        )
            return target, None

        # Pure reinforcement transition (Titans low-surprise redundant automated sighting)
        reinforce_target_id = meta.get("reinforces")
        if reinforce_target_id:
            target = db.get_node(str(reinforce_target_id))
            if target is not None:
                target.seen_count += 1
                day = atom.ts[:10] if len(atom.ts) >= 10 else ""
                if day and day not in target.seen_dates:
                    target.seen_dates.append(day)
                    target.seen_dates.sort()
                if atom.id not in target.evidence_ids:
                    target.evidence_ids.append(atom.id)
                target.l0 = build_l0_line(target)
                target.updated_at = atom.ts
                emb = self.providers.embedder.embed([node_embedding_text(target)])[0]
                db.upsert_node(target, embedding=emb)
            return target, None

        prefix = KIND_PREFIX.get(atom.kind, "MEM")
        node_id = str(meta.get("node_id") or db.next_sequence_id(prefix))
        severity = normalize_severity(meta.get("severity"), kind=atom.kind)
        rule, rationale = split_rule_and_rationale(atom.raw)
        if meta.get("rule"):
            rule = str(meta["rule"])
        if meta.get("rationale"):
            rationale = str(meta["rationale"])
        entity_src = f"{rule} {rationale}" if (meta.get("rule") and meta.get("compat")) else atom.raw
        entities = extract_entities(entity_src, meta.get("entities"))
        triggers = extract_triggers(rule if meta.get("rule") else atom.raw, atom.scope, entities, meta.get("trigger_when"))

        source_ref, source_hash = resolve_legacy_source_ref(atom.scope or "global", atom.source, meta)
        verified_at = str(meta.get("verified_at") or (atom.ts[:10] if len(atom.ts) >= 10 else ""))
        compat = dict(meta.get("compat") or {})

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
            seen_dates=[atom.ts[:10]] if len(atom.ts) >= 10 else [],
            confidence=float(
                meta.get(
                    "confidence",
                    0.92 if atom.source == "manual:correct" else (0.85 if atom.source.startswith("manual") else 0.72),
                )
            ),
            trigger_when=triggers,
            rule=rule,
            rationale=rationale,
            guard_hint=str(meta.get("guard_hint") or ""),
            guard_pattern=str(meta.get("guard_pattern") or ""),
            graduated=bool(meta.get("graduated", False)),
            source_ref=source_ref,
            source_hash=source_hash,
            verified_at=verified_at,
            compat=compat,
            entities=entities,
            contradicts=list(meta.get("contradicts") or []),
            evidence_ids=[atom.id],
            created_at=atom.ts,
            updated_at=atom.ts,
        )
        node.l0 = build_l0_line(node)

        emb = self.providers.embedder.embed([node_embedding_text(node)])[0]
        db.upsert_node(node, embedding=emb)

        # Handle explicit supersession (--supersedes DEC-xxx or merge supersedes_ids=[...])
        superseded_node: CompiledNode | None = None
        sup_targets: list[str] = []
        if meta.get("supersedes"):
            sup_targets.append(str(meta["supersedes"]))
        for sid in meta.get("supersedes_ids") or []:
            s = str(sid)
            if s and s not in sup_targets:
                sup_targets.append(s)
        for sup_id in sup_targets:
            old = db.get_node(sup_id)
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
                if superseded_node is None:
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

        # Reverse provenance links: repo evidence nodes --SUPPORTS--> promoted global node
        for src_id in meta.get("supported_by") or []:
            if src_id and db.get_node(str(src_id)) is not None:
                db.add_edge(
                    GraphEdge(
                        src_id=str(src_id),
                        dst_id=node.id,
                        edge_type="SUPPORTS",
                        valid_from=atom.ts,
                        meta={"atom_id": atom.id},
                    )
                )

        # Titans surprise conflict edge when new memory opposes an existing active rule
        conflict_id = meta.get("surprise_conflict_with")
        if conflict_id and str(conflict_id) != node.id and db.get_node(str(conflict_id)) is not None:
            db.add_edge(
                GraphEdge(
                    src_id=node.id,
                    dst_id=str(conflict_id),
                    edge_type="CONTRADICTS",
                    valid_from=atom.ts,
                    meta={"atom_id": atom.id, "detector": "surprise_gate"},
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
        scope_clean = normalize_scope(scope, strict=source.startswith("manual"))
        raw_clean = content.strip()
        is_lifecycle_op = any(k in meta_clean for k in ("archives", "graduates", "supersedes", "reinforces", "edits"))

        # Titans-style surprise & conflict gate against active same-scope/global memories
        surprise_info: dict[str, Any] = {
            "surprise": 1.0,
            "classification": "novel",
            "closest_node_id": None,
            "similarity": 0.0,
        }
        if not is_lifecycle_op:
            with self.snapshots.open_current(read_only=True) as db:
                existing_active = db.list_nodes(scope=None if scope_clean == "global" else scope_clean, as_of=None)
            same_bucket = [n for n in existing_active if n.scope == scope_clean and n.type == kind]
            if same_bucket:
                probe_text = str(meta_clean.get("rule") or raw_clean)
                texts = [probe_text] + [n.rule for n in same_bucket]
                vecs = self.providers.embedder.embed(texts)
                if len(vecs) == len(texts):
                    surprise_info = evaluate_surprise(
                        probe_text,
                        vecs[0],
                        list(zip(same_bucket, vecs[1:])),
                    )
                    if surprise_info["classification"] == "redundant" and not source.startswith("manual") and surprise_info["closest_node_id"]:
                        # Low-surprise automated duplicate: reinforce existing card instead of duplicating
                        meta_clean["reinforces"] = surprise_info["closest_node_id"]
                    elif surprise_info["classification"] == "conflict" and surprise_info["closest_node_id"]:
                        meta_clean["surprise_conflict_with"] = surprise_info["closest_node_id"]

        atom = LedgerAtom(
            id=atom_id or new_atom_id(),
            ts=ts or now_iso(),
            device=device or self.device,
            kind=kind,
            scope=scope_clean,
            source=source,
            raw=raw_clean,
            meta=meta_clean,
        )

        # Persist to append-only ledger first (source of truth)
        persisted = self.ledger.append_atom(atom)

        # Index immediately into current active snapshot
        with self.snapshots.open_current() as db:
            # Verify supersedes/archives/graduates target exists if provided
            sup_id = persisted.meta.get("supersedes")
            if sup_id and db.get_node(str(sup_id)) is None:
                raise NotFoundError(f"Supersedes target node not found: {sup_id}")
            arc_id = persisted.meta.get("archives")
            if arc_id and db.get_node(str(arc_id)) is None:
                raise NotFoundError(f"Archive target node not found: {arc_id}")
            grad_id = persisted.meta.get("graduates")
            if grad_id and db.get_node(str(grad_id)) is None:
                raise NotFoundError(f"Graduate target node not found: {grad_id}")
            edit_id = persisted.meta.get("edits")
            if edit_id and db.get_node(str(edit_id)) is None:
                raise NotFoundError(f"Edit target node not found: {edit_id}")
            node, superseded = self._compile_atom_into_db(db, persisted, synthesize_eval=True)
            db.set_meta("atom_count", str(len(self.ledger.load_all_atoms())))
            snap_ver = db.get_meta("snapshot_version", self.snapshots.current_version() or "v0001")

        return {
            "atom": persisted.to_dict(),
            "node": node.to_dict() if node else None,
            "superseded": superseded.id if (superseded and sup_id) else None,
            "archived": superseded.id if (superseded and arc_id) else None,
            "graduated": superseded.id if (superseded and grad_id) else None,
            "edited": edit_id,
            "reinforced": persisted.meta.get("reinforces"),
            "surprise": surprise_info,
            "redacted": bool(persisted.meta.get("redacted_types")),
            "snapshot_version": snap_ver,
        }

    def edit_node(
        self,
        node_id: str,
        *,
        rule: str | None = None,
        rationale: str | None = None,
        scope: str | None = None,
        severity: str | None = None,
        kind: str | None = None,
        guard_hint: str | None = None,
        guard_pattern: str | None = None,
        source_ref: str | None = None,
        trigger_when: list[str] | None = None,
        entities: list[str] | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Edit an existing card's structured fields in place via an immutable `manual:edit` ledger event."""
        with self.snapshots.open_current(read_only=True) as db:
            old = db.get_node(node_id)
            if old is None:
                raise NotFoundError(
                    f"Node not found: {node_id}",
                    hint="run `fos recall <query>` to find node IDs",
                )
            snap_ver = db.get_meta("snapshot_version", self.snapshots.current_version() or "v0001")

        meta: dict[str, Any] = {"edits": node_id}
        changed_fields: list[str] = []

        new_type = old.type
        if kind is not None:
            k = kind.strip()
            if k not in VALID_KINDS:
                raise UsageError(f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}")
            if k != old.type:
                new_type = k
                meta["type"] = k
                changed_fields.append("type")

        new_scope = old.scope
        if scope is not None:
            sc = normalize_scope(scope, strict=True)
            if sc != old.scope:
                new_scope = sc
                meta["scope"] = sc
                changed_fields.append("scope")

        if severity is not None:
            sev = severity.strip().upper()
            if sev not in ("P0", "P1", "P2"):
                raise UsageError(f"Invalid severity '{severity}'. Must be one of: P0, P1, P2")
            if sev != old.severity:
                meta["severity"] = sev
                changed_fields.append("severity")

        new_rule = old.rule
        if rule is not None:
            r = rule.strip()
            if not r:
                raise UsageError("Rule must be non-empty.")
            if r != old.rule:
                new_rule = r
                meta["rule"] = r
                changed_fields.append("rule")

        if rationale is not None:
            rat = rationale.strip()
            if rat != old.rationale:
                meta["rationale"] = rat
                changed_fields.append("rationale")

        if guard_hint is not None:
            gh = guard_hint.strip()
            if gh != old.guard_hint:
                meta["guard_hint"] = gh
                changed_fields.append("guard_hint")

        if guard_pattern is not None:
            gp = guard_pattern.strip()
            if gp != old.guard_pattern:
                meta["guard_pattern"] = gp
                changed_fields.append("guard_pattern")

        if source_ref is not None:
            sr = source_ref.strip()
            if sr != old.source_ref:
                meta["source_ref"] = sr
                changed_fields.append("source_ref")

        if trigger_when is not None:
            tw = _clean_string_list(trigger_when, redact=True)
            if tw != old.trigger_when:
                meta["trigger_when"] = tw
                changed_fields.append("trigger_when")

        if entities is not None:
            ents = _clean_string_list(entities, redact=True)
            if ents != old.entities:
                meta["entities"] = ents
                changed_fields.append("entities")

        if not changed_fields:
            return {
                "atom": None,
                "node": old.to_dict(),
                "edited": node_id,
                "changed_fields": [],
                "redacted": False,
                "snapshot_version": snap_ver,
            }

        meta["edit_fields"] = changed_fields
        res = self.add_atom(
            kind=new_type,
            content=f"Edit {node_id} ({', '.join(changed_fields)}): {new_rule}",
            scope=new_scope,
            source="manual:edit",
            device=device,
            meta=meta,
        )
        res["changed_fields"] = changed_fields
        return res

    def correct_node(
        self,
        node_id: str,
        new_content: str,
        *,
        reason: str = "",
        severity: str | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Self-correct an existing node by superseding it with a refined rule (immutable ledger)."""
        if not new_content or not new_content.strip():
            raise UsageError("Corrected content must be non-empty.")
        with self.snapshots.open_current(read_only=True) as db:
            old = db.get_node(node_id)
            if old is None:
                raise NotFoundError(f"Node not found: {node_id}", hint="run `fos recall <query>` to find node IDs")
            kind = old.type
            scope = old.scope
            old_sev = old.severity

        meta: dict[str, Any] = {
            "supersedes": node_id,
            "severity": severity or old_sev,
        }
        if reason.strip():
            meta["rationale"] = reason.strip()
        res = self.add_atom(
            kind=kind,
            content=new_content.strip(),
            scope=scope,
            source="manual:correct",
            device=device,
            meta=meta,
        )
        # Implicit PlugMem feedback: superseded node was outdated/inaccurate
        record_utility_feedback(self.data_dir, node_id, "noise", context=f"Superseded by {(res.get('node') or {}).get('id', 'correction')}")
        return res

    def graduate_node(
        self,
        node_id: str,
        *,
        guard_pattern: str | None = None,
        reason: str = "",
        device: str | None = None,
    ) -> dict[str, Any]:
        """Graduate a rule to mechanical enforcement (keeps active in `fos guard`, frees L0 wakeup tokens)."""
        with self.snapshots.open_current(read_only=True) as db:
            old = db.get_node(node_id)
            if old is None:
                raise NotFoundError(f"Node not found: {node_id}")
            kind = old.type
            scope = old.scope
            existing_pat = old.guard_pattern

        final_pat = (guard_pattern if guard_pattern is not None else existing_pat).strip()
        meta: dict[str, Any] = {
            "graduates": node_id,
            "graduate_reason": reason.strip() or "Graduated to deterministic static guard",
        }
        if final_pat:
            meta["guard_pattern"] = final_pat
        return self.add_atom(
            kind=kind,
            content=f"Graduate {node_id}: {meta['graduate_reason']}",
            scope=scope,
            source="manual:graduate",
            device=device,
            meta=meta,
        )

    def feedback_node(
        self,
        node_id: str,
        verdict: str,
        *,
        context: str = "",
    ) -> dict[str, Any]:
        """Record PlugMem utility feedback (`useful` | `noise`) for a node."""
        if verdict not in ("useful", "noise"):
            raise UsageError(f"Invalid verdict '{verdict}'. Must be 'useful' or 'noise'.")
        with self.snapshots.open_current(read_only=True) as db:
            if db.get_node(node_id) is None:
                raise NotFoundError(f"Node not found: {node_id}")
        return record_utility_feedback(self.data_dir, node_id, verdict, context=context)

    def archive_node(
        self,
        node_id: str,
        *,
        reason: str = "",
        device: str | None = None,
    ) -> dict[str, Any]:
        """Retire an obsolete/expired/already-guarded node cleanly (preserves --as-of history)."""
        with self.snapshots.open_current(read_only=True) as db:
            target = db.get_node(node_id)
            if target is None:
                raise NotFoundError(f"Node not found: {node_id}", hint="run `fos recall <query>` to find node IDs")
            scope = target.scope

        reason_clean = reason.strip() or "archived as obsolete/expired"
        return self.add_atom(
            kind="outcome",
            content=f"Archive {node_id}: {reason_clean}",
            scope=scope,
            source="manual:archive",
            device=device,
            meta={"archives": node_id, "archive_reason": reason_clean},
        )

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

    def boot(
        self,
        *,
        project: str | None = None,
        scopes: list[str] | None = None,
        budget: int = 300,
    ) -> dict[str, Any]:
        with self.snapshots.open_current(read_only=True) as db:
            return query_snapshot_boot(db, project=project, scopes=scopes, budget=budget, mode="online")

    def recall(
        self,
        query: str,
        *,
        kind: str | None = None,
        scope: str | None = None,
        scopes: list[str] | None = None,
        as_of: str | None = None,
        budget: int = 1200,
        limit: int = 20,
        cwd_stack: dict[str, str] | None = None,
        exclude_ids: set[str] | list[str] | None = None,
    ) -> dict[str, Any]:
        usage = load_usage_stats(self.data_dir)
        with self.snapshots.open_current(read_only=True) as db:
            res = query_snapshot_recall(
                db,
                query,
                embedder=self.providers.embedder,
                reranker=self.providers.reranker,
                kind=kind,
                scope=scope,
                scopes=scopes,
                as_of=as_of,
                budget=budget,
                limit=limit,
                mode="online",
                usage_stats=usage,
                cwd_stack=cwd_stack,
                exclude_ids=exclude_ids,
            )
        if as_of is None and res.get("results"):
            record_recall_hits(self.data_dir, [str(r["id"]) for r in res["results"] if r.get("id")])
        return res

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

    def mirror(
        self,
        proposal: str,
        *,
        scope: str | None = None,
        scopes: list[str] | None = None,
        cwd_stack: dict[str, str] | None = None,
    ) -> dict[str, Any]:
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
                scopes=scopes,
                cwd_stack=cwd_stack,
                mode="online",
            )

    def export_compact_snapshot(self) -> dict[str, Any]:
        with self.snapshots.open_current(read_only=True) as db:
            return db.export_compact_bundle()

    def dashboard_summary(self, *, ledger_limit: int = 80) -> dict[str, Any]:
        """Aggregate full observability snapshot for the fosd Web UI."""
        with self.snapshots.open_current(read_only=True) as db:
            all_nodes = db.list_nodes(include_inactive=True)
            all_edges = db.all_edges()
            meta = db.all_meta()
            has_vec = db.has_sqlite_vec

        atoms = self.ledger.load_all_atoms()
        golden = self.ledger.load_eval_cases()
        pinned_ids: set[str] = set()
        for c in golden:
            if c.pinned:
                if c.node_id:
                    pinned_ids.add(c.node_id)
                pinned_ids.update(c.expected_ids)

        by_status: dict[str, int] = {"active": 0, "superseded": 0, "archived": 0}
        by_type: dict[str, int] = {}
        by_scope: dict[str, int] = {}
        by_severity: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0}

        usage_stats = load_usage_stats(self.data_dir)
        active_rule_tokens: list[int] = []
        bloated_nodes = 0
        pointer_nodes = 0
        drifted_nodes = 0
        broken_ref_nodes = 0
        guard_rules_count = 0
        graduated_nodes = 0
        code_patterns_count = 0

        node_dicts: list[dict[str, Any]] = []
        for n in all_nodes:
            by_status[n.status] = by_status.get(n.status, 0) + 1
            fresh = check_source_freshness(n.source_ref, n.source_hash) if n.status == "active" else "untracked"
            if n.status == "active":
                by_type[n.type] = by_type.get(n.type, 0) + 1
                by_scope[n.scope] = by_scope.get(n.scope, 0) + 1
                by_severity[n.severity] = by_severity.get(n.severity, 0) + 1
                rt = estimate_tokens(n.rule)
                active_rule_tokens.append(rt)
                if rt > 100:
                    bloated_nodes += 1
                if n.source_ref:
                    pointer_nodes += 1
                if n.guard_pattern:
                    guard_rules_count += 1
                if n.graduated:
                    graduated_nodes += 1
                if n.compat.get("pattern") == "code_exemplar":
                    code_patterns_count += 1
                if fresh == "drifted":
                    drifted_nodes += 1
                elif fresh == "broken_ref":
                    broken_ref_nodes += 1
            ust = usage_stats.get(n.id) or {}
            d = n.to_dict()
            d["pinned"] = n.id in pinned_ids
            d["freshness"] = fresh
            d["recall_hits"] = int(ust.get("hits", 0))
            d["useful_count"] = int(ust.get("useful", 0))
            d["noise_count"] = int(ust.get("noise", 0))
            d["guard_blocks"] = int(ust.get("guard_blocks", 0))
            d["utility_score"] = node_utility_score(ust)
            node_dicts.append(d)

        # Rejected snapshots count for regression gate visibility
        rejected_dir = self.snapshots.rejected_dir
        rejected_snaps = sorted(p.stem for p in rejected_dir.glob("v*.db")) if rejected_dir.exists() else []

        # Pending proposals count (outside immutable ledger/)
        from fos.proposals import ProposalStore

        pending_proposals = sum(1 for p in ProposalStore(self.data_dir).load_all() if p.status == "pending")
        active_cnt = len(active_rule_tokens)
        avg_rule_tokens = round(sum(active_rule_tokens) / active_cnt, 1) if active_cnt else 0.0
        pointer_pct = round(100.0 * pointer_nodes / active_cnt, 1) if active_cnt else 0.0
        utility_summary = summarize_utility_analytics(self.data_dir, all_nodes)

        recent_atoms = [a.to_dict() for a in reversed(atoms[-ledger_limit:])]
        return {
            "snapshot_version": self.snapshots.current_version(),
            "history": self.snapshots.history(),
            "rejected_snapshots": rejected_snaps,
            "sqlite_vec": has_vec,
            "embedder": getattr(self.providers.embedder, "model_name", "local_hash"),
            "meta": meta,
            "counts": {
                "total_nodes": len(all_nodes),
                "active_nodes": by_status["active"],
                "superseded_nodes": by_status["superseded"],
                "archived_nodes": by_status["archived"],
                "ledger_atoms": len(atoms),
                "golden_cases": len(golden),
                "pinned_nodes": len(pinned_ids),
                "edges": len(all_edges),
                "pending_proposals": pending_proposals,
            },
            "hygiene": {
                "avg_rule_tokens": avg_rule_tokens,
                "bloated_nodes": bloated_nodes,
                "pointer_nodes": pointer_nodes,
                "pointer_coverage_pct": pointer_pct,
                "drifted_nodes": drifted_nodes,
                "broken_ref_nodes": broken_ref_nodes,
                "guard_rules_count": guard_rules_count,
                "graduated_nodes": graduated_nodes,
                "code_patterns_count": code_patterns_count,
            },
            "utility": utility_summary,
            "distributions": {
                "by_status": by_status,
                "by_type": by_type,
                "by_scope": by_scope,
                "by_severity": by_severity,
            },
            "scopes": sorted(by_scope.keys(), key=lambda s: (0 if s == "global" else 1, s)),
            "nodes": node_dicts,
            "edges": [e.to_dict() for e in all_edges],
            "recent_atoms": recent_atoms,
        }

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
                lines.append(f"**{n.rule}**")
                lines.append("")
                if n.rationale:
                    lines.append(f"- **原因**: {n.rationale}")
                if n.guard_hint:
                    lines.append(f"- **守卫建议 (Guard)**: {n.guard_hint}")
                if n.trigger_when:
                    lines.append(f"- **触发场景**: {', '.join(n.trigger_when)}")
                dates_str = f" ({', '.join(n.seen_dates)})" if n.seen_dates else ""
                lines.append(f"- **复发**: Seen {n.seen_count}×{dates_str}")
                lines.append(f"- **有效期**: `{n.valid_from}` → `{n.valid_until or 'present'}`")
                if n.superseded_by:
                    lines.append(f"- **已替代为**: `{n.superseded_by}`")
                if n.evidence_ids:
                    lines.append(f"- **证据**: {', '.join(f'`{e}`' for e in n.evidence_ids)}")
                lines.append("")
            fpath = out / fname
            fpath.write_text("\n".join(lines), encoding="utf-8")
            written.append(str(fpath))

        readme = out / "README.md"
        readme.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
        written.append(str(readme))
        return {"snapshot_version": snap_ver, "out_dir": str(out), "files": written, "node_count": len(nodes)}
