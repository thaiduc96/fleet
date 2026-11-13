"""Drive a background session's terminal: read its screen, answer its menus, switch mode, type.

Claude Code has no API for answering a background session's plan approval,
permission prompt or question from outside; its docs say to attach. So Fleet
does what a person in a terminal does: `claude attach <id>` in a pseudo-terminal,
read the screen (fleetd.vt + fleetd.prompts), press keys, detach. The session
keeps running when the attach process goes away.

Safety rules: only keys for an option that is on the screen right now are
pressed; typed text has control characters removed, so it can never become a
key sequence; one attach per session at a time.
"""

from __future__ import annotations

import codecs
import fcntl
import logging
import os
import pty
import re
import select
import signal
import struct
import termios
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Protocol

from fleetd import prompts
from fleetd.models import Prompt, SessionScreen, SettableMode
from fleetd.vt import Screen

log = logging.getLogger(__name__)

COLS, ROWS = 100, 40
FIRST_OUTPUT_SEC = 6.0    # attach + first full redraw
QUIET_SEC = 0.45          # no output for this long = the screen has settled
MAX_SETTLE_SEC = 6.0
CLOSE_GRACE_SEC = 1.0
MAX_MODE_STEPS = 6        # shift+tab cycles through at most this many modes
ESC = "\x1b"
SHIFT_TAB = "\x1b[Z"
ENTER = "\r"
PASTE_START, PASTE_END = "\x1b[200~", "\x1b[201~"
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # keeps \t and \n


class AttachError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Tty(Protocol):
    def read(self, timeout: float) -> bytes:
        """Output available within `timeout` seconds; b"" if none (or the process ended)."""

    def write(self, data: bytes) -> None: ...

    def close(self) -> None: ...


class AttachProcess:
    """`claude attach <id>` in a pseudo-terminal of COLS x ROWS."""

    def __init__(self, claude_bin: str, short_id: str) -> None:
        pid, fd = pty.fork()
        if pid == 0:  # child: become the attach client
            try:
                os.execvp(claude_bin, [claude_bin, "attach", short_id])
            finally:
                os._exit(127)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        self.pid, self.fd = pid, fd

    def read(self, timeout: float) -> bytes:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return b""
        try:
            return os.read(self.fd, 65536)
        except OSError:  # EIO: the client exited
            return b""

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)

    def close(self) -> None:
        # SIGHUP like a closed terminal; `claude attach` sometimes ignores it inside a text field.
        for sig in (signal.SIGHUP, signal.SIGKILL):
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + CLOSE_GRACE_SEC
            while time.monotonic() < deadline:
                if os.waitpid(self.pid, os.WNOHANG) != (0, 0):
                    os.close(self.fd)
                    return
                time.sleep(0.05)
        os.close(self.fd)


OpenTty = Callable[[str], Tty]


def clean_text(text: str) -> str:
    """Typed text without control characters: it can never turn into ESC, Enter or a shortcut."""
    return CONTROL_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


class Terminal:
    """One attach: a tty plus the emulated screen it draws."""

    def __init__(self, tty: Tty, short_id: str, clock: Callable[[], float]) -> None:
        self.tty = tty
        self.short_id = short_id
        self.clock = clock
        self.screen = Screen(COLS, ROWS)
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")  # a read may end mid-character

    def settle(self, first_wait: float = QUIET_SEC) -> None:
        """Read until the TUI stops redrawing."""
        deadline = self.clock() + MAX_SETTLE_SEC
        wait = first_wait
        while self.clock() < deadline:
            chunk = self.tty.read(wait)
            if not chunk:
                return
            self.screen.feed(self._decoder.decode(chunk))
            wait = QUIET_SEC

    def send(self, keys: str) -> None:
        self.tty.write(keys.encode())
        self.settle()

    def view(self) -> SessionScreen:
        lines = self.screen.lines()
        parsed = prompts.parse(lines)
        return SessionScreen(id=self.short_id, text="\n".join(lines).rstrip("\n"),
                             mode=parsed.mode, prompt=parsed.prompt)


