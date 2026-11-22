"""FastAPI `fosd` Brain HTTP server with SHA-256 Bearer Token auth, CLI distribution, and install.sh."""

from __future__ import annotations

import hmac
import re
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from fos import __version__
from fos.config import FosConfig, hash_token, load_config
from fos.dream import run_dream
from fos.engine import BrainEngine
from fos.errors import FosError, GateFailedError, NotFoundError, UsageError
from fos.eval_gate import pin_node_in_engine, run_eval_on_engine
from fos.update import find_published_wheel

_HOST_RE = re.compile(r"^[A-Za-z0-9.:\[\]-]+$")


class AddAtomRequest(BaseModel):
    kind: str
    content: str
    scope: str = "global"
    source: str = "manual"
    device: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    id: str | None = None
    ts: str | None = None


class OutboxFlushRequest(BaseModel):
    atoms: list[AddAtomRequest] = Field(default_factory=list)


class MirrorRequest(BaseModel):
    proposal: str
    scope: str | None = None


class DreamRequest(BaseModel):
    dry_run: bool = False
    no_gate: bool = False


class EvalRequest(BaseModel):
    suite: str = "all"


class CorrectRequest(BaseModel):
    content: str
    reason: str = ""
    severity: str | None = None
    device: str | None = None


class ArchiveRequest(BaseModel):
    reason: str = ""
    device: str | None = None


def _http_from_fos_error(exc: FosError) -> HTTPException:
    detail = {"error": str(exc), "code": int(exc.exit_code), "hint": exc.hint, "details": exc.details}
    if isinstance(exc, UsageError):
        return HTTPException(status_code=400, detail=detail)
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=detail)
    if isinstance(exc, GateFailedError):
        return HTTPException(status_code=422, detail=detail)
    return HTTPException(status_code=500, detail=detail)


def _brain_base_url(request: Request) -> str:
    host = request.headers.get("host", "")
    if not host or not _HOST_RE.match(host):
        raise HTTPException(status_code=400, detail={"error": "bad host"})
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    scheme = "https" if proto.lower() == "https" else "http"
    return f"{scheme}://{host}"


