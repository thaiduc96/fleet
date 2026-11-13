"""Talk to the other Macs' fleetd. Keeps the last good snapshot per peer so an
offline machine still shows what it was doing."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from fleetd.auth import WRITE_HEADER
from fleetd.config import Peer
from fleetd.models import MachineSnapshot, PeerInfo
from fleetd.state import StateFile

log = logging.getLogger(__name__)
WRITE_TIMEOUT = 75.0
UPLOAD_TIMEOUT = 120.0  # 25 MB over a slow tailnet hop
# FastAPI's own replies for a route the peer does not have at all.
ROUTE_MISSING = {(404, "Not Found"), (405, "Method Not Allowed")}


def response_detail(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:300]
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail) if detail is not None else r.text[:300]


def is_outdated(r: httpx.Response) -> bool:
    """The peer answered, but lacks the endpoint: it runs an older Fleet."""
    return (r.status_code, response_detail(r)) in ROUTE_MISSING


def outdated_message(label: str) -> str:
    return (f"{label} đang chạy Fleet bản cũ, chưa có chức năng này. "
            "Cập nhật Fleet trên máy đó (hoặc bấm 'Cập nhật tất cả máy').")


MAX_PEERS = 32


def norm_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/").lower()


def tailnet_of(url: str | None) -> str | None:
    """'https://home.tail1234.ts.net' -> 'tail1234.ts.net' (None if not a MagicDNS name)."""
    host = (urlsplit(url or "").hostname or "").lower()
    labels = host.split(".")
    if len(labels) >= 4 and host.endswith(".ts.net"):
        return ".".join(labels[1:])
    return None


def safe_peer_url(url: str | None, own_url: str | None) -> bool:
    """Where the shared token may be sent for peers learned at runtime.

    Only HTTPS MagicDNS names (*.ts.net), never IPs or localhost, and — once this
    machine knows its own tailnet — only hosts in that same tailnet. Peers written
    by hand in config.toml are trusted as-is (that is the owner's decision).
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host.endswith(".ts.net") or parts.path not in ("", "/"):
        return False
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    mine = tailnet_of(own_url)
    return mine is None or tailnet_of(url) == mine


class PeerRegistry:
    """Known machines = static ones from config.toml + dynamic ones in state.json.

    Dynamic peers come from invite-code joins and from gossip (every snapshot
    lists the machines its sender knows). A removed machine is remembered as a
    tombstone so gossip does not bring it back; only an explicit join clears it.
    """

    def __init__(self, static: tuple[Peer, ...], state: StateFile, self_name: str,
                 own_url: Callable[[], str | None] = lambda: None) -> None:
        self.static = static
        self.state = state
        self.self_name = self_name
        self.own_url = own_url

    def _dynamic(self) -> list[PeerInfo]:
        raw = self.state.get("peers", [])
        out: list[PeerInfo] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                info = PeerInfo.model_validate(item)
            except ValidationError:
                continue
            if safe_peer_url(info.url, self.own_url()):
                out.append(info)
        return out

    def _removed(self) -> set[str]:
        raw = self.state.get("removedPeers", [])
        return {str(x) for x in raw} if isinstance(raw, list) else set()

    def _write(self, dynamic: list[PeerInfo], removed: set[str]) -> None:
        self.state.update(peers=[d.model_dump(by_alias=True) for d in dynamic[:MAX_PEERS]],
                          removedPeers=sorted(removed))

    def all(self) -> dict[str, Peer]:
        removed = self._removed()
        peers = {p.name: p for p in self.static if p.name not in removed}
        urls = {norm_url(p.url) for p in peers.values()} | {norm_url(self.own_url())}
        for d in self._dynamic():
            if d.machine in removed or d.machine == self.self_name or d.machine in peers:
                continue
            if norm_url(d.url) in urls:  # same Mac known under another name (config.toml)
                continue
            urls.add(norm_url(d.url))
            peers[d.machine] = Peer(name=d.machine, url=(d.url or "").rstrip("/"), label=d.label or d.machine)
        return peers

    def infos(self) -> list[PeerInfo]:
        return [PeerInfo(machine=p.name, label=p.label, url=p.url) for p in self.all().values()]

    def add(self, info: PeerInfo) -> bool:
        """Explicit join: add or update a machine and clear its tombstone."""
        if info.machine == self.self_name or not safe_peer_url(info.url, self.own_url()):
            log.warning("refusing peer %s with url %r", info.machine, info.url)
            return False
        with self.state.lock:
            dynamic = [d for d in self._dynamic() if d.machine != info.machine]
            self._write([*dynamic, info], self._removed() - {info.machine})
        log.info("peer %s added (%s)", info.machine, info.url)
        return True

    def remove(self, machine: str) -> None:
        with self.state.lock:
            dynamic = [d for d in self._dynamic() if d.machine != machine]
            self._write(dynamic, self._removed() | {machine})
        log.info("peer %s removed", machine)

    def learn(self, infos: list[PeerInfo]) -> list[str]:
        """Gossip: add machines never heard of. Never clears a tombstone."""
        added: list[str] = []
        with self.state.lock:  # check-and-add must not interleave with remove()
            known = self.all()
            known_urls = {norm_url(p.url) for p in known.values()} | {norm_url(self.own_url())}
            skip = set(known) | self._removed() | {self.self_name}
            dynamic = self._dynamic()
            for i in infos[:MAX_PEERS]:
                if i.machine in skip or norm_url(i.url) in known_urls:
                    continue
                if not safe_peer_url(i.url, self.own_url()) or len(dynamic) >= MAX_PEERS:
                    continue
                dynamic.append(i)
                skip.add(i.machine)
                known_urls.add(norm_url(i.url))
                added.append(i.machine)
            if added:
                self._write(dynamic, self._removed())
        return added

    def refresh_url(self, machine: str, url: str | None) -> bool:
        """A peer reports its own (new) URL: follow it, if it is a dynamic peer."""
        if not url or not safe_peer_url(url, self.own_url()):
            return False
        with self.state.lock:
            dynamic = self._dynamic()
            for idx, d in enumerate(dynamic):
                if d.machine == machine and norm_url(d.url) != norm_url(url):
                    dynamic[idx] = d.model_copy(update={"url": url.rstrip("/")})
                    self._write(dynamic, self._removed())
                    log.info("peer %s moved to %s", machine, url)
                    return True
        return False


class PeerClient:
    def __init__(self, registry: PeerRegistry, token: str, cache_dir: Path,
                 client: httpx.AsyncClient | None = None, timeout: float = 4.0) -> None:
        self.registry = registry
        self.cache_dir: Path = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client: httpx.AsyncClient = client or httpx.AsyncClient(timeout=timeout)
        self.client.headers["Authorization"] = f"Bearer {token}"

    @property
    def peers(self) -> dict[str, Peer]:
        return self.registry.all()

    async def __aenter__(self) -> PeerClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.client.aclose()

    def _cache_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.json"

    def _read_cache(self, peer: Peer) -> MachineSnapshot:
        try:
            return MachineSnapshot.model_validate_json(self._cache_path(peer.name).read_text())
        except (OSError, ValidationError):
            return MachineSnapshot(machine=peer.name)

    def _write_cache(self, peer: Peer, snap: MachineSnapshot) -> None:
        tmp = self._cache_path(peer.name).with_suffix(".tmp")
        try:
            tmp.write_text(snap.model_dump_json())
            tmp.replace(self._cache_path(peer.name))
        except OSError as e:
            log.warning("cannot cache snapshot for %s: %s", peer.name, e)

    def _offline(self, peer: Peer, error: str) -> MachineSnapshot:
        snap = self._read_cache(peer)
        return snap.model_copy(update={
            "label": peer.label or snap.label, "online": False, "is_self": False,
            "error": error, "last_seen": snap.generated_at,
        })

    async def snapshot(self, peer: Peer) -> MachineSnapshot:
        try:
            r = await self.client.get(f"{peer.url}/api/local")
            r.raise_for_status()
            snap = MachineSnapshot.model_validate_json(r.content)
        except (httpx.HTTPError, ValidationError) as e:
            log.info("peer %s unreachable: %s", peer.name, e)
            return self._offline(peer, f"{type(e).__name__}: {e}"[:300])
        snap = snap.model_copy(update={
            "label": peer.label or snap.label, "online": True, "is_self": False,
            "fetched_at": time.time(),
        })
        await asyncio.to_thread(self._write_cache, peer, snap)
        if snap.machine == peer.name:
            await asyncio.to_thread(self.registry.refresh_url, peer.name, snap.url)
        learned = await asyncio.to_thread(self.registry.learn, snap.peers)
        if learned:
            log.info("learned peers %s from %s", learned, peer.name)
        return snap

    async def all(self) -> list[MachineSnapshot]:
        return list(await asyncio.gather(*(self.snapshot(p) for p in self.peers.values())))

    def label(self, machine: str) -> str:
        peer = self.peers.get(machine)
        return (peer.label or peer.name) if peer else machine

    def _peer(self, machine: str) -> Peer:
        peer = self.peers.get(machine)
        if peer is None:
            raise PeerGoneError(machine)
        return peer

    async def get(self, machine: str, path: str) -> httpx.Response:
        return await self.client.get(f"{self._peer(machine).url}{path}")

    async def request(self, machine: str, method: str, path: str,
                      json_body: dict[str, object] | None = None) -> httpx.Response:
        """State-changing call to a peer (stop/resume can take a few seconds)."""
        return await self.client.request(
            method, f"{self._peer(machine).url}{path}", json=json_body,
            headers={WRITE_HEADER: "1"}, timeout=WRITE_TIMEOUT,
        )


    async def upload(self, machine: str, name: str, data: bytes) -> httpx.Response:
        """Send an attachment's bytes to the peer that runs the session."""
        return await self.client.post(
            f"{self._peer(machine).url}/api/uploads", params={"name": name}, content=data,
            headers={WRITE_HEADER: "1", "content-type": "application/octet-stream"},
            timeout=UPLOAD_TIMEOUT,
        )


class PeerGoneError(LookupError):
    """The machine was removed while a request for it was in flight."""