class Attacher:
    def __init__(self, open_tty: OpenTty, clock: Callable[[], float] = time.monotonic) -> None:
        self.open_tty = open_tty
        self.clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @classmethod
    def for_cli(cls, claude_bin: str) -> Attacher:
        return cls(lambda short_id: AttachProcess(claude_bin, short_id))

    @contextmanager
    def _attached(self, short_id: str) -> Iterator[Terminal]:
        with self._guard:
            lock = self._locks.setdefault(short_id, threading.Lock())
        if not lock.acquire(timeout=MAX_SETTLE_SEC * 3):
            raise AttachError(409, "Đang có thao tác khác trên session này, thử lại sau.")
        try:
            tty = self.open_tty(short_id)
            try:
                term = Terminal(tty, short_id, self.clock)
                term.settle(FIRST_OUTPUT_SEC)
                yield term
            finally:
                tty.close()
        finally:
            lock.release()

    # ------------------------------------------------------------ actions
    def screen(self, short_id: str) -> SessionScreen:
        with self._attached(short_id) as term:
            return term.view()

    def choose(self, short_id: str, option: str, text: str | None) -> SessionScreen:
        with self._attached(short_id) as term:
            prompt = _require_prompt(term.view().prompt)
            picked = next((o for o in prompt.options if o.key == option), None)
            if picked is None:
                raise AttachError(409, f"Lựa chọn {option} không còn trên màn hình.")
            typed = clean_text(text or "").replace("\n", " ").strip()
            if picked.needs_text and not typed:
                raise AttachError(400, f'"{picked.label}" cần nội dung.')
            log.info("session %s: choose %s (%s)", short_id, option, picked.label)
            term.send(option)
            if typed:
                term.send(typed)
                term.send(ENTER)
            elif _still_asking(term.view().prompt, prompt, option):
                term.send(ENTER)  # menus where a digit only moves the highlight
            return term.view()

    def interrupt(self, short_id: str) -> SessionScreen:
        with self._attached(short_id) as term:
            log.info("session %s: esc", short_id)
            term.send(ESC)
            return term.view()

    def set_mode(self, short_id: str, mode: SettableMode) -> SessionScreen:
        with self._attached(short_id) as term:
            view = term.view()
            if view.prompt is not None:
                raise AttachError(409, "Session đang chờ trả lời; trả lời trước rồi mới đổi mode.")
            for _ in range(MAX_MODE_STEPS):
                if view.mode == mode:
                    log.info("session %s: mode %s", short_id, mode)
                    return view
                term.send(SHIFT_TAB)
                view = term.view()
            raise AttachError(409, f"Không chuyển được sang mode {mode} (đang là {view.mode or 'không rõ'}).")

    def type_message(self, short_id: str, text: str) -> SessionScreen:
        with self._attached(short_id) as term:
            if term.view().prompt is not None:
                raise AttachError(409, "Session đang chờ trả lời một câu hỏi; hãy chọn ở đó.")
            body = clean_text(text).strip()
            if not body:
                raise AttachError(400, "Tin nhắn trống.")
            log.info("session %s: typed a message (%d chars)", short_id, len(body))
            term.send(f"{PASTE_START}{body}{PASTE_END}")  # one paste keeps newlines inside the message
            term.send(ENTER)
            return term.view()


def _require_prompt(prompt: Prompt | None) -> Prompt:
    if prompt is None:
        raise AttachError(409, "Session không còn chờ trả lời.")
    return prompt


def _still_asking(now: Prompt | None, before: Prompt, option: str) -> bool:
    """Same menu still open with our option highlighted: the digit only moved the cursor."""
    if now is None or now.title != before.title:
        return False
    return any(o.key == option and o.selected for o in now.options)