def create_app(
    *,
    engine: BrainEngine | None = None,
    token_hash: str | None = None,
    home: Path | None = None,
) -> FastAPI:
    cfg: FosConfig = load_config(home)
    brain = engine or BrainEngine.from_config(cfg, home=home)
    expected_hash = token_hash if token_hash is not None else cfg.token_hash
    dist_dir = brain.data_dir / "dist"

    app = FastAPI(title="FlowOS Brain (fosd)", version=__version__)

    @app.middleware("http")
    async def attach_cli_version_header(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        published = find_published_wheel(dist_dir)
        if published is not None:
            response.headers["X-Fos-Version"] = published[0]
        return response

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        if not expected_hash:
            return
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail={"error": "Missing Bearer token"})
        presented = authorization[len("Bearer ") :].strip()
        if not hmac.compare_digest(hash_token(presented), expected_hash):
            raise HTTPException(status_code=403, detail={"error": "Invalid Bearer token"})

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        published = find_published_wheel(dist_dir)
        return {
            "status": "ok",
            "version": __version__,
            "cli_version": published[0] if published else "",
            "snapshot_version": brain.snapshots.current_version(),
            "auth_enabled": bool(expected_hash),
        }

    # --- CLI distribution & one-line installer (learned from fops) ---

    @app.get("/install.sh")
    def install_script(request: Request) -> PlainTextResponse:
        base_url = _brain_base_url(request)
        tmpl = resources.files("fos.assets").joinpath("install.sh").read_text(encoding="utf-8")
        rendered = tmpl.replace("{{BRAIN}}", base_url)
        return PlainTextResponse(rendered, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-cache"})

    @app.get("/v1/cli/version", dependencies=[Depends(require_auth)])
    def cli_version() -> dict[str, Any]:
        published = find_published_wheel(dist_dir)
        if published is None:
            raise HTTPException(status_code=404, detail={"error": "No CLI wheel published on this brain (run `fosd publish`)"})
        ver, wheel, digest = published
        return {
            "version": ver,
            "filename": wheel.name,
            "size_bytes": wheel.stat().st_size,
            "sha256": digest,
        }

    @app.get("/v1/cli/download", dependencies=[Depends(require_auth)])
    def cli_download() -> FileResponse:
        published = find_published_wheel(dist_dir)
        if published is None:
            raise HTTPException(status_code=404, detail={"error": "No CLI wheel published on this brain (run `fosd publish`)"})
        ver, wheel, digest = published
        return FileResponse(
            path=str(wheel),
            media_type="application/octet-stream",
            filename=wheel.name,
            headers={
                "X-Fos-Version": ver,
                "X-Fos-Sha256": digest,
                "Cache-Control": "no-store",
            },
        )

    # --- Cognitive Memory API ---

    @app.get("/v1/boot", dependencies=[Depends(require_auth)])
    def boot(
        project: str | None = Query(default=None),
        budget: int = Query(default=300, ge=32, le=4000),
    ) -> dict[str, Any]:
        try:
            return brain.boot(project=project, budget=budget)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.get("/v1/recall", dependencies=[Depends(require_auth)])
    def recall(
        q: str = Query(..., min_length=1),
        kind: str | None = Query(default=None),
        scope: str | None = Query(default=None),
        as_of: str | None = Query(default=None),
        budget: int = Query(default=1200, ge=64, le=16000),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        try:
            return brain.recall(q, kind=kind, scope=scope, as_of=as_of, budget=budget, limit=limit)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.get("/v1/nodes/{node_id}", dependencies=[Depends(require_auth)])
    def get_node(
        node_id: str,
        tier: str = Query(default="L1"),
        graph: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            return brain.get(node_id, tier=tier, include_graph=graph)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/nodes/{node_id}/correct", dependencies=[Depends(require_auth)])
    def correct_node(node_id: str, req: CorrectRequest) -> dict[str, Any]:
        try:
            return brain.correct_node(
                node_id,
                req.content,
                reason=req.reason,
                severity=req.severity,
                device=req.device,
            )
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/nodes/{node_id}/archive", dependencies=[Depends(require_auth)])
    def archive_node(node_id: str, req: ArchiveRequest) -> dict[str, Any]:
        try:
            return brain.archive_node(node_id, reason=req.reason, device=req.device)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/mirror", dependencies=[Depends(require_auth)])
    def mirror(req: MirrorRequest) -> dict[str, Any]:
        try:
            return brain.mirror(req.proposal, scope=req.scope)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/atoms", dependencies=[Depends(require_auth)])
    def add_atom(req: AddAtomRequest) -> dict[str, Any]:
        try:
            return brain.add_atom(
                kind=req.kind,
                content=req.content,
                scope=req.scope,
                source=req.source,
                device=req.device,
                meta=req.meta,
                atom_id=req.id,
                ts=req.ts,
            )
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/sync/outbox", dependencies=[Depends(require_auth)])
    def sync_outbox(req: OutboxFlushRequest) -> dict[str, Any]:
        accepted: list[str] = []
        errors: list[dict[str, Any]] = []
        for item in req.atoms:
            try:
                res = brain.add_atom(
                    kind=item.kind,
                    content=item.content,
                    scope=item.scope,
                    source=item.source,
                    device=item.device,
                    meta=item.meta,
                    atom_id=item.id,
                    ts=item.ts,
                )
                accepted.append(res["atom"]["id"])
            except FosError as exc:
                errors.append({"id": item.id, "error": str(exc)})
        return {
            "accepted": accepted,
            "accepted_count": len(accepted),
            "errors": errors,
            "snapshot_version": brain.snapshots.current_version(),
        }

    @app.get("/v1/sync/snapshot", dependencies=[Depends(require_auth)])
    def sync_snapshot() -> dict[str, Any]:
        return brain.export_compact_snapshot()

    @app.post("/v1/dream", dependencies=[Depends(require_auth)])
    def dream(req: DreamRequest) -> dict[str, Any]:
        try:
            return run_dream(brain, dry_run=req.dry_run, no_gate=req.no_gate)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/eval", dependencies=[Depends(require_auth)])
    def run_eval(req: EvalRequest) -> dict[str, Any]:
        try:
            return run_eval_on_engine(brain, suite=req.suite)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/eval/pin/{node_id}", dependencies=[Depends(require_auth)])
    def pin_eval(node_id: str) -> dict[str, Any]:
        try:
            return pin_node_in_engine(brain, node_id)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/undo", dependencies=[Depends(require_auth)])
    def undo() -> dict[str, Any]:
        try:
            return brain.snapshots.undo()
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/rebuild", dependencies=[Depends(require_auth)])
    def rebuild() -> dict[str, Any]:
        try:
            return brain.rebuild_from_ledger()
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    return app
