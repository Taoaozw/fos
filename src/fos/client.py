"""Lightweight FlowOS client: online fosd HTTP + automatic offline SQLite cache + outbox queue."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fos.config import FosConfig, default_fos_home, load_config, save_config
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
        db_path = (
            self.cache_dir / "client_state.db"
            if self.is_remote
            else self.cfg.resolved_data_dir(self.home) / "state.db"
        )
        legacy = self.cache_dir / "ingest_cursors.json"
        if legacy.is_file():
            from fos.state_db import StateDB

            with StateDB(db_path) as db:
                db.migrate_legacy_cursors(legacy)
        return db_path

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

    def _try_local_daemon(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """In local mode on default FOS_HOME, reuse warm local `fosd` daemon (keeps MLX GPU weights hot)."""
        if self.is_remote or self.http_transport is not None:
            return None
        if os.environ.get("FOS_NO_LOCAL_DAEMON") == "1":
            return None
        resolved_home = (self.home or default_fos_home()).resolve()
        if resolved_home != default_fos_home():
            return None
        # Only worthwhile when a heavy embedder (e.g. mlx) is configured
        if self.cfg.providers.embedder == "local_hash":
            return None

        port = int(os.environ.get("FOS_LOCAL_PORT", "7622"))
        url = f"http://127.0.0.1:{port}{path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        tok = self.cfg.token
        if not tok:
            tok_file = resolved_home / ".initial_client_token"
            if tok_file.is_file():
                try:
                    tok = tok_file.read_text(encoding="utf-8").strip()
                except OSError:
                    tok = ""
        if tok:
            headers["Authorization"] = f"Bearer {tok}"

        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            import httpx

            timeout = httpx.Timeout(10.0, connect=0.15)
            with httpx.Client(timeout=timeout) as hc:
                resp = hc.request(method, url, headers=headers, params=clean_params, json=json_body)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    data["mode"] = "local_daemon"
                    return data
        except Exception:
            return None
        return None

    def recall(
        self,
        query: str,
        *,
        kind: str | None = None,
        scope: str | None = None,
        as_of: str | None = None,
        budget: int = 1200,
        limit: int = 20,
        cwd_stack: dict[str, str] | None = None,
        exclude_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        from fos.code_pattern import format_stack_param

        stack_param = format_stack_param(cwd_stack)
        excl_param = ",".join(x.strip() for x in (exclude_ids or []) if x.strip()) or None
        if not self.is_remote:
            warm = self._try_local_daemon(
                "GET",
                "/v1/recall",
                params={
                    "q": query,
                    "kind": kind,
                    "scope": scope,
                    "as_of": as_of,
                    "budget": budget,
                    "limit": limit,
                    "cwd_stack": stack_param,
                    "exclude": excl_param,
                },
            )
            if warm is not None:
                return warm
            return self._engine().recall(
                query,
                kind=kind,
                scope=scope,
                as_of=as_of,
                budget=budget,
                limit=limit,
                cwd_stack=cwd_stack,
                exclude_ids=exclude_ids,
            )
        try:
            return self._request(
                "GET",
                "/v1/recall",
                params={
                    "q": query,
                    "kind": kind,
                    "scope": scope,
                    "as_of": as_of,
                    "budget": budget,
                    "limit": limit,
                    "cwd_stack": stack_param,
                    "exclude": excl_param,
                },
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
                    cwd_stack=cwd_stack,
                    exclude_ids=exclude_ids,
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
            warm = self._try_local_daemon(
                "POST",
                "/v1/mirror",
                json_body={"proposal": proposal, "scope": scope},
            )
            if warm is not None:
                return warm
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
        source_ref: str | None = None,
        source_hash: str | None = None,
        verified_at: str | None = None,
        guard_pattern: str | None = None,
        rule: str | None = None,
        rationale: str | None = None,
        compat: dict[str, Any] | None = None,
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
        if source_ref:
            meta["source_ref"] = source_ref
        if source_hash:
            meta["source_hash"] = source_hash
        if verified_at:
            meta["verified_at"] = verified_at
        if guard_pattern:
            meta["guard_pattern"] = guard_pattern
        if rule:
            meta["rule"] = rule
        if rationale:
            meta["rationale"] = rationale
        if compat:
            meta["compat"] = compat

        if not self.is_remote:
            warm = self._try_local_daemon(
                "POST",
                "/v1/atoms",
                json_body={
                    "device": self.cfg.device,
                    "kind": kind,
                    "scope": scope,
                    "source": source,
                    "content": content,
                    "meta": meta,
                },
            )
            if warm is not None:
                return warm
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

    def correct(
        self,
        node_id: str,
        new_content: str,
        *,
        reason: str = "",
        severity: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_remote:
            return self._engine().correct_node(
                node_id,
                new_content,
                reason=reason,
                severity=severity,
                device=self.cfg.device,
            )
        try:
            return self._request(
                "POST",
                f"/v1/nodes/{node_id}/correct",
                json_body={
                    "content": new_content,
                    "reason": reason,
                    "severity": severity,
                    "device": self.cfg.device,
                },
            )
        except OfflineError:
            meta: dict[str, Any] = {"supersedes": node_id}
            if severity:
                meta["severity"] = severity
            if reason:
                meta["rationale"] = reason
            queued = self._queue_outbox(
                {
                    "kind": "lesson",
                    "scope": "global",
                    "source": "manual:correct",
                    "content": new_content,
                    "meta": meta,
                }
            )
            return {"mode": "offline_outbox", "queued": True, "atom": queued, "outbox_path": str(self.outbox_path)}

    def archive(self, node_id: str, *, reason: str = "") -> dict[str, Any]:
        if not self.is_remote:
            return self._engine().archive_node(node_id, reason=reason, device=self.cfg.device)
        try:
            return self._request(
                "POST",
                f"/v1/nodes/{node_id}/archive",
                json_body={"reason": reason, "device": self.cfg.device},
            )
        except OfflineError:
            reason_clean = reason.strip() or "archived as obsolete/expired"
            queued = self._queue_outbox(
                {
                    "kind": "outcome",
                    "scope": "global",
                    "source": "manual:archive",
                    "content": f"Archive {node_id}: {reason_clean}",
                    "meta": {"archives": node_id, "archive_reason": reason_clean},
                }
            )
            return {"mode": "offline_outbox", "queued": True, "atom": queued, "outbox_path": str(self.outbox_path)}

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
                source_ref=ev["meta"].get("source_ref"),
                source_hash=ev["meta"].get("source_hash"),
                verified_at=ev["meta"].get("verified_at"),
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

    def dream(
        self,
        *,
        dry_run: bool = False,
        no_gate: bool = False,
        export_songe: Path | None = None,
        reasoner: str | None = None,
        cli_command: str | None = None,
    ) -> dict[str, Any]:
        if self.is_remote and export_songe is None and not reasoner:
            return self._request("POST", "/v1/dream", json_body={"dry_run": dry_run, "no_gate": no_gate})
        return run_dream(
            self._engine(),
            dry_run=dry_run,
            no_gate=no_gate,
            export_songe_path=export_songe,
            reasoner_override=reasoner,
            cli_command=cli_command,
        )

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

    def list_proposals(self, *, status: str = "pending") -> dict[str, Any]:
        if not self.is_remote:
            from fos.proposals import list_proposals

            return list_proposals(self._engine(), status=status)
        return self._request("GET", "/v1/proposals", params={"status": status})

    def scan_proposals(
        self,
        *,
        root_dir: Path | None = None,
        reasoner: str | None = None,
        cli_command: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if not self.is_remote:
            from fos.proposals import scan_proposals
            from fos.providers import CLISubprocessReasoner

            rsn = None
            if reasoner == "cli":
                rsn = CLISubprocessReasoner(cli_command or self.cfg.providers.cli_command)
            return scan_proposals(self._engine(), root_dir=root_dir, reasoner=rsn, force=force)
        return self._request(
            "POST",
            "/v1/proposals/scan",
            json_body={
                "root_dir": str(root_dir) if root_dir else None,
                "reasoner": reasoner,
                "force": force,
            },
            timeout=120.0,
        )

    def accept_proposal(
        self,
        proposal_id: str,
        *,
        rule: str | None = None,
        rationale: str | None = None,
        guard_hint: str | None = None,
        severity: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if not self.is_remote:
            from fos.proposals import accept_proposal

            return accept_proposal(
                self._engine(),
                proposal_id,
                rule=rule,
                rationale=rationale,
                guard_hint=guard_hint,
                severity=severity,
                force=force,
                device=self.cfg.device,
            )
        return self._request(
            "POST",
            f"/v1/proposals/{proposal_id}/accept",
            json_body={
                "rule": rule,
                "rationale": rationale,
                "guard_hint": guard_hint,
                "severity": severity,
                "force": force,
                "device": self.cfg.device,
            },
        )

    def dismiss_proposal(self, proposal_id: str, *, reason: str = "") -> dict[str, Any]:
        if not self.is_remote:
            from fos.proposals import dismiss_proposal

            return dismiss_proposal(self._engine(), proposal_id, reason=reason)
        return self._request(
            "POST",
            f"/v1/proposals/{proposal_id}/dismiss",
            json_body={"reason": reason},
        )

    def guard_check(
        self,
        *,
        diff_text: str | None = None,
        command: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_remote:
            from fos.guard import check_guard

            return check_guard(self._engine(), diff_text=diff_text, command=command, scope=scope)
        return self._request(
            "POST",
            "/v1/guard/check",
            json_body={"diff_text": diff_text, "command": command, "scope": scope},
        )

    def graduate(
        self,
        node_id: str,
        *,
        guard_pattern: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        if not self.is_remote:
            return self._engine().graduate_node(
                node_id,
                guard_pattern=guard_pattern,
                reason=reason,
                device=self.cfg.device,
            )
        return self._request(
            "POST",
            f"/v1/nodes/{node_id}/graduate",
            json_body={"guard_pattern": guard_pattern, "reason": reason, "device": self.cfg.device},
        )

    def feedback(self, node_id: str, verdict: str, *, context: str = "") -> dict[str, Any]:
        if not self.is_remote:
            return self._engine().feedback_node(node_id, verdict, context=context)
        return self._request(
            "POST",
            f"/v1/nodes/{node_id}/feedback",
            json_body={"verdict": verdict, "context": context},
        )

    def add_pattern(
        self,
        target: str,
        rule: str,
        *,
        rationale: str = "",
        bench_cmd: str = "",
        scope: str | None = None,
        severity: str = "P1",
        kind: str = "decision",
        supersedes: str | None = None,
        entities: list[str] | None = None,
        trigger_when: list[str] | None = None,
        stack_override: dict[str, str] | None = None,
        max_lines: int = 45,
    ) -> dict[str, Any]:
        """Capture a high-leverage code pattern: timeless L1 invariant + symbol-anchored L2 skeleton + SemVer compat."""
        from fos.code_pattern import (
            detect_repo_root,
            detect_repo_stack_versions,
            extract_code_symbol,
        )

        if not target or not target.strip():
            raise UsageError("Target file[::symbol] is required.")
        if not rule or not rule.strip():
            raise UsageError("A concise timeless invariant/rule is required.")

        raw_path, sep, symbol = target.strip().partition("::")
        file_path = Path(raw_path).expanduser().resolve()
        symbol = symbol.strip() if sep else ""
        if not file_path.is_file():
            raise NotFoundError(f"Source file not found: {file_path}")

        extracted = extract_code_symbol(file_path, symbol, max_lines=max_lines)
        if not extracted.get("found"):
            raise NotFoundError(str(extracted.get("error") or f"Symbol '{symbol}' not found in {file_path.name}"))

        repo_root = detect_repo_root(file_path)
        detected_stack, manifest_rel = detect_repo_stack_versions(file_path)
        stack = dict(detected_stack)
        if stack_override:
            stack.update(stack_override)

        resolved_scope = (scope or (repo_root.name.lower() if repo_root else "global")).strip().lower()
        source_ref = f"{file_path}::{symbol}" if symbol else str(file_path)

        compat: dict[str, Any] = {
            "pattern": "code_exemplar",
            "lang": extracted["lang"],
        }
        if stack:
            compat["stack"] = stack
        if manifest_rel:
            compat["manifest"] = manifest_rel
        if bench_cmd.strip():
            compat["bench_cmd"] = bench_cmd.strip()

        # Compose clean verbatim L2 ledger payload (timeless essence + compact skeleton fallback)
        l2_lines = [f"Invariant: {rule.strip()}"]
        if rationale.strip():
            l2_lines.append(f"Rationale: {rationale.strip()}")
        if bench_cmd.strip():
            l2_lines.append(f"Verify: {bench_cmd.strip()}")
        l2_lines.append(f"Source: {source_ref} (L{extracted['start_line']}-L{extracted['end_line']})")
        if stack:
            l2_lines.append("Stack: " + ", ".join(f"{k}={v}" for k, v in sorted(stack.items())))
        l2_lines.append("")
        l2_lines.append(f"```{extracted['lang']}")
        l2_lines.append(str(extracted["snippet"]))
        l2_lines.append("```")

        ent_list = list(entities or [])
        if symbol and not symbol.upper().startswith("L"):
            if symbol.lower() not in [e.lower() for e in ent_list]:
                ent_list.append(symbol.lower())
        for sk in stack:
            if sk.lower() not in [e.lower() for e in ent_list]:
                ent_list.append(sk.lower())

        return self.add(
            kind=kind,
            content="\n".join(l2_lines),
            scope=resolved_scope,
            source="manual:pattern",
            supersedes=supersedes,
            severity=severity,
            entities=ent_list or None,
            trigger_when=trigger_when,
            source_ref=source_ref,
            source_hash=str(extracted["hash"]),
            verified_at=now_iso()[:10],
            rule=rule.strip(),
            rationale=rationale.strip(),
            compat=compat,
        )

    def sync_pattern(
        self,
        node_id: str,
        *,
        rule: str | None = None,
        rationale: str | None = None,
        bench_cmd: str | None = None,
        stack_override: dict[str, str] | None = None,
        max_lines: int = 45,
    ) -> dict[str, Any]:
        """Re-slice an existing code exemplar from disk and supersede it with an evolved vN+1 node."""
        from fos.code_pattern import detect_repo_stack_versions, extract_code_symbol

        detail = self.get(node_id, tier="L1")
        old = detail.get("node") or {}
        source_ref = str(old.get("source_ref") or "").strip()
        if not source_ref:
            raise UsageError(f"Node {node_id} has no source_ref to sync from.")

        raw_path, sep, symbol = source_ref.partition("::")
        file_path = Path(raw_path).expanduser().resolve()
        sym = symbol.strip() if sep else ""
        if not file_path.is_file():
            raise NotFoundError(f"Source file for {node_id} no longer exists: {file_path}")

        extracted = extract_code_symbol(file_path, sym, max_lines=max_lines)
        if not extracted.get("found"):
            raise NotFoundError(str(extracted.get("error") or f"Symbol '{sym}' not found in {file_path.name}"))

        old_compat = dict(old.get("compat") or {})
        old_stack = dict(old_compat.get("stack") or {})
        detected_stack, _ = detect_repo_stack_versions(file_path)
        new_stack = dict(detected_stack)
        if stack_override:
            new_stack.update(stack_override)

        new_rule = (rule if rule is not None and rule.strip() else str(old.get("rule") or "")).strip()
        new_rationale = (rationale if rationale is not None else str(old.get("rationale") or "")).strip()
        new_bench = (bench_cmd if bench_cmd is not None else str(old_compat.get("bench_cmd") or "")).strip()

        no_change = (
            str(extracted.get("hash") or "") == str(old.get("source_hash") or "")
            and new_stack == old_stack
            and new_rule == str(old.get("rule") or "").strip()
            and new_rationale == str(old.get("rationale") or "").strip()
            and new_bench == str(old_compat.get("bench_cmd") or "").strip()
        )
        if no_change:
            return {
                "changed": False,
                "node_id": node_id,
                "source_ref": source_ref,
                "freshness": "fresh",
                "note": "No changes detected in anchored symbol, stack versions, or metadata.",
            }

        res = self.add_pattern(
            source_ref,
            new_rule,
            rationale=new_rationale,
            bench_cmd=new_bench,
            scope=str(old.get("scope") or "global"),
            severity=str(old.get("severity") or "P1"),
            kind=str(old.get("type") or "decision"),
            supersedes=node_id,
            entities=list(old.get("entities") or []) or None,
            trigger_when=list(old.get("trigger_when") or []) or None,
            stack_override=stack_override,
            max_lines=max_lines,
        )
        res["changed"] = True
        return res

    def list_patterns(
        self,
        *,
        scope: str | None = None,
        cwd_stack: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """List active code exemplar cards with live symbol freshness and SemVer compat check."""
        from fos.code_pattern import check_compat_drift
        from fos.freshness import check_source_freshness

        if not self.is_remote:
            eng = self._engine()
            with eng.snapshots.open_current(read_only=True) as db:
                nodes = db.list_nodes(scope=scope, as_of=None)
                snap_ver = db.get_meta("snapshot_version", "unknown")
        else:
            dash = self._request("GET", "/v1/dashboard", params={"ledger_limit": 1})
            from fos.models import CompiledNode

            nodes = [
                CompiledNode.from_dict(nd)
                for nd in (dash.get("nodes") or [])
                if nd.get("status") == "active" and (not scope or nd.get("scope") in (scope, "global"))
            ]
            snap_ver = dash.get("snapshot_version")

        items: list[dict[str, Any]] = []
        for n in nodes:
            if n.compat.get("pattern") != "code_exemplar":
                continue
            fresh = check_source_freshness(n.source_ref, n.source_hash)
            cc = check_compat_drift(n.compat, cwd_stack) if cwd_stack else None
            entry: dict[str, Any] = {
                "id": n.id,
                "type": n.type,
                "scope": n.scope,
                "severity": n.severity,
                "rule": n.rule,
                "rationale": n.rationale,
                "source_ref": n.source_ref,
                "freshness": fresh,
                "compat": n.compat,
            }
            if cc is not None:
                entry["compat_check"] = cc
            items.append(entry)

        return {
            "snapshot_version": snap_ver,
            "count": len(items),
            "cwd_stack": cwd_stack or {},
            "patterns": items,
        }
