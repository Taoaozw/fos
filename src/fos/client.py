"""Lightweight FlowOS client: online fosd HTTP + automatic offline SQLite cache + outbox queue."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fos.config import FosConfig, load_config, save_config
from fos.dream import run_dream
from fos.engine import (
    BrainEngine,
    query_snapshot_boot,
    query_snapshot_mirror,
    query_snapshot_recall,
)
from fos.errors import AuthError, FosError, GateFailedError, NotFoundError, OfflineError, UsageError
from fos.eval_gate import pin_node_in_engine, run_eval_on_engine
from fos.ingest import collect_hindsight_events
from fos.models import VALID_KINDS, now_iso
from fos.providers import LexicalReranker
from fos.redact import redact_secrets
from fos.store import SnapshotDB
from fos.ulid import new_atom_id


class FosClient:
    """Unified facade over local brain OR remote fosd (with offline cache fallback + outbox)."""

    def __init__(
        self,
        cfg: FosConfig | None = None,
        *,
        home: Path | None = None,
        timeout_s: float = 5.0,
        http_transport: Any | None = None,
    ) -> None:
        self.home = home
        self.cfg = cfg or load_config(home)
        self.timeout_s = timeout_s
        self.http_transport = http_transport
        self._local_engine: BrainEngine | None = None

    @property
    def cache_dir(self) -> Path:
        d = self.cfg.resolved_cache_dir(self.home)
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def cache_db_path(self) -> Path:
        return self.cache_dir / "snapshot.db"

    @property
    def outbox_path(self) -> Path:
        return self.cache_dir / "outbox.jsonl"

    @property
    def cursor_path(self) -> Path:
        return self.cache_dir / "ingest_cursors.json"

    @property
    def is_remote(self) -> bool:
        return self.cfg.mode == "remote" and bool(self.cfg.brain_url)

    def _engine(self) -> BrainEngine:
        if self._local_engine is None:
            self._local_engine = BrainEngine.from_config(self.cfg, home=self.home)
        return self._local_engine

    # --- HTTP helpers (lazy httpx import to keep CLI cold start < 60ms) ---

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.cfg.token:
            headers["Authorization"] = f"Bearer {self.cfg.token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import httpx

        url = f"{self.cfg.brain_url.rstrip('/')}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self.http_transport) as client:
                resp = client.request(method, url, headers=self._headers(), params=clean_params, json=json_body)
        except httpx.HTTPError as exc:
            raise OfflineError(f"Brain unreachable at {self.cfg.brain_url}: {exc}") from exc

        if resp.status_code in (401, 403):
            raise AuthError(f"Authentication failed ({resp.status_code}) against {self.cfg.brain_url}")

        if resp.status_code >= 400:
            detail: Any = {}
            try:
                body = resp.json()
                detail = body.get("detail", body)
            except Exception:
                detail = {"error": resp.text}
            msg = detail.get("error", resp.text) if isinstance(detail, dict) else str(detail)
            details = detail.get("details", {}) if isinstance(detail, dict) else {}
            if resp.status_code == 400:
                raise UsageError(msg, details=details)
            if resp.status_code == 404:
                raise NotFoundError(msg, details=details)
            if resp.status_code == 422:
                raise GateFailedError(msg, details=details)
            raise FosError(f"Brain error ({resp.status_code}): {msg}", details=details)

        # Opportunistically trigger debounced background self-update when brain advertises a newer CLI wheel
        if remote_ver := (resp.headers.get("X-Fos-Version") or "").strip():
            from fos.update import maybe_trigger_auto_update

            maybe_trigger_auto_update(self.cache_dir, remote_ver, home=self.home)

        return resp.json()

    # --- Offline cache & outbox helpers ---

    def _save_offline_snapshot(self, bundle: dict[str, Any]) -> str:
        tmp_path = self.cache_dir / "snapshot.tmp.db"
        if tmp_path.exists():
            tmp_path.unlink()
        with SnapshotDB(tmp_path, enable_vec=False) as db:
            db.import_compact_bundle(bundle)
            snap_ver = db.get_meta("snapshot_version", "unknown") or "unknown"
        os.replace(tmp_path, self.cache_db_path)
        for suffix in ("-wal", "-shm"):
            side = Path(str(tmp_path) + suffix)
            if side.exists():
                side.unlink()
        return snap_ver

    def _open_offline_cache(self) -> SnapshotDB:
        if not self.cache_db_path.exists():
            raise OfflineError("Brain is unreachable and no local offline cache exists. Run `fos sync` when online.")
        return SnapshotDB(self.cache_db_path, enable_vec=False, read_only=True)

    def _queue_outbox(self, item: dict[str, Any]) -> dict[str, Any]:
        # Always redact before touching disk even in offline outbox
        red = redact_secrets(item["content"])
        meta = dict(item.get("meta") or {})
        if red.had_secrets:
            meta["redacted_types"] = list(red.redacted_types)
        record = {
            "id": item.get("id") or new_atom_id(),
            "ts": item.get("ts") or now_iso(),
            "device": item.get("device") or self.cfg.device,
            "kind": item["kind"],
            "scope": item.get("scope") or "global",
            "source": item.get("source") or "manual",
            "content": red.text,
            "meta": meta,
        }
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        with self.outbox_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
        return record

    def _read_outbox(self) -> list[dict[str, Any]]:
        if not self.outbox_path.exists():
            return []
        items: list[dict[str, Any]] = []
        with self.outbox_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return items

    # --- Public client operations ---

    def init_remote(self, brain_url: str, token: str) -> dict[str, Any]:
        if not brain_url.strip():
            raise UsageError("brain_url is required")
        self.cfg.mode = "remote"
        self.cfg.brain_url = brain_url.strip().rstrip("/")
        self.cfg.token = token.strip()
        # Verify connectivity + pull initial compact cache
        health = self._request("GET", "/v1/health")
        bundle = self._request("GET", "/v1/sync/snapshot")
        snap_ver = self._save_offline_snapshot(bundle)
        cfg_file = save_config(self.cfg, self.home)
        return {
            "mode": "remote",
            "brain_url": self.cfg.brain_url,
            "config_path": str(cfg_file),
            "brain_health": health,
            "cached_snapshot": snap_ver,
            "cached_nodes": len(bundle.get("nodes") or []),
        }

    def sync(self) -> dict[str, Any]:
        if not self.is_remote:
            # Local mode: refresh local compact cache for parity
            bundle = self._engine().export_compact_snapshot()
            snap_ver = self._save_offline_snapshot(bundle)
            return {
                "mode": "local",
                "flushed_outbox": 0,
                "remaining_outbox": 0,
                "snapshot_version": snap_ver,
                "cached_nodes": len(bundle.get("nodes") or []),
            }

        pending = self._read_outbox()
        flushed = 0
        remaining: list[dict[str, Any]] = []
        if pending:
            resp = self._request("POST", "/v1/sync/outbox", json_body={"atoms": pending})
            accepted_ids = set(resp.get("accepted") or [])
            flushed = len(accepted_ids)
            remaining = [item for item in pending if item.get("id") not in accepted_ids]
            if remaining:
                self.outbox_path.write_text(
                    "".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in remaining),
                    encoding="utf-8",
                )
            elif self.outbox_path.exists():
                self.outbox_path.unlink()

        bundle = self._request("GET", "/v1/sync/snapshot")
        snap_ver = self._save_offline_snapshot(bundle)
        return {
            "mode": "online",
            "flushed_outbox": flushed,
            "remaining_outbox": len(remaining),
            "snapshot_version": snap_ver,
            "cached_nodes": len(bundle.get("nodes") or []),
        }

    def boot(self, *, project: str | None = None, budget: int = 300) -> dict[str, Any]:
        if not self.is_remote:
            return self._engine().boot(project=project, budget=budget)
        try:
            return self._request("GET", "/v1/boot", params={"project": project, "budget": budget})
        except OfflineError:
            with self._open_offline_cache() as db:
                return query_snapshot_boot(db, project=project, budget=budget, mode="offline_cache")

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
        if not self.is_remote:
            return self._engine().recall(query, kind=kind, scope=scope, as_of=as_of, budget=budget, limit=limit)
        try:
            return self._request(
                "GET",
                "/v1/recall",
                params={"q": query, "kind": kind, "scope": scope, "as_of": as_of, "budget": budget, "limit": limit},
            )
        except OfflineError:
            with self._open_offline_cache() as db:
                return query_snapshot_recall(
                    db,
                    query,
                    embedder=None,
                    reranker=LexicalReranker(),
                    kind=kind,
                    scope=scope,
                    as_of=as_of,
                    budget=budget,
                    limit=limit,
                    mode="offline_cache",
                )

    def get(self, node_id: str, *, tier: str = "L1", include_graph: bool = False) -> dict[str, Any]:
        if not self.is_remote:
            return self._engine().get(node_id, tier=tier, include_graph=include_graph)
        try:
            return self._request("GET", f"/v1/nodes/{node_id}", params={"tier": tier, "graph": include_graph})
        except OfflineError:
            with self._open_offline_cache() as db:
                node = db.get_node(node_id)
                if node is None:
                    raise NotFoundError(f"Node not found in offline cache: {node_id}")
                out: dict[str, Any] = {
                    "mode": "offline_cache",
                    "snapshot_version": db.get_meta("snapshot_version", "unknown"),
                    "tier": tier.upper(),
                    "id": node.id,
                    "l0": node.l0,
                }
                if tier.upper() != "L0":
                    out["node"] = node.to_l1_dict()
                if tier.upper() == "L2":
                    out["evidence"] = []
                    out["note"] = "L2 verbatim ledger requires online brain connection."
                if include_graph:
                    out["graph"] = [e.to_dict() for e in db.get_edges(node.id, include_inactive=True)]
                return out

    def mirror(self, proposal: str, *, scope: str | None = None) -> dict[str, Any]:
        if not self.is_remote:
            return self._engine().mirror(proposal, scope=scope)
        try:
            return self._request("POST", "/v1/mirror", json_body={"proposal": proposal, "scope": scope})
        except OfflineError:
            with self._open_offline_cache() as db:
                return query_snapshot_mirror(
                    db,
                    proposal,
                    embedder=None,
                    reranker=LexicalReranker(),
                    reasoner=None,
                    scope=scope,
                    mode="offline_cache",
                )

    def add(
        self,
        *,
        kind: str,
        content: str,
        scope: str = "global",
        source: str = "manual",
        supersedes: str | None = None,
        severity: str | None = None,
        entities: list[str] | None = None,
        evidence_ref: str | None = None,
        trigger_when: list[str] | None = None,
    ) -> dict[str, Any]:
        if kind not in VALID_KINDS:
            raise UsageError(f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}")
        if not content or not content.strip():
            raise UsageError("Content must be non-empty.")

        meta: dict[str, Any] = {}
        if supersedes:
            meta["supersedes"] = supersedes
        if severity:
            meta["severity"] = severity
        if entities:
            meta["entities"] = entities
        if evidence_ref:
            meta["evidence_ref"] = evidence_ref
        if trigger_when:
            meta["trigger_when"] = trigger_when

        if not self.is_remote:
            return self._engine().add_atom(
                kind=kind,
                content=content,
                scope=scope,
                source=source,
                device=self.cfg.device,
                meta=meta,
            )

        payload = {
            "id": new_atom_id(),
            "ts": now_iso(),
            "device": self.cfg.device,
            "kind": kind,
            "scope": scope,
            "source": source,
            "content": content,
            "meta": meta,
        }
        try:
            return self._request("POST", "/v1/atoms", json_body=payload)
        except OfflineError:
            queued = self._queue_outbox(payload)
            return {
                "mode": "offline_outbox",
                "queued": True,
                "atom": queued,
                "outbox_path": str(self.outbox_path),
            }

    def ingest(
        self,
        *,
        source: str,
        since: str | None = "24h",
        root_dir: Path | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        events = collect_hindsight_events(
            source,
            since=since,
            root_dir=root_dir,
            cursor_path=self.cursor_path,
            limit=limit,
        )
        ingested: list[dict[str, Any]] = []
        queued = 0
        for ev in events:
            res = self.add(
                kind=ev["kind"],
                content=ev["raw"],
                scope=ev["scope"],
                source=ev["source"],
                severity=ev["meta"].get("severity"),
                evidence_ref=ev["meta"].get("evidence_ref"),
            )
            if res.get("queued"):
                queued += 1
            ingested.append(res)
        return {
            "source": source,
            "since": since,
            "collected": len(events),
            "ingested": len(ingested) - queued,
            "queued_offline": queued,
        }

    # --- Brain maintenance operations ---

    def dream(self, *, dry_run: bool = False, no_gate: bool = False, export_songe: Path | None = None) -> dict[str, Any]:
        if self.is_remote and export_songe is None:
            return self._request("POST", "/v1/dream", json_body={"dry_run": dry_run, "no_gate": no_gate})
        return run_dream(self._engine(), dry_run=dry_run, no_gate=no_gate, export_songe_path=export_songe)

    def eval(self, *, suite: str = "all") -> dict[str, Any]:
        if self.is_remote:
            return self._request("POST", "/v1/eval", json_body={"suite": suite})
        return run_eval_on_engine(self._engine(), suite=suite)

    def eval_pin(self, node_id: str) -> dict[str, Any]:
        if self.is_remote:
            return self._request("POST", f"/v1/eval/pin/{node_id}")
        return pin_node_in_engine(self._engine(), node_id)

    def undo(self) -> dict[str, Any]:
        if self.is_remote:
            return self._request("POST", "/v1/undo")
        return self._engine().snapshots.undo()

    def rebuild(self) -> dict[str, Any]:
        if self.is_remote:
            return self._request("POST", "/v1/rebuild")
        return self._engine().rebuild_from_ledger()

    def export_views(self, out_dir: Path) -> dict[str, Any]:
        if self.is_remote:
            # Export from synced offline cache or live compact snapshot
            bundle = self._request("GET", "/v1/sync/snapshot")
            self._save_offline_snapshot(bundle)
            # Build markdown from cache DB
            out = Path(out_dir).resolve()
            out.mkdir(parents=True, exist_ok=True)
            with self._open_offline_cache() as db:
                nodes = db.list_nodes(include_inactive=True)
                snap_ver = db.get_meta("snapshot_version", "unknown")
            by_kind: dict[str, list[Any]] = {k: [] for k in VALID_KINDS}
            for n in nodes:
                by_kind.setdefault(n.type, []).append(n)
            files: list[str] = []
            for k, items in by_kind.items():
                p = out / f"{k}s.md"
                lines = [f"# {k.title()}s (`{snap_ver}`)", ""]
                for n in items:
                    lines.append(f"## {n.id} (`{n.status}` · `{n.severity}` · `{n.scope}`)")
                    lines.append(f"- {n.rule}")
                    if n.rationale:
                        lines.append(f"- Rationale: {n.rationale}")
                    lines.append("")
                p.write_text("\n".join(lines), encoding="utf-8")
                files.append(str(p))
            return {"snapshot_version": snap_ver, "out_dir": str(out), "files": files, "node_count": len(nodes)}
        return self._engine().export_markdown(out_dir)

    def status(self, *, offline: bool = False) -> dict[str, Any]:
        """Diagnostic status report: CLI version, brain connectivity, cache, outbox, auto-update & AI skills."""
        from fos import __version__
        from fos.skill import list_installed_skills
        from fos.update import (
            auto_update_enabled,
            find_published_wheel,
            is_editable_install,
            read_update_state,
        )

        outbox_count = len(self._read_outbox())
        cache_nodes = 0
        cache_snap = None
        if self.cache_db_path.exists():
            try:
                with SnapshotDB(self.cache_db_path, enable_vec=False, read_only=True) as db:
                    cache_nodes = db.count_nodes()
                    cache_snap = db.get_meta("snapshot_version")
            except Exception:
                pass

        upd_state = read_update_state(self.cache_dir / "update_state.json")
        rep: dict[str, Any] = {
            "version": __version__,
            "mode": self.cfg.mode,
            "device": self.cfg.device,
            "brain_url": self.cfg.brain_url or None,
            "editable_install": is_editable_install(),
            "auto_update_enabled": auto_update_enabled(),
            "last_update": (
                f"{upd_state.updated_from} -> {upd_state.updated_to}"
                if upd_state.updated_to
                else None
            ),
            "cache": {
                "present": self.cache_db_path.exists(),
                "snapshot_version": cache_snap,
                "nodes": cache_nodes,
                "outbox_pending": outbox_count,
            },
            "installed_skills": list_installed_skills(),
        }

        if not self.is_remote:
            eng = self._engine()
            with eng.snapshots.open_current(read_only=True) as db:
                node_count = db.count_nodes()
                has_vec = db.has_sqlite_vec
            pub = find_published_wheel(eng.data_dir / "dist")
            rep["brain"] = {
                "reachable": True,
                "data_dir": str(eng.data_dir),
                "snapshot_version": eng.snapshots.current_version(),
                "nodes": node_count,
                "sqlite_vec": has_vec,
                "published_cli_version": pub[0] if pub else None,
            }
        elif offline:
            rep["brain"] = {"reachable": None, "skipped": "offline"}
        else:
            try:
                health = self._request("GET", "/v1/health")
                rep["brain"] = {"reachable": True, **health}
            except FosError as exc:
                rep["brain"] = {"reachable": False, "error": str(exc)}

        return rep

    def update(
        self,
        *,
        check_only: bool = False,
        background: bool = False,
        installer_fn: Any | None = None,
    ) -> dict[str, Any]:
        from fos.update import perform_update

        return perform_update(
            self.cfg,
            self.cache_dir,
            home=self.home,
            check_only=check_only,
            background=background,
            http_transport=self.http_transport,
            installer_fn=installer_fn,
        )
