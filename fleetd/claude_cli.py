"""Thin wrapper around the `claude` CLI and its on-disk job state.

Everything that depends on Claude Code internals lives here and in
transcripts.py, so a CLI change only needs fixing in one place.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from enum import StrEnum
from pathlib import Path

from fleetd.models import SessionState

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]|\r")
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# agent view groups: Needs input, Working, Idle, Completed / Failed / Stopped
_STATE_MAP: dict[str, SessionState] = {
    "needs_input": SessionState.NEEDS_INPUT, "blocked": SessionState.NEEDS_INPUT,
    "waiting": SessionState.NEEDS_INPUT, "requires_action": SessionState.NEEDS_INPUT,
    "asked": SessionState.NEEDS_INPUT,
    "working": SessionState.WORKING, "running": SessionState.WORKING,
    "busy": SessionState.WORKING, "starting": SessionState.WORKING,
    "idle": SessionState.IDLE,
    "done": SessionState.DONE, "completed": SessionState.DONE,
    "success": SessionState.DONE, "settled": SessionState.DONE,
    "failed": SessionState.FAILED, "error": SessionState.FAILED, "crashed": SessionState.FAILED,
    "stopped": SessionState.STOPPED, "killed": SessionState.STOPPED,
    "exited": SessionState.STOPPED, "closed": SessionState.STOPPED,
}

AgentEntry = dict[str, object]


def normalize_state(entry: AgentEntry) -> SessionState:
    state = str(entry.get("state") or "").lower()
    if state in _STATE_MAP:
        return _STATE_MAP[state]
    if state:
        return SessionState.UNKNOWN
    busy = str(entry.get("status", "")).lower() == "busy"
    return SessionState.WORKING if busy else SessionState.IDLE


CURSOR_FORWARD = re.compile(r"\x1b\[(\d{0,3})C")
# Spinner redraw frames left in the terminal capture: "✻Spelunking…", "✳8", "⏺", "4".
SPINNER_LINE = re.compile(r"^\s*[✻✶✳✢·✽*⏺]?\s*(?:\d{1,3}|[A-Za-z]+…)?\s*$")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def clean_terminal(text: str) -> str:
    """Strip ANSI codes, spinner frames and repeated redraw lines."""
    # The TUI draws spaces as "cursor forward n" (ESC[nC); keep them as spaces.
    text = CURSOR_FORWARD.sub(lambda m: " " * int(m.group(1) or 1), text)
    out: list[str] = []
    for line in ANSI.sub("", text).split("\n"):
        line = line.rstrip()
        if SPINNER_LINE.match(line) and line.strip():
            continue
        if out and line == out[-1]:
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


class CLIErrorKind(StrEnum):
    NOT_FOUND = "not_found"
    UNTRUSTED = "untrusted"
    OTHER = "other"


class CLIError(RuntimeError):
    @property
    def kind(self) -> CLIErrorKind:
        msg = str(self)
        if "No job matching" in msg:
            return CLIErrorKind.NOT_FOUND
        if "not trusted" in msg:
            return CLIErrorKind.UNTRUSTED
        return CLIErrorKind.OTHER


# "backgrounded · c1da82bb · fleet-spike"
BACKGROUNDED_RE = re.compile(r"backgrounded\s*·\s*([A-Za-z0-9_-]+)")
# Remote callers may not pick modes that skip permission prompts entirely.
ALLOWED_PERMISSION_MODES = ("manual", "auto", "acceptEdits", "plan")


class ClaudeCLI:
    def __init__(self, binary: str, claude_home: Path, timeout: float = 20.0) -> None:
        self.binary: str = binary
        self.claude_home: Path = claude_home
        self.timeout: float = timeout
        self._lock = threading.Lock()
        self._agents_cache: tuple[float, list[AgentEntry]] | None = None

    def run(self, *args: str, timeout: float | None = None, cwd: Path | None = None) -> str:
        env = dict(os.environ, NO_COLOR="1", TERM="dumb")
        cmd = f"claude {' '.join(args[:3])}"
        try:
            p = subprocess.run(
                [self.binary, *args], capture_output=True, text=True, check=False,
                timeout=timeout or self.timeout, env=env, stdin=subprocess.DEVNULL, cwd=cwd,
            )
        except FileNotFoundError as e:
            raise CLIError(f"claude binary not found: {self.binary}") from e
        except subprocess.TimeoutExpired as e:
            raise CLIError(f"`{cmd}` timed out") from e
        if p.returncode != 0:
            msg = ANSI.sub("", (p.stderr or p.stdout).strip())[:500]
            raise CLIError(f"`{cmd}` exited {p.returncode}: {msg}")
        return p.stdout

    def version(self) -> str:
        try:
            return self.run("--version", timeout=10).strip()
        except CLIError:
            return "unknown"

    def agents(self, max_age: float = 2.0) -> list[AgentEntry]:
        """`claude agents --json --all`, cached briefly (several clients poll)."""
        with self._lock:
            now = time.monotonic()
            if self._agents_cache and now - self._agents_cache[0] < max_age:
                return self._agents_cache[1]
            try:
                data = json.loads(self.run("agents", "--json", "--all") or "[]")
            except ValueError as e:
                raise CLIError("`claude agents --json` returned invalid JSON") from e
            if not isinstance(data, list):
                raise CLIError("unexpected `claude agents --json` output")
            entries = [d for d in data if isinstance(d, dict)]
            self._agents_cache = (now, entries)
            return entries

    def logs(self, ident: str) -> str:
        return clean_terminal(self.run("logs", ident))

    def invalidate(self) -> None:
        with self._lock:
            self._agents_cache = None

    def _action(self, *args: str, cwd: Path | None = None) -> str:
        try:
            return clean_terminal(self.run(*args, timeout=60, cwd=cwd))
        finally:
            self.invalidate()

    def stop(self, ident: str) -> str:
        return self._action("stop", ident)

    def respawn(self, ident: str) -> str:
        return self._action("respawn", ident)

    def remove(self, ident: str) -> str:
        """`claude rm` — refuses on its own if the worktree has unpushed work."""
        return self._action("rm", ident)

    def start(self, cwd: Path, prompt: str, name: str | None = None,
              permission_mode: str | None = None) -> str:
        """Start a background session; returns its short id."""
        args = ["--bg"]
        if name:
            args += ["-n", name]
        if permission_mode:
            if permission_mode not in ALLOWED_PERMISSION_MODES:
                raise ValueError(f"permission mode not allowed: {permission_mode}")
            args += ["--permission-mode", permission_mode]
        return self._parse_id(self._action(*args, "--", prompt, cwd=cwd))

    def resume(self, session_id: str, cwd: Path, message: str) -> str:
        """Continue a session that is not running, in the background, same id."""
        return self._parse_id(self._action("--bg", "--resume", session_id, "--", message, cwd=cwd))

    @staticmethod
    def _parse_id(output: str) -> str:
        m = BACKGROUNDED_RE.search(output)
        if not m:
            raise CLIError(f"could not read session id from: {output[:300]}")
        return m.group(1)

    def is_running(self, short_id: str, pid: int | None) -> bool:
        """True while the session's process or its daemon worker still exists.

        Measured on 2.1.323: right after `claude stop` the agents JSON already
        drops `pid`, but the process and its worker live ~0.5s longer, and a
        `--resume` in that window starts a copy instead of continuing.
        """
        if pid and _pid_alive(pid):
            return True
        entry = next((a for a in self.agents(max_age=0) if a.get("id") == short_id), None)
        if entry and entry.get("pid"):
            return True
        return short_id in self._roster_workers()

    def _roster_workers(self) -> set[str]:
        try:
            data = json.loads((self.claude_home / "daemon" / "roster.json").read_text())
        except (OSError, ValueError):
            return set()
        workers = data.get("workers") if isinstance(data, dict) else None
        return set(workers) if isinstance(workers, dict) else set()

    def job_state(self, short_id: str) -> dict[str, object]:
        """~/.claude/jobs/<id>/state.json (background sessions only)."""
        if not ID_RE.fullmatch(short_id or ""):
            return {}
        path = self.claude_home / "jobs" / short_id / "state.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
