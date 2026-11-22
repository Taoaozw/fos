"""SQLite WAL + FTS5 + sqlite-vec + Bi-Temporal Graph + Atomic Snapshot Manager."""

from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import struct
from pathlib import Path
from typing import Any

from fos.errors import NotFoundError
from fos.models import CompiledNode, GraphEdge, is_valid_at, now_iso
from fos.tokens import tokenize_for_fts

SCHEMA_VERSION = "1"


def _pack_vec(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec  # type: ignore[import-not-found]

        if not hasattr(conn, "enable_load_extension"):
            return False
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        try:
            conn.enable_load_extension(False)
        except Exception:
            pass
        return False


def _sanitize_fts_query(query: str) -> str:
    tokens = tokenize_for_fts(query).split()
    if not tokens:
        return ""
    # Deduplicate while preserving order; quote each token to avoid FTS5 syntax errors on '-' or ':'
    seen: set[str] = set()
    parts: list[str] = []
    for tok in tokens:
        clean = tok.replace('"', "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            parts.append(f'"{clean}"')
        if len(parts) >= 24:
            break
    return " OR ".join(parts)


class SnapshotDB:
    """Single compiled SQLite WAL database with FTS5, sqlite-vec, and bi-temporal graph."""

    def __init__(
        self,
        db_path: Path,
        *,
        embedding_dim: int = 256,
        enable_vec: bool = True,
        read_only: bool = False,
    ) -> None:
        self.db_path = Path(db_path).resolve()
        self.embedding_dim = embedding_dim
        self.read_only = read_only
        if not read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        uri = f"file:{self.db_path}?mode=ro" if read_only else str(self.db_path)
        self.conn = sqlite3.connect(uri, uri=read_only, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.has_sqlite_vec = _try_load_sqlite_vec(self.conn) if enable_vec else False

        if not read_only:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        else:
            # Read stored embedding_dim if present
            dim_val = self.get_meta("embedding_dim")
            if dim_val and dim_val.isdigit():
                self.embedding_dim = int(dim_val)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> SnapshotDB:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                severity TEXT NOT NULL DEFAULT 'P1',
                valid_from TEXT NOT NULL,
                valid_until TEXT,
                superseded_by TEXT,
                seen_count INTEGER NOT NULL DEFAULT 1,
                confidence REAL NOT NULL DEFAULT 0.8,
                l0 TEXT NOT NULL,
                rule TEXT NOT NULL,
                rationale TEXT NOT NULL DEFAULT '',
                trigger_when_json TEXT NOT NULL DEFAULT '[]',
                entities_json TEXT NOT NULL DEFAULT '[]',
                contradicts_json TEXT NOT NULL DEFAULT '[]',
                evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_scope ON nodes(scope);
            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
            CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);

            CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                node_id UNINDEXED,
                scope UNINDEXED,
                node_type UNINDEXED,
                tokens,
                tokenize='unicode61'
            );

            CREATE TABLE IF NOT EXISTS node_vectors_raw (
                node_id TEXT PRIMARY KEY,
                dim INTEGER NOT NULL,
                embedding BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                src_id TEXT NOT NULL,
                dst_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_until TEXT,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(src_id, dst_id, edge_type, valid_from)
            );

            CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
            CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);
            """
        )

        if self.has_sqlite_vec:
            cur.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS node_vectors USING vec0("
                f"node_id TEXT PRIMARY KEY, embedding float[{self.embedding_dim}])"
            )

        cur.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        cur.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('embedding_dim', ?)", (str(self.embedding_dim),))
        self.conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        try:
            row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else default
        except sqlite3.OperationalError:
            return default

    def all_meta(self) -> dict[str, str]:
        try:
            rows = self.conn.execute("SELECT key, value FROM meta").fetchall()
            return {str(r["key"]): str(r["value"]) for r in rows}
        except sqlite3.OperationalError:
            return {}

    def _row_to_node(self, row: sqlite3.Row) -> CompiledNode:
        return CompiledNode(
            id=str(row["id"]),
            type=str(row["type"]),
            scope=str(row["scope"]),
            status=str(row["status"]),
            severity=str(row["severity"]),
            valid_from=str(row["valid_from"]),
            valid_until=row["valid_until"],
            superseded_by=row["superseded_by"],
            seen_count=int(row["seen_count"]),
            confidence=float(row["confidence"]),
            l0=str(row["l0"]),
            rule=str(row["rule"]),
            rationale=str(row["rationale"]),
            trigger_when=json.loads(row["trigger_when_json"]),
            entities=json.loads(row["entities_json"]),
            contradicts=json.loads(row["contradicts_json"]),
            evidence_ids=json.loads(row["evidence_ids_json"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _node_search_blob(self, node: CompiledNode) -> str:
        parts = [
            node.id,
            node.scope,
            node.type,
            node.l0,
            node.rule,
            node.rationale,
            " ".join(node.trigger_when),
            " ".join(node.entities),
        ]
        return tokenize_for_fts(" ".join(p for p in parts if p))

    def upsert_node(self, node: CompiledNode, *, embedding: list[float] | None = None) -> None:
        now = now_iso()
        if not node.created_at:
            node.created_at = node.valid_from or now
        node.updated_at = now

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO nodes (
                id, type, scope, status, severity, valid_from, valid_until,
                superseded_by, seen_count, confidence, l0, rule, rationale,
                trigger_when_json, entities_json, contradicts_json, evidence_ids_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node.id,
                node.type,
                node.scope,
                node.status,
                node.severity,
                node.valid_from,
                node.valid_until,
                node.superseded_by,
                node.seen_count,
                node.confidence,
                node.l0,
                node.rule,
                node.rationale,
                json.dumps(node.trigger_when, ensure_ascii=False),
                json.dumps(node.entities, ensure_ascii=False),
                json.dumps(node.contradicts, ensure_ascii=False),
                json.dumps(node.evidence_ids, ensure_ascii=False),
                node.created_at,
                node.updated_at,
            ),
        )

        # Keep FTS5 in sync
        cur.execute("DELETE FROM nodes_fts WHERE node_id = ?", (node.id,))
        cur.execute(
            "INSERT INTO nodes_fts (node_id, scope, node_type, tokens) VALUES (?, ?, ?, ?)",
            (node.id, node.scope, node.type, self._node_search_blob(node)),
        )

        if embedding is not None:
            blob = _pack_vec(embedding)
            cur.execute(
                "INSERT OR REPLACE INTO node_vectors_raw (node_id, dim, embedding) VALUES (?, ?, ?)",
                (node.id, len(embedding), blob),
            )
            if self.has_sqlite_vec and len(embedding) == self.embedding_dim:
                import sqlite_vec  # type: ignore[import-not-found]

                cur.execute("DELETE FROM node_vectors WHERE node_id = ?", (node.id,))
                cur.execute(
                    "INSERT INTO node_vectors (node_id, embedding) VALUES (?, ?)",
                    (node.id, sqlite_vec.serialize_float32(embedding)),
                )

        self.conn.commit()

    def get_node(self, node_id: str) -> CompiledNode | None:
        row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return self._row_to_node(row) if row else None

    def list_nodes(
        self,
        *,
        scope: str | None = None,
        kind: str | None = None,
        as_of: str | None = None,
        include_inactive: bool = False,
    ) -> list[CompiledNode]:
        clauses: list[str] = []
        params: list[Any] = []
        if scope:
            clauses.append("(scope = ? OR scope = 'global')")
            params.append(scope)
        if kind:
            clauses.append("type = ?")
            params.append(kind)
        sql = "SELECT * FROM nodes"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY valid_from DESC, id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        nodes = [self._row_to_node(r) for r in rows]
        if include_inactive:
            return nodes
        return [
            n
            for n in nodes
            if is_valid_at(n.valid_from, n.valid_until, as_of)
            and (as_of is not None or n.status == "active")
        ]

    def count_nodes(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()
        return int(row["c"]) if row else 0

    def next_sequence_id(self, prefix: str) -> str:
        rows = self.conn.execute(
            "SELECT id FROM nodes WHERE id LIKE ?",
            (f"{prefix}-%",),
        ).fetchall()
        max_num = 0
        for r in rows:
            parts = str(r["id"]).split("-")
            if len(parts) == 2 and parts[1].isdigit():
                max_num = max(max_num, int(parts[1]))
        return f"{prefix}-{max_num + 1:03d}"

    # --- Bi-temporal Graph Edges ---

    def add_edge(self, edge: GraphEdge) -> None:
        created = edge.created_at or now_iso()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO edges (
                src_id, dst_id, edge_type, valid_from, valid_until, meta_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.src_id,
                edge.dst_id,
                edge.edge_type,
                edge.valid_from,
                edge.valid_until,
                json.dumps(edge.meta, ensure_ascii=False),
                created,
            ),
        )
        self.conn.commit()

    def get_edges(self, node_id: str, *, as_of: str | None = None, include_inactive: bool = False) -> list[GraphEdge]:
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE src_id = ? OR dst_id = ? ORDER BY valid_from ASC",
            (node_id, node_id),
        ).fetchall()
        edges: list[GraphEdge] = []
        for r in rows:
            e = GraphEdge(
                src_id=str(r["src_id"]),
                dst_id=str(r["dst_id"]),
                edge_type=str(r["edge_type"]),
                valid_from=str(r["valid_from"]),
                valid_until=r["valid_until"],
                meta=json.loads(r["meta_json"]),
                created_at=str(r["created_at"]),
            )
            if include_inactive or is_valid_at(e.valid_from, e.valid_until, as_of):
                edges.append(e)
        return edges

    def all_edges(self) -> list[GraphEdge]:
        rows = self.conn.execute("SELECT * FROM edges ORDER BY id ASC").fetchall()
        return [
            GraphEdge(
                src_id=str(r["src_id"]),
                dst_id=str(r["dst_id"]),
                edge_type=str(r["edge_type"]),
                valid_from=str(r["valid_from"]),
                valid_until=r["valid_until"],
                meta=json.loads(r["meta_json"]),
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    def expand_graph(
        self,
        seed_ids: list[str],
        *,
        hops: int = 2,
        as_of: str | None = None,
    ) -> dict[str, int]:
        """BFS 1-2 hop expansion over relevant edges; returns {node_id: hop_distance}."""
        visited: dict[str, int] = {}
        frontier = list(dict.fromkeys(seed_ids))
        for sid in frontier:
            visited[sid] = 0

        for depth in range(1, hops + 1):
            next_frontier: list[str] = []
            for nid in frontier:
                for edge in self.get_edges(nid, as_of=as_of):
                    if edge.edge_type == "SUPERSEDES":
                        # Only traverse forward to superseding active node, never back to obsolete node
                        neighbor = edge.src_id if edge.dst_id == nid else None
                    else:
                        neighbor = edge.dst_id if edge.src_id == nid else edge.src_id
                    if neighbor and neighbor not in visited:
                        visited[neighbor] = depth
                        next_frontier.append(neighbor)
            frontier = next_frontier
            if not frontier:
                break

        return {nid: d for nid, d in visited.items() if d > 0}

    # --- Search channels ---

    def search_fts(self, query: str, *, limit: int = 30) -> list[tuple[str, float]]:
        fts_q = _sanitize_fts_query(query)
        if not fts_q:
            return []
        try:
            rows = self.conn.execute(
                """
                SELECT node_id, bm25(nodes_fts) AS rank_score
                FROM nodes_fts
                WHERE nodes_fts MATCH ?
                ORDER BY rank_score ASC
                LIMIT ?
                """,
                (fts_q, limit),
            ).fetchall()
            # SQLite bm25() returns negative values where more negative = better match
            return [(str(r["node_id"]), -float(r["rank_score"])) for r in rows]
        except sqlite3.OperationalError:
            return []

    def search_vec(self, query_vec: list[float], *, limit: int = 30) -> list[tuple[str, float]]:
        if not query_vec:
            return []
        if self.has_sqlite_vec and len(query_vec) == self.embedding_dim:
            try:
                import sqlite_vec  # type: ignore[import-not-found]

                rows = self.conn.execute(
                    """
                    SELECT node_id, distance
                    FROM node_vectors
                    WHERE embedding MATCH ? AND k = ?
                    ORDER BY distance ASC
                    """,
                    (sqlite_vec.serialize_float32(query_vec), limit),
                ).fetchall()
                return [(str(r["node_id"]), max(0.0, 1.0 - float(r["distance"]) / 2.0)) for r in rows]
            except sqlite3.OperationalError:
                pass

        # Fallback: scan node_vectors_raw in Python (for clients without sqlite-vec)
        rows = self.conn.execute("SELECT node_id, dim, embedding FROM node_vectors_raw").fetchall()
        scored: list[tuple[str, float]] = []
        for r in rows:
            if int(r["dim"]) != len(query_vec):
                continue
            sim = _cosine_similarity(query_vec, _unpack_vec(r["embedding"]))
            if sim > 0.0:
                scored.append((str(r["node_id"]), sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    # --- Compact offline cache bundle export/import ---

    def export_compact_bundle(self) -> dict[str, Any]:
        """Export lightweight L0/L1 + graph snapshot for portable clients (no heavy vectors)."""
        nodes = [n.to_dict() for n in self.list_nodes(include_inactive=True)]
        edges = [e.to_dict() for e in self.all_edges()]
        return {
            "schema_version": SCHEMA_VERSION,
            "meta": self.all_meta(),
            "nodes": nodes,
            "edges": edges,
        }

    def import_compact_bundle(self, bundle: dict[str, Any]) -> None:
        """Populate a clean offline cache DB from a compact bundle (L0/L1 + FTS5 + edges)."""
        for k, v in (bundle.get("meta") or {}).items():
            self.set_meta(str(k), str(v))
        for nd in bundle.get("nodes") or []:
            self.upsert_node(CompiledNode.from_dict(nd), embedding=None)
        for ed in bundle.get("edges") or []:
            self.add_edge(
                GraphEdge(
                    src_id=str(ed["src_id"]),
                    dst_id=str(ed["dst_id"]),
                    edge_type=str(ed["edge_type"]),
                    valid_from=str(ed["valid_from"]),
                    valid_until=ed.get("valid_until"),
                    meta=dict(ed.get("meta") or {}),
                    created_at=str(ed.get("created_at") or ""),
                )
            )


class SnapshotManager:
    """Manages versioned SQLite snapshots under <data_dir>/snapshots/ with atomic current.json pointer."""

    def __init__(self, data_dir: Path, *, embedding_dim: int = 256) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.snapshots_dir = self.data_dir / "snapshots"
        self.rejected_dir = self.snapshots_dir / "rejected"
        self.pointer_path = self.data_dir / "current.json"
        self.embedding_dim = embedding_dim
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def _read_pointer(self) -> dict[str, Any]:
        if not self.pointer_path.exists():
            return {"current": None, "history": [], "updated_at": None}
        return json.loads(self.pointer_path.read_text(encoding="utf-8"))

    def _write_pointer_atomic(self, state: dict[str, Any]) -> None:
        state["updated_at"] = now_iso()
        tmp = self.pointer_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.pointer_path)

    def current_version(self) -> str | None:
        return self._read_pointer().get("current")

    def history(self) -> list[str]:
        return list(self._read_pointer().get("history") or [])

    def version_path(self, version: str) -> Path:
        return self.snapshots_dir / f"{version}.db"

    def ensure_initialized(self) -> str:
        cur = self.current_version()
        if cur and self.version_path(cur).exists():
            return cur
        version = "v0001"
        path = self.version_path(version)
        with SnapshotDB(path, embedding_dim=self.embedding_dim) as db:
            db.set_meta("snapshot_version", version)
            db.set_meta("built_at", now_iso())
        self._write_pointer_atomic({"current": version, "history": [version]})
        return version

    def open_current(self, *, read_only: bool = False) -> SnapshotDB:
        ver = self.ensure_initialized()
        return SnapshotDB(self.version_path(ver), embedding_dim=self.embedding_dim, read_only=read_only)

    def open_version(self, version: str, *, read_only: bool = True) -> SnapshotDB:
        path = self.version_path(version)
        if not path.exists():
            raise NotFoundError(f"Snapshot version not found: {version}")
        return SnapshotDB(path, embedding_dim=self.embedding_dim, read_only=read_only)

    def next_version_name(self) -> str:
        existing = [p.stem for p in self.snapshots_dir.glob("v*.db")]
        nums = [int(v[1:]) for v in existing if v.startswith("v") and v[1:].isdigit()]
        state = self._read_pointer()
        for v in state.get("history") or []:
            if isinstance(v, str) and v.startswith("v") and v[1:].isdigit():
                nums.append(int(v[1:]))
        nxt = (max(nums) + 1) if nums else 1
        return f"v{nxt:04d}"

    def create_candidate(self) -> tuple[str, Path]:
        ver = self.next_version_name()
        path = self.version_path(ver)
        if path.exists():
            path.unlink()
        return ver, path

    def promote(self, version: str) -> dict[str, Any]:
        path = self.version_path(version)
        if not path.exists():
            raise NotFoundError(f"Cannot promote missing snapshot: {version}")
        state = self._read_pointer()
        hist: list[str] = list(state.get("history") or [])
        if not hist or hist[-1] != version:
            hist.append(version)
        new_state = {"current": version, "history": hist}
        self._write_pointer_atomic(new_state)
        return new_state

    def discard(self, version: str) -> Path | None:
        src = self.version_path(version)
        if not src.exists():
            return None
        self.rejected_dir.mkdir(parents=True, exist_ok=True)
        dst = self.rejected_dir / src.name
        shutil.move(str(src), str(dst))
        # Also remove WAL/SHM sidecars if present
        for suffix in ("-wal", "-shm"):
            side = Path(str(src) + suffix)
            if side.exists():
                side.unlink()
        return dst

    def undo(self) -> dict[str, Any]:
        state = self._read_pointer()
        hist: list[str] = list(state.get("history") or [])
        cur = state.get("current")
        if len(hist) < 2:
            raise NotFoundError("No previous snapshot available to roll back to.")
        # Find previous existing version in history
        prev_idx = len(hist) - 2
        while prev_idx >= 0 and not self.version_path(hist[prev_idx]).exists():
            prev_idx -= 1
        if prev_idx < 0:
            raise NotFoundError("Previous snapshot file is missing on disk.")
        target = hist[prev_idx]
        new_hist = hist[: prev_idx + 1]
        self._write_pointer_atomic({"current": target, "history": new_hist})
        return {"previous": cur, "current": target, "history": new_hist}
