"""Backend support for Fleet.app: hello, join, gossip, settings, update requests."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fleetd import API_LEVEL
from fleetd.config import Peer
from fleetd.models import PeerInfo
from fleetd.peers import PeerRegistry
from fleetd.state import StateFile
from tests.conftest import AUTH, TOKEN

MakeClient = Callable[..., TestClient]
WRITE = {**AUTH, "X-Fleet-Request": "1"}
WORK = Peer(name="work", url="https://work.ts.net", label="💼 Công ty")


def snapshot_of(machine: str, peers: list[dict[str, object]]) -> dict[str, object]:
    return {"machine": machine, "sessions": [], "history": [], "generatedAt": 1.0, "peers": peers}


@pytest.fixture
def state(tmp_path: Path) -> StateFile:
    return StateFile(tmp_path / "state.json")


# ---------------------------------------------------------------- hello
def test_hello_needs_no_token_and_reveals_no_secret(make_client: MakeClient) -> None:
    c = make_client(app_version="1.2.3")

    r = c.get("/api/hello")

    assert r.json() == {
        "fleet": True,
        "machine": "home",
        "label": "🏠 Nhà",
        "apiLevel": API_LEVEL,
        "appVersion": "1.2.3",
    }


def test_snapshot_reports_app_version_and_peers(make_client: MakeClient) -> None:
    c = make_client(app_version="1.2.3", peers=(WORK,))

    snap = c.get("/api/local", headers=AUTH).json()

    assert (snap["appVersion"], snap["peers"]) == (
        "1.2.3",
        [{"machine": "work", "label": "💼 Công ty", "url": "https://work.ts.net"}],
    )


# ---------------------------------------------------------------- join
def test_join_requires_csrf_header(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/peers/join", headers=AUTH, json={"machine": "lap", "url": "https://lap.ts.net"})

    assert r.status_code == 403


def test_join_requires_token(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post(
        "/api/peers/join",
        headers={"X-Fleet-Request": "1"},
        json={"machine": "lap", "url": "https://lap.ts.net"},
    )

    assert r.status_code == 401


def test_join_adds_machine_and_returns_roster_without_it(make_client: MakeClient) -> None:
    c = make_client(peers=(WORK,))

    r = c.post(
        "/api/peers/join",
        headers=WRITE,
        json={"machine": "lap", "label": "💻 Laptop", "url": "https://lap.ts.net"},
    )

    assert r.json() == {
        "machine": "home",
        "label": "🏠 Nhà",
        "url": None,
        "peers": [{"machine": "work", "label": "💼 Công ty", "url": "https://work.ts.net"}],
    }


def test_joined_machine_is_listed_in_fleet(make_client: MakeClient) -> None:
    c = make_client(peer_handler=lambda r: httpx.Response(200, json=snapshot_of("lap", [])))
    c.post("/api/peers/join", headers=WRITE, json={"machine": "lap", "url": "https://lap.ts.net"})

    machines = [m["machine"] for m in c.get("/api/fleet", headers=AUTH).json()["machines"]]

    assert machines == ["home", "lap"]


@pytest.mark.parametrize(
    "body",
    [
        {"machine": "lap", "url": "file:///etc/passwd"},
        {"machine": "lap", "url": "https://lap.ts.net/../x"},
        {"machine": "../evil", "url": "https://lap.ts.net"},
        {"machine": "", "url": "https://lap.ts.net"},
        {"machine": "lap"},
    ],
)
def test_join_rejects_bad_input(make_client: MakeClient, body: dict[str, str]) -> None:
    c = make_client()

    r = c.post("/api/peers/join", headers=WRITE, json=body)

    assert r.status_code == 422


# ---------------------------------------------------------------- gossip
def test_fleet_learns_machines_known_by_a_peer(make_client: MakeClient) -> None:
    def work(req: httpx.Request) -> httpx.Response:
        if req.url.host == "work.ts.net":
            return httpx.Response(
                200,
                json=snapshot_of(
                    "work",
                    [
                        {"machine": "home", "url": "https://home.ts.net"},  # us: ignored
                        {"machine": "lap", "label": "💻", "url": "https://lap.ts.net"},
                    ],
                ),
            )
        return httpx.Response(200, json=snapshot_of("lap", []))

    c = make_client(peers=(WORK,), peer_handler=work)
    c.get("/api/fleet", headers=AUTH)  # learns "lap" from work

    machines = [m["machine"] for m in c.get("/api/fleet", headers=AUTH).json()["machines"]]

    assert machines == ["home", "work", "lap"]


def test_removed_machine_is_not_relearned_by_gossip(make_client: MakeClient) -> None:
    lap = {"machine": "lap", "url": "https://lap.ts.net"}
    c = make_client(
        peers=(WORK,), peer_handler=lambda r: httpx.Response(200, json=snapshot_of("work", [lap]))
    )
    c.get("/api/fleet", headers=AUTH)
    c.delete("/api/peers/lap", headers=WRITE)
    c.get("/api/fleet", headers=AUTH)  # work still gossips "lap"

    machines = [m["machine"] for m in c.get("/api/fleet", headers=AUTH).json()["machines"]]

    assert machines == ["home", "work"]


def test_explicit_join_revives_a_removed_machine(make_client: MakeClient) -> None:
    c = make_client(peer_handler=lambda r: httpx.Response(200, json=snapshot_of("lap", [])))
    c.post("/api/peers/join", headers=WRITE, json={"machine": "lap", "url": "https://lap.ts.net"})
    c.delete("/api/peers/lap", headers=WRITE)
    c.post("/api/peers/join", headers=WRITE, json={"machine": "lap", "url": "https://lap.ts.net"})

    machines = [m["machine"] for m in c.get("/api/fleet", headers=AUTH).json()["machines"]]

    assert machines == ["home", "lap"]


# ---------------------------------------------------------------- PeerRegistry unit
def test_registry_merges_static_and_dynamic_peers(state: StateFile) -> None:
    reg = PeerRegistry((WORK,), state, "home")
    reg.add(PeerInfo(machine="lap", label="💻", url="https://lap.ts.net/"))

    result = {name: p.url for name, p in reg.all().items()}

    assert result == {"work": "https://work.ts.net", "lap": "https://lap.ts.net"}


def test_registry_never_lists_itself(state: StateFile) -> None:
    reg = PeerRegistry((), state, "home")

    added = reg.add(PeerInfo(machine="home", url="https://home.ts.net"))

    assert (added, reg.all()) == (False, {})


def test_registry_skips_corrupt_entries(state: StateFile) -> None:
    state.update(peers=[{"machine": "ok", "url": "https://ok.ts.net"}, {"machine": "../x"}, "junk"])
    reg = PeerRegistry((), state, "home")

    result = list(reg.all())

    assert result == ["ok"]


def test_registry_add_keeps_other_state_keys(state: StateFile) -> None:
    state.update(keepAwake="ac")
    reg = PeerRegistry((), state, "home")

    reg.add(PeerInfo(machine="lap", url="https://lap.ts.net"))

    assert json.loads(state.path.read_text())["keepAwake"] == "ac"


# ---------------------------------------------------------------- settings
def test_settings_change_label_and_url(make_client: MakeClient) -> None:
    c = make_client()
    c.post("/api/settings", headers=WRITE, json={"label": "🏡 Home", "url": "https://home.ts.net/"})

    snap = c.get("/api/local", headers=AUTH).json()

    assert (snap["label"], snap["url"], c.get("/api/hello").json()["label"]) == (
        "🏡 Home",
        "https://home.ts.net",
        "🏡 Home",
    )


def test_settings_reject_bad_url(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/settings", headers=WRITE, json={"url": "javascript:alert(1)"})

    assert r.status_code == 422


# ---------------------------------------------------------------- update requests
def test_update_writes_request_file_for_the_app(make_client: MakeClient, tmp_path: Path) -> None:
    c = make_client(app_version="1.0.0")

    r = c.post("/api/m/home/update", headers=WRITE)

    assert (r.json(), (tmp_path / "fleet/update-request").exists()) == (
        {"requested": True, "appManaged": True},
        True,
    )


def test_update_without_app_says_not_app_managed(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/update", headers=WRITE)

    assert r.json() == {"requested": True, "appManaged": False}


def test_update_is_forwarded_to_peer(make_client: MakeClient) -> None:
    seen: list[tuple[str, str, str | None]] = []

    def work(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, req.headers.get("authorization")))
        return httpx.Response(200, json={"requested": True, "appManaged": True})

    c = make_client(peers=(WORK,), peer_handler=work)

    r = c.post("/api/m/work/update", headers=WRITE)

    assert (r.json(), seen) == (
        {"requested": True, "appManaged": True},
        [("POST", "/api/update", f"Bearer {TOKEN}")],
    )
