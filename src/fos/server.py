"""FastAPI `fosd` Brain HTTP server with SHA-256 Bearer Token auth, CLI distribution, and install.sh."""

from __future__ import annotations

import hmac
import os
import re
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fos import __version__
from fos.code_pattern import parse_stack_param
from fos.config import FosConfig, hash_token, load_config
from fos.dream import run_dream
from fos.engine import BrainEngine
from fos.errors import FosError, GateFailedError, NotFoundError, UsageError
from fos.eval_gate import pin_node_in_engine, run_eval_on_engine
from fos.guard import check_guard
from fos.proposals import (
    ProposalStateError,
    accept_proposal,
    dismiss_proposal,
    list_proposals,
    scan_proposals,
)
from fos.providers import CLISubprocessReasoner
from fos.scopes import parse_scopes_param
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
    scopes: list[str] | None = None


class DreamRequest(BaseModel):
    dry_run: bool = False
    no_gate: bool = False
    if_changed: bool = False


class EvalRequest(BaseModel):
    suite: str = "all"


class CorrectRequest(BaseModel):
    content: str
    reason: str = ""
    severity: str | None = None
    device: str | None = None


class EditNodeRequest(BaseModel):
    rule: str | None = None
    rationale: str | None = None
    scope: str | None = None
    severity: str | None = None
    type: str | None = None
    guard_hint: str | None = None
    guard_pattern: str | None = None
    source_ref: str | None = None
    trigger_when: list[str] | None = None
    entities: list[str] | None = None
    device: str | None = None


class ArchiveRequest(BaseModel):
    reason: str = ""
    device: str | None = None


class ProposalScanRequest(BaseModel):
    root_dir: str | None = None
    reasoner: str | None = None
    force: bool = False


class ProposalAcceptRequest(BaseModel):
    rule: str | None = None
    rationale: str | None = None
    guard_hint: str | None = None
    severity: str | None = None
    scope: str | None = None
    trigger_when: list[str] | None = None
    force: bool = False
    device: str | None = None


class ProposalDismissRequest(BaseModel):
    reason: str = ""


class GuardCheckRequest(BaseModel):
    diff_text: str | None = None
    command: str | None = None
    scope: str | None = None
    scopes: list[str] | None = None


class GraduateRequest(BaseModel):
    guard_pattern: str | None = None
    reason: str = ""
    device: str | None = None


class FeedbackRequest(BaseModel):
    verdict: str  # useful | noise
    context: str = ""


def _http_from_fos_error(exc: FosError) -> HTTPException:
    detail = {
        "error": str(exc),
        "code": int(exc.exit_code),
        "error_code": exc.error_code,
        "hint": exc.hint,
        "details": exc.details,
    }
    if isinstance(exc, UsageError):
        return HTTPException(status_code=400, detail=detail)
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=detail)
    if isinstance(exc, GateFailedError):
        return HTTPException(status_code=422, detail=detail)
    if isinstance(exc, ProposalStateError):
        status = 422 if exc.error_code == "L0_BUDGET_EXCEEDED" else 409
        return HTTPException(status_code=status, detail=detail)
    return HTTPException(status_code=500, detail=detail)


def _brain_base_url(request: Request) -> str:
    host = request.headers.get("host", "")
    if not host or not _HOST_RE.match(host):
        raise HTTPException(status_code=400, detail={"error": "bad host"})
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    scheme = "https" if proto.lower() == "https" else "http"
    return f"{scheme}://{host}"


def _resolve_web_dist() -> Path | None:
    env_dist = os.environ.get("FOS_WEB_DIST")
    if env_dist:
        p = Path(env_dist).expanduser().resolve()
        if (p / "index.html").is_file():
            return p
    repo_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if (repo_dist / "index.html").is_file():
        return repo_dist
    return None


