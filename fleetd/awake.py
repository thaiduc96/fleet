"""Keep this Mac awake so its sessions stay reachable from the other machine.

Claude Code itself only holds a ~5 minute `caffeinate -i` while a session is
working; a session waiting for your answer lets the Mac idle-sleep, and then
neither Fleet nor Remote Control can reach it.

fleetd runs `caffeinate <flags> -w <fleetd pid>` as a child, so the assertion
disappears by itself if fleetd dies. The chosen mode is persisted in
~/.fleet/state.json and survives restarts.

Note: on a MacBook, closing the lid still sleeps the machine unless an external
display is attached; caffeinate cannot override that.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections.abc import Callable
from typing import Protocol

from fleetd.models import Awake, AwakeMode
from fleetd.state import StateFile

log = logging.getLogger(__name__)
CAFFEINATE = "/usr/bin/caffeinate"
FLAGS: dict[AwakeMode, tuple[str, ...]] = {
    AwakeMode.AC: ("-s",),
    AwakeMode.ALWAYS: ("-i", "-s"),
}


class Proc(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


Spawn = Callable[[list[str]], Proc]


def spawn_process(argv: list[str]) -> Proc:
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


class KeepAwake:
    def __init__(self, state: StateFile, default: AwakeMode = AwakeMode.OFF,
                 spawn: Spawn = spawn_process, pid: int | None = None) -> None:
        self.state = state
        self.default = default
        self._spawn = spawn
        self._pid = pid or os.getpid()
        self._proc: Proc | None = None
        self._lock = threading.Lock()
        self._mode = self._load()

    # ---- persistence -------------------------------------------------------
    def _load(self) -> AwakeMode:
        try:
            return AwakeMode(self.state.get("keepAwake"))
        except ValueError:
            return self.default

    def _save(self) -> None:
        self.state.update(keepAwake=self._mode.value)

    # ---- process -----------------------------------------------------------
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _stop_proc(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.warning("caffeinate did not exit after SIGTERM")
        self._proc = None

    def _ensure(self) -> None:
        """Make the child match the mode; restart it if it died."""
        if self._mode is AwakeMode.OFF:
            self._stop_proc()
            return
        if self._alive():
            return
        argv = [CAFFEINATE, *FLAGS[self._mode], "-w", str(self._pid)]
        try:
            self._proc = self._spawn(argv)
            log.info("keep-awake %s: %s", self._mode.value, " ".join(argv))
        except OSError as e:
            self._proc = None
            log.error("cannot start caffeinate: %s", e)

    # ---- public ------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            self._ensure()

    def stop(self) -> None:
        with self._lock:
            self._stop_proc()

    def set_mode(self, mode: AwakeMode) -> Awake:
        with self._lock:
            if mode is not self._mode:
                self._stop_proc()
                self._mode = mode
                self._save()
            self._ensure()
            return Awake(mode=self._mode, active=self._alive())

    def status(self) -> Awake:
        with self._lock:
            self._ensure()  # self-heal if caffeinate was killed
            return Awake(mode=self._mode, active=self._alive())
