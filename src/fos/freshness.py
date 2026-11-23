"""Deterministic anti-rot provenance, PlugMem utility telemetry, and Titans surprise gating.

Unified sidecar primitives (outside immutable `ledger/`):
- Source-file freshness verification (`fresh` | `drifted` | `broken_ref` | `untracked`).
- PlugMem-style Bayesian utility tracking (`state.db` table `usage_stats`: hits, useful, noise, guard_blocks).
- Titans-style surprise & conflict scoring on incoming memories (`redundant` | `conflict` | `novel`).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Literal

from fos.models import CompiledNode, now_iso
from fos.state_db import StateDB
from fos.tokens import extract_terms

FreshnessState = Literal["fresh", "drifted", "broken_ref", "untracked"]
FeedbackVerdict = Literal["useful", "noise", "guard_block"]
SurpriseClass = Literal["redundant", "conflict", "novel"]

_BOILERPLATE_BLOCK_RE = re.compile(
    r"<!--\s*(context7|rtk-owned)\s*-->.*?<!--\s*(/context7|context7|/rtk-owned|rtk-owned)\s*-->",
    re.DOTALL | re.IGNORECASE,
)

_NEG_POLARITY_RE = re.compile(
    r"(?:禁止|不要|不得|不可|严禁|切勿|避免|不能|放弃|拒绝|无需|不必|\b(?:never|avoid|must\s+not|do\s+not|don't|forbid|ban|no\s+longer)\b)",
    re.IGNORECASE,
)


def compute_doc_digest(path: Path) -> str:
    """Compute the same 16-char SHA-256 content digest used by `ingest.py --from docs`."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    cleaned = _BOILERPLATE_BLOCK_RE.sub("", raw).strip()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]


def parse_source_ref_path(source_ref: str) -> tuple[Path | None, str]:
    """Split `/abs/path/file.md#section` or `/abs/path/File.kt::Symbol` into `(Path, anchor)`."""
    ref = (source_ref or "").strip()
    if not ref or ref.startswith(("git:", "http://", "https://", "ledger:")):
        return None, ""
    if "::" in ref:
        path_part, _, symbol = ref.partition("::")
        p = Path(path_part).expanduser()
        if not p.is_absolute():
            return None, symbol.strip()
        return p, symbol.strip()
    path_part, _, anchor = ref.partition("#")
    p = Path(path_part).expanduser()
    if not p.is_absolute():
        return None, anchor
    return p, anchor


def check_source_freshness(source_ref: str, source_hash: str = "") -> FreshnessState:
    """Deterministically verify whether a local `source_ref` doc or code symbol still exists and matches its hash."""
    ref = (source_ref or "").strip()
    if "::" in ref:
        from fos.code_pattern import extract_code_symbol, parse_symbol_ref

        sym_path, symbol = parse_symbol_ref(ref)
        if sym_path is None:
            return "untracked"
        if not sym_path.is_file():
            return "broken_ref"
        extracted = extract_code_symbol(sym_path, symbol)
        if not extracted.get("found"):
            return "broken_ref"
        if not source_hash:
            return "fresh"
        return "fresh" if extracted.get("hash") == source_hash else "drifted"

    path, _anchor = parse_source_ref_path(ref)
    if path is None:
        return "untracked"
    if not path.is_file():
        return "broken_ref"
    if not source_hash:
        return "fresh"
    try:
        current = compute_doc_digest(path)
    except OSError:
        return "broken_ref"
    return "fresh" if current == source_hash else "drifted"


def freshness_multiplier(state: str) -> float:
    """Score multiplier applied during recall so stale/broken pointers never outrank fresh truth."""
    if state == "broken_ref":
        return 0.35
    if state == "drifted":
        return 0.75
    return 1.0


