"""State-changing operations on this machine's sessions.

Sending a message is the tricky one. `claude --bg --resume <id> "msg"` continues
the *same* session only when it is not running (otherwise it starts a copy), so:

  * stopped / exited / history session   -> resume with the message
  * live background session, turn over   -> stop, then resume with the message
  * live background session, still working -> refuse unless force=True
  * live interactive session (open in a terminal) -> refuse; use Remote Control
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from fleetd.claude_cli import (
    ALLOWED_PERMISSION_MODES,
    ID_RE,
    AgentEntry,
    ClaudeCLI,
    CLIError,
    CLIErrorKind,
    normalize_state,
)
from fleetd.models import (
    ActionResult,
    MessageBody,
    NewSessionBody,
    ReleaseBody,
    ReleaseResult,
    SessionState,
)
from fleetd.transcripts import UUID_RE, TranscriptIndex

log = logging.getLogger(__name__)
STOP_TIMEOUT = 15.0
STOP_POLL = 0.5


class ActionError(Exception):
    """An action failed in a way the dashboard should show; carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status: int = status
        self.message: str = message


def from_cli_error(e: CLIError) -> ActionError:
    status = {CLIErrorKind.NOT_FOUND: 404, CLIErrorKind.UNTRUSTED: 409}.get(e.kind, 502)
    if e.kind is CLIErrorKind.UNTRUSTED:
        return ActionError(status, "Thư mục chưa được trust: mở `claude` ở thư mục đó một lần "
                                   "trên máy này và chấp nhận trust prompt.")
    return ActionError(status, str(e))


def check_id(ident: str) -> str:
    if not ID_RE.fullmatch(ident or ""):
        raise ActionError(400, "bad id")
    return ident


