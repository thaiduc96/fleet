"""fleetd HTTP API + dashboard.

Listens on 127.0.0.1 only. Expose it to your tailnet with
`tailscale serve --bg 7777` (HTTPS, no open ports).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, TypeVar
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.types import Scope

from fleetd import API_LEVEL, auth, config, snapshot, system
from fleetd.actions import ActionError, Actions, check_id
from fleetd.attach import Attacher, AttachError
from fleetd.awake import KeepAwake, Spawn, spawn_process
from fleetd.claude_cli import ID_RE, ClaudeCLI, CLIError
from fleetd.handoff import Git, Handoff, run_git
from fleetd.models import (
    ActionResult,
    Awake,
    AwakeBody,
    AwakeMode,
    Fleet,
    HandoffBody,
    HandoffBundle,
    HandoffResult,
    Health,
    HealthResponse,
    Hello,
    ImportBody,
    ImportResult,
    JoinBody,
    LoginBody,
    Logs,
    MachineSnapshot,
    MessageBody,
    NewSessionBody,
    PeerInfo,
    ReleaseBody,
    ReleaseResult,
    Roster,
    SessionInputBody,
    SessionScreen,
    SettingsBody,
    Transcript,
    UpdateResult,
    Upload,
)
from fleetd.peers import (
    PeerClient,
    PeerGoneError,
    PeerRegistry,
    is_outdated,
    outdated_message,
    response_detail,
)
from fleetd.state import StateFile
from fleetd.transcripts import TranscriptIndex, conversation
from fleetd.transcripts import version as transcript_version
from fleetd.uploads import MAX_UPLOAD_BYTES, Uploads

M = TypeVar("M", bound=BaseModel)
UploadName = Annotated[str, Query(min_length=1, max_length=255)]
WEB_DIR = Path(__file__).resolve().parent / "web"
VERSIONED_ASSETS = ("app.js", "style.css")
NO_CACHE = {"Cache-Control": "no-cache"}
SCREEN_TIMEOUT = 30.0  # a peer attaches to the session and waits for the TUI to settle


def versioned_index(web_dir: Path) -> str:
    """index.html with ?v=<content hash> on its script and stylesheet.

    After an update the URL changes, so a browser can never pair the new page
    with an app.js it cached heuristically before (1.1.0: the 📎 button showed
    up, but the old script behind it did nothing).
    """
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    for name in VERSIONED_ASSETS:
        digest = hashlib.sha256((web_dir / name).read_bytes()).hexdigest()[:8]
        html = html.replace(f'/static/{name}"', f'/static/{name}?v={digest}"')
    return html


class RevalidatedStaticFiles(StaticFiles):
    """Static files that browsers must revalidate (cheap 304 via ETag) before reuse."""

    def file_response(self, full_path: str | os.PathLike[str], stat_result: os.stat_result,
                      scope: Scope, status_code: int = 200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers.update(NO_CACHE)
        return response
log = logging.getLogger("fleetd")


@dataclass
class Runtime:
    """Per-app state filled in at startup."""

    claude_version: str = "unknown"
    peers: PeerClient | None = field(default=None)


def _config_from_env() -> config.Config:
    path = os.environ.get("FLEET_CONFIG")
    return config.load(Path(path) if path else None)


def create_app(cfg: config.Config | None = None, token: str | None = None,
               cli: ClaudeCLI | None = None,
               peer_http: httpx.AsyncClient | None = None,
               clock: Callable[[], float] = time.monotonic,
               sleep: Callable[[float], None] = time.sleep,
               spawn: Spawn = spawn_process,
               health_fn: Callable[[Awake | None, int | None], Health] = system.health,
               git: Git = run_git,
               app_version: str | None = None,
               wall_clock: Callable[[], float] = time.time,
               attacher: Attacher | None = None) -> FastAPI:
    cfg = cfg or _config_from_env()
    token = token or auth.load_token(cfg.token)
    # Set by Fleet.app when it launches fleetd; absent for the old LaunchAgent install.
    app_version = app_version or os.environ.get("FLEET_APP_VERSION") or None
    cli = cli or ClaudeCLI(cfg.claude_bin, cfg.claude_home)
    idx = TranscriptIndex(cfg.claude_home)
    actions = Actions(cli, idx, cfg.user_home, clock=clock, sleep=sleep)
    attacher = attacher or Attacher.for_cli(cfg.claude_bin)
    handoff = Handoff(cfg.claude_home, idx, cfg.user_home, cfg.machine, git=git)
    try:
        default_mode = AwakeMode(cfg.keep_awake)
    except ValueError:
        log.warning("unknown keep_awake %r in config; using off", cfg.keep_awake)
        default_mode = AwakeMode.OFF
    state = StateFile(cfg.home / "state.json")
    registry = PeerRegistry(cfg.peers, state, cfg.machine, own_url=lambda: my_url())
    keep = KeepAwake(state, default_mode, spawn=spawn)
    uploads = Uploads(cfg.home / "uploads", now=wall_clock)
    rt = Runtime()

    def my_label() -> str:
        value = state.get("label")
        return value if isinstance(value, str) and value else cfg.label

    def my_url() -> str | None:
        value = state.get("selfUrl")
        return value if isinstance(value, str) and value else cfg.url

    def identity() -> snapshot.Identity:
        return snapshot.Identity(label=my_label(), url=my_url(), peers=registry.infos(),
                                 app_version=app_version)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        rt.claude_version = await asyncio.to_thread(cli.version)
        log.info("fleetd %s (%s, app %s), %d peer(s)", cfg.machine, rt.claude_version,
                 app_version or "-", len(registry.all()))
        await asyncio.to_thread(keep.start)
        await asyncio.to_thread(uploads.prune)
        try:
            async with PeerClient(registry, token, cfg.cache_dir, client=peer_http) as peers:
                rt.peers = peers
                yield
        finally:
            await asyncio.to_thread(keep.stop)

    def current_health() -> Health:
        return health_fn(keep.status(), os.getpid())

    app = FastAPI(title="fleetd", lifespan=lifespan, docs_url=None, redoc_url=None)

    def require(request: Request) -> None:
        auth.check(request, token)

    def require_write(request: Request) -> None:
        """Auth + a custom header: a cross-site form/fetch cannot send it without
        a CORS preflight, which fleetd never approves (CSRF protection)."""
        auth.check(request, token)
        if request.headers.get(auth.WRITE_HEADER) != "1":
            raise HTTPException(403, f"missing {auth.WRITE_HEADER} header")

    @app.exception_handler(PeerGoneError)
    async def peer_gone(_: Request, e: PeerGoneError) -> JSONResponse:
        return JSONResponse({"detail": f"unknown machine {e}"}, status_code=404)

    @app.exception_handler(ActionError)
    async def action_error(_: Request, e: ActionError) -> JSONResponse:
        return JSONResponse({"detail": e.message}, status_code=e.status)

    @app.exception_handler(AttachError)
    async def attach_error(_: Request, e: AttachError) -> JSONResponse:
        return JSONResponse({"detail": e.message}, status_code=e.status)

    async def on_machine(machine: str, method: str, path: str,
                         local: Callable[[], BaseModel], body: BaseModel | None = None) -> Response:
        """Run an action here, or forward it to the peer that owns the session."""
        if machine == cfg.machine:
            result = await asyncio.to_thread(local)
            return Response(result.model_dump_json(), media_type="application/json")
        if machine not in peers().peers:
            raise HTTPException(404, f"unknown machine {machine}")
        try:
            r = await peers().request(machine, method, path,
                                      body.model_dump(by_alias=True) if body else None)
        except httpx.HTTPError as e:
            raise HTTPException(504, f"{machine} unreachable: {e}") from e
        return relay(machine, r)

    def relay(machine: str, r: httpx.Response) -> Response:
        """Pass a peer's reply through, explaining 'route missing' as an old Fleet."""
        if is_outdated(r):
            detail = outdated_message(peers().label(machine))
            return JSONResponse({"detail": detail}, status_code=r.status_code)
        return Response(r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type", "application/json"))

    def peers() -> PeerClient:
        assert rt.peers is not None, "lifespan not started"
        return rt.peers

    def local_snap() -> MachineSnapshot:
        return snapshot.build(cfg, cli, idx, rt.claude_version, current_health(), identity())

    def read_transcript(session_id: str, limit: int, since: str | None = None) -> Transcript:
        path = idx.find(session_id)
        if not path:
            raise HTTPException(404, "transcript not found")
        current = transcript_version(path)
        if since == current:  # the dashboard polls; most polls change nothing
            return Transcript(session_id=session_id, summary=idx.summary(path), messages=[],
                              version=current, unchanged=True)
        conv = conversation(path, limit=max(1, min(limit, 400)))
        return Transcript(session_id=session_id, summary=idx.summary(path), messages=conv.messages,
                          version=current, model=conv.model, context_tokens=conv.context_tokens)

    def read_logs(ident: str) -> Logs:
        if not ID_RE.fullmatch(ident):
            raise HTTPException(400, "bad id")
        try:
            return Logs(id=ident, logs=cli.logs(ident))
        except CLIError as e:
            raise HTTPException(502, str(e)) from e

    # ---- auth ----------------------------------------------------------
    @app.post("/api/login")
    def login(body: LoginBody, request: Request) -> JSONResponse:
        if not auth.valid(body.token.strip(), token):
            raise HTTPException(401, "wrong token")
        resp = JSONResponse({"ok": True})
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(auth.COOKIE, token, httponly=True, samesite="strict",
                        secure=secure, max_age=365 * 86400)
        return resp

    @app.post("/api/logout")
    def logout() -> JSONResponse:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.COOKIE)
        return resp

    # ---- this machine ----------------------------------------------------
    @app.get("/api/health", dependencies=[Depends(require)])
    def health() -> HealthResponse:
        h = current_health()
        return HealthResponse(machine=cfg.machine, label=my_label(),
                              claude_version=rt.claude_version, **h.model_dump())

    @app.get("/api/local", dependencies=[Depends(require)])
    def local() -> MachineSnapshot:
        return local_snap()

    # ---- app support ---------------------------------------------------------
    @app.get("/api/hello")
    def hello() -> Hello:
        """No auth: lets the app check that a URL is a Fleet before using a token."""
        return Hello(machine=cfg.machine, label=my_label(), api_level=API_LEVEL, app_version=app_version)

    @app.post("/api/peers/join", dependencies=[Depends(require_write)])
    def join(body: JoinBody) -> Roster:
        """A new machine (holding our token via an invite code) introduces itself."""
        if body.machine == cfg.machine:
            raise ActionError(409, f"Máy này đã tên là '{cfg.machine}'; đặt tên khác cho máy mới.")
        if not registry.add(PeerInfo(machine=body.machine, label=body.label, url=body.url)):
            raise ActionError(400, "URL không hợp lệ: chỉ chấp nhận https://<máy>.<tailnet>.ts.net "
                                   "(cùng tailnet với máy này).")
        others = [p for p in registry.infos() if p.machine != body.machine]
        return Roster(machine=cfg.machine, label=my_label(), url=my_url(), peers=others)

    @app.delete("/api/peers/{machine}", dependencies=[Depends(require_write)])
    async def remove_peer(machine: str, everywhere: bool = False) -> dict[str, object]:
        """Forget a machine here; with everywhere=true also on every other machine
        (tombstones are local, so otherwise the others keep it)."""
        told: list[str] = []
        if everywhere:
            others = [m for m in peers().peers if m != machine]
            replies = await asyncio.gather(*(peers().request(m, "DELETE", f"/api/peers/{machine}")
                                             for m in others), return_exceptions=True)
            told = [m for m, r in zip(others, replies, strict=True)
                    if isinstance(r, httpx.Response) and r.status_code < 400]
        await asyncio.to_thread(registry.remove, machine)
        return {"ok": True, "alsoRemovedOn": told}

    @app.post("/api/settings", dependencies=[Depends(require_write)])
    def settings(body: SettingsBody) -> Hello:
        # Only the fields actually sent change; the other one is left alone.
        changes: dict[str, object] = {}
        if "label" in body.model_fields_set and body.label:
            changes["label"] = body.label
        if "url" in body.model_fields_set and body.url:
            changes["selfUrl"] = body.url
        if changes:
            state.update(**changes)
        return hello()

    def request_update() -> UpdateResult:
        (cfg.home / "update-request").write_text(str(time.time()))
        log.info("update requested (app managed: %s)", bool(app_version))
        return UpdateResult(requested=True, app_managed=bool(app_version))

    @app.post("/api/update", dependencies=[Depends(require_write)])
    def update() -> UpdateResult:
        return request_update()

    @app.post("/api/m/{machine}/update", dependencies=[Depends(require_write)])
    async def m_update(machine: str) -> Response:
        if machine == cfg.machine:
            result = await asyncio.to_thread(request_update)
            return Response(result.model_dump_json(), media_type="application/json")
        if machine not in peers().peers:
            raise HTTPException(404, f"unknown machine {machine}")
        try:
            r = await peers().request(machine, "POST", "/api/update")
        except httpx.HTTPError as e:
            raise HTTPException(504, f"{machine} unreachable: {e}") from e
        return relay(machine, r)

    @app.get("/api/sessions/{session_id}/transcript", dependencies=[Depends(require)])
    def transcript(session_id: str, limit: int = 60, since: str | None = None) -> Transcript:
        return read_transcript(session_id, limit, since)

    @app.get("/api/sessions/{ident}/logs", dependencies=[Depends(require)])
    def logs(ident: str) -> Logs:
        return read_logs(ident)

    # ---- whole fleet -----------------------------------------------------
    @app.get("/api/fleet", dependencies=[Depends(require)])
    async def fleet() -> Fleet:
        mine, others = await asyncio.gather(asyncio.to_thread(local_snap), peers().all())
        return Fleet(self_machine=cfg.machine, machines=[mine, *others])

    @app.get("/api/m/{machine}/sessions/{ident}/{what}", dependencies=[Depends(require)])
    async def per_machine(machine: str, ident: str, what: str, limit: int = 60,
                          since: str | None = None) -> Response:
        """Read-only proxy so the dashboard talks to one origin only."""
        if what not in ("transcript", "logs", "screen") or not ID_RE.fullmatch(ident):
            raise HTTPException(404)
        if machine == cfg.machine:
            body: BaseModel
            if what == "transcript":
                body = await asyncio.to_thread(read_transcript, ident, limit, since)
            elif what == "screen":
                body = await asyncio.to_thread(read_screen, ident)
            else:
                body = await asyncio.to_thread(read_logs, ident)
            return Response(body.model_dump_json(), media_type="application/json")
        if machine not in peers().peers:
            raise HTTPException(404, f"unknown machine {machine}")
        query = urlencode({"limit": limit, **({"since": since} if since else {})})
        path = f"/api/sessions/{ident}/{what}" + (f"?{query}" if what == "transcript" else "")
        try:
            r = await peers().get(machine, path, timeout=SCREEN_TIMEOUT if what == "screen" else None)
        except httpx.HTTPError as e:
            raise HTTPException(504, f"{machine} unreachable: {e}") from e
        return relay(machine, r)

    # ---- control (Phase 2) -----------------------------------------------
    @app.post("/api/sessions", dependencies=[Depends(require_write)])
    def start_session(body: NewSessionBody) -> ActionResult:
        return actions.start(body)

    @app.post("/api/sessions/{ident}/stop", dependencies=[Depends(require_write)])
    def stop_session(ident: str) -> ActionResult:
        return actions.stop(ident)

    @app.post("/api/sessions/{ident}/respawn", dependencies=[Depends(require_write)])
    def respawn_session(ident: str) -> ActionResult:
        return actions.respawn(ident)

    @app.post("/api/sessions/{ident}/message", dependencies=[Depends(require_write)])
    def message_session(ident: str, body: MessageBody) -> ActionResult:
        return actions.message(ident, body)

    # ---- the session's own terminal (answer menus, mode, esc, typing) -----
    def read_screen(ident: str) -> SessionScreen:
        return attacher.screen(actions.live_background(ident))

    def send_input(ident: str, body: SessionInputBody) -> SessionScreen:
        short_id = actions.live_background(ident)
        match body.input.action:
            case "choose":
                return attacher.choose(short_id, body.input.option, body.input.text)
            case "key":
                return attacher.interrupt(short_id)
            case "mode":
                return attacher.set_mode(short_id, body.input.mode)
            case "type":
                return attacher.type_message(short_id, body.input.text)

    @app.get("/api/sessions/{ident}/screen", dependencies=[Depends(require)])
    def session_screen(ident: str) -> SessionScreen:
        return read_screen(ident)

    @app.post("/api/sessions/{ident}/input", dependencies=[Depends(require_write)])
    def session_input(ident: str, body: SessionInputBody) -> SessionScreen:
        return send_input(ident, body)

    @app.delete("/api/sessions/{ident}", dependencies=[Depends(require_write)])
    def remove_session(ident: str) -> ActionResult:
        return actions.remove(ident)

    # ---- handoff (Phase 4, experimental) ------------------------------------
    @app.get("/api/sessions/{session_id}/export", dependencies=[Depends(require)])
    def export_session(session_id: str, meta_only: bool = False) -> HandoffBundle:
        return handoff.export(session_id, meta_only)

    @app.post("/api/import", dependencies=[Depends(require_write)])
    def import_session(body: ImportBody) -> ImportResult:
        result = handoff.import_(body)
        log.info("import %s into %s (dry_run=%s, %d files)", body.bundle.session_id, result.cwd,
                 body.dry_run, len(result.written))
        return result

    @app.post("/api/sessions/{session_id}/release", dependencies=[Depends(require_write)])
    def release_session(session_id: str, body: ReleaseBody) -> ReleaseResult:
        return actions.release(session_id, body)

    async def call(machine: str, method: str, path: str, body: dict[str, object] | None,
                   local: Callable[[], M], model: type[M]) -> M:
        """Run a step on `machine`: in-process if it is us, else on the peer."""
        if machine == cfg.machine:
            return await asyncio.to_thread(local)
        if machine not in peers().peers:
            raise ActionError(404, f"không biết máy {machine}")
        try:
            r = await peers().request(machine, method, path, body)
        except httpx.HTTPError as e:
            raise ActionError(504, f"{machine} không trả lời: {e}") from e
        if r.status_code >= 400:
            label = peers().label(machine)
            if is_outdated(r):
                raise ActionError(r.status_code, outdated_message(label))
            raise ActionError(r.status_code, f"{label}: {response_detail(r)}")
        return model.model_validate_json(r.content)

    @app.post("/api/handoff", dependencies=[Depends(require_write)])
    async def run_handoff(body: HandoffBody) -> HandoffResult:
        sid, src, dst, dry = body.session_id, body.source, body.target, body.dry_run
        if src == dst:
            raise ActionError(400, "máy nguồn và máy đích phải khác nhau")
        for m in (src, dst):
            if m != cfg.machine and m not in peers().peers:
                raise ActionError(404, f"không biết máy {m}")
        rel_body = ReleaseBody(force=body.force, dry_run=dry)
        released = await call(src, "POST", f"/api/sessions/{sid}/release", rel_body.model_dump(by_alias=True),
                              lambda: actions.release(sid, rel_body), ReleaseResult)
        try:
            bundle = await call(src, "GET", f"/api/sessions/{sid}/export?meta_only={str(dry).lower()}", None,
                                lambda: handoff.export(sid, dry), HandoffBundle)
            imp_body = ImportBody(bundle=bundle, target_cwd=body.target_cwd, dry_run=dry,
                                  overwrite=body.overwrite)
            imported = await call(dst, "POST", "/api/import", imp_body.model_dump(by_alias=True),
                                  lambda: handoff.import_(imp_body), ImportResult)
            resumed: str | None = None
            if body.message and not dry:
                msg = MessageBody(text=body.message)
                r = await call(dst, "POST", f"/api/sessions/{sid}/message", msg.model_dump(by_alias=True),
                               lambda: actions.message(sid, msg), ActionResult)
                resumed = r.id
        except ActionError as e:
            if released.stopped:
                raise ActionError(e.status, f"{e.message} — session ở {src} đã được dừng, hội thoại vẫn "
                                            "còn nguyên ở đó (có thể gửi tin nhắn để chạy lại).") from e
            raise
        warnings = list(imported.warnings)
        if dry and released.was_live:
            warnings.insert(0, f"Session đang chạy ở {src} và sẽ được dừng trước khi chuyển.")
        log.info("handoff %s %s -> %s (dry_run=%s)", sid, src, dst, dry)
        return HandoffResult(session_id=sid, source=src, target=dst, cwd=imported.cwd, dry_run=dry,
                             stopped_source=released.stopped, resumed_id=resumed, warnings=warnings)

    @app.post("/api/awake", dependencies=[Depends(require_write)])
    def set_awake(body: AwakeBody) -> Awake:
        result = keep.set_mode(body.mode)
        log.info("keep-awake set to %s (active=%s)", result.mode.value, result.active)
        return result

    @app.post("/api/m/{machine}/awake", dependencies=[Depends(require_write)])
    async def m_awake(machine: str, body: AwakeBody) -> Response:
        if machine == cfg.machine:
            result = await asyncio.to_thread(set_awake, body)
            return Response(result.model_dump_json(), media_type="application/json")
        if machine not in peers().peers:
            raise HTTPException(404, f"unknown machine {machine}")
        try:
            r = await peers().request(machine, "POST", "/api/awake", body.model_dump(by_alias=True))
        except httpx.HTTPError as e:
            raise HTTPException(504, f"{machine} unreachable: {e}") from e
        return relay(machine, r)

    @app.post("/api/m/{machine}/sessions", dependencies=[Depends(require_write)])
    async def m_start(machine: str, body: NewSessionBody) -> Response:
        return await on_machine(machine, "POST", "/api/sessions", lambda: actions.start(body), body)

    @app.post("/api/m/{machine}/sessions/{ident}/message", dependencies=[Depends(require_write)])
    async def m_message(machine: str, ident: str, body: MessageBody) -> Response:
        check_id(ident)
        return await on_machine(machine, "POST", f"/api/sessions/{ident}/message",
                                lambda: actions.message(ident, body), body)

    @app.post("/api/m/{machine}/sessions/{ident}/input", dependencies=[Depends(require_write)])
    async def m_input(machine: str, ident: str, body: SessionInputBody) -> Response:
        check_id(ident)
        return await on_machine(machine, "POST", f"/api/sessions/{ident}/input",
                                lambda: send_input(ident, body), body)

    @app.post("/api/m/{machine}/sessions/{ident}/{action}", dependencies=[Depends(require_write)])
    async def m_action(machine: str, ident: str, action: str) -> Response:
        check_id(ident)
        fns = {"stop": actions.stop, "respawn": actions.respawn}
        if action not in fns:
            raise HTTPException(404)
        return await on_machine(machine, "POST", f"/api/sessions/{ident}/{action}",
                                lambda: fns[action](ident))

    @app.delete("/api/m/{machine}/sessions/{ident}", dependencies=[Depends(require_write)])
    async def m_remove(machine: str, ident: str) -> Response:
        check_id(ident)
        return await on_machine(machine, "DELETE", f"/api/sessions/{ident}", lambda: actions.remove(ident))

    # ---- attachments -----------------------------------------------------
    async def read_upload(request: Request) -> bytes:
        """The raw request body, refused with 413 as soon as it passes the limit."""
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file too large: {declared} bytes (max {MAX_UPLOAD_BYTES})")
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"file too large (max {MAX_UPLOAD_BYTES} bytes)")
            chunks.append(chunk)
        return b"".join(chunks)

    @app.post("/api/uploads", dependencies=[Depends(require_write)])
    async def upload(request: Request, name: UploadName) -> Upload:
        data = await read_upload(request)
        return await asyncio.to_thread(uploads.save, name, data)

    @app.post("/api/m/{machine}/uploads", dependencies=[Depends(require_write)])
    async def m_upload(machine: str, request: Request, name: UploadName) -> Response:
        data = await read_upload(request)
        if machine == cfg.machine:
            result = await asyncio.to_thread(uploads.save, name, data)
            return Response(result.model_dump_json(), media_type="application/json")
        if machine not in peers().peers:
            raise HTTPException(404, f"unknown machine {machine}")
        try:
            r = await peers().upload(machine, name, data)
        except httpx.HTTPError as e:
            raise HTTPException(504, f"{machine} unreachable: {e}") from e
        return relay(machine, r)

    # ---- dashboard -------------------------------------------------------
    index_html = versioned_index(WEB_DIR)  # assets do not change while fleetd runs

    @app.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse(index_html, headers=NO_CACHE)

    # At the root, not /static: a web app manifest's scope starts from where it is served.
    @app.get("/manifest.webmanifest")
    def manifest() -> Response:
        return Response((WEB_DIR / "manifest.webmanifest").read_bytes(),
                        media_type="application/manifest+json", headers=NO_CACHE)

    app.mount("/static", RevalidatedStaticFiles(directory=WEB_DIR), name="static")
    return app


def wait_for_parent_exit(parent_pid: int, getppid: Callable[[], int] = os.getppid,
                         sleep: Callable[[float], None] = time.sleep, interval: float = 2.0) -> None:
    """Block until our parent changes (it died and we were re-parented)."""
    while getppid() == parent_pid:
        sleep(interval)


def watch_parent() -> threading.Thread:
    """Exit when the process that started us (Fleet.app) is gone, even if it was
    SIGKILLed or crashed, so no orphaned fleetd keeps holding the port.

    Watches the parent we actually have at start (a wrapper such as `uv run`
    included), not the pid in FLEET_PARENT_PID, which is only the on-switch.
    """
    parent = os.getppid()

    def loop() -> None:
        wait_for_parent_exit(parent)
        log.warning("parent %s is gone; exiting", parent)
        os._exit(0)

    t = threading.Thread(target=loop, name="parent-watch", daemon=True)
    t.start()
    return t


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Set by Fleet.app. Removed so the processes fleetd starts (claude, …) don't inherit it.
    if os.environ.pop("FLEET_PARENT_PID", ""):
        watch_parent()
    cfg = _config_from_env()
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=cfg.port, log_level="info",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    run()
