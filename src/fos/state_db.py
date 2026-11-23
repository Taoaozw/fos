"""Unified persistent SQLite WAL state database (`state.db`) for FlowOS.

Single durable database storing all non-snapshot FlowOS state:
- `ledger_atoms`: immutable verbatim L2 experience atoms (replaces `ledger/events-*.jsonl`)
- `eval_cases`: golden regression test cases (replaces `eval/golden.jsonl`)
- `proposals`: governance inbox lifecycle & dismissal cooldowns (replaces `proposals.jsonl`)
- `usage_stats`: PlugMem recall utility telemetry (replaces `usage_stats.json`)
- `distill_cache`: LLM distillation & sleep reflection cache (replaces `distill_cache.json`)
- `kv_store`: snapshot pointer (`current.json`), proposal scan cache, ingest cursors, migration markers

Transparently and idempotently migrates any legacy JSON/JSONL files on first open.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fos.models import EvalCase, LedgerAtom, now_iso


class StateDB:
    """Persistent SQLite WAL store under `<data_dir>/state.db` (or an explicit `.db` path)."""

    def __init__(self, path: Path) -> None:
        p = Path(path).resolve()
        if p.suffix == ".db":
            self.db_path = p
            self.data_dir = p.parent
        else:
            self.data_dir = p
            self.db_path = p / "state.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self._migrate_legacy_files()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> StateDB:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ledger_atoms (
                id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                device TEXT NOT NULL,
                kind TEXT NOT NULL,
                scope TEXT NOT NULL,
                source TEXT NOT NULL,
                raw TEXT NOT NULL,
                meta_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_ledger_atoms_ts ON ledger_atoms(ts, id);
            CREATE INDEX IF NOT EXISTS idx_ledger_atoms_scope ON ledger_atoms(scope);
            CREATE INDEX IF NOT EXISTS idx_ledger_atoms_kind ON ledger_atoms(kind);

            CREATE TABLE IF NOT EXISTS eval_cases (
                id TEXT PRIMARY KEY,
                suite TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                scope TEXT,
                as_of TEXT,
                expected_ids_json TEXT NOT NULL DEFAULT '[]',
                forbidden_ids_json TEXT NOT NULL DEFAULT '[]',
                must_preserve_terms_json TEXT NOT NULL DEFAULT '[]',
                evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                node_id TEXT,
                pinned INTEGER NOT NULL DEFAULT 0,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                seq INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_eval_cases_seq ON eval_cases(seq);

            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                fingerprint TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                proposed_type TEXT NOT NULL DEFAULT 'principle',
                proposed_scope TEXT NOT NULL DEFAULT 'global',
                proposed_severity TEXT NOT NULL DEFAULT 'P1',
                proposed_rule TEXT NOT NULL DEFAULT '',
                proposed_rationale TEXT NOT NULL DEFAULT '',
                proposed_guard_hint TEXT NOT NULL DEFAULT '',
                proposed_trigger_when_json TEXT NOT NULL DEFAULT '[]',
                evidence_repos_json TEXT NOT NULL DEFAULT '[]',
                evidence_nodes_json TEXT NOT NULL DEFAULT '[]',
                evidence_manifests_json TEXT NOT NULL DEFAULT '[]',
                target_node_ids_json TEXT NOT NULL DEFAULT '[]',
                estimated_l0_delta INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0.8,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT,
                result_node_id TEXT,
                dismiss_reason TEXT NOT NULL DEFAULT '',
                seq INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
            CREATE INDEX IF NOT EXISTS idx_proposals_fingerprint ON proposals(fingerprint);
            CREATE INDEX IF NOT EXISTS idx_proposals_seq ON proposals(seq);

            CREATE TABLE IF NOT EXISTS usage_stats (
                node_id TEXT PRIMARY KEY,
                hits INTEGER NOT NULL DEFAULT 0,
                useful INTEGER NOT NULL DEFAULT 0,
                noise INTEGER NOT NULL DEFAULT 0,
                guard_blocks INTEGER NOT NULL DEFAULT 0,
                utility_score REAL NOT NULL DEFAULT 0.5,
                last_hit_at TEXT NOT NULL DEFAULT '',
                last_feedback_at TEXT NOT NULL DEFAULT '',
                recent_notes_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS distill_cache (
                cache_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kv_store (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            );
            """
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Generic KV store
    # ------------------------------------------------------------------

    def get_kv(self, namespace: str, key: str, default: Any = None) -> Any:
        row = self.conn.execute(
            "SELECT value_json FROM kv_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    def set_kv(self, namespace: str, key: str, value: Any, *, commit: bool = True) -> None:
        self.conn.execute(
            """
            INSERT INTO kv_store (namespace, key, value_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(namespace, key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (namespace, key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), now_iso()),
        )
        if commit:
            self.conn.commit()

    # ------------------------------------------------------------------
    # Transparent one-time legacy JSON/JSONL migration
    # ------------------------------------------------------------------

    def _done_migrations(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT key FROM kv_store WHERE namespace = 'migration'"
        ).fetchall()
        return {str(r["key"]) for r in rows}

    def _mark_migrated(self, name: str, count: int) -> None:
        self.set_kv("migration", name, {"migrated_at": now_iso(), "count": count}, commit=False)

    def _migrate_legacy_files(self) -> None:
        done = self._done_migrations()
        changed = False

        # 1. ledger/events-*.jsonl -> ledger_atoms
        ledger_dir = self.data_dir / "ledger"
        if "ledger" not in done and ledger_dir.is_dir():
            shards = sorted(ledger_dir.glob("events-*.jsonl"))
            if shards:
                cnt = 0
                for shard in shards:
                    try:
                        with shard.open("r", encoding="utf-8") as fh:
                            for line in fh:
                                s = line.strip()
                                if not s:
                                    continue
                                try:
                                    atom = LedgerAtom.from_dict(json.loads(s))
                                except (json.JSONDecodeError, KeyError, TypeError):
                                    continue
                                cur = self.conn.execute(
                                    """
                                    INSERT OR IGNORE INTO ledger_atoms
                                        (id, ts, device, kind, scope, source, raw, meta_json)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        atom.id,
                                        atom.ts,
                                        atom.device,
                                        atom.kind,
                                        atom.scope,
                                        atom.source,
                                        atom.raw,
                                        json.dumps(atom.meta, ensure_ascii=False, separators=(",", ":")),
                                    ),
                                )
                                cnt += max(0, cur.rowcount)
                    except OSError:
                        continue
                self._mark_migrated("ledger", cnt)
                changed = True

        # 2. eval/golden.jsonl -> eval_cases
        golden_path = self.data_dir / "eval" / "golden.jsonl"
        if "eval_golden" not in done and golden_path.is_file():
            by_id: dict[str, EvalCase] = {}
            order: list[str] = []
            try:
                with golden_path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        s = line.strip()
                        if not s:
                            continue
                        try:
                            c = EvalCase.from_dict(json.loads(s))
                        except (json.JSONDecodeError, KeyError, TypeError):
                            continue
                        if c.id not in by_id:
                            order.append(c.id)
                        by_id[c.id] = c
            except OSError:
                pass
            cnt = 0
            for idx, cid in enumerate(order, start=1):
                c = by_id[cid]
                cur = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO eval_cases
                        (id, suite, query, scope, as_of,
                         expected_ids_json, forbidden_ids_json, must_preserve_terms_json,
                         evidence_ids_json, node_id, pinned, description, created_at, seq)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        c.id,
                        c.suite,
                        c.query,
                        c.scope,
                        c.as_of,
                        json.dumps(c.expected_ids, ensure_ascii=False),
                        json.dumps(c.forbidden_ids, ensure_ascii=False),
                        json.dumps(c.must_preserve_terms, ensure_ascii=False),
                        json.dumps(c.evidence_ids, ensure_ascii=False),
                        c.node_id,
                        1 if c.pinned else 0,
                        c.description,
                        c.created_at,
                        idx,
                    ),
                )
                cnt += max(0, cur.rowcount)
            self._mark_migrated("eval_golden", cnt)
            changed = True

        # 3. proposals.jsonl -> proposals
        proposals_path = self.data_dir / "proposals.jsonl"
        if "proposals" not in done and proposals_path.is_file():
            p_by_id: dict[str, dict[str, Any]] = {}
            p_order: list[str] = []
            try:
                with proposals_path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        s = line.strip()
                        if not s:
                            continue
                        try:
                            d = json.loads(s)
                            pid = str(d.get("id") or "")
                            if not pid:
                                continue
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            continue
                        if pid not in p_by_id:
                            p_order.append(pid)
                        p_by_id[pid] = d
            except OSError:
                pass
            cnt = 0
            for idx, pid in enumerate(p_order, start=1):
                d = p_by_id[pid]
                cur = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO proposals (
                        id, kind, status, fingerprint, title,
                        proposed_type, proposed_scope, proposed_severity,
                        proposed_rule, proposed_rationale, proposed_guard_hint,
                        proposed_trigger_when_json, evidence_repos_json,
                        evidence_nodes_json, evidence_manifests_json, target_node_ids_json,
                        estimated_l0_delta, confidence, created_at, updated_at,
                        resolved_at, result_node_id, dismiss_reason, seq
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        str(d.get("kind") or "promote_global"),
                        str(d.get("status") or "pending"),
                        str(d.get("fingerprint") or ""),
                        str(d.get("title") or ""),
                        str(d.get("proposed_type") or "principle"),
                        str(d.get("proposed_scope") or "global"),
                        str(d.get("proposed_severity") or "P1"),
                        str(d.get("proposed_rule") or ""),
                        str(d.get("proposed_rationale") or ""),
                        str(d.get("proposed_guard_hint") or ""),
                        json.dumps(d.get("proposed_trigger_when") or [], ensure_ascii=False),
                        json.dumps(d.get("evidence_repos") or [], ensure_ascii=False),
                        json.dumps(d.get("evidence_nodes") or [], ensure_ascii=False),
                        json.dumps(d.get("evidence_manifests") or [], ensure_ascii=False),
                        json.dumps(d.get("target_node_ids") or [], ensure_ascii=False),
                        int(d.get("estimated_l0_delta") or 0),
                        float(d.get("confidence") if d.get("confidence") is not None else 0.8),
                        str(d.get("created_at") or now_iso()),
                        str(d.get("updated_at") or now_iso()),
                        d.get("resolved_at"),
                        d.get("result_node_id"),
                        str(d.get("dismiss_reason") or ""),
                        idx,
                    ),
                )
                cnt += max(0, cur.rowcount)
            self._mark_migrated("proposals", cnt)
            changed = True

        # 4. proposal_scan_cache.json -> kv_store('proposal_scan_cache', 'state')
        scan_cache_path = self.data_dir / "proposal_scan_cache.json"
        if "proposal_scan_cache" not in done and scan_cache_path.is_file():
            try:
                data = json.loads(scan_cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and self.get_kv("proposal_scan_cache", "state") is None:
                    self.set_kv("proposal_scan_cache", "state", data, commit=False)
            except (OSError, json.JSONDecodeError):
                pass
            self._mark_migrated("proposal_scan_cache", 1)
            changed = True

        # 5. usage_stats.json -> usage_stats
        usage_path = self.data_dir / "usage_stats.json"
        if "usage_stats" not in done and usage_path.is_file():
            cnt = 0
            try:
                data = json.loads(usage_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    ts = now_iso()
                    for nid, ent in data.items():
                        if not isinstance(ent, dict):
                            continue
                        cur = self.conn.execute(
                            """
                            INSERT OR IGNORE INTO usage_stats (
                                node_id, hits, useful, noise, guard_blocks,
                                utility_score, last_hit_at, last_feedback_at,
                                recent_notes_json, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                str(nid),
                                int(ent.get("hits") or 0),
                                int(ent.get("useful") or 0),
                                int(ent.get("noise") or 0),
                                int(ent.get("guard_blocks") or 0),
                                float(ent.get("utility_score") if ent.get("utility_score") is not None else 0.5),
                                str(ent.get("last_hit_at") or ""),
                                str(ent.get("last_feedback_at") or ""),
                                json.dumps(ent.get("recent_notes") or [], ensure_ascii=False),
                                ts,
                            ),
                        )
                        cnt += max(0, cur.rowcount)
            except (OSError, json.JSONDecodeError):
                pass
            self._mark_migrated("usage_stats", cnt)
            changed = True

        # 6. distill_cache.json -> distill_cache
        distill_path = self.data_dir / "distill_cache.json"
        if "distill_cache" not in done and distill_path.is_file():
            cnt = 0
            try:
                data = json.loads(distill_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    ts = now_iso()
                    for ckey, payload in data.items():
                        if not isinstance(payload, dict):
                            continue
                        cur = self.conn.execute(
                            """
                            INSERT OR IGNORE INTO distill_cache (cache_key, payload_json, updated_at)
                            VALUES (?, ?, ?)
                            """,
                            (str(ckey), json.dumps(payload, ensure_ascii=False, separators=(",", ":")), ts),
                        )
                        cnt += max(0, cur.rowcount)
            except (OSError, json.JSONDecodeError):
                pass
            self._mark_migrated("distill_cache", cnt)
            changed = True

        # 7. current.json -> kv_store('snapshot', 'pointer')
        pointer_path = self.data_dir / "current.json"
        if "snapshot_pointer" not in done and pointer_path.is_file():
            try:
                data = json.loads(pointer_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and self.get_kv("snapshot", "pointer") is None:
                    self.set_kv("snapshot", "pointer", data, commit=False)
            except (OSError, json.JSONDecodeError):
                pass
            self._mark_migrated("snapshot_pointer", 1)
            changed = True

        if changed:
            self.conn.commit()

    def migrate_legacy_cursors(self, legacy_json_path: Path) -> None:
        """One-time migration of legacy `ingest_cursors.json` into `kv_store('ingest_cursors', 'state')`."""
        done = self._done_migrations()
        if "ingest_cursors" in done or not legacy_json_path.is_file():
            return
        try:
            data = json.loads(legacy_json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and self.get_kv("ingest_cursors", "state") is None:
                self.set_kv("ingest_cursors", "state", data, commit=False)
        except (OSError, json.JSONDecodeError):
            pass
        self._mark_migrated("ingest_cursors", 1)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Ledger Atoms
    # ------------------------------------------------------------------

    def insert_atom(self, atom: LedgerAtom) -> LedgerAtom:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO ledger_atoms
                (id, ts, device, kind, scope, source, raw, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                atom.id,
                atom.ts,
                atom.device,
                atom.kind,
                atom.scope,
                atom.source,
                atom.raw,
                json.dumps(atom.meta, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        self.conn.commit()
        return atom

    def _row_to_atom(self, row: sqlite3.Row) -> LedgerAtom:
        return LedgerAtom(
            id=row["id"],
            ts=row["ts"],
            device=row["device"],
            kind=row["kind"],
            scope=row["scope"],
            source=row["source"],
            raw=row["raw"],
            meta=json.loads(row["meta_json"] or "{}"),
        )

    def get_atom(self, atom_id: str) -> LedgerAtom | None:
        row = self.conn.execute(
            "SELECT * FROM ledger_atoms WHERE id = ?",
            (atom_id,),
        ).fetchone()
        return self._row_to_atom(row) if row is not None else None

    def find_atom_by_evidence_ref(
        self,
        scope: str,
        *,
        evidence_ref: str = "",
        commit_sha: str = "",
    ) -> LedgerAtom | None:
        """Cross-device idempotency lookup by `(scope, evidence_ref|commit_sha)` in `meta_json`."""
        ref = evidence_ref.strip()
        sha = commit_sha.strip()
        if not ref and not sha:
            return None
        clauses: list[str] = []
        params: list[Any] = [scope]
        if ref:
            clauses.append("json_extract(meta_json, '$.evidence_ref') = ?")
            params.append(ref)
        if sha:
            clauses.append("json_extract(meta_json, '$.commit_sha') = ?")
            params.append(sha)
        where = " OR ".join(clauses)
        row = self.conn.execute(
            f"SELECT * FROM ledger_atoms WHERE scope = ? AND ({where}) LIMIT 1",
            tuple(params),
        ).fetchone()
        return self._row_to_atom(row) if row is not None else None

    def get_atoms_by_ids(self, atom_ids: list[str]) -> list[LedgerAtom]:
        if not atom_ids:
            return []
        placeholders = ",".join("?" for _ in atom_ids)
        rows = self.conn.execute(
            f"SELECT * FROM ledger_atoms WHERE id IN ({placeholders})",
            tuple(atom_ids),
        ).fetchall()
        by_id = {r["id"]: self._row_to_atom(r) for r in rows}
        return [by_id[aid] for aid in atom_ids if aid in by_id]

    def list_atoms(self) -> list[LedgerAtom]:
        rows = self.conn.execute(
            "SELECT * FROM ledger_atoms ORDER BY ts ASC, id ASC"
        ).fetchall()
        return [self._row_to_atom(r) for r in rows]

    # ------------------------------------------------------------------
    # Golden Eval Cases
    # ------------------------------------------------------------------

    def upsert_eval_case(self, case: EvalCase) -> EvalCase:
        existing = self.conn.execute(
            "SELECT seq FROM eval_cases WHERE id = ?", (case.id,)
        ).fetchone()
        if existing is not None:
            seq = int(existing["seq"])
        else:
            max_row = self.conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM eval_cases").fetchone()
            seq = int(max_row["m"]) + 1
        self.conn.execute(
            """
            INSERT INTO eval_cases
                (id, suite, query, scope, as_of,
                 expected_ids_json, forbidden_ids_json, must_preserve_terms_json,
                 evidence_ids_json, node_id, pinned, description, created_at, seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                suite = excluded.suite,
                query = excluded.query,
                scope = excluded.scope,
                as_of = excluded.as_of,
                expected_ids_json = excluded.expected_ids_json,
                forbidden_ids_json = excluded.forbidden_ids_json,
                must_preserve_terms_json = excluded.must_preserve_terms_json,
                evidence_ids_json = excluded.evidence_ids_json,
                node_id = excluded.node_id,
                pinned = excluded.pinned,
                description = excluded.description,
                created_at = excluded.created_at
            """,
            (
                case.id,
                case.suite,
                case.query,
                case.scope,
                case.as_of,
                json.dumps(case.expected_ids, ensure_ascii=False),
                json.dumps(case.forbidden_ids, ensure_ascii=False),
                json.dumps(case.must_preserve_terms, ensure_ascii=False),
                json.dumps(case.evidence_ids, ensure_ascii=False),
                case.node_id,
                1 if case.pinned else 0,
                case.description,
                case.created_at,
                seq,
            ),
        )
        self.conn.commit()
        return case

    def list_eval_cases(self) -> list[EvalCase]:
        rows = self.conn.execute("SELECT * FROM eval_cases ORDER BY seq ASC").fetchall()
        out: list[EvalCase] = []
        for r in rows:
            out.append(
                EvalCase(
                    id=r["id"],
                    suite=r["suite"],
                    query=r["query"],
                    scope=r["scope"],
                    as_of=r["as_of"],
                    expected_ids=json.loads(r["expected_ids_json"] or "[]"),
                    forbidden_ids=json.loads(r["forbidden_ids_json"] or "[]"),
                    must_preserve_terms=json.loads(r["must_preserve_terms_json"] or "[]"),
                    evidence_ids=json.loads(r["evidence_ids_json"] or "[]"),
                    node_id=r["node_id"],
                    pinned=bool(r["pinned"]),
                    description=r["description"],
                    created_at=r["created_at"],
                )
            )
        return out

    # ------------------------------------------------------------------
    # Proposals (Governance Inbox)
    # ------------------------------------------------------------------

    def upsert_proposal_dict(self, d: dict[str, Any]) -> dict[str, Any]:
        pid = str(d["id"])
        existing = self.conn.execute(
            "SELECT seq FROM proposals WHERE id = ?", (pid,)
        ).fetchone()
        if existing is not None:
            seq = int(existing["seq"])
        else:
            max_row = self.conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM proposals").fetchone()
            seq = int(max_row["m"]) + 1
        self.conn.execute(
            """
            INSERT INTO proposals (
                id, kind, status, fingerprint, title,
                proposed_type, proposed_scope, proposed_severity,
                proposed_rule, proposed_rationale, proposed_guard_hint,
                proposed_trigger_when_json, evidence_repos_json,
                evidence_nodes_json, evidence_manifests_json, target_node_ids_json,
                estimated_l0_delta, confidence, created_at, updated_at,
                resolved_at, result_node_id, dismiss_reason, seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind = excluded.kind,
                status = excluded.status,
                fingerprint = excluded.fingerprint,
                title = excluded.title,
                proposed_type = excluded.proposed_type,
                proposed_scope = excluded.proposed_scope,
                proposed_severity = excluded.proposed_severity,
                proposed_rule = excluded.proposed_rule,
                proposed_rationale = excluded.proposed_rationale,
                proposed_guard_hint = excluded.proposed_guard_hint,
                proposed_trigger_when_json = excluded.proposed_trigger_when_json,
                evidence_repos_json = excluded.evidence_repos_json,
                evidence_nodes_json = excluded.evidence_nodes_json,
                evidence_manifests_json = excluded.evidence_manifests_json,
                target_node_ids_json = excluded.target_node_ids_json,
                estimated_l0_delta = excluded.estimated_l0_delta,
                confidence = excluded.confidence,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                resolved_at = excluded.resolved_at,
                result_node_id = excluded.result_node_id,
                dismiss_reason = excluded.dismiss_reason
            """,
            (
                pid,
                str(d.get("kind") or "promote_global"),
                str(d.get("status") or "pending"),
                str(d.get("fingerprint") or ""),
                str(d.get("title") or ""),
                str(d.get("proposed_type") or "principle"),
                str(d.get("proposed_scope") or "global"),
                str(d.get("proposed_severity") or "P1"),
                str(d.get("proposed_rule") or ""),
                str(d.get("proposed_rationale") or ""),
                str(d.get("proposed_guard_hint") or ""),
                json.dumps(d.get("proposed_trigger_when") or [], ensure_ascii=False),
                json.dumps(d.get("evidence_repos") or [], ensure_ascii=False),
                json.dumps(d.get("evidence_nodes") or [], ensure_ascii=False),
                json.dumps(d.get("evidence_manifests") or [], ensure_ascii=False),
                json.dumps(d.get("target_node_ids") or [], ensure_ascii=False),
                int(d.get("estimated_l0_delta") or 0),
                float(d.get("confidence") if d.get("confidence") is not None else 0.8),
                str(d.get("created_at") or now_iso()),
                str(d.get("updated_at") or now_iso()),
                d.get("resolved_at"),
                d.get("result_node_id"),
                str(d.get("dismiss_reason") or ""),
                seq,
            ),
        )
        self.conn.commit()
        return d

    def _row_to_proposal_dict(self, r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"],
            "kind": r["kind"],
            "status": r["status"],
            "fingerprint": r["fingerprint"],
            "title": r["title"],
            "proposed_type": r["proposed_type"],
            "proposed_scope": r["proposed_scope"],
            "proposed_severity": r["proposed_severity"],
            "proposed_rule": r["proposed_rule"],
            "proposed_rationale": r["proposed_rationale"],
            "proposed_guard_hint": r["proposed_guard_hint"],
            "proposed_trigger_when": json.loads(r["proposed_trigger_when_json"] or "[]"),
            "evidence_repos": json.loads(r["evidence_repos_json"] or "[]"),
            "evidence_nodes": json.loads(r["evidence_nodes_json"] or "[]"),
            "evidence_manifests": json.loads(r["evidence_manifests_json"] or "[]"),
            "target_node_ids": json.loads(r["target_node_ids_json"] or "[]"),
            "estimated_l0_delta": int(r["estimated_l0_delta"]),
            "confidence": float(r["confidence"]),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "resolved_at": r["resolved_at"],
            "result_node_id": r["result_node_id"],
            "dismiss_reason": r["dismiss_reason"],
        }

    def get_proposal_dict(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        return self._row_to_proposal_dict(row) if row is not None else None

    def list_proposal_dicts(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status and status != "all":
            rows = self.conn.execute(
                "SELECT * FROM proposals WHERE status = ? ORDER BY seq ASC",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM proposals ORDER BY seq ASC").fetchall()
        return [self._row_to_proposal_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Usage Stats (PlugMem Utility Telemetry)
    # ------------------------------------------------------------------

    def _row_to_usage_dict(self, r: sqlite3.Row) -> dict[str, Any]:
        out: dict[str, Any] = {
            "hits": int(r["hits"]),
            "useful": int(r["useful"]),
            "noise": int(r["noise"]),
            "guard_blocks": int(r["guard_blocks"]),
            "utility_score": float(r["utility_score"]),
        }
        if r["last_hit_at"]:
            out["last_hit_at"] = r["last_hit_at"]
        if r["last_feedback_at"]:
            out["last_feedback_at"] = r["last_feedback_at"]
        notes = json.loads(r["recent_notes_json"] or "[]")
        if notes:
            out["recent_notes"] = notes
        return out

    def load_usage_stats(self) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM usage_stats").fetchall()
        return {str(r["node_id"]): self._row_to_usage_dict(r) for r in rows}

    def upsert_usage_entry(self, node_id: str, entry: dict[str, Any], *, commit: bool = True) -> None:
        self.conn.execute(
            """
            INSERT INTO usage_stats (
                node_id, hits, useful, noise, guard_blocks,
                utility_score, last_hit_at, last_feedback_at,
                recent_notes_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                hits = excluded.hits,
                useful = excluded.useful,
                noise = excluded.noise,
                guard_blocks = excluded.guard_blocks,
                utility_score = excluded.utility_score,
                last_hit_at = excluded.last_hit_at,
                last_feedback_at = excluded.last_feedback_at,
                recent_notes_json = excluded.recent_notes_json,
                updated_at = excluded.updated_at
            """,
            (
                node_id,
                int(entry.get("hits") or 0),
                int(entry.get("useful") or 0),
                int(entry.get("noise") or 0),
                int(entry.get("guard_blocks") or 0),
                float(entry.get("utility_score") if entry.get("utility_score") is not None else 0.5),
                str(entry.get("last_hit_at") or ""),
                str(entry.get("last_feedback_at") or ""),
                json.dumps(entry.get("recent_notes") or [], ensure_ascii=False),
                now_iso(),
            ),
        )
        if commit:
            self.conn.commit()

    def get_usage_entry(self, node_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM usage_stats WHERE node_id = ?", (node_id,)
        ).fetchone()
        return self._row_to_usage_dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # Distill Cache
    # ------------------------------------------------------------------

    def load_distill_cache(self) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute("SELECT cache_key, payload_json FROM distill_cache").fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            try:
                val = json.loads(r["payload_json"])
                if isinstance(val, dict):
                    out[str(r["cache_key"])] = val
            except json.JSONDecodeError:
                continue
        return out

    def upsert_distill_entries(self, entries: dict[str, dict[str, Any]]) -> None:
        if not entries:
            return
        ts = now_iso()
        self.conn.executemany(
            """
            INSERT INTO distill_cache (cache_key, payload_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            [
                (k, json.dumps(v, ensure_ascii=False, separators=(",", ":")), ts)
                for k, v in entries.items()
            ],
        )
        self.conn.commit()