def safe_dir(raw: str, home: Path) -> Path:
    """An existing directory inside the user's home (~ is expanded)."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise ActionError(400, "cwd phải là đường dẫn tuyệt đối hoặc bắt đầu bằng ~")
    p = p.resolve()
    if not p.is_relative_to(home.resolve()):
        raise ActionError(400, "cwd phải nằm trong thư mục home")
    if not p.is_dir():
        raise ActionError(404, f"không có thư mục {raw}")
    return p


class Actions:
    def __init__(self, cli: ClaudeCLI, idx: TranscriptIndex, home: Path | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cli = cli
        self.idx = idx
        self.home: Path = home or Path.home()
        self.clock = clock
        self.sleep = sleep

    def _call(self, action: str, ident: str | None, fn: Callable[..., str], *args: object) -> str:
        try:
            out = fn(*args)
        except CLIError as e:
            log.warning("%s %s failed: %s", action, ident, e)
            raise from_cli_error(e) from e
        log.info("%s %s ok", action, ident)
        return out

    def stop(self, ident: str) -> ActionResult:
        out = self._call("stop", ident, self.cli.stop, check_id(ident))
        return ActionResult(action="stop", id=ident, output=out)

    def respawn(self, ident: str) -> ActionResult:
        out = self._call("respawn", ident, self.cli.respawn, check_id(ident))
        return ActionResult(action="respawn", id=ident, output=out)

    def remove(self, ident: str) -> ActionResult:
        out = self._call("rm", ident, self.cli.remove, check_id(ident))
        return ActionResult(action="rm", id=ident, output=out)

    def live_background(self, ident: str) -> str:
        """Short id of a running background session: the only kind `claude attach` can drive."""
        check_id(ident)
        try:
            agents = self.cli.agents(max_age=0)
        except CLIError as e:
            raise from_cli_error(e) from e
        entry = next((a for a in agents if ident in (a.get("id"), a.get("sessionId"))), None)
        if entry is None or not entry.get("pid"):
            raise ActionError(409, "Session không chạy. Gửi tin nhắn để mở lại nó.")
        if entry.get("kind") != "background":
            raise ActionError(409, "Session đang mở trong terminal; hãy trả lời ở đó "
                                   "hoặc qua Remote Control (claude.ai/code).")
        return str(entry["id"])

    def start(self, body: NewSessionBody) -> ActionResult:
        cwd = safe_dir(body.cwd, self.home)
        mode = body.permission_mode or None
        if mode and mode not in ALLOWED_PERMISSION_MODES:
            raise ActionError(400, f"permission mode không được phép: {mode}")
        new_id = self._call("start", None, self.cli.start, cwd, body.prompt.strip(),
                            (body.name or "").strip() or None, mode)
        return ActionResult(action="start", id=new_id)

    def message(self, ident: str, body: MessageBody) -> ActionResult:
        """`ident` is a short id (live sessions) or a full session UUID (history)."""
        check_id(ident)
        try:
            agents = self.cli.agents(max_age=0)
        except CLIError as e:
            raise from_cli_error(e) from e
        entry = next((a for a in agents if ident in (a.get("id"), a.get("sessionId"))), None)
        session_id = str(entry.get("sessionId") or "") if entry else (ident if UUID_RE.match(ident) else "")
        if not session_id:
            raise ActionError(404, "không tìm thấy session")
        summary = self.idx.summary_for(session_id)
        cwd_raw = (entry or {}).get("cwd") or (summary.cwd if summary else None)
        if not cwd_raw:
            raise ActionError(409, "không biết thư mục của session này")
        cwd = safe_dir(str(cwd_raw), self.home)

        if entry and entry.get("pid"):  # process alive
            self._stop_live(entry, body.force)
        new_id = self._call("message", session_id, self.cli.resume, session_id, cwd, body.text.strip())
        if not session_id.startswith(new_id):
            log.warning("resume of %s started a copy %s", session_id, new_id)
            return ActionResult(action="message", id=new_id,
                                output=f"CLI đã tạo bản sao {new_id} thay vì tiếp tục session cũ")
        return ActionResult(action="message", id=new_id)

    def _check_stoppable(self, entry: AgentEntry, force: bool) -> None:
        if entry.get("kind") != "background":
            raise ActionError(409, "Session này đang mở trong terminal; hãy trả lời ở đó "
                                   "hoặc qua Remote Control (claude.ai/code).")
        if normalize_state(entry) is SessionState.WORKING and not force:
            raise ActionError(409, "Session đang chạy dở. Đợi xong, hoặc chọn "
                                   "force để ngắt lượt hiện tại.")

    def _stop_live(self, entry: AgentEntry, force: bool) -> None:
        self._check_stoppable(entry, force)
        short = str(entry["id"])
        pid = entry.get("pid")
        self._call("stop", short, self.cli.stop, short)
        # `claude stop` returns before the process is gone; resuming too early
        # makes the CLI start a *copy* instead of continuing this session.
        self._wait_until_stopped(short, pid if isinstance(pid, int) else None)

    def release(self, session_id: str, body: ReleaseBody) -> ReleaseResult:
        """Make sure the session is not running here (before moving it away)."""
        if not UUID_RE.match(session_id):
            raise ActionError(400, "bad session id")
        try:
            agents = self.cli.agents(max_age=0)
        except CLIError as e:
            raise from_cli_error(e) from e
        entry = next((a for a in agents if a.get("sessionId") == session_id and a.get("pid")), None)
        if entry is None:
            return ReleaseResult(session_id=session_id, was_live=False, stopped=False)
        if body.dry_run:
            self._check_stoppable(entry, body.force)
            return ReleaseResult(session_id=session_id, was_live=True, stopped=False)
        self._stop_live(entry, body.force)
        return ReleaseResult(session_id=session_id, was_live=True, stopped=True)

    def _wait_until_stopped(self, short_id: str, pid: int | None) -> None:
        deadline = self.clock() + STOP_TIMEOUT
        while True:
            try:
                running = self.cli.is_running(short_id, pid)
            except CLIError as e:
                raise from_cli_error(e) from e
            if not running:
                return
            if self.clock() >= deadline:
                raise ActionError(504, f"session {short_id} chưa dừng sau {STOP_TIMEOUT:.0f}s; "
                                       "thử gửi lại sau")
            self.sleep(STOP_POLL)
