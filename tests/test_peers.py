import json
from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from fleetd.config import Peer
from tests.conftest import AUTH, TOKEN

MakeClient = Callable[..., TestClient]
WORK = Peer(name="work", url="https://work.ts.net", label="💼 Công ty")
PEER_SNAPSHOT = {
    "machine": "work", "label": "work", "generatedAt": 1794580000.0,
    "sessions": [{"id": "w1", "name": "deploy", "state": "working", "live": True}], "history": [],
}


def peer_ok(request: httpx.Request) -> httpx.Response:
    if request.headers.get("authorization") != f"Bearer {TOKEN}":
        return httpx.Response(401)
    if request.url.path == "/api/local":
        return httpx.Response(200, json=PEER_SNAPSHOT)
    return httpx.Response(200, json={"id": "w1", "logs": "from work"})


def write_cache(tmp_path: Path) -> None:
    cache = tmp_path / "fleet/cache"
    cache.mkdir(parents=True)
    (cache / "work.json").write_text(json.dumps(PEER_SNAPSHOT))


def test_fleet_includes_online_peer_with_its_label(make_client: MakeClient) -> None:
    c = make_client(peers=(WORK,), peer_handler=peer_ok)

    work = c.get("/api/fleet", headers=AUTH).json()["machines"][1]

    assert (work["online"], work["label"], work["sessions"][0]["name"]) == (True, "💼 Công ty", "deploy")


def test_fleet_caches_peer_snapshot_to_disk(make_client: MakeClient, tmp_path: Path) -> None:
    c = make_client(peers=(WORK,), peer_handler=peer_ok)

    c.get("/api/fleet", headers=AUTH)

    assert json.loads((tmp_path / "fleet/cache/work.json").read_text())["machine"] == "work"


def test_fleet_shows_cached_snapshot_when_peer_offline(make_client: MakeClient, tmp_path: Path) -> None:
    write_cache(tmp_path)
    c = make_client(peers=(WORK,))

    work = c.get("/api/fleet", headers=AUTH).json()["machines"][1]

    assert (work["online"], work["lastSeen"], work["sessions"][0]["name"]) == (False, 1794580000.0, "deploy")


def test_fleet_shows_empty_offline_peer_without_cache(make_client: MakeClient) -> None:
    c = make_client(peers=(WORK,))

    work = c.get("/api/fleet", headers=AUTH).json()["machines"][1]

    assert (work["online"], work["sessions"], work["lastSeen"]) == (False, [], None)


def test_peer_rejecting_token_is_shown_offline(make_client: MakeClient) -> None:
    c = make_client(peers=(WORK,), peer_handler=lambda r: httpx.Response(401))

    work = c.get("/api/fleet", headers=AUTH).json()["machines"][1]

    assert (work["online"], "401" in work["error"]) == (False, True)


def test_machine_route_proxies_logs_to_peer(make_client: MakeClient) -> None:
    c = make_client(peers=(WORK,), peer_handler=peer_ok)

    r = c.get("/api/m/work/sessions/w1/logs", headers=AUTH)

    assert r.json() == {"id": "w1", "logs": "from work"}


def test_machine_route_returns_504_when_peer_unreachable(make_client: MakeClient) -> None:
    c = make_client(peers=(WORK,))

    r = c.get("/api/m/work/sessions/w1/logs", headers=AUTH)

    assert r.status_code == 504
