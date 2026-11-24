"""Folder browser for the new-session form: the subfolders of a directory inside ~.

Only what `safe_dir` would accept is listed, so every folder offered can be opened
and used as a session's cwd. Hidden folders are left out: projects rarely live there.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fleetd.actions import ActionError, safe_dir
from fleetd.models import Folders
from fleetd.peers import READ_TIMEOUT

MAX_FOLDERS = 500  # ~/Library-sized directories would otherwise flood the phone screen
# Answer before the Mac that asked gives up, so its dashboard shows why instead of "unreachable".
LIST_DEADLINE = READ_TIMEOUT - 1.0
# macOS can hold open() on ~/Documents for seconds (privacy check, iCloud). A thread stuck
# there cannot be cancelled, so listings get their own threads and cannot starve the rest.
_listing_threads = ThreadPoolExecutor(max_workers=2, thread_name_prefix="folders")


def macos_stalled(machine_label: str) -> ActionError:
    return ActionError(503, "macOS chưa cho Fleet đọc thư mục này (thường do bước kiểm tra quyền riêng tư "
                            "hoặc iCloud bị kẹt). Thử lại sau ít phút; nếu vẫn lỗi, "
                            f"thoát và mở lại Fleet trên máy {machine_label}.")


def tilde(path: Path, home: Path) -> str:
    """`/Users/me/code` -> `~/code` (paths here are always inside home)."""
    rel = path.relative_to(home)
    return "~" if rel == Path() else f"~/{rel.as_posix()}"


def expand(raw: str, home: Path) -> str:
    """Resolve `~` against the given home, which in tests is not the real one."""
    if raw in ("", "~"):
        return str(home)
    return str(home / raw[2:]) if raw.startswith("~/") else raw


async def list_folders_in_time(raw: str, home: Path, machine_label: str) -> Folders:
    """`list_folders`, but a 503 naming the cause when macOS stalls it past LIST_DEADLINE."""
    listing = asyncio.get_running_loop().run_in_executor(_listing_threads, list_folders, raw, home)
    try:
        return await asyncio.wait_for(listing, LIST_DEADLINE)
    except (TimeoutError, InterruptedError) as e:  # EINTR is how a stalled privacy check ends
        raise macos_stalled(machine_label) from e


def list_folders(raw: str, home: Path) -> Folders:
    """Subfolders of `raw` (absolute or ~-relative; empty means ~), sorted by name.

    Raises ActionError: 400 outside home, 404 missing, 403 when macOS refuses to read it.
    Raises InterruptedError when macOS gives up on its privacy check for the folder.
    """
    home = home.resolve()
    folder = safe_dir(expand(raw.strip(), home), home)
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.casefold())
    except PermissionError as e:
        raise ActionError(403, f"không có quyền đọc {tilde(folder, home)}") from e
    names = [p.name for p in entries if not p.name.startswith(".") and is_usable(p, home)]
    return Folders(path=tilde(folder, home),
                   parent=tilde(folder.parent, home) if folder != home else None,
                   folders=names[:MAX_FOLDERS], truncated=len(names) > MAX_FOLDERS)


def is_usable(entry: Path, home: Path) -> bool:
    """A directory (or a link to one) that stays inside home once resolved."""
    try:
        return entry.is_dir() and entry.resolve().is_relative_to(home)
    except OSError:  # broken or looping link, or unreadable: not something to offer
        return False
