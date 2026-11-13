"""~/.fleet/state.json: settings changed at runtime (through fleetd's API only).

Several components read-modify-write the same file (keep-awake mode, peers,
label, own URL), so they share one StateFile and its lock. fleetd is the only
writer: Fleet.app changes settings through the HTTP API, never the file.
Reads are cached until the file's mtime/size changes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

Json = dict[str, object]


class StateFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cache: tuple[tuple[int, int], Json] | None = None

    def _read(self) -> Json:
        try:
            st = os.stat(self.path)
        except OSError:
            return {}
        key = (st.st_mtime_ns, st.st_size)
        if self._cache and self._cache[0] == key:
            return dict(self._cache[1])
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        data = data if isinstance(data, dict) else {}
        self._cache = (key, data)
        return dict(data)

    def get(self, key: str, default: object = None) -> object:
        with self._lock:
            return self._read().get(key, default)

    @property
    def lock(self) -> threading.RLock:
        """Hold this to make a read-then-update sequence atomic."""
        return self._lock

    def update(self, **changes: object) -> None:
        """Set keys (None deletes a key) atomically, keeping all others."""
        with self._lock:
            data = self._read()
            for k, v in changes.items():
                if v is None:
                    data.pop(k, None)
                else:
                    data[k] = v
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
            tmp.replace(self.path)
            self._cache = None
