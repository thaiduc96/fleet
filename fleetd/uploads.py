"""Files attached from the dashboard, stored where the session's Claude can read them.

Claude Code itself handles a pasted image by saving it and putting the *path* in
the prompt; Fleet does the same. Files live under ~/.fleet/uploads/<date>/ on the
machine that runs the session. The path is always built here, never taken from
the client, and old days are pruned.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from fleetd.models import Upload

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_NAME_CHARS = 80
KEEP_DAYS = 30
DAY_SEC = 86400
DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UNSAFE_CHARS_RE = re.compile(r"[^\w.\-]+")
FALLBACK_NAME = "file"


def clean_name(name: str) -> str:
    """A safe file name from whatever the browser sent: no directories, no control chars."""
    base = re.split(r"[/\\]", name)[-1]
    base = UNSAFE_CHARS_RE.sub("_", base).strip("._") or FALLBACK_NAME
    if len(base) <= MAX_NAME_CHARS:
        return base
    stem, dot, ext = base.rpartition(".")
    if dot and 0 < len(ext) <= 10 and stem:
        return f"{stem[:MAX_NAME_CHARS - len(ext) - 1]}.{ext}"
    return base[:MAX_NAME_CHARS]


class Uploads:
    def __init__(self, root: Path, now: Callable[[], float] = time.time) -> None:
        self.root = root
        self.now = now

    def save(self, name: str, data: bytes) -> Upload:
        """Write `data` to a new file and return where it is. Never overwrites."""
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(f"upload of {len(data)} bytes exceeds {MAX_UPLOAD_BYTES}")
        day = self.root / time.strftime("%Y-%m-%d", time.localtime(self.now()))
        day.mkdir(parents=True, exist_ok=True, mode=0o700)
        safe = clean_name(name)
        path = day / f"{uuid.uuid4().hex[:8]}-{safe}"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        log.info("saved upload %s (%d bytes)", path.name, len(data))
        return Upload(path=str(path), name=safe, size=len(data))

    def prune(self, keep_days: int = KEEP_DAYS) -> int:
        """Delete day folders whose files are all older than `keep_days`; returns how many."""
        if not self.root.is_dir():
            return 0
        cutoff = self.now() - keep_days * DAY_SEC
        removed = 0
        for day in self.root.iterdir():
            if not (day.is_dir() and DAY_DIR_RE.match(day.name)):
                continue
            newest = max((f.stat().st_mtime for f in day.iterdir()), default=0.0)
            if newest < cutoff:
                shutil.rmtree(day)
                removed += 1
        if removed:
            log.info("pruned %d old upload folder(s)", removed)
        return removed