def resolve_legacy_source_ref(scope: str, source: str, meta: dict[str, Any]) -> tuple[str, str]:
    """Backfill `(source_ref, source_hash)` for pre-existing `hindsight:docs:*` ledger atoms."""
    if meta.get("source_ref"):
        return str(meta["source_ref"]), str(meta.get("source_hash") or "")
    doc_file = str(meta.get("doc_file") or "").strip()
    section = str(meta.get("doc_section") or "").strip()[:60]
    if not doc_file:
        ev_ref = str(meta.get("evidence_ref") or "").strip()
        if ev_ref.startswith("doc:"):
            file_part, _, sec_part = ev_ref[4:].partition("#")
            doc_file = file_part.strip()
            section = section or sec_part.strip()[:60]
    if not doc_file:
        return "", ""
    home = Path.home()
    search_dirs: list[Path] = []
    if scope == "global":
        if "gemini" in source:
            search_dirs.append(home / ".gemini")
        if "codex" in source:
            search_dirs.append(home / ".codex")
        if "agent" in source or "pi" in source:
            search_dirs.append(home / ".pi" / "agent")
        if "claude" in source:
            search_dirs.extend([home / ".claude", home / ".claude" / "rules"])
    else:
        git_root = Path(os.environ.get("FOS_GIT_ROOT") or (home / "project" / "clive"))
        repo = git_root / scope
        search_dirs.extend([repo, repo / "docs", repo / "docs" / "agents"])

    target_lower = doc_file.lower()
    for d in search_dirs:
        if not d.is_dir():
            continue
        direct = d / doc_file
        cand: Path | None = direct if direct.is_file() else None
        if cand is None:
            for p in d.iterdir():
                if p.is_file() and p.name.lower() == target_lower:
                    cand = p
                    break
        if cand is not None:
            ref = f"{cand.resolve()}#{section}" if section else str(cand.resolve())
            try:
                digest = compute_doc_digest(cand)
            except OSError:
                digest = ""
            return ref, digest
    return "", ""


# ---------------------------------------------------------------------------
# PlugMem-Inspired Utility Feedback Loop (SQLite `state.db` table `usage_stats`)
# ---------------------------------------------------------------------------


