"""Peer registry safety: where the shared token may go, duplicates, URL moves, settings, removal."""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fleetd.config import Peer
from fleetd.models import PeerInfo
from fleetd.peers import PeerRegistry, safe_peer_url
from fleetd.state import StateFile
from tests.conftest import AUTH

MakeClient = Callable[..., TestClient]
WRITE = {**AUTH, "X-Fleet-Request": "1"}
WORK = Peer(name="work", url="https://work.ts.net", label="💼 Công ty")


def snapshot_of(machine: str, peers: list[dict[str, object]], url: str | None = None) -> dict[str, object]:
    return {"machine": machine, "sessions": [], "history": [], "generatedAt": 1.0, "peers": peers, "url": url}


@pytest.fixture
def state(tmp_path: Path) -> StateFile:
    return StateFile(tmp_path / "state.json")


# ---------------------------------------------------------------- where the token may be sent
@pytest.mark.parametrize(
    ("url", "own", "ok"),
    [
        ("https://lap.tail1.ts.net", None, True),
        ("https://lap.tail1.ts.net", "https://home.tail1.ts.net", True),
        ("https://lap.tail2.ts.net", "https://home.tail1.ts.net", False),  # another tailnet
        ("http://lap.tail1.ts.net", None, False),  # cleartext
        ("https://attacker.example", None, False),
        ("https://127.0.0.1", None, False),
        ("https://localhost", None, False),
        ("https://100.64.0.1", None, False),
        ("https://lap.tail1.ts.net/x", None, False),
        ("https://user:pw@lap.tail1.ts.net", None, False),
        (None, None, False),
    ],
)
def test_safe_peer_url(url: str | None, own: str | None, ok: bool) -> None:
    result = safe_peer_url(url, own)

    assert result is ok


def test_join_with_unsafe_url_is_refused_with_reason(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/peers/join", headers=WRITE, json={"machine": "lap", "url": "http://lap.ts.net"})

    assert (r.status_code, "https://" in r.json()["detail"]) == (400, True)


def test_join_with_own_name_is_refused(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/peers/join", headers=WRITE, json={"machine": "home", "url": "https://x.t.ts.net"})

    assert r.status_code == 409


def test_gossip_never_learns_unsafe_urls(make_client: MakeClient) -> None:
    bad = [{"machine": "evil", "url": "http://evil.example"}, {"machine": "ip", "url": "https://10.0.0.1"}]
    c = make_client(peers=(WORK,), peer_handler=lambda r: httpx.Response(200, json=snapshot_of("work", bad)))
    c.get("/api/fleet", headers=AUTH)

    machines = [m["machine"] for m in c.get("/api/fleet", headers=AUTH).json()["machines"]]

    assert machines == ["home", "work"]


def test_unsafe_entry_already_in_state_is_ignored(state: StateFile) -> None:
    state.update(peers=[{"machine": "evil", "url": "http://evil.example"}])
    reg = PeerRegistry((), state, "home")

    result = reg.all()

    assert result == {}


# ---------------------------------------------------------------- duplicates and limits
def test_gossip_skips_a_url_already_known_under_another_name(state: StateFile) -> None:
    office = Peer(name="office", url="https://work.t.ts.net", label="Office")
    reg = PeerRegistry((office,), state, "home")

    added = reg.learn([PeerInfo(machine="work", url="https://work.t.ts.net")])

    assert (added, list(reg.all())) == ([], ["office"])


def test_gossip_skips_our_own_url_under_another_name(state: StateFile) -> None:
    reg = PeerRegistry((), state, "home", own_url=lambda: "https://home.t.ts.net")

    added = reg.learn([PeerInfo(machine="nha", url="https://home.t.ts.net/")])

    assert added == []


def test_gossip_is_capped(state: StateFile) -> None:
    reg = PeerRegistry((), state, "home")

    reg.learn([PeerInfo(machine=f"m{i}", url=f"https://m{i}.t.ts.net") for i in range(100)])

    assert len(reg.all()) == 32


def test_gossip_does_not_clear_a_tombstone(state: StateFile) -> None:
    reg = PeerRegistry((), state, "home")
    reg.add(PeerInfo(machine="lap", url="https://lap.t.ts.net"))
    reg.remove("lap")

    added = reg.learn([PeerInfo(machine="lap", url="https://lap.t.ts.net")])

    assert (added, state.get("removedPeers")) == ([], ["lap"])


# ---------------------------------------------------------------- URL moves
def test_peer_reporting_a_new_url_is_followed(state: StateFile) -> None:
    reg = PeerRegistry((), state, "home")
    reg.add(PeerInfo(machine="lap", url="https://old.t.ts.net"))

    reg.refresh_url("lap", "https://new.t.ts.net")

    assert reg.all()["lap"].url == "https://new.t.ts.net"


def test_fleet_follows_url_reported_by_the_peer_itself(make_client: MakeClient) -> None:
    c = make_client(
        peer_handler=lambda r: httpx.Response(200, json=snapshot_of("lap", [], "https://lap2.t.ts.net"))
    )
    c.post("/api/peers/join", headers=WRITE, json={"machine": "lap", "url": "https://lap.t.ts.net"})
    c.get("/api/fleet", headers=AUTH)

    peers = c.get("/api/local", headers=AUTH).json()["peers"]

    assert peers == [{"machine": "lap", "label": "lap", "url": "https://lap2.t.ts.net"}]


# ---------------------------------------------------------------- settings and removal
def test_settings_with_only_label_keeps_url(make_client: MakeClient) -> None:
    c = make_client()
    c.post("/api/settings", headers=WRITE, json={"url": "https://home.t.ts.net"})
    c.post("/api/settings", headers=WRITE, json={"label": "🏡"})

    snap = c.get("/api/local", headers=AUTH).json()

    assert (snap["label"], snap["url"]) == ("🏡", "https://home.t.ts.net")


def test_remove_everywhere_tells_the_other_machines(make_client: MakeClient) -> None:
    seen: list[tuple[str, str]] = []

    def work(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        return httpx.Response(200, json={"ok": True})

    c = make_client(peers=(WORK,), peer_handler=work)
    c.post("/api/peers/join", headers=WRITE, json={"machine": "lap", "url": "https://lap.t.ts.net"})

    r = c.delete("/api/peers/lap?everywhere=true", headers=WRITE)

    assert (r.json()["alsoRemovedOn"], ("DELETE", "/api/peers/lap") in seen) == (["work"], True)


def test_request_to_machine_removed_mid_flight_is_404(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/m/ghost/sessions/x1/stop", headers=WRITE)

    assert r.status_code == 404
