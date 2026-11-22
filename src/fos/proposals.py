"""Cross-project preference & governance Proposal Inbox for FlowOS.

First-principles separation:
- Machine scans produce typed, auditable Proposals stored in `<brain_data_dir>/proposals.jsonl`
  (never polluting immutable `ledger/` until human approval).
- Explicit 4-state lifecycle: `pending` -> `accepted` | `dismissed` | `expired`.
- Accepting a proposal writes a normal `manual:proposal` LedgerAtom via BrainEngine
  (with `SUPPORTS` or `SUPERSEDES` graph edges), keeping `ledger/` the single source of truth.
- Dismissing records a stable fingerprint + evidence repo set so the scanner never nags twice
  unless a brand-new owned repository introduces fresh corroborating evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fos.engine import BrainEngine, build_l0_line, node_embedding_text
from fos.errors import ExitCode, FosError, NotFoundError, UsageError
from fos.models import CompiledNode, VALID_KINDS, normalize_severity, now_iso, parse_timestamp
from fos.providers import HeuristicReasoner, Reasoner
from fos.tokens import estimate_tokens as count_tokens, tokenize_for_fts
from fos.ulid import new_ulid


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)

ProposalKind = Literal["promote_global", "merge_dedup", "retire_stale"]
ProposalStatus = Literal["pending", "accepted", "dismissed", "expired"]

VALID_PROPOSAL_KINDS: tuple[str, ...] = ("promote_global", "merge_dedup", "retire_stale")
VALID_PROPOSAL_STATUSES: tuple[str, ...] = ("pending", "accepted", "dismissed", "expired")

L0_GLOBAL_BUDGET = 300
PROPOSAL_EXPIRY_DAYS = 30
MAX_PENDING_PROPOSALS = 10

_STOP_TOKENS = {
    "the", "and", "for", "with", "that", "this", "from", "must", "only", "when", "into",
    "use", "using", "not", "all", "are", "was", "were", "can", "may", "should", "avoid",
    "必须", "禁止", "不要", "使用", "进行", "通过", "以及", "所有", "保持", "确保", "一个",
    "我们", "如果", "需要", "可以", "提供", "支持", "实现", "处理", "用于", "作为", "相关",
}


class ProposalStateError(FosError):
    """Structured enumerable error for proposal state/budget/evidence guards."""

    exit_code = ExitCode.ERROR
    error_code = "PROPOSAL_STATE_ERROR"


@dataclass
class Proposal:
    """Unified governance recommendation (cross-repo promotion, dedup merge, or stale retirement)."""

    id: str
    kind: str  # promote_global | merge_dedup | retire_stale
    status: str = "pending"  # pending | accepted | dismissed | expired
    fingerprint: str = ""
    title: str = ""
    proposed_type: str = "principle"  # principle | decision | lesson
    proposed_scope: str = "global"
    proposed_severity: str = "P1"  # P0 | P1 | P2
    proposed_rule: str = ""
    proposed_rationale: str = ""
    proposed_guard_hint: str = ""
    proposed_trigger_when: list[str] = field(default_factory=list)
    evidence_repos: list[str] = field(default_factory=list)
    evidence_nodes: list[dict[str, Any]] = field(default_factory=list)
    evidence_manifests: list[dict[str, Any]] = field(default_factory=list)
    target_node_ids: list[str] = field(default_factory=list)
    estimated_l0_delta: int = 0
    confidence: float = 0.8
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    resolved_at: str | None = None
    result_node_id: str | None = None
    dismiss_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Proposal:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class ProposalStore:
    """Append-only JSONL proposal log compacted by latest ID (outside immutable ledger/)."""

    def __init__(self, brain_data_dir: Path) -> None:
        self.brain_data_dir = Path(brain_data_dir)
        self.path = self.brain_data_dir / "proposals.jsonl"
        self.cache_path = self.brain_data_dir / "proposal_scan_cache.json"

    def save(self, proposal: Proposal) -> Proposal:
        self.brain_data_dir.mkdir(parents=True, exist_ok=True)
        proposal.updated_at = now_iso()
        line = json.dumps(proposal.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return proposal

    def load_all(self) -> list[Proposal]:
        if not self.path.exists():
            return []
        by_id: dict[str, Proposal] = {}
        order: list[str] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                try:
                    p = Proposal.from_dict(json.loads(s))
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue
                if p.id not in by_id:
                    order.append(p.id)
                by_id[p.id] = p
        return [by_id[pid] for pid in order]

    def get(self, proposal_id: str) -> Proposal | None:
        for p in self.load_all():
            if p.id == proposal_id:
                return p
        return None

    def load_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_cache(self, data: dict[str, Any]) -> None:
        self.brain_data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.cache_path)


# ---------------------------------------------------------------------------
# Stage 1A: Owned Repository Discovery (excludes 3rd-party clones)
# ---------------------------------------------------------------------------


def _current_git_identity() -> tuple[str, str]:
    """Read local git user.email and user.name for owned-repo matching."""
    email = ""
    name = ""
    for key in ("user.email", "user.name"):
        try:
            proc = subprocess.run(
                ["git", "config", "--get", key],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            val = proc.stdout.strip().lower()
            if key == "user.email":
                email = val
            else:
                name = val
        except OSError:
            pass
    return email, name


def _extract_github_handle(email: str) -> str:
    # e.g. 38341571+taoaozw@users.noreply.github.com -> taoaozw
    m = re.search(r"\+([a-z0-9_-]+)@users\.noreply\.github\.com", email)
    if m:
        return m.group(1)
    if "@" in email:
        return email.split("@", 1)[0]
    return ""


def _is_owned_repo(
    repo: Path,
    *,
    owner_email: str,
    owner_name: str,
    owner_handle: str,
    max_age_days: int = 365,
) -> bool:
    """Return True if `repo` belongs to the user (origin URL match or recent authored commits)."""
    # 1. Check origin remote URL first (fast & unambiguous for user's own GitHub repos)
    try:
        rem = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        remote_url = rem.stdout.strip().lower()
    except OSError:
        remote_url = ""

    remote_owned = bool(
        remote_url
        and any(
            tok and f"/{tok}/" in remote_url or (tok and f":{tok}/" in remote_url)
            for tok in (owner_handle, owner_name)
            if len(tok) >= 3
        )
    )

    # 2. Check recent commits (within max_age_days) & author identity
    try:
        log = subprocess.run(
            ["git", "-C", str(repo), "log", "-n", "15", "--format=%ct|%ae|%an"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except OSError:
        return False

    if log.returncode != 0 or not log.stdout.strip():
        return False

    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp())
    latest_ts = 0
    user_commits = 0
    total_commits = 0
    for line in log.stdout.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) < 3:
            continue
        try:
            cts = int(parts[0])
        except ValueError:
            continue
        total_commits += 1
        latest_ts = max(latest_ts, cts)
        ae = parts[1].strip().lower()
        an = parts[2].strip().lower()
        if (
            (owner_email and ae == owner_email)
            or (owner_handle and owner_handle in ae)
            or (owner_name and len(owner_name) >= 2 and an == owner_name)
        ):
            user_commits += 1

    if latest_ts < cutoff_ts:
        return False
    if remote_owned:
        return True
    # No matching remote (e.g. local-only or private org repo): require user authored >= 30% of recent commits
    return total_commits > 0 and user_commits >= max(1, int(total_commits * 0.3))


def discover_owned_repos(
    root_dir: Path | None = None,
    *,
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
    max_age_days: int = 365,
) -> list[Path]:
    """Discover user-owned git repositories under `root_dir`, skipping third-party clones."""
    if root_dir is None:
        env_root = os.environ.get("FOS_SCAN_ROOT") or os.environ.get("FOS_GIT_ROOT")
        if env_root:
            root_dir = Path(env_root)
        else:
            default_clive = Path.home() / "project" / "clive"
            root_dir = default_clive if default_clive.is_dir() else Path.cwd().parent

    base = Path(root_dir).expanduser().resolve()
    if not base.exists():
        return []

    allow = {x.strip().lower() for x in (allowlist or []) if x.strip()}
    deny = {x.strip().lower() for x in (denylist or []) if x.strip()}
    owner_email, owner_name = _current_git_identity()
    owner_handle = _extract_github_handle(owner_email)

    candidates: list[Path] = []
    if (base / ".git").exists():
        candidates.append(base)
    else:
        for child in sorted(base.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and (child / ".git").exists():
                candidates.append(child)

    owned: list[Path] = []
    for repo in candidates:
        name = repo.name.lower()
        if name in deny:
            continue
        # Skip accidental duplicate copies like "zhijiang 2"
        if " " in repo.name:
            continue
        if allow:
            if name in allow:
                owned.append(repo)
            continue
        if _is_owned_repo(
            repo,
            owner_email=owner_email,
            owner_name=owner_name,
            owner_handle=owner_handle,
            max_age_days=max_age_days,
        ):
            owned.append(repo)
    return owned


# ---------------------------------------------------------------------------
# Stage 1B: Lightweight Manifest Fingerprinting
# ---------------------------------------------------------------------------


def _inspect_package_json(path: Path, tags: set[str]) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    deps: dict[str, Any] = {}
    for k in ("dependencies", "devDependencies", "peerDependencies"):
        sub = data.get(k)
        if isinstance(sub, dict):
            deps.update(sub)
    dep_keys = {k.lower() for k in deps}
    if "react" in dep_keys:
        tags.add("React")
    if "typescript" in dep_keys:
        tags.add("TypeScript")
    if "vite" in dep_keys:
        tags.add("Vite")
    if "tailwindcss" in dep_keys:
        tw_ver = str(deps.get("tailwindcss", ""))
        tags.add("Tailwind CSS v4" if "4" in tw_ver else "Tailwind CSS")
    if "@base-ui/react" in dep_keys or "@base-ui-components/react" in dep_keys:
        tags.add("Base UI (shadcn)")
    if "@tanstack/react-query" in dep_keys:
        tags.add("TanStack Query")
    if "@tabler/icons-react" in dep_keys:
        tags.add("Tabler Icons")
    if "sonner" in dep_keys:
        tags.add("Sonner")
    if "next" in dep_keys:
        tags.add("Next.js")


def scan_repo_manifests(repos: list[Path]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Extract normalized tech-stack tags from root/frontend manifests of owned repos."""
    per_repo: list[dict[str, Any]] = []
    tag_to_repos: dict[str, list[str]] = defaultdict(list)

    for repo in repos:
        scope = repo.name.lower()
        tags: set[str] = set()

        # Frontend manifests (root, web/, frontend/, web/console/)
        for sub in ("", "web", "frontend", "web/console"):
            sub_dir = repo / sub if sub else repo
            pkg = sub_dir / "package.json"
            if pkg.is_file():
                _inspect_package_json(pkg, tags)
            comp = sub_dir / "components.json"
            if comp.is_file():
                tags.add("shadcn/ui")
                try:
                    cdata = json.loads(comp.read_text(encoding="utf-8", errors="replace"))
                    style = str(cdata.get("style") or "")
                    if "base" in style.lower():
                        tags.add("shadcn Base UI")
                except (OSError, json.JSONDecodeError):
                    pass
            if (sub_dir / "bun.lock").is_file() or (sub_dir / "bun.lockb").is_file():
                tags.add("Bun")

        # Go manifests
        gomod = repo / "go.mod"
        if gomod.is_file():
            tags.add("Go")
            try:
                gtxt = gomod.read_text(encoding="utf-8", errors="replace")
                if "modernc.org/sqlite" in gtxt:
                    tags.add("modernc.org/sqlite (无 CGO)")
                if "golang.org/x/sync" in gtxt:
                    tags.add("Go errgroup")
            except OSError:
                pass

        # Python manifests
        pyproj = repo / "pyproject.toml"
        if pyproj.is_file():
            tags.add("Python")
            if (repo / "uv.lock").is_file():
                tags.add("uv")
            try:
                ptxt = pyproj.read_text(encoding="utf-8", errors="replace").lower()
                if "fastapi" in ptxt:
                    tags.add("FastAPI")
                if "pytest" in ptxt:
                    tags.add("pytest")
            except OSError:
                pass

        # Kotlin / Gradle manifests
        if (repo / "build.gradle.kts").is_file() or (repo / "settings.gradle.kts").is_file():
            tags.add("Kotlin/Gradle")

        # Skill bundle / CLI-first indicator
        if any(repo.glob("**/SKILL.md")):
            tags.add("CLI + Skill 交付")

        if tags:
            sorted_tags = sorted(tags)
            per_repo.append({"repo": scope, "stack_tags": sorted_tags})
            for t in sorted_tags:
                tag_to_repos[t].append(scope)

    recurring_tags = {t: sorted(set(rs)) for t, rs in tag_to_repos.items() if len(set(rs)) >= 2}
    return per_repo, recurring_tags


