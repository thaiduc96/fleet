"""Machine health: uptime, battery, and who is keeping the Mac awake."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable

from fleetd.models import Awake, Battery, Health, SleepHolder

# Assertion kinds that keep the *system* (not just the display) from sleeping.
KEEPS_AWAKE = ("PreventSystemSleep", "PreventUserIdleSystemSleep")
# powerd holds this while the display is on; it vanishes when the screen sleeps,
# so it says nothing about whether the Mac stays reachable.
DISPLAY_ON = "Prevent sleep while display is on"
MIN_AGE_SEC = 60  # ignore short network/OS tasks (nsurlsessiond, runningboardd…)

ASSERTION_RE = re.compile(
    r"^\s*pid (?P<pid>\d+)\((?P<proc>[^)]+)\): \[[^\]]*\] (?P<age>\d+:\d\d:\d\d) "
    r"(?P<kind>\w+) named: \"(?P<name>[^\"]*)\""
)
ON_BEHALF_RE = re.compile(r"on behalf of Process ID (\d+)")


def _sh(*args: str) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5, check=False).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def parse_battery(pmset_batt: str) -> Battery:
    m = re.search(r"(\d+)%;\s*([^;]+);", pmset_batt)
    return Battery(
        percent=int(m.group(1)) if m else None,
        state=m.group(2).strip() if m else None,
        on_ac="AC Power" in pmset_batt,
    )


def _age(hms: str) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def process_label(command: str) -> str:
    """'/x/.venv/bin/python -m homecam.supervisor' -> 'homecam.supervisor'."""
    parts = command.split()
    if not parts:
        return "?"
    if "-m" in parts and parts.index("-m") + 1 < len(parts):
        return parts[parts.index("-m") + 1]
    return parts[0].rsplit("/", 1)[-1]


def parse_assertions(pmset_assertions: str, owner_of: Callable[[int], str | None],
                     parent_of: Callable[[int], str | None] = lambda _: None) -> list[SleepHolder]:
    """Per-process assertions from `pmset -g assertions` that keep the system up.

    For caffeinate, `owner` is the process it asserts on behalf of (`-w pid`),
    else its parent (e.g. Claude Code's `caffeinate -i -t 300`).
    """
    holders: list[SleepHolder] = []
    lines = pmset_assertions.splitlines()
    for i, line in enumerate(lines):
        m = ASSERTION_RE.match(line)
        if not m or m["kind"] not in KEEPS_AWAKE or DISPLAY_ON in m["name"]:
            continue
        is_caffeinate = m["proc"] == "caffeinate"
        if not is_caffeinate and _age(m["age"]) < MIN_AGE_SEC:
            continue
        owner = None
        if is_caffeinate:
            detail = lines[i + 1] if i + 1 < len(lines) else ""
            behalf = ON_BEHALF_RE.search(detail)
            owner = owner_of(int(behalf.group(1))) if behalf else parent_of(int(m["pid"]))
        holder = SleepHolder(pid=int(m["pid"]), process=m["proc"], kind=m["kind"], owner=owner)
        if not any(h.pid == holder.pid and h.kind == holder.kind for h in holders):
            holders.append(holder)
    return holders


def command_of(pid: int) -> str | None:
    out = _sh("ps", "-o", "command=", "-p", str(pid)).strip()
    return process_label(out) if out else None


def parent_command_of(pid: int) -> str | None:
    ppid = _sh("ps", "-o", "ppid=", "-p", str(pid)).strip()
    return command_of(int(ppid)) if ppid.isdigit() and int(ppid) > 1 else None


def boot_time() -> float | None:
    m = re.search(r"sec = (\d+)", _sh("sysctl", "-n", "kern.boottime"))
    return float(m.group(1)) if m else None


def health(awake: Awake | None = None, self_pid: int | None = None) -> Health:
    bt = boot_time()

    def owner_of(pid: int) -> str | None:
        return "fleetd" if pid == self_pid else command_of(pid)

    holders = parse_assertions(_sh("pmset", "-g", "assertions"), owner_of, parent_command_of)
    return Health(
        uptime_sec=int(time.time() - bt) if bt else None,
        battery=parse_battery(_sh("pmset", "-g", "batt")),
        sleep_prevented=bool(holders),
        holders=holders,
        awake=awake,
    )
