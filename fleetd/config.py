"""Configuration loaded from ~/.fleet/config.toml.

Example:

    machine = "home"            # short id, unique per Mac
    label = "🏠 Nhà"            # shown in the dashboard
    port = 7777                 # fleetd listens on 127.0.0.1 only
    claude_bin = "/Users/me/.local/bin/claude"

    [[peers]]
    name = "work"
    label = "💼 Công ty"
    url = "https://work-mac.tailnet-name.ts.net"
"""

from __future__ import annotations

import os
import shutil
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def fleet_home() -> Path:
    return Path(os.environ.get("FLEET_HOME", Path.home() / ".fleet"))


def user_home() -> Path:
    """The user's home as fleetd sees it (FLEET_USER_HOME overrides, for tests/dev)."""
    return Path(os.environ.get("FLEET_USER_HOME", Path.home()))


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", user_home() / ".claude"))


@dataclass(frozen=True)
class Peer:
    name: str
    url: str
    label: str = ""


@dataclass(frozen=True)
class Config:
    machine: str
    label: str
    port: int = 7777
    claude_bin: str = "claude"
    peers: tuple[Peer, ...] = ()
    token: str | None = None  # normally read from Keychain, see auth.py
    url: str | None = None    # own tailnet URL; the app sets it via POST /api/settings (stored in state.json)
    claude_home: Path = field(default_factory=claude_home)
    home: Path = field(default_factory=fleet_home)
    user_home: Path = field(default_factory=user_home)  # sessions may only start under here
    keep_awake: str = "off"  # initial mode: off | ac | always (changed later from the dashboard)

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"


def _default_claude_bin() -> str:
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local/bin/claude"
    return str(local) if local.exists() else "claude"


def load(path: Path | None = None) -> Config:
    path = path or fleet_home() / "config.toml"
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    host = socket.gethostname().split(".")[0]
    peers = tuple(
        Peer(name=p["name"], url=p["url"].rstrip("/"), label=p.get("label", p["name"]))
        for p in data.get("peers", [])
    )
    machine = data.get("machine", host)
    return Config(
        machine=machine,
        label=data.get("label", machine),
        port=int(data.get("port", 7777)),
        claude_bin=data.get("claude_bin") or _default_claude_bin(),
        peers=peers,
        token=data.get("token"),
        url=(data.get("url") or "").rstrip("/") or None,
        keep_awake=str(data.get("keep_awake", "off")),
    )