def load_usage_stats(data_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        with StateDB(Path(data_dir)) as db:
            return db.load_usage_stats()
    except Exception:
        return {}


def node_utility_score(entry: dict[str, Any] | None) -> float:
    """Bayesian Beta(1,1) smoothed utility score in (0, 1); neutral 0.50 when unrated."""
    if not entry:
        return 0.5
    useful = max(0, int(entry.get("useful", 0)))
    noise = max(0, int(entry.get("noise", 0)))
    return round((useful + 1.0) / (useful + noise + 2.0), 4)


def utility_multiplier(entry: dict[str, Any] | None) -> float:
    """Bounded ranking multiplier in [0.75, 1.25] (1.0 neutral for unrated nodes)."""
    score = node_utility_score(entry)
    return 0.75 + 0.50 * score


def record_recall_hits(data_dir: Path, node_ids: list[str]) -> None:
    """Increment lightweight SQLite recall counters (outside immutable ledger_atoms)."""
    if not node_ids:
        return
    ts = now_iso()
    try:
        with StateDB(Path(data_dir)) as db:
            for nid in node_ids:
                entry = db.get_usage_entry(nid) or {"hits": 0, "useful": 0, "noise": 0, "guard_blocks": 0}
                entry["hits"] = int(entry.get("hits", 0)) + 1
                entry["last_hit_at"] = ts
                entry["utility_score"] = node_utility_score(entry)
                db.upsert_usage_entry(nid, entry, commit=False)
            db.conn.commit()
    except Exception:
        pass


def record_utility_feedback(
    data_dir: Path,
    node_id: str,
    verdict: str,
    *,
    context: str = "",
) -> dict[str, Any]:
    """Record explicit/implicit PlugMem utility feedback (`useful` | `noise` | `guard_block`)."""
    ts = now_iso()
    with StateDB(Path(data_dir)) as db:
        entry = db.get_usage_entry(node_id) or {"hits": 0, "useful": 0, "noise": 0, "guard_blocks": 0}

        if verdict == "useful":
            entry["useful"] = int(entry.get("useful", 0)) + 1
        elif verdict == "noise":
            entry["noise"] = int(entry.get("noise", 0)) + 1
        elif verdict == "guard_block":
            entry["guard_blocks"] = int(entry.get("guard_blocks", 0)) + 1
            entry["useful"] = int(entry.get("useful", 0)) + 1
        else:
            raise ValueError(f"Invalid feedback verdict: {verdict}")

        entry["last_feedback_at"] = ts
        entry["utility_score"] = node_utility_score(entry)
        if context.strip():
            notes = list(entry.get("recent_notes") or [])
            notes.append({"ts": ts, "verdict": verdict, "context": context.strip()[:160]})
            entry["recent_notes"] = notes[-5:]
        db.upsert_usage_entry(node_id, entry, commit=True)
    return {"node_id": node_id, "verdict": verdict, **entry}


def summarize_utility_analytics(
    data_dir: Path,
    active_nodes: list[CompiledNode],
) -> dict[str, Any]:
    """Build PlugMem utility analytics summary for dashboard & governance."""
    stats = load_usage_stats(data_dir)
    active_map = {n.id: n for n in active_nodes if n.status == "active"}

    total_hits = 0
    total_useful = 0
    total_noise = 0
    total_guard_blocks = 0
    rated_scores: list[float] = []
    top_useful: list[dict[str, Any]] = []
    low_utility: list[dict[str, Any]] = []

    for nid, node in active_map.items():
        ent = stats.get(nid) or {}
        hits = int(ent.get("hits", 0))
        useful = int(ent.get("useful", 0))
        noise = int(ent.get("noise", 0))
        gblocks = int(ent.get("guard_blocks", 0))
        util = node_utility_score(ent)
        total_hits += hits
        total_useful += useful
        total_noise += noise
        total_guard_blocks += gblocks
        if useful + noise > 0:
            rated_scores.append(util)
        if useful > 0 or gblocks > 0:
            top_useful.append(
                {
                    "id": nid,
                    "scope": node.scope,
                    "rule": node.rule[:90],
                    "hits": hits,
                    "useful": useful,
                    "noise": noise,
                    "guard_blocks": gblocks,
                    "utility_score": util,
                }
            )
        # High-hit / low-utility noise or repeatedly flagged noise
        if (hits >= 5 and util < 0.38) or (noise >= 2 and util < 0.4):
            low_utility.append(
                {
                    "id": nid,
                    "scope": node.scope,
                    "rule": node.rule[:90],
                    "hits": hits,
                    "useful": useful,
                    "noise": noise,
                    "utility_score": util,
                }
            )

    top_useful.sort(key=lambda x: (-x["useful"], -x["utility_score"], -x["hits"]))
    low_utility.sort(key=lambda x: (x["utility_score"], -x["noise"], -x["hits"]))
    avg_util = round(sum(rated_scores) / len(rated_scores), 3) if rated_scores else 0.5
    return {
        "total_recall_hits": total_hits,
        "total_useful": total_useful,
        "total_noise": total_noise,
        "total_guard_blocks": total_guard_blocks,
        "rated_nodes": len(rated_scores),
        "avg_utility": avg_util,
        "low_utility_count": len(low_utility),
        "top_useful": top_useful[:6],
        "low_utility_nodes": low_utility[:6],
    }


# ---------------------------------------------------------------------------
# Titans-Inspired Surprise & Conflict Gating
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def evaluate_surprise(
    new_text: str,
    new_vec: list[float],
    candidates: list[tuple[CompiledNode, list[float]]],
) -> dict[str, Any]:
    """Compute Titans-style surprise score & conflict classification against active memories.

    Returns:
    - `redundant`: very high similarity (>=0.86) and same polarity (reinforce instead of duplicating)
    - `conflict`: moderate-to-high similarity (>=0.45) with opposing negation polarity (maximum surprise!)
    - `novel`: genuinely new information (`surprise = 1 - max_sim`)
    """
    if not candidates or not new_vec:
        return {
            "surprise": 1.0,
            "classification": "novel",
            "closest_node_id": None,
            "similarity": 0.0,
        }

    new_neg = bool(_NEG_POLARITY_RE.search(new_text))
    new_terms = extract_terms(new_text)
    best_sim = -1.0
    best_node: CompiledNode | None = None
    conflict_node: CompiledNode | None = None
    conflict_sim = 0.0

    for node, vec in candidates:
        cos = _cosine(new_vec, vec)
        n_terms = extract_terms(node.rule)
        shared_terms = new_terms & n_terms
        dice = (2.0 * len(shared_terms) / max(1, len(new_terms) + len(n_terms))) if (new_terms and n_terms) else 0.0
        sim = max(cos, dice)
        if sim > best_sim:
            best_sim = sim
            best_node = node
        node_neg = bool(_NEG_POLARITY_RE.search(node.rule))
        is_same_topic = (cos >= 0.75) or (dice >= 0.35) or (max(cos, dice) >= 0.55 and len(shared_terms) >= 3)
        if (new_neg != node_neg) and is_same_topic and sim > conflict_sim:
            conflict_sim = sim
            conflict_node = node

    best_sim = max(0.0, min(1.0, best_sim))
    if conflict_node is not None:
        return {
            "surprise": 1.0,
            "classification": "conflict",
            "closest_node_id": conflict_node.id,
            "similarity": round(conflict_sim, 4),
        }
    if best_node is not None and best_sim >= 0.86:
        return {
            "surprise": round(1.0 - best_sim, 4),
            "classification": "redundant",
            "closest_node_id": best_node.id,
            "similarity": round(best_sim, 4),
        }
    return {
        "surprise": round(1.0 - best_sim, 4),
        "classification": "novel",
        "closest_node_id": best_node.id if best_node else None,
        "similarity": round(best_sim, 4),
    }