def create_app(
    *,
    engine: BrainEngine | None = None,
    token_hash: str | None = None,
    home: Path | None = None,
    web_dist: Path | None = None,
) -> FastAPI:
    cfg: FosConfig = load_config(home)
    brain = engine or BrainEngine.from_config(cfg, home=home)
    expected_hash = token_hash if token_hash is not None else cfg.token_hash
    dist_dir = brain.data_dir / "dist"
    resolved_web_dist = web_dist if web_dist is not None else _resolve_web_dist()

    app = FastAPI(title="FlowOS Brain (fosd)", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if resolved_web_dist is not None and (resolved_web_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(resolved_web_dist / "assets")), name="web-assets")

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

    @app.get("/", response_class=HTMLResponse)
    def web_ui() -> HTMLResponse:
        if resolved_web_dist is not None and (resolved_web_dist / "index.html").is_file():
            html = (resolved_web_dist / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(content=html, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(
            content=(
                "<!doctype html><html><body style='font-family:sans-serif;padding:32px'>"
                "<h2>FlowOS Web UI not built yet</h2>"
                "<p>Run <code>cd web &amp;&amp; bun install &amp;&amp; bun run build</code> (or <code>bun run dev</code>).</p>"
                "</body></html>"
            ),
            status_code=200,
        )

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

    @app.get("/v1/dashboard", dependencies=[Depends(require_auth)])
    def dashboard(ledger_limit: int = Query(default=80, ge=1, le=500)) -> dict[str, Any]:
        return brain.dashboard_summary(ledger_limit=ledger_limit)

    @app.get("/v1/ledger", dependencies=[Depends(require_auth)])
    def ledger_list(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        atoms = brain.ledger.load_all_atoms()
        return {
            "total": len(atoms),
            "atoms": [a.to_dict() for a in reversed(atoms[-limit:])],
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
        scopes: str | None = Query(default=None),
        budget: int = Query(default=300, ge=32, le=4000),
    ) -> dict[str, Any]:
        try:
            scope_list = parse_scopes_param(scopes) if scopes else None
            return brain.boot(project=project, scopes=scope_list or None, budget=budget)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.get("/v1/recall", dependencies=[Depends(require_auth)])
    def recall(
        q: str = Query(..., min_length=1),
        kind: str | None = Query(default=None),
        scope: str | None = Query(default=None),
        scopes: str | None = Query(default=None),
        as_of: str | None = Query(default=None),
        budget: int = Query(default=1200, ge=64, le=16000),
        limit: int = Query(default=20, ge=1, le=100),
        cwd_stack: str | None = Query(default=None),
        exclude: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            stack_map = parse_stack_param(cwd_stack) if cwd_stack else None
            scope_list = parse_scopes_param(scopes) if scopes else None
            excl_list = [x.strip() for x in exclude.split(",") if x.strip()] if exclude else None
            return brain.recall(
                q,
                kind=kind,
                scope=scope,
                scopes=scope_list or None,
                as_of=as_of,
                budget=budget,
                limit=limit,
                cwd_stack=stack_map or None,
                exclude_ids=excl_list,
            )
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

    @app.patch("/v1/nodes/{node_id}", dependencies=[Depends(require_auth)])
    def edit_node(node_id: str, req: EditNodeRequest) -> dict[str, Any]:
        try:
            return brain.edit_node(
                node_id,
                rule=req.rule,
                rationale=req.rationale,
                scope=req.scope,
                severity=req.severity,
                kind=req.type,
                guard_hint=req.guard_hint,
                guard_pattern=req.guard_pattern,
                source_ref=req.source_ref,
                trigger_when=req.trigger_when,
                entities=req.entities,
                device=req.device,
            )
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
            return brain.mirror(req.proposal, scope=req.scope, scopes=req.scopes)
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
            return run_dream(brain, dry_run=req.dry_run, no_gate=req.no_gate, if_changed=req.if_changed)
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

    # --- Proposal Governance Inbox ---

    @app.get("/v1/proposals", dependencies=[Depends(require_auth)])
    def proposals_list(status: str = Query(default="pending")) -> dict[str, Any]:
        try:
            return list_proposals(brain, status=status)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/proposals/scan", dependencies=[Depends(require_auth)])
    def proposals_scan(req: ProposalScanRequest) -> dict[str, Any]:
        try:
            rdir = Path(req.root_dir).expanduser().resolve() if req.root_dir else None
            rsn = None
            if req.reasoner == "cli":
                rsn = CLISubprocessReasoner(cfg.providers.cli_command)
            return scan_proposals(brain, root_dir=rdir, reasoner=rsn, force=req.force)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/proposals/{proposal_id}/accept", dependencies=[Depends(require_auth)])
    def proposals_accept(proposal_id: str, req: ProposalAcceptRequest) -> dict[str, Any]:
        try:
            return accept_proposal(
                brain,
                proposal_id,
                rule=req.rule,
                rationale=req.rationale,
                guard_hint=req.guard_hint,
                severity=req.severity,
                scope=req.scope,
                trigger_when=req.trigger_when,
                force=req.force,
                device=req.device,
            )
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/proposals/{proposal_id}/dismiss", dependencies=[Depends(require_auth)])
    def proposals_dismiss(proposal_id: str, req: ProposalDismissRequest) -> dict[str, Any]:
        try:
            return dismiss_proposal(brain, proposal_id, reason=req.reason)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    # --- Deterministic Guardrails & PlugMem Utility Feedback ---

    @app.post("/v1/guard/check", dependencies=[Depends(require_auth)])
    def guard_check(req: GuardCheckRequest) -> dict[str, Any]:
        try:
            return check_guard(
                brain,
                diff_text=req.diff_text,
                command=req.command,
                scope=req.scope,
                scopes=req.scopes,
            )
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/nodes/{node_id}/graduate", dependencies=[Depends(require_auth)])
    def graduate_node(node_id: str, req: GraduateRequest) -> dict[str, Any]:
        try:
            return brain.graduate_node(
                node_id,
                guard_pattern=req.guard_pattern,
                reason=req.reason,
                device=req.device,
            )
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    @app.post("/v1/nodes/{node_id}/feedback", dependencies=[Depends(require_auth)])
    def feedback_node(node_id: str, req: FeedbackRequest) -> dict[str, Any]:
        try:
            return brain.feedback_node(node_id, req.verdict, context=req.context)
        except FosError as exc:
            raise _http_from_fos_error(exc) from exc

    return app