# ---------------------------------------------------------------------------
# Stage 1C: Cross-Repo Card Clustering & Global L0 Overlap Detection
# ---------------------------------------------------------------------------


def _meaningful_tokens(node: CompiledNode) -> set[str]:
    text = f"{node.rule} {' '.join(node.entities)} {' '.join(node.trigger_when)}"
    toks = [t for t in tokenize_for_fts(text).split() if len(t) >= 2 and t not in _STOP_TOKENS]
    return set(toks)


def _node_similarity(
    a: CompiledNode,
    b: CompiledNode,
    emb_map: dict[str, list[float]],
    tok_map: dict[str, set[str]],
) -> float:
    ta, tb = tok_map[a.id], tok_map[b.id]
    jacc = len(ta & tb) / max(1, len(ta | tb)) if (ta and tb) else 0.0
    ea, eb = emb_map.get(a.id), emb_map.get(b.id)
    cos = _cosine_similarity(ea, eb) if (ea and eb) else 0.0
    return max(jacc, 0.55 * cos + 0.45 * jacc)


def find_cross_repo_clusters(
    nodes: list[CompiledNode],
    emb_map: dict[str, list[float]],
    *,
    allowed_scopes: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Cluster active repo-scoped nodes across >=2 distinct repos by semantic/entity overlap."""
    scoped = [
        n
        for n in nodes
        if n.status == "active"
        and n.scope != "global"
        and n.type in ("decision", "lesson", "principle")
        and (allowed_scopes is None or n.scope.lower() in allowed_scopes)
    ]
    tok_map = {n.id: _meaningful_tokens(n) for n in scoped}

    # Build adjacency across DIFFERENT scopes (avoid loose CJK bigram chaining)
    adj: dict[str, set[str]] = defaultdict(set)
    for i in range(len(scoped)):
        for j in range(i + 1, len(scoped)):
            a, b = scoped[i], scoped[j]
            if a.scope == b.scope:
                continue
            sim = _node_similarity(a, b, emb_map, tok_map)
            if sim >= 0.36:
                adj[a.id].add(b.id)
                adj[b.id].add(a.id)

    # Connected components spanning >= 2 repos
    by_id = {n.id: n for n in scoped}
    visited: set[str] = set()
    clusters: list[dict[str, Any]] = []
    for n in scoped:
        if n.id in visited or n.id not in adj:
            continue
        comp: list[CompiledNode] = []
        stack = [n.id]
        visited.add(n.id)
        while stack:
            cur = stack.pop()
            comp.append(by_id[cur])
            for nxt in adj[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        repos = sorted({c.scope for c in comp})
        if len(repos) >= 2:
            # Cap cluster size to top 6 highest-severity nodes for clean LLM input
            comp_sorted = sorted(comp, key=lambda x: (x.severity, -x.seen_count, x.id))[:6]
            clusters.append(
                {
                    "repos": repos,
                    "nodes": [
                        {
                            "id": c.id,
                            "scope": c.scope,
                            "type": c.type,
                            "severity": c.severity,
                            "rule": c.rule[:260],
                            "rationale": c.rationale[:160],
                            "guard_hint": c.guard_hint[:140],
                        }
                        for c in comp_sorted
                    ],
                }
            )
    clusters.sort(key=lambda c: (-len(c["repos"]), -len(c["nodes"])))
    return clusters[:8]


def find_global_overlap_clusters(
    nodes: list[CompiledNode],
    emb_map: dict[str, list[float]],
) -> list[dict[str, Any]]:
    """Detect overlapping active global principles/decisions that can be merged to save L0 tokens."""
    globals_ = [n for n in nodes if n.status == "active" and n.scope == "global"]
    tok_map = {n.id: _meaningful_tokens(n) for n in globals_}

    adj: dict[str, set[str]] = defaultdict(set)
    for i in range(len(globals_)):
        for j in range(i + 1, len(globals_)):
            a, b = globals_[i], globals_[j]
            if a.type != b.type:
                continue
            sim = _node_similarity(a, b, emb_map, tok_map)
            if sim >= 0.40:
                adj[a.id].add(b.id)
                adj[b.id].add(a.id)

    by_id = {n.id: n for n in globals_}
    visited: set[str] = set()
    overlaps: list[dict[str, Any]] = []
    for n in globals_:
        if n.id in visited or n.id not in adj:
            continue
        comp: list[CompiledNode] = []
        stack = [n.id]
        visited.add(n.id)
        while stack:
            cur = stack.pop()
            comp.append(by_id[cur])
            for nxt in adj[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        if len(comp) >= 2:
            comp.sort(key=lambda x: x.id)
            overlaps.append(
                {
                    "node_ids": [c.id for c in comp],
                    "current_l0_tokens": sum(count_tokens(c.l0 or build_l0_line(c)) for c in comp),
                    "nodes": [
                        {
                            "id": c.id,
                            "scope": c.scope,
                            "type": c.type,
                            "severity": c.severity,
                            "rule": c.rule[:300],
                            "rationale": c.rationale[:160],
                            "guard_hint": c.guard_hint[:140],
                        }
                        for c in comp
                    ],
                }
            )
    return overlaps[:4]


def find_stale_candidates(
    nodes: list[CompiledNode],
    pinned_ids: set[str],
    *,
    stale_days: int = 90,
) -> list[dict[str, Any]]:
    """Find unpinned non-P0 nodes older than `stale_days` with seen_count==1."""
    now_dt = datetime.now(timezone.utc)
    stale: list[dict[str, Any]] = []
    for n in nodes:
        if n.status != "active" or n.severity == "P0" or n.type == "principle" or n.id in pinned_ids:
            continue
        if n.seen_count > 1:
            continue
        dt = parse_timestamp(n.updated_at or n.valid_from)
        if dt is None:
            continue
        age_days = (now_dt - dt).total_seconds() / 86400.0
        if age_days >= stale_days:
            stale.append(
                {
                    "id": n.id,
                    "scope": n.scope,
                    "type": n.type,
                    "severity": n.severity,
                    "age_days": int(age_days),
                    "rule": n.rule[:220],
                }
            )
    return stale[:5]


# ---------------------------------------------------------------------------
# Stage 2: Reasoner Synthesis (LLM with Incremental Cache + Heuristic Fallback)
# ---------------------------------------------------------------------------

_PROPOSAL_SYSTEM_PROMPT = """You are the FlowOS Architectural Governance & Cross-Project Taste Synthesizer.
Your job is to inspect:
1. Existing active GLOBAL principles/decisions (DO NOT propose duplicates of what already exists in global!).
2. Cross-repo card clusters (rules/decisions that independently appeared across >= 2 of the user's own repos).
3. Cross-repo tech-stack manifest frequencies (real stacks used across >= 2 of the user's own repos).
4. Overlapping GLOBAL nodes (candidates for `merge_dedup` to save precious 300-token L0 wakeup budget).
5. Stale candidates (optional `retire_stale`).

Return STRICT JSON ONLY:
{
  "proposals": [
    {
      "kind": "promote_global" | "merge_dedup" | "retire_stale",
      "topic_key": "short-english-slug-for-stable-dedup (e.g. frontend-shadcn-tokens, merge-core-engineering-prns, backend-go-single-binary)",
      "title": "Concise Simplified Chinese title (<=28 chars)",
      "proposed_type": "principle" | "decision" | "lesson",
      "proposed_severity": "P0" | "P1",
      "proposed_rule": "Crisp, directly enforceable Simplified Chinese rule (1-2 sentences, concrete, no fluff)",
      "proposed_rationale": "Structural reason why this preference compounds across projects",
      "proposed_guard_hint": "Optional static lint / CI / review check (or empty string)",
      "proposed_trigger_when": ["2-4 concise Chinese/English recall cues"],
      "evidence_repos": ["repo1", "repo2"],
      "target_node_ids": ["NODE-IDs: for promote_global list supporting repo node IDs; for merge_dedup list ALL overlapping global node IDs to supersede; for retire_stale list node IDs to archive"],
      "confidence": 0.85
    }
  ]
}

STRICT GOVERNANCE RULES:
- Return at most 6 high-compounding proposals. Quality > quantity!
- `promote_global`: MUST be backed by >= 2 distinct owned repos (either in cross-repo card clusters or manifest frequencies).
  - Tech-stack / UI / frontend / backend / database preferences MUST default to `proposed_severity: "P1"` (on-demand recall via `trigger_when`, preserving the 300-token L0 wakeup kernel!).
  - Reserve `P0` ONLY for unconditional universal principles.
  - Consolidate closely related stack signals into cohesive architectural preferences (e.g., ONE crisp frontend stack & shadcn/token preference rather than 5 separate micro-proposals for React, Vite, Bun, Tailwind, shadcn).
- `merge_dedup`: If overlapping global P0 nodes say nearly the same thing (e.g. PRN-003/004/009), propose ONE crisp consolidated `P0` principle preserving EVERY unique constraint while cutting token bloat, listing ALL overlapping IDs in `target_node_ids`.
- NEVER propose something already covered by an active global node unless it is a `merge_dedup` consolidation.
- Output Simplified Chinese for `title`, `proposed_rule`, `proposed_rationale`, `proposed_guard_hint`, keeping tech identifiers/paths in original English."""


def _proposal_fingerprint(kind: str, topic_key: str, target_ids: list[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (topic_key or "").lower()).strip("-")
    if slug:
        raw = f"{kind}:{slug}"
    else:
        raw = f"{kind}:{','.join(sorted(target_ids))}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _estimate_l0_delta(
    kind: str,
    proposed_scope: str,
    proposed_severity: str,
    proposed_rule: str,
    target_nodes: list[CompiledNode],
) -> int:
    if proposed_scope != "global":
        return 0
    if kind == "retire_stale":
        return -sum(count_tokens(n.l0 or build_l0_line(n)) for n in target_nodes if n.severity == "P0")
    new_l0 = count_tokens(f"[PRN-NEW|{proposed_severity}|seen:1] {proposed_rule[:80]}") if proposed_severity == "P0" else 0
    if kind == "merge_dedup":
        old_l0 = sum(count_tokens(n.l0 or build_l0_line(n)) for n in target_nodes if n.scope == "global" and n.severity == "P0")
        return new_l0 - old_l0
    # promote_global: P0 adds to L0; P1 costs 0 L0 tokens!
    return new_l0


def _heuristic_synthesize(
    *,
    cross_clusters: list[dict[str, Any]],
    recurring_manifests: dict[str, list[str]],
    global_overlaps: list[dict[str, Any]],
    stale_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministic fallback synthesizer when no LLM reasoner is configured."""
    out: list[dict[str, Any]] = []

    for ov in global_overlaps[:2]:
        nodes = ov["nodes"]
        if len(nodes) < 2:
            continue
        ids = [n["id"] for n in nodes]
        merged_rule = nodes[0]["rule"]
        out.append(
            {
                "kind": "merge_dedup",
                "topic_key": f"merge-{'-'.join(ids).lower()}",
                "title": f"合并重叠全局原则（{' / '.join(ids)}）",
                "proposed_type": nodes[0]["type"],
                "proposed_severity": nodes[0]["severity"],
                "proposed_rule": merged_rule,
                "proposed_rationale": "合并高度重叠的全局规则以释放 L0 300-token 唤醒内核空间。",
                "proposed_guard_hint": "",
                "proposed_trigger_when": ["工程规范", "代码改动", "全局原则"],
                "evidence_repos": ["global"],
                "target_node_ids": ids,
                "confidence": 0.82,
            }
        )

    for idx, cl in enumerate(cross_clusters[:3], start=1):
        repos = cl["repos"]
        nodes = cl["nodes"]
        lead = nodes[0]
        out.append(
            {
                "kind": "promote_global",
                "topic_key": f"cross-repo-pattern-{idx}-{'-'.join(repos[:2])}",
                "title": f"跨仓复用规范（{' / '.join(repos[:3])}）",
                "proposed_type": "principle",
                "proposed_severity": "P1",
                "proposed_rule": lead["rule"],
                "proposed_rationale": f"该约定已在 {len(repos)} 个自有仓库（{', '.join(repos)}）独立重复出现，适合沉淀为全局 P1 偏好。",
                "proposed_guard_hint": lead.get("guard_hint", ""),
                "proposed_trigger_when": repos[:3],
                "evidence_repos": repos,
                "target_node_ids": [n["id"] for n in nodes],
                "confidence": 0.78,
            }
        )

    # Group recurring frontend stack tags if >=2 repos share React + TypeScript or shadcn
    fe_tags = [t for t in ("React", "TypeScript", "Vite", "Tailwind CSS v4", "shadcn/ui", "shadcn Base UI", "Bun") if t in recurring_manifests]
    if len(fe_tags) >= 2:
        fe_repos = sorted({r for t in fe_tags for r in recurring_manifests[t]})
        if len(fe_repos) >= 2:
            out.append(
                {
                    "kind": "promote_global",
                    "topic_key": "frontend-react-ts-shadcn-stack",
                    "title": "前端默认技术栈与组件规范",
                    "proposed_type": "principle",
                    "proposed_severity": "P1",
                    "proposed_rule": f"新建独立前端项目优先采用 {' + '.join(fe_tags[:6])}，统一使用共享 UI 组件与语义主题 Token，禁止裸写原生交互控件或硬编码调色板颜色。",
                    "proposed_rationale": f"在 {len(fe_repos)} 个自有仓库（{', '.join(fe_repos)}）中持续复用同一套前端栈。",
                    "proposed_guard_hint": "通过 ESLint 限制共享组件目录外的原生交互标签与硬编码色值。",
                    "proposed_trigger_when": ["新建前端", "React TypeScript", "shadcn UI", "Tailwind"],
                    "evidence_repos": fe_repos,
                    "target_node_ids": [],
                    "confidence": 0.84,
                }
            )

    for st in stale_candidates[:1]:
        out.append(
            {
                "kind": "retire_stale",
                "topic_key": f"retire-{st['id'].lower()}",
                "title": f"归档长期未复用条目 {st['id']}",
                "proposed_type": st["type"],
                "proposed_severity": st["severity"],
                "proposed_rule": st["rule"],
                "proposed_rationale": f"该条目已超过 {st['age_days']} 天且仅出现 1 次，建议归档以保持检索信噪比。",
                "proposed_guard_hint": "",
                "proposed_trigger_when": [],
                "evidence_repos": [st["scope"]],
                "target_node_ids": [st["id"]],
                "confidence": 0.72,
            }
        )

    return out


def _current_global_l0_tokens(engine: BrainEngine) -> int:
    boot = engine.boot(project=None, budget=4000)
    return int(boot.get("used_tokens", 0))


def _expire_stale_pending(store: ProposalStore, active_node_ids: set[str]) -> int:
    now_dt = datetime.now(timezone.utc)
    expired_count = 0
    for p in store.load_all():
        if p.status != "pending":
            continue
        dt = parse_timestamp(p.created_at)
        age_expired = dt is not None and (now_dt - dt).total_seconds() > PROPOSAL_EXPIRY_DAYS * 86400
        # For merge_dedup / retire_stale, if any required target is no longer active, expire it
        target_gone = (
            p.kind in ("merge_dedup", "retire_stale")
            and bool(p.target_node_ids)
            and any(tid not in active_node_ids for tid in p.target_node_ids)
        )
        if age_expired or target_gone:
            p.status = "expired"
            p.resolved_at = now_iso()
            store.save(p)
            expired_count += 1
    return expired_count


def list_proposals(
    engine: BrainEngine,
    *,
    status: str = "pending",
) -> dict[str, Any]:
    """List proposals with current global L0 budget summary."""
    store = ProposalStore(engine.data_dir)
    with engine.snapshots.open_current(read_only=True) as db:
        active_ids = {n.id for n in db.list_nodes(as_of=None)}
    _expire_stale_pending(store, active_ids)

    all_props = store.load_all()
    if status != "all":
        filtered = [p for p in all_props if p.status == status]
    else:
        filtered = all_props
    # Newest/highest-confidence pending first
    filtered.sort(key=lambda p: (0 if p.status == "pending" else 1, -len(p.evidence_repos), -p.confidence, p.created_at))

    counts = {s: sum(1 for p in all_props if p.status == s) for s in VALID_PROPOSAL_STATUSES}
    l0_used = _current_global_l0_tokens(engine)
    return {
        "status_filter": status,
        "counts": counts,
        "l0_budget": {
            "used_tokens": l0_used,
            "limit_tokens": L0_GLOBAL_BUDGET,
            "remaining_tokens": max(0, L0_GLOBAL_BUDGET - l0_used),
        },
        "proposals": [p.to_dict() for p in filtered],
    }


def scan_proposals(
    engine: BrainEngine,
    *,
    root_dir: Path | None = None,
    reasoner: Reasoner | None = None,
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run two-stage funnel to discover cross-project preferences & governance proposals."""
    store = ProposalStore(engine.data_dir)
    pinned_ids = {c.node_id for c in engine.ledger.load_eval_cases() if c.pinned and c.node_id}

    with engine.snapshots.open_current(read_only=True) as db:
        active_nodes = db.list_nodes(as_of=None)
        node_map = {n.id: n for n in active_nodes}

    emb_map: dict[str, list[float]] = {}
    if active_nodes:
        vecs = engine.providers.embedder.embed([node_embedding_text(n) for n in active_nodes])
        for n, vec in zip(active_nodes, vecs):
            emb_map[n.id] = vec

    expired_count = _expire_stale_pending(store, set(node_map.keys()))

    # Stage 1: Deterministic signal mining
    owned_repos = discover_owned_repos(root_dir=root_dir, allowlist=allowlist, denylist=denylist)
    owned_scopes = {r.name.lower() for r in owned_repos}
    repo_manifests, recurring_manifests = scan_repo_manifests(owned_repos)

    # Include any active non-global scopes if no git repos were passed (e.g. pure unit test without git root)
    cluster_scopes = owned_scopes if owned_scopes else None
    cross_clusters = find_cross_repo_clusters(active_nodes, emb_map, allowed_scopes=cluster_scopes)
    global_overlaps = find_global_overlap_clusters(active_nodes, emb_map)
    stale_candidates = find_stale_candidates(active_nodes, pinned_ids)

    existing_globals = [
        {"id": n.id, "type": n.type, "severity": n.severity, "rule": n.rule[:220]}
        for n in active_nodes
        if n.scope == "global"
    ]
    repo_rules_sample = [
        {"id": n.id, "scope": n.scope, "type": n.type, "severity": n.severity, "rule": n.rule[:180]}
        for n in sorted(
            (x for x in active_nodes if x.scope != "global" and (cluster_scopes is None or x.scope.lower() in cluster_scopes)),
            key=lambda x: (x.severity, x.scope, x.id),
        )[:40]
    ]

    signal_payload = {
        "existing_globals": existing_globals,
        "cross_repo_clusters": cross_clusters,
        "repo_rules_sample": repo_rules_sample,
        "recurring_manifests": recurring_manifests,
        "global_overlaps": global_overlaps,
        "stale_candidates": stale_candidates,
    }
    signal_hash = hashlib.sha256(
        json.dumps(signal_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    # Stage 2: Reasoner synthesis with incremental cache
    active_reasoner = reasoner or engine.providers.reasoner
    reasoner_label = getattr(active_reasoner, "model_name", getattr(active_reasoner, "name", "heuristic"))
    cache = store.load_cache()
    used_cache = False
    raw_items: list[dict[str, Any]] | None = None

    if not force and cache.get("signal_hash") == signal_hash and isinstance(cache.get("raw_items"), list):
        raw_items = cache["raw_items"]
        used_cache = True
    elif not isinstance(active_reasoner, HeuristicReasoner):
        prompt = (
            "Analyze the user's cross-project signals below and return high-compounding governance proposals.\n\n"
            + json.dumps(signal_payload, ensure_ascii=False, indent=2)
        )
        resp = active_reasoner.complete_json(prompt, system=_PROPOSAL_SYSTEM_PROMPT)
        if resp and isinstance(resp.get("proposals"), list):
            raw_items = [x for x in resp["proposals"] if isinstance(x, dict)]
            store.save_cache(
                {
                    "signal_hash": signal_hash,
                    "reasoner": reasoner_label,
                    "updated_at": now_iso(),
                    "raw_items": raw_items,
                }
            )

    if raw_items is None:
        raw_items = _heuristic_synthesize(
            cross_clusters=cross_clusters,
            recurring_manifests=recurring_manifests,
            global_overlaps=global_overlaps,
            stale_candidates=stale_candidates,
        )

    # Index existing proposals by fingerprint for dedup & monotonic dismissal cooldown
    existing_all = store.load_all()
    pending_by_fp = {p.fingerprint: p for p in existing_all if p.status == "pending"}
    dismissed_by_fp = {p.fingerprint: p for p in existing_all if p.status == "dismissed"}
    accepted_fps = {p.fingerprint for p in existing_all if p.status == "accepted"}
    pending_count = len(pending_by_fp)

    manifest_by_repo = {m["repo"]: m for m in repo_manifests}
    created: list[Proposal] = []
    skipped_cooldown = 0
    skipped_existing = 0

    for item in raw_items:
        kind = str(item.get("kind") or "").strip()
        if kind not in VALID_PROPOSAL_KINDS:
            continue
        rule = str(item.get("proposed_rule") or "").strip()
        if not rule:
            continue

        target_ids = [str(tid) for tid in (item.get("target_node_ids") or []) if str(tid) in node_map]
        ev_repos = sorted({str(r).strip().lower() for r in (item.get("evidence_repos") or []) if str(r).strip()})
        for tid in target_ids:
            sc = node_map[tid].scope.lower()
            if sc != "global" and sc not in ev_repos:
                ev_repos.append(sc)
        ev_repos = sorted(set(ev_repos))

        # Hard invariant: promote_global MUST have >= 2 distinct repos of evidence
        if kind == "promote_global" and len([r for r in ev_repos if r != "global"]) < 2:
            continue
        if kind == "merge_dedup" and len(target_ids) < 2:
            continue
        if kind == "retire_stale" and len(target_ids) < 1:
            continue

        fp = _proposal_fingerprint(kind, str(item.get("topic_key") or item.get("title") or ""), target_ids)
        if fp in pending_by_fp or fp in accepted_fps:
            skipped_existing += 1
            continue
        if fp in dismissed_by_fp:
            prev_repos = set(dismissed_by_fp[fp].evidence_repos)
            # Only wake up a dismissed proposal if a brand-new repo adds fresh corroborating evidence
            if not (set(ev_repos) - prev_repos):
                skipped_cooldown += 1
                continue

        if pending_count >= MAX_PENDING_PROPOSALS:
            break

        ptype = str(item.get("proposed_type") or "principle")
        if ptype not in VALID_KINDS:
            ptype = "principle"
        psev = normalize_severity(str(item.get("proposed_severity") or ("P0" if kind == "merge_dedup" else "P1")), kind=ptype)
        pscope = "global" if kind in ("promote_global", "merge_dedup") else (node_map[target_ids[0]].scope if target_ids else "global")

        target_nodes = [node_map[tid] for tid in target_ids]
        ev_nodes = [
            {
                "id": n.id,
                "scope": n.scope,
                "type": n.type,
                "severity": n.severity,
                "rule": n.rule[:220],
            }
            for n in target_nodes
        ]
        ev_manifests = [manifest_by_repo[r] for r in ev_repos if r in manifest_by_repo]
        delta = _estimate_l0_delta(kind, pscope, psev, rule, target_nodes)

        prop = Proposal(
            id=f"prp_{new_ulid()}",
            kind=kind,
            status="pending",
            fingerprint=fp,
            title=str(item.get("title") or rule[:28]).strip(),
            proposed_type=ptype,
            proposed_scope=pscope,
            proposed_severity=psev,
            proposed_rule=rule,
            proposed_rationale=str(item.get("proposed_rationale") or "").strip(),
            proposed_guard_hint=str(item.get("proposed_guard_hint") or "").strip(),
            proposed_trigger_when=[str(t).strip() for t in (item.get("proposed_trigger_when") or []) if str(t).strip()][:5],
            evidence_repos=ev_repos,
            evidence_nodes=ev_nodes,
            evidence_manifests=ev_manifests,
            target_node_ids=target_ids,
            estimated_l0_delta=delta,
            confidence=min(1.0, max(0.5, float(item.get("confidence", 0.8)))),
        )
        store.save(prop)
        pending_by_fp[fp] = prop
        pending_count += 1
        created.append(prop)

    listing = list_proposals(engine, status="pending")
    return {
        "owned_repos": [r.name.lower() for r in owned_repos],
        "recurring_stack_tags": recurring_manifests,
        "cross_repo_clusters": len(cross_clusters),
        "global_overlap_clusters": len(global_overlaps),
        "reasoner": reasoner_label,
        "cache_hit": used_cache,
        "expired": expired_count,
        "created": len(created),
        "skipped_existing": skipped_existing,
        "skipped_cooldown": skipped_cooldown,
        "new_proposals": [p.to_dict() for p in created],
        "pending_count": listing["counts"]["pending"],
        "l0_budget": listing["l0_budget"],
    }


# ---------------------------------------------------------------------------
# Stage 3: Explicit Human Approval (Accept / Dismiss)
# ---------------------------------------------------------------------------


def accept_proposal(
    engine: BrainEngine,
    proposal_id: str,
    *,
    rule: str | None = None,
    rationale: str | None = None,
    guard_hint: str | None = None,
    severity: str | None = None,
    trigger_when: list[str] | None = None,
    force: bool = False,
    device: str | None = None,
) -> dict[str, Any]:
    """Approve a pending proposal and commit it into immutable `ledger/` via BrainEngine."""
    store = ProposalStore(engine.data_dir)
    prop = store.get(proposal_id)
    if prop is None:
        raise NotFoundError(
            f"Proposal not found: {proposal_id}",
            error_code="PROPOSAL_NOT_FOUND",
        )
    if prop.status != "pending":
        raise ProposalStateError(
            f"Proposal {proposal_id} is already '{prop.status}' and cannot be accepted.",
            error_code="PROPOSAL_ALREADY_RESOLVED",
            details={"proposal_id": proposal_id, "status": prop.status},
        )

    final_rule = (rule if rule is not None else prop.proposed_rule).strip()
    final_rationale = (rationale if rationale is not None else prop.proposed_rationale).strip()
    final_guard = (guard_hint if guard_hint is not None else prop.proposed_guard_hint).strip()
    final_severity = normalize_severity(severity or prop.proposed_severity, kind=prop.proposed_type)
    final_triggers = list(trigger_when) if trigger_when is not None else list(prop.proposed_trigger_when)

    if not final_rule and prop.kind != "retire_stale":
        raise UsageError("Proposed rule must be non-empty.")

    # Verify evidence freshness for merge_dedup / retire_stale
    with engine.snapshots.open_current(read_only=True) as db:
        target_nodes = [db.get_node(tid) for tid in prop.target_node_ids]
        stale_ids = [
            tid
            for tid, node in zip(prop.target_node_ids, target_nodes)
            if node is None or node.status != "active"
        ]
        live_targets = [n for n in target_nodes if n is not None and n.status == "active"]

    if stale_ids and prop.kind in ("merge_dedup", "retire_stale") and not force:
        raise ProposalStateError(
            f"Target nodes changed since scan ({', '.join(stale_ids)}); re-scan or pass force=true.",
            error_code="EVIDENCE_STALE",
            details={"stale_node_ids": stale_ids},
        )

    # Check global L0 300-token budget guard when adding/promoting P0
    est_delta = _estimate_l0_delta(prop.kind, prop.proposed_scope, final_severity, final_rule, live_targets)
    current_l0 = _current_global_l0_tokens(engine)
    if (
        prop.proposed_scope == "global"
        and final_severity == "P0"
        and est_delta > 0
        and current_l0 + est_delta > L0_GLOBAL_BUDGET
        and not force
    ):
        raise ProposalStateError(
            f"Accepting as P0 would exceed L0 budget ({current_l0} + {est_delta} > {L0_GLOBAL_BUDGET} tokens). "
            "Accept as P1 instead, merge overlapping P0 nodes first, or pass force=true.",
            exit_code=ExitCode.GATE_FAILED,
            error_code="L0_BUDGET_EXCEEDED",
            hint="Downgrade severity to P1 so it recalls on-demand without consuming L0 wakeup tokens.",
            details={
                "current_l0_tokens": current_l0,
                "estimated_delta": est_delta,
                "budget_tokens": L0_GLOBAL_BUDGET,
            },
        )

    # Execute actual immutable ledger write through BrainEngine
    write_result: dict[str, Any]
    result_node_id: str | None = None

    if prop.kind == "retire_stale":
        archived_ids: list[str] = []
        for n in live_targets:
            engine.archive_node(n.id, reason=final_rationale or f"Accepted proposal {prop.id}", device=device)
            archived_ids.append(n.id)
        write_result = {"archived": archived_ids}
        result_node_id = archived_ids[0] if archived_ids else None
    elif prop.kind == "merge_dedup":
        sup_ids = [n.id for n in live_targets]
        meta: dict[str, Any] = {
            "severity": final_severity,
            "rule": final_rule,
            "rationale": final_rationale,
            "guard_hint": final_guard,
            "trigger_when": final_triggers,
            "proposal_id": prop.id,
            "proposal_kind": prop.kind,
        }
        if sup_ids:
            meta["supersedes"] = sup_ids[0]
            if len(sup_ids) > 1:
                meta["supersedes_ids"] = sup_ids
        write_result = engine.add_atom(
            kind=prop.proposed_type,
            content=final_rule,
            scope=prop.proposed_scope,
            source="manual:proposal",
            device=device,
            meta=meta,
        )
        result_node_id = (write_result.get("node") or {}).get("id")
    else:  # promote_global
        meta = {
            "severity": final_severity,
            "rule": final_rule,
            "rationale": final_rationale,
            "guard_hint": final_guard,
            "trigger_when": final_triggers,
            "proposal_id": prop.id,
            "proposal_kind": prop.kind,
            "evidence_repos": list(prop.evidence_repos),
            "supported_by": [n.id for n in live_targets],
        }
        write_result = engine.add_atom(
            kind=prop.proposed_type,
            content=final_rule,
            scope=prop.proposed_scope,
            source="manual:proposal",
            device=device,
            meta=meta,
        )
        result_node_id = (write_result.get("node") or {}).get("id")

    prop.status = "accepted"
    prop.proposed_rule = final_rule
    prop.proposed_rationale = final_rationale
    prop.proposed_guard_hint = final_guard
    prop.proposed_severity = final_severity
    prop.proposed_trigger_when = final_triggers
    prop.resolved_at = now_iso()
    prop.result_node_id = result_node_id
    store.save(prop)

    return {
        "proposal": prop.to_dict(),
        "result_node_id": result_node_id,
        "write_result": write_result,
        "l0_budget": {
            "used_tokens": _current_global_l0_tokens(engine),
            "limit_tokens": L0_GLOBAL_BUDGET,
        },
    }


def dismiss_proposal(
    engine: BrainEngine,
    proposal_id: str,
    *,
    reason: str = "",
) -> dict[str, Any]:
    """Dismiss a pending proposal and record monotonic cooldown fingerprint."""
    store = ProposalStore(engine.data_dir)
    prop = store.get(proposal_id)
    if prop is None:
        raise NotFoundError(
            f"Proposal not found: {proposal_id}",
            error_code="PROPOSAL_NOT_FOUND",
        )
    if prop.status != "pending":
        raise ProposalStateError(
            f"Proposal {proposal_id} is already '{prop.status}'.",
            error_code="PROPOSAL_ALREADY_RESOLVED",
            details={"proposal_id": proposal_id, "status": prop.status},
        )

    prop.status = "dismissed"
    prop.dismiss_reason = reason.strip()
    prop.resolved_at = now_iso()
    store.save(prop)
    return {"proposal": prop.to_dict()}
