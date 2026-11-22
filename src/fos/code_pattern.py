"""Code Pattern / Exemplar primitives: symbol-anchored slicing, manifest stack versions, SemVer drift.

First-principles decoupling:
- Timeless engineering invariant lives in L1 `rule` + `rationale` (never rots with framework churn).
- Perishable framework implementation lives in live disk `source_ref` (`file::symbol`) + compact L2 skeleton
  + `compat` SemVer envelope (`stack`, `manifest`, `bench_cmd`, `lang`).
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

CompatStatus = Literal["compatible", "minor_drift", "major_drift", "cross_stack"]

_EXT_LANG: dict[str, str] = {
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".java": "java",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".go": "go",
    ".py": "python",
    ".rs": "rust",
    ".sql": "sql",
    ".sh": "bash",
}

_LINE_RANGE_RE = re.compile(r"^L?(\d+)(?:-L?(\d+))?$", re.IGNORECASE)
_SEMVER_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")

# Key frameworks/runtimes to track from manifests (keeps compat envelopes crisp & high-signal)
_GRADLE_TRACKED_VERSIONS = {
    "kotlin",
    "spring-boot",
    "springboot",
    "spring",
    "reactor",
    "netty",
    "kotlinx-serialization",
    "kotlinx-coroutines",
    "coroutines",
    "r2dbc",
    "blockhound",
    "ktor",
    "jdk",
    "java",
}
_NPM_TRACKED_PKGS = (
    "react",
    "next",
    "vite",
    "typescript",
    "tailwindcss",
    "@tanstack/react-query",
    "vue",
    "svelte",
    "hono",
    "fastify",
    "express",
)
_PY_TRACKED_PKGS = (
    "fastapi",
    "pydantic",
    "sqlalchemy",
    "django",
    "httpx",
    "mlx",
    "mlx-embeddings",
    "torch",
)
_CARGO_TRACKED_CRATES = (
    "tokio",
    "axum",
    "actix-web",
    "serde",
    "sqlx",
    "hyper",
)


def detect_language(path: Path) -> str:
    return _EXT_LANG.get(path.suffix.lower(), "text")


def normalize_major_minor(raw_ver: str) -> str | None:
    """Normalize version strings like '^19.1.2', '>=3.12', '4.1.1-SNAPSHOT' -> '19.1' / '3.12' / '4.1'."""
    m = _SEMVER_RE.search(raw_ver or "")
    if not m:
        return None
    major = m.group(1)
    minor = m.group(2) if m.group(2) is not None else "0"
    return f"{major}.{minor}"


def _major_minor_tuple(ver: str) -> tuple[int, int] | None:
    m = _SEMVER_RE.search(ver or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0)


def parse_symbol_ref(source_ref: str) -> tuple[Path | None, str]:
    """Split `/abs/path/File.kt::Symbol` into `(Path, symbol)`. Returns `(None, '')` if not a `::` code ref."""
    ref = (source_ref or "").strip()
    if "::" not in ref or ref.startswith(("git:", "http://", "https://", "ledger:")):
        return None, ""
    path_part, _, symbol = ref.partition("::")
    p = Path(path_part).expanduser()
    if not p.is_absolute():
        return None, symbol.strip()
    return p, symbol.strip()


def _hash_lines(lines: list[str]) -> str:
    normalized = "\n".join(line.rstrip() for line in lines).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _leading_comment_start(lines: list[str], decl_idx: int, *, max_comment_lines: int = 12) -> int:
    """Include an immediately preceding docblock (`/** ... */` or consecutive `//`/`#` lines) if short."""
    i = decl_idx - 1
    while i >= 0 and not lines[i].strip():
        return decl_idx  # Blank line separates docblock from declaration
    if i < 0:
        return decl_idx

    s = lines[i].strip()
    # Block doc comment ending with */
    if s.endswith("*/"):
        start = i
        while start >= 0 and (decl_idx - start) <= max_comment_lines:
            if "/*" in lines[start]:
                return start
            start -= 1
        return decl_idx

    # Line comments (// or #)
    if s.startswith(("//", "#")):
        start = i
        while start > 0 and (decl_idx - start) < max_comment_lines and lines[start - 1].strip().startswith(("//", "#")):
            start -= 1
        return start

    return decl_idx


def _find_brace_symbol_span(lines: list[str], symbol: str) -> tuple[int, int] | None:
    """Locate a class/object/interface/fun/func/function/const/struct/fn declaration and its balanced brace block."""
    esc = re.escape(symbol)
    # Matches Kotlin/Java/TS/JS/Go/Rust declarations cleanly
    decl_re = re.compile(
        rf"^\s*(?:export\s+)?(?:default\s+)?(?:pub(?:\([^)]*\))?\s+)?"
        rf"(?:(?:public|private|protected|internal|open|final|abstract|sealed|data|value|enum|inner|inline|noinline|crossinline|suspend|override|operator|infix|tailrec|async|static|const|readonly)\s+)*"
        rf"(?:(?:class|object|interface|fun|func|function|struct|enum|trait|type|impl|fn|const|let|var)\s+)"
        rf"(?:<[^>]+>\s+)?"
        rf"(?:\([^)]*\)\s+)?"  # Go receiver `(r *Recv)`
        rf"(?:[A-Za-z0-9_?.<>]+\.)?"  # Kotlin extension receiver `Foo.bar`
        rf"{esc}\b"
    )
    decl_idx: int | None = None
    for idx, line in enumerate(lines):
        if decl_re.search(line):
            decl_idx = idx
            break
    if decl_idx is None:
        return None

    # Walk forward to find opening '{' and track brace balance (or single-expression `= ...` function)
    depth = 0
    seen_brace = False
    end_idx = decl_idx
    in_block_comment = False

    for idx in range(decl_idx, len(lines)):
        raw = lines[idx]
        j = 0
        n = len(raw)
        in_str: str | None = None
        while j < n:
            ch = raw[j]
            nxt = raw[j + 1] if j + 1 < n else ""
            if in_block_comment:
                if ch == "*" and nxt == "/":
                    in_block_comment = False
                    j += 2
                    continue
                j += 1
                continue
            if in_str:
                if ch == "\\":
                    j += 2
                    continue
                if ch == in_str:
                    in_str = None
                j += 1
                continue
            if ch == "/" and nxt == "/":
                break
            if ch == "/" and nxt == "*":
                in_block_comment = True
                j += 2
                continue
            if ch in ('"', "'", "`"):
                in_str = ch
                j += 1
                continue
            if ch == "{":
                depth += 1
                seen_brace = True
            elif ch == "}":
                depth -= 1
            j += 1

        end_idx = idx
        if seen_brace and depth <= 0:
            break
        # Single-expression Kotlin/TS arrow/equals without braces: stop at next blank line or next declaration after signature
        if not seen_brace and idx > decl_idx and not raw.strip():
            end_idx = idx - 1
            break
        if not seen_brace and idx - decl_idx >= 12:
            break

    start_idx = _leading_comment_start(lines, decl_idx)
    return start_idx, end_idx


def _find_python_symbol_span(lines: list[str], symbol: str) -> tuple[int, int] | None:
    """Locate a Python `def` / `async def` / `class` block by indentation."""
    esc = re.escape(symbol)
    decl_re = re.compile(rf"^(\s*)(?:async\s+def|def|class)\s+{esc}\b")
    decl_idx: int | None = None
    base_indent = 0
    for idx, line in enumerate(lines):
        m = decl_re.match(line)
        if m:
            decl_idx = idx
            base_indent = len(m.group(1))
            break
    if decl_idx is None:
        return None

    end_idx = decl_idx
    for idx in range(decl_idx + 1, len(lines)):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= base_indent:
            break
        end_idx = idx

    start_idx = _leading_comment_start(lines, decl_idx)
    return start_idx, end_idx


def extract_code_symbol(
    file_path: Path,
    symbol: str = "",
    *,
    max_lines: int = 45,
) -> dict[str, Any]:
    """Extract a symbol-anchored (or line-range) code skeleton and its symbol-level SHA-256 hash."""
    path = Path(file_path).expanduser().resolve()
    lang = detect_language(path)
    if not path.is_file():
        return {"found": False, "lang": lang, "error": f"File not found: {path}"}

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    sym = (symbol or "").strip()

    if not sym:
        span_lines = lines
        start_line = 1
        end_line = len(lines)
    else:
        m_range = _LINE_RANGE_RE.match(sym)
        if m_range:
            s_l = max(1, int(m_range.group(1)))
            e_l = int(m_range.group(2)) if m_range.group(2) else min(len(lines), s_l + max_lines - 1)
            if s_l > len(lines):
                return {"found": False, "lang": lang, "error": f"Line {s_l} exceeds file length ({len(lines)})"}
            e_l = min(len(lines), max(s_l, e_l))
            span_lines = lines[s_l - 1 : e_l]
            start_line, end_line = s_l, e_l
        else:
            span = _find_python_symbol_span(lines, sym) if lang == "python" else _find_brace_symbol_span(lines, sym)
            if span is None:
                return {"found": False, "lang": lang, "error": f"Symbol '{sym}' not found in {path.name}"}
            s_idx, e_idx = span
            span_lines = lines[s_idx : e_idx + 1]
            start_line, end_line = s_idx + 1, e_idx + 1

    sym_hash = _hash_lines(span_lines)
    truncated = len(span_lines) > max_lines
    shown = span_lines[:max_lines]
    snippet = "\n".join(shown)
    if truncated:
        comment_prefix = "#" if lang in ("python", "bash") else "//"
        snippet += f"\n{comment_prefix} ... ({len(span_lines) - max_lines} more lines; read source_ref for full body)"

    return {
        "found": True,
        "lang": lang,
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": len(span_lines),
        "truncated": truncated,
        "hash": sym_hash,
        "snippet": snippet,
    }


def detect_repo_root(path: Path) -> Path | None:
    """Walk upwards from `path` (file or directory) to find git repo root or manifest root."""
    cur = Path(path).expanduser().resolve()
    if cur.is_file():
        cur = cur.parent
    for cand in (cur, *cur.parents):
        if (cand / ".git").exists():
            return cand
    for cand in (cur, *cur.parents):
        for mf in ("gradle/libs.versions.toml", "package.json", "go.mod", "pyproject.toml", "Cargo.toml"):
            if (cand / mf).is_file():
                return cand
    return None


def _parse_gradle_toml(path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return versions
    raw_vers = data.get("versions") if isinstance(data.get("versions"), dict) else {}
    for k, v in raw_vers.items():
        if not isinstance(v, str):
            continue
        k_norm = k.strip().lower().replace("_", "-")
        if k_norm == "springboot":
            k_norm = "spring-boot"
        if k_norm in _GRADLE_TRACKED_VERSIONS:
            nv = normalize_major_minor(v)
            if nv:
                versions[k_norm] = nv

    # Detect key Spring/Reactor capabilities managed by Spring Boot BOM
    libs = data.get("libraries") if isinstance(data.get("libraries"), dict) else {}
    sb_ver = versions.get("spring-boot")
    lib_text = json.dumps(libs).lower()
    if "webflux" in lib_text and sb_ver:
        versions["webflux"] = sb_ver
    if "r2dbc" in lib_text and sb_ver and "r2dbc" not in versions:
        versions["r2dbc"] = sb_ver
    return versions


def _parse_package_json(path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return versions
    deps: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        sec = data.get(key)
        if isinstance(sec, dict):
            deps.update(sec)
    for pkg in _NPM_TRACKED_PKGS:
        raw_v = deps.get(pkg)
        if isinstance(raw_v, str):
            nv = normalize_major_minor(raw_v)
            if nv:
                versions[pkg] = nv
    return versions


def _parse_go_mod(path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return versions
    if m := re.search(r"^go\s+(\d+\.\d+)", txt, re.MULTILINE):
        versions["go"] = m.group(1)
    for mod, alias in (
        ("github.com/gin-gonic/gin", "gin"),
        ("github.com/labstack/echo", "echo"),
        ("github.com/gofiber/fiber", "fiber"),
        ("github.com/go-chi/chi", "chi"),
        ("github.com/jackc/pgx", "pgx"),
        ("modernc.org/sqlite", "sqlite"),
    ):
        m2 = re.search(rf"{re.escape(mod)}(?:/v\d+)?\s+v?(\d+\.\d+)", txt)
        if m2:
            versions[alias] = m2.group(1)
    return versions


def _parse_pyproject_toml(path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return versions
    proj = data.get("project") if isinstance(data.get("project"), dict) else {}
    req_py = proj.get("requires-python")
    if isinstance(req_py, str):
        nv = normalize_major_minor(req_py)
        if nv:
            versions["python"] = nv
    deps = proj.get("dependencies") if isinstance(proj.get("dependencies"), list) else []
    opt = proj.get("optional-dependencies") if isinstance(proj.get("optional-dependencies"), dict) else {}
    all_deps = list(deps)
    for vals in opt.values():
        if isinstance(vals, list):
            all_deps.extend(vals)
    for d in all_deps:
        if not isinstance(d, str):
            continue
        low = d.strip().lower()
        for pkg in _PY_TRACKED_PKGS:
            if low.startswith(pkg) and (len(low) == len(pkg) or low[len(pkg)] in " <>!=~;["):
                nv = normalize_major_minor(low[len(pkg) :])
                if nv:
                    versions[pkg] = nv
                break
    return versions


def _parse_cargo_toml(path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return versions
    pkg = data.get("package") if isinstance(data.get("package"), dict) else {}
    if isinstance(pkg.get("edition"), str):
        versions["rust-edition"] = pkg["edition"]
    deps = data.get("dependencies") if isinstance(data.get("dependencies"), dict) else {}
    for crate in _CARGO_TRACKED_CRATES:
        val = deps.get(crate)
        raw_v = val if isinstance(val, str) else (val.get("version") if isinstance(val, dict) else None)
        if isinstance(raw_v, str):
            nv = normalize_major_minor(raw_v)
            if nv:
                versions[crate] = nv
    return versions


def detect_repo_stack_versions(path: Path | None = None) -> tuple[dict[str, str], str]:
    """Detect key framework/runtime major.minor versions from the nearest repo manifest."""
    root = detect_repo_root(path or Path.cwd())
    if root is None:
        return {}, ""

    stack: dict[str, str] = {}
    manifests_found: list[str] = []

    checks = (
        ("gradle/libs.versions.toml", _parse_gradle_toml),
        ("go.mod", _parse_go_mod),
        ("pyproject.toml", _parse_pyproject_toml),
        ("Cargo.toml", _parse_cargo_toml),
        ("package.json", _parse_package_json),
        ("web/package.json", _parse_package_json),
    )
    for rel, parser in checks:
        p = root / rel
        if p.is_file():
            parsed = parser(p)
            if parsed:
                for k, v in parsed.items():
                    stack.setdefault(k, v)
                manifests_found.append(rel)

    return stack, manifests_found[0] if manifests_found else ""


def format_stack_param(stack: dict[str, str] | None) -> str | None:
    if not stack:
        return None
    parts = [f"{k}={v}" for k, v in sorted(stack.items()) if k and v]
    return ",".join(parts) if parts else None


def parse_stack_param(raw: str | None) -> dict[str, str]:
    if not raw or not raw.strip():
        return {}
    s = raw.strip()
    if s.startswith("{"):
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                return {str(k).strip().lower(): str(v).strip() for k, v in d.items() if str(k).strip() and str(v).strip()}
        except json.JSONDecodeError:
            return {}
    out: dict[str, str] = {}
    for pair in s.split(","):
        k, sep, v = pair.partition("=")
        if sep and k.strip() and v.strip():
            out[k.strip().lower()] = v.strip()
    return out


def check_compat_drift(
    compat: dict[str, Any] | None,
    cwd_stack: dict[str, str] | None,
) -> dict[str, Any] | None:
    """Compare a pattern card's SemVer stack envelope against the current workspace's manifest versions.

    Advisory only (never hides timeless ideas): alerts the agent when a framework crossed a major version
    so it preserves the optimization essence while adapting syntax to the newer framework API.
    """
    if not compat or not isinstance(compat, dict):
        return None
    card_stack = compat.get("stack")
    if not isinstance(card_stack, dict) or not card_stack or not cwd_stack:
        return None

    overlap = [k for k in card_stack if k in cwd_stack]
    if not overlap:
        return {"status": "cross_stack"}

    major_drifts: list[str] = []
    minor_drifts: list[str] = []
    for k in sorted(overlap):
        c_ver = str(card_stack[k])
        w_ver = str(cwd_stack[k])
        ct = _major_minor_tuple(c_ver)
        wt = _major_minor_tuple(w_ver)
        if ct is None or wt is None:
            continue
        if ct[0] != wt[0]:
            major_drifts.append(f"{k} {c_ver} -> {w_ver}")
        elif ct[1] != wt[1]:
            minor_drifts.append(f"{k} {c_ver} -> {w_ver}")

    if major_drifts:
        return {
            "status": "major_drift",
            "drifts": major_drifts,
            "note": "核心优化思想仍适用，但框架已跨主版本；请先核对新版框架 API 再按核心本质重写，勿直接照抄旧写法",
        }
    if minor_drifts:
        return {
            "status": "minor_drift",
            "drifts": minor_drifts,
        }
    return {"status": "compatible"}
