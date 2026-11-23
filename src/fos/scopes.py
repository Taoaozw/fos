"""Hierarchical scope taxonomy, multi-branch context profile resolution, and LUB promotion helpers.

Design:
- Shallow taxonomy (<= 3 segments) + orthogonal facets (`entities`) + multi-branch repo profiles.
- Level 0: `global` (thin cross-industry personal kernel)
- Level 1: `<domain>` (extensible 360-industry roots, e.g. `eng`, `product`, `writing`, `biz`)
- Level 2: `<domain>/<specialty>` (e.g. `eng/frontend`, `eng/backend`, `eng/ops`, `eng/ai`)
- Level 3: `<domain>/<specialty>/<stack>` (e.g. `eng/frontend/react`, `eng/backend/python`)
- Repo leaf: `<project>` or `repo/<project>` (concrete repository; mounts multiple domain branches)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from fos.code_pattern import (
    _parse_cargo_toml,
    _parse_go_mod,
    _parse_gradle_toml,
    _parse_package_json,
    _parse_pyproject_toml,
    detect_repo_root,
    detect_repo_stack_versions,
)
from fos.errors import UsageError

ScopeKind = Literal["global", "domain", "repo"]

MAX_SCOPE_DEPTH = 3

# Extensible top-level domains ("360 industries" roots).
# Any slash-separated non-`repo/` path (e.g. `music/piano`, `legal/ip`) is also treated as a domain scope.
DOMAIN_ROOTS: frozenset[str] = frozenset(
    {
        "eng",
        "tech",
        "product",
        "design",
        "writing",
        "biz",
        "finance",
        "life",
        "research",
        "management",
    }
)

_AMBIENT_ROOT_DOC_NAMES: frozenset[str] = frozenset({"agents.md", "claude.md", "gemini.md"})
_SCOPE_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# Deterministic mapping from detected manifest stack keys -> canonical <=3-level scope paths.
STACK_TO_SCOPE: dict[str, str] = {
    # Frontend
    "react": "eng/frontend/react",
    "@tanstack/react-query": "eng/frontend/react",
    "next": "eng/frontend/nextjs",
    "nextjs": "eng/frontend/nextjs",
    "vue": "eng/frontend/vue",
    "nuxt": "eng/frontend/vue",
    "svelte": "eng/frontend/svelte",
    "tailwindcss": "eng/frontend/css",
    "tailwind": "eng/frontend/css",
    "shadcn": "eng/frontend/shadcn",
    "@base-ui/react": "eng/frontend/shadcn",
    "vite": "eng/frontend",
    "typescript": "eng/frontend",
    # Backend
    "python": "eng/backend/python",
    "fastapi": "eng/backend/python",
    "django": "eng/backend/python",
    "sqlalchemy": "eng/backend/python",
    "pydantic": "eng/backend/python",
    "httpx": "eng/backend/python",
    "go": "eng/backend/go",
    "gin": "eng/backend/go",
    "echo": "eng/backend/go",
    "fiber": "eng/backend/go",
    "chi": "eng/backend/go",
    "rust": "eng/backend/rust",
    "rust-edition": "eng/backend/rust",
    "tokio": "eng/backend/rust",
    "axum": "eng/backend/rust",
    "actix-web": "eng/backend/rust",
    "serde": "eng/backend/rust",
    "hyper": "eng/backend/rust",
    "kotlin": "eng/backend/jvm",
    "java": "eng/backend/jvm",
    "jdk": "eng/backend/jvm",
    "spring-boot": "eng/backend/jvm",
    "spring": "eng/backend/jvm",
    "webflux": "eng/backend/jvm",
    "reactor": "eng/backend/jvm",
    "netty": "eng/backend/jvm",
    "ktor": "eng/backend/jvm",
    "r2dbc": "eng/backend/jvm",
    "coroutines": "eng/backend/jvm",
    "kotlinx-coroutines": "eng/backend/jvm",
    "kotlinx-serialization": "eng/backend/jvm",
    "hono": "eng/backend/node",
    "fastify": "eng/backend/node",
    "express": "eng/backend/node",
    "pgx": "eng/backend/postgres",
    "postgres": "eng/backend/postgres",
    "postgresql": "eng/backend/postgres",
    "sqlite": "eng/backend/sqlite",
    "sqlx": "eng/backend/sql",
    # AI / ML
    "mlx": "eng/ai",
    "mlx-embeddings": "eng/ai",
    "torch": "eng/ai",
    "openai": "eng/ai",
    "anthropic": "eng/ai",
}

# Deterministic keyword cues for Least Upper Bound (LUB) scope inference across repos.
# Ordered from most specific (level 3) to broader specialties (level 2).
_LUB_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("eng/frontend/shadcn", ("shadcn", "components/ui", "base ui", "base-ui", "radix", "语义主题 token", "语义 token")),
    ("eng/frontend/nextjs", ("next.js", "nextjs", "app router", "server component", "server action")),
    ("eng/frontend/react", ("react", "useeffect", "usestate", "jsx", "tsx", "react-query", "tanstack")),
    ("eng/frontend/vue", ("vue", "nuxt", "pinia")),
    ("eng/frontend/css", ("tailwind", "css", "scss", "样式", "色板")),
    (
        "eng/frontend",
        ("前端", "frontend", "组件库", "原生控件", "button", "input", "dom", "browser", "vite", "ui 组件", "界面"),
    ),
    ("eng/backend/postgres", ("postgres", "postgresql", "pgx", "psycopg", "asyncpg", "ddl", "psql")),
    ("eng/backend/sqlite", ("sqlite", "modernc.org/sqlite", "sqlite-vec", "wal")),
    ("eng/backend/go", ("golang", "go.mod", "goroutine", "cgo", "单机 go", "纯 go", "go 工具")),
    ("eng/backend/python", ("python", "pyproject", "pytest", "fastapi", "pydantic", "asyncio", "uv ")),
    ("eng/backend/rust", ("rust", "cargo", "tokio", "axum", "borrow checker")),
    ("eng/backend/jvm", ("kotlin", "spring boot", "spring-boot", "webflux", "r2dbc", "jvm", "gradle", "netty", "reactor")),
    ("eng/backend/redis", ("redis", "redisson", "pub/sub")),
    ("eng/backend", ("后端", "backend", "api", "grpc", "sql", "事务", "幂等", "数据库", "数据库迁移", "migration", "微服务")),
    (
        "eng/ops",
        ("docker", "rsync", "ssh", "nginx", "k8s", "kubernetes", "systemd", "deploy", "部署", "运维", "ci/cd", "macos", "linux", "tailscale"),
    ),
    ("eng/ai", ("llm", "agent", "prompt", "mcp", "embedding", "rerank", "context window", "rag", "eval", "token")),
)


def normalize_scope(raw: str | None, *, strict: bool = False) -> str:
    """Normalize a scope string into canonical lowercase slash form.

    Examples:
        None / "" -> "global"
        "ENG/Frontend/React/" -> "eng/frontend/react"
        "fos" -> "fos"
        "repo/fos" -> "repo/fos"
    """
    if not raw or not str(raw).strip():
        return "global"
    cleaned = str(raw).strip().lower().replace("\\", "/")
    parts = [p.strip() for p in cleaned.split("/") if p.strip()]
    if not parts:
        return "global"
    if parts[0] == "global":
        if strict and len(parts) > 1:
            raise UsageError(
                f"Invalid scope '{raw}': `global` cannot have child segments.",
                hint="Use `global` or a domain path like `eng/frontend`.",
            )
        return "global"
    if parts[0] == "repo":
        if len(parts) < 2:
            if strict:
                raise UsageError(f"Invalid scope '{raw}': expected `repo/<project>`.", hint="Example: `repo/fos` or `fos`.")
            return "global"
        repo_seg = parts[1]
        if strict and (len(parts) > 2 or not _SCOPE_SEGMENT_RE.match(repo_seg)):
            raise UsageError(
                f"Invalid repo scope '{raw}': expected `repo/<project>` with alphanumeric/-/_/. characters.",
                hint="Example: `repo/fos` or `fos`.",
            )
        return f"repo/{repo_seg}"

    if strict:
        if len(parts) > MAX_SCOPE_DEPTH:
            raise UsageError(
                f"Scope '{raw}' exceeds max hierarchy depth ({MAX_SCOPE_DEPTH}).",
                hint="Keep scope <= 3 levels (e.g. `eng/frontend/react`) and put finer facets in `--entities`.",
            )
        for seg in parts:
            if not _SCOPE_SEGMENT_RE.match(seg):
                raise UsageError(
                    f"Invalid scope segment '{seg}' in '{raw}'.",
                    hint="Use lowercase letters, digits, `-`, `_`, or `.` separated by `/`.",
                )
    else:
        parts = [p for p in parts if _SCOPE_SEGMENT_RE.match(p)]
        if not parts:
            return "global"
        if len(parts) > MAX_SCOPE_DEPTH:
            parts = parts[:MAX_SCOPE_DEPTH]

    return "/".join(parts)


def scope_kind(scope: str | None) -> ScopeKind:
    """Classify a scope as `global`, `domain` (hierarchical taxonomy), or `repo`."""
    s = normalize_scope(scope, strict=False)
    if s == "global":
        return "global"
    if s.startswith("repo/"):
        return "repo"
    if "/" in s or s in DOMAIN_ROOTS:
        return "domain"
    return "repo"


def canonical_repo_name(scope: str | None) -> str:
    """Return bare repository name for repo scopes (`repo/fos` -> `fos`, `fos` -> `fos`)."""
    s = normalize_scope(scope, strict=False)
    if s.startswith("repo/"):
        return s[5:]
    return s


def scope_depth(scope: str | None) -> int:
    """Return monotonic specificity depth: global=0, domain=1..3, repo=4."""
    s = normalize_scope(scope, strict=False)
    kind = scope_kind(s)
    if kind == "global":
        return 0
    if kind == "repo":
        return 4
    return min(MAX_SCOPE_DEPTH, len(s.split("/")))


def expand_scope_ancestors(scope: str | None) -> list[str]:
    """Expand a single scope into its ordered ancestor closure (most specific -> `global`).

    Examples:
        "eng/frontend/react" -> ["eng/frontend/react", "eng/frontend", "eng", "global"]
        "product"            -> ["product", "global"]
        "fos"                -> ["fos", "repo/fos", "global"]
        "repo/fos"           -> ["repo/fos", "fos", "global"]
        "global"             -> ["global"]
    """
    s = normalize_scope(scope, strict=False)
    if s == "global":
        return ["global"]
    kind = scope_kind(s)
    if kind == "repo":
        bare = canonical_repo_name(s)
        out = [s]
        alt = bare if s.startswith("repo/") else f"repo/{bare}"
        if alt not in out:
            out.append(alt)
        out.append("global")
        return out

    parts = s.split("/")
    out = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    out.append("global")
    return out


def parse_scopes_param(raw: str | None) -> list[str]:
    """Parse comma-separated scope list into normalized unique scopes."""
    if not raw or not raw.strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        if not part.strip():
            continue
        sc = normalize_scope(part, strict=False)
        if sc not in seen:
            seen.add(sc)
            out.append(sc)
    return out


def format_scopes_param(scopes: list[str] | None) -> str | None:
    """Serialize scope list into compact comma-separated string for HTTP/CLI transport."""
    if not scopes:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for raw in scopes:
        if not raw or not str(raw).strip():
            continue
        sc = normalize_scope(raw, strict=False)
        if sc not in seen:
            seen.add(sc)
            out.append(sc)
    return ",".join(out) if out else None


def _detect_subdir_package_root(cwd: Path, repo_root: Path) -> Path | None:
    """If `cwd` sits inside a sub-package of `repo_root` with its own manifest, return that sub-package dir."""
    cur = cwd.expanduser().resolve()
    if cur.is_file():
        cur = cur.parent
    root = repo_root.resolve()
    if cur == root:
        return None
    try:
        cur.relative_to(root)
    except ValueError:
        return None

    manifest_names = ("package.json", "pyproject.toml", "go.mod", "Cargo.toml", "gradle/libs.versions.toml")
    node = cur
    while node != root and node != node.parent:
        if any((node / mf).is_file() for mf in manifest_names):
            return node
        node = node.parent
    return None


def _parse_manifests_in_dir(target_dir: Path) -> dict[str, str]:
    stack: dict[str, str] = {}
    checks = (
        ("gradle/libs.versions.toml", _parse_gradle_toml),
        ("go.mod", _parse_go_mod),
        ("pyproject.toml", _parse_pyproject_toml),
        ("Cargo.toml", _parse_cargo_toml),
        ("package.json", _parse_package_json),
    )
    for rel, parser in checks:
        p = target_dir / rel
        if p.is_file():
            for k, v in parser(p).items():
                stack.setdefault(k, v)
    return stack


def _detect_marker_scopes(base_dir: Path, *, include_web_subdir: bool = True) -> list[str]:
    """Detect lightweight filesystem markers for ops/shadcn/sql scopes (fast stat-only checks)."""
    found: list[str] = []
    # Ops / infrastructure markers
    ops_files = ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
    ops_dirs = ("deploy", "k8s", "terraform", ".github/workflows")
    if any((base_dir / f).is_file() for f in ops_files) or any((base_dir / d).is_dir() for d in ops_dirs):
        found.append("eng/ops")
    # shadcn / UI kit markers
    if (base_dir / "components.json").is_file() or (include_web_subdir and (base_dir / "web" / "components.json").is_file()):
        found.append("eng/frontend/shadcn")
    # SQL / migrations markers
    if any((base_dir / d).is_dir() for d in ("migrations", "alembic", "prisma")):
        found.append("eng/backend/sql")
    return found


def map_stack_to_scopes(stack: dict[str, str] | None) -> list[str]:
    """Map detected SemVer stack dict -> expanded unique domain/stack scopes (excluding `global`)."""
    if not stack:
        return []
    leaves: list[str] = []
    for key in stack:
        mapped = STACK_TO_SCOPE.get(key.strip().lower())
        if mapped and mapped not in leaves:
            leaves.append(mapped)
    out: list[str] = []
    seen: set[str] = set()
    for leaf in leaves:
        for anc in expand_scope_ancestors(leaf):
            if anc != "global" and anc not in seen:
                seen.add(anc)
                out.append(anc)
    return out


def detect_repo_scopes(
    path: Path | None = None,
    *,
    cwd_stack: dict[str, str] | None = None,
) -> list[str]:
    """Auto-detect mounted domain/stack scopes for a repository or sub-package directory.

    - At repo root (e.g. fullstack repo with `pyproject.toml` + `web/package.json` + `deploy/`):
      mounts all detected branches (`eng/backend/python`, `eng/frontend/react`, `eng/ops`, ...).
    - Inside a dedicated sub-package (e.g. `fos/web/` with its own `package.json`):
      focuses on that sub-package's branch (`eng/frontend/*`) without pulling sibling backend stacks.
    """
    detected: list[str] = []
    seen: set[str] = set()

    def _add_with_ancestors(sc: str) -> None:
        for anc in expand_scope_ancestors(sc):
            if anc != "global" and anc not in seen:
                seen.add(anc)
                detected.append(anc)

    if cwd_stack:
        for sc in map_stack_to_scopes(cwd_stack):
            _add_with_ancestors(sc)

    if path is not None:
        resolved = path.expanduser().resolve()
        repo_root = detect_repo_root(resolved)
        if repo_root is not None:
            _add_with_ancestors("eng")
            subpkg = _detect_subdir_package_root(resolved, repo_root)
            if subpkg is not None:
                sub_stack = _parse_manifests_in_dir(subpkg)
                for sc in map_stack_to_scopes(sub_stack):
                    _add_with_ancestors(sc)
                for sc in _detect_marker_scopes(subpkg, include_web_subdir=False):
                    _add_with_ancestors(sc)
            else:
                if cwd_stack is None:
                    root_stack, _ = detect_repo_stack_versions(repo_root)
                    for sc in map_stack_to_scopes(root_stack):
                        _add_with_ancestors(sc)
                for sc in _detect_marker_scopes(repo_root, include_web_subdir=True):
                    _add_with_ancestors(sc)

    detected.sort(key=lambda s: (-scope_depth(s), s))
    return detected


def resolve_active_scopes(
    scope: str | None = None,
    *,
    extra_scopes: list[str] | None = None,
    cwd_stack: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> list[str]:
    """Build the ordered Active Scope Set (most specific first -> `global` last).

    Returns `[]` when all inputs are empty (preserving unfiltered "all scopes" behavior).
    """
    has_any = bool(scope and str(scope).strip()) or bool(extra_scopes) or bool(cwd_stack) or (cwd is not None)
    if not has_any:
        return []

    seen: set[str] = set()
    ordered: list[str] = []

    def _add(sc: str) -> None:
        norm = normalize_scope(sc, strict=False)
        if norm not in seen:
            seen.add(norm)
            ordered.append(norm)

    primary_kind: ScopeKind | None = None
    if scope and str(scope).strip():
        norm_primary = normalize_scope(scope, strict=False)
        primary_kind = scope_kind(norm_primary)
        for anc in expand_scope_ancestors(norm_primary):
            _add(anc)
        # Every concrete code repo automatically inherits top-level `eng` principles.
        if primary_kind == "repo":
            _add("eng")

    for ex in extra_scopes or []:
        if ex and str(ex).strip():
            for anc in expand_scope_ancestors(ex):
                _add(anc)

    # Only attach repo/stack auto-detection when not explicitly narrowed to a non-engineering domain
    # (e.g. `--scope product` or `--scope writing` stays cleanly isolated from code stacks).
    allow_stack_mount = primary_kind in (None, "repo") or (
        scope is not None and normalize_scope(scope, strict=False).split("/", 1)[0] in ("eng", "tech")
    )
    if allow_stack_mount and (cwd_stack or cwd is not None):
        for sc in detect_repo_scopes(cwd, cwd_stack=cwd_stack):
            _add(sc)

    _add("global")
    ordered.sort(key=lambda s: (-scope_depth(s), s))
    return ordered


def _cue_matches(cue: str, text: str) -> bool:
    """Match ASCII word cues with word boundaries to avoid false substrings (e.g. `dom` in `domain`, `rag` in `storage`)."""
    if cue.isascii() and cue[0].isalnum() and cue[-1].isalnum():
        return bool(re.search(rf"(?<![a-z0-9_]){re.escape(cue)}(?![a-z0-9_])", text))
    return cue in text


def infer_lub_scope(
    nodes: list[Any],
    *,
    repo_scopes_map: dict[str, set[str]] | None = None,
    fallback: str = "eng",
) -> str:
    """Infer the Least Upper Bound (LUB) scope for a set of corroborating cross-repo nodes.

    Picks the narrowest shared domain/stack scope (<= 3 levels) supported by node content,
    `compat` metadata, and optional repo manifest scopes — preventing `global` bloat.
    """
    if not nodes:
        return fallback

    # 1. Check if all nodes share a common compat stack scope (e.g. code_exemplars)
    compat_sets: list[set[str]] = []
    for n in nodes:
        comp = getattr(n, "compat", None) if not isinstance(n, dict) else n.get("compat")
        if isinstance(comp, dict) and isinstance(comp.get("stack"), dict) and comp["stack"]:
            compat_sets.append(set(map_stack_to_scopes(comp["stack"])))
    if compat_sets and len(compat_sets) == len(nodes):
        common_compat = set.intersection(*compat_sets)
        if common_compat:
            return sorted(common_compat, key=lambda s: (-scope_depth(s), s))[0]

    # 2. Content & entity cue voting across the cluster
    blobs: list[str] = []
    for n in nodes:
        if isinstance(n, dict):
            rule = str(n.get("rule") or "")
            rat = str(n.get("rationale") or "")
            ents = " ".join(str(e) for e in (n.get("entities") or []))
            gh = str(n.get("guard_hint") or "")
        else:
            rule = str(getattr(n, "rule", "") or "")
            rat = str(getattr(n, "rationale", "") or "")
            ents = " ".join(str(e) for e in (getattr(n, "entities", None) or []))
            gh = str(getattr(n, "guard_hint", "") or "")
        blobs.append(f"{rule} {rat} {ents} {gh}".lower())

    combined_text = " ".join(blobs)
    matched_scopes: list[str] = []
    for cand_scope, cues in _LUB_CUES:
        if any(_cue_matches(cue, combined_text) for cue in cues):
            matched_scopes.append(cand_scope)

    if matched_scopes:
        # Check if multiple distinct top specialties matched (e.g. both frontend and backend) -> lift to `eng`
        specialties = {"/".join(s.split("/")[:2]) for s in matched_scopes}
        if len(specialties) == 1:
            return matched_scopes[0]
        # If one specialty dominates (e.g. frontend cue in all nodes, backend mentioned once in passing), pick majority
        spec_counts: dict[str, int] = {}
        for blob in blobs:
            hit_specs: set[str] = set()
            for cand_scope, cues in _LUB_CUES:
                if any(_cue_matches(cue, blob) for cue in cues):
                    hit_specs.add("/".join(cand_scope.split("/")[:2]))
            for sp in hit_specs:
                spec_counts[sp] = spec_counts.get(sp, 0) + 1
        if spec_counts:
            best_spec, best_cnt = max(spec_counts.items(), key=lambda kv: kv[1])
            other_max = max((c for sp, c in spec_counts.items() if sp != best_spec), default=0)
            if best_cnt > other_max:
                for cand_scope in matched_scopes:
                    if cand_scope == best_spec or cand_scope.startswith(best_spec + "/"):
                        return cand_scope
        return "eng"

    # 3. Fallback to common repo manifest scopes if available
    if repo_scopes_map:
        repo_sets: list[set[str]] = []
        for n in nodes:
            sc = str(n.get("scope") if isinstance(n, dict) else getattr(n, "scope", "") or "")
            rname = canonical_repo_name(sc)
            if rname in repo_scopes_map and repo_scopes_map[rname]:
                repo_sets.append(repo_scopes_map[rname])
        if repo_sets and len(repo_sets) >= 2:
            common = set.intersection(*repo_sets)
            if common:
                return sorted(common, key=lambda s: (-scope_depth(s), s))[0]

    return fallback


def is_repo_root_ambient_doc(node_scope: str | None, source_ref: str | None) -> bool:
    """Return True if `source_ref` points to a repo's root auto-loaded `AGENTS.md`/`CLAUDE.md`/`GEMINI.md`.

    Non-root docs (e.g. `docs/DESIGN.md`, `LESSONS.md`, `docs/agents/*.md`, or nested subdir `web/AGENTS.md`)
    return False so they are NOT suppressed from L0 boot.
    """
    if not source_ref or scope_kind(node_scope) != "repo":
        return False
    doc_part = source_ref.strip().split("#", 1)[0].split("::", 1)[0].strip()
    if not doc_part:
        return False
    p = Path(doc_part)
    if p.name.lower() not in _AMBIENT_ROOT_DOC_NAMES:
        return False
    # Bare filename without directory (e.g. "AGENTS.md") is root-level
    if len(p.parts) == 1:
        return True
    repo_name = canonical_repo_name(node_scope)
    parent = p.parent
    if parent.name.lower() == repo_name:
        return True
    try:
        if (parent / ".git").exists():
            return True
    except OSError:
        pass
    return False
