"""Deterministic anti-rot provenance & freshness primitives for FlowOS.

Inspired by `stalebrain` (file-drift verification) and Claude Code / `claude-mem`
(pointer-first progressive disclosure):
- Compiled L1 cards stay compact (summary + `source_ref` pointer).
- Drift is checked deterministically on local files without LLM cost.
- Recall hit counters live in sidecar `usage_stats.json` (outside immutable `ledger/`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from fos.models import now_iso

FreshnessState = Literal["fresh", "drifted", "broken_ref", "untracked"]

_BOILERPLATE_BLOCK_RE = re.compile(
    r"<!--\s*(context7|rtk-owned)\s*-->.*?<!--\s*(/context7|context7|/rtk-owned|rtk-owned)\s*-->",
    re.DOTALL | re.IGNORECASE,
)


def compute_doc_digest(path: Path) -> str:
    """Compute the same 16-char SHA-256 content digest used by `ingest.py --from docs`."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    cleaned = _BOILERPLATE_BLOCK_RE.sub("", raw).strip()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]


def parse_source_ref_path(source_ref: str) -> tuple[Path | None, str]:
    """Split `/abs/path/file.md#section` into `(Path, section)` if it is a local file pointer."""
    ref = (source_ref or "").strip()
    if not ref or ref.startswith(("git:", "http://", "https://", "ledger:")):
        return None, ""
    path_part, _, anchor = ref.partition("#")
    p = Path(path_part).expanduser()
    if not p.is_absolute():
        return None, anchor
    return p, anchor


def check_source_freshness(source_ref: str, source_hash: str = "") -> FreshnessState:
    """Deterministically verify whether a local `source_ref` file still exists and matches its hash."""
    path, _anchor = parse_source_ref_path(source_ref)
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
    if not doc_file:
        return "", ""
    section = str(meta.get("doc_section") or "").strip()[:60]
    home = Path.home()
    candidates: list[Path] = []
    if scope == "global":
        if "gemini" in source:
            candidates.append(home / ".gemini" / doc_file)
        if "codex" in source:
            candidates.append(home / ".codex" / doc_file)
        if "agent" in source or "pi" in source:
            candidates.append(home / ".pi" / "agent" / doc_file)
        if "claude" in source:
            candidates.extend([home / ".claude" / doc_file, home / ".claude" / "rules" / doc_file])
    else:
        git_root = Path(os.environ.get("FOS_GIT_ROOT") or (home / "project" / "clive"))
        repo = git_root / scope
        candidates.extend(
            [
                repo / doc_file,
                repo / "docs" / doc_file,
                repo / "docs" / "agents" / doc_file,
            ]
        )
    for cand in candidates:
        if cand.is_file():
            ref = f"{cand.resolve()}#{section}" if section else str(cand.resolve())
            try:
                digest = compute_doc_digest(cand)
            except OSError:
                digest = ""
            return ref, digest
    return "", ""


def load_usage_stats(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = Path(data_dir) / "usage_stats.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def record_recall_hits(data_dir: Path, node_ids: list[str]) -> None:
    """Increment lightweight sidecar recall counters (outside immutable ledger/)."""
    if not node_ids:
        return
    path = Path(data_dir) / "usage_stats.json"
    stats = load_usage_stats(data_dir)
    ts = now_iso()
    for nid in node_ids:
        entry = stats.get(nid)
        if not isinstance(entry, dict):
            entry = {"hits": 0, "last_hit_at": ts}
        entry["hits"] = int(entry.get("hits", 0)) + 1
        entry["last_hit_at"] = ts
        stats[nid] = entry
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(stats, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
