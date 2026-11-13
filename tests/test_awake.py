"""Phase 3: keep the Mac awake (caffeinate child managed by fleetd)."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fleetd.awake import KeepAwake
from fleetd.config import Peer
from fleetd.models import Awake, AwakeMode
from fleetd.state import StateFile
from tests.conftest import AUTH, TOKEN, FakeProcs

MakeClient = Callable[..., TestClient]
WRITE = {**AUTH, "X-Fleet-Request": "1"}
PID = 4242


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


# ---------------------------------------------------------------- KeepAwake unit
@pytest.mark.parametrize(("mode", "argv"), [
    (AwakeMode.AC, ["/usr/bin/caffeinate", "-s", "-w", "4242"]),
    (AwakeMode.ALWAYS, ["/usr/bin/caffeinate", "-i", "-s", "-w", "4242"]),
])
def test_start_spawns_caffeinate_tied_to_fleetd_pid(state_file: Path, mode: AwakeMode,
                                                    argv: list[str]) -> None:
    procs = FakeProcs()
    keep = KeepAwake(StateFile(state_file), mode, spawn=procs, pid=PID)

    keep.start()

    assert procs.running == [argv]


def test_start_in_off_mode_spawns_nothing(state_file: Path) -> None:
    procs = FakeProcs()
    keep = KeepAwake(StateFile(state_file), AwakeMode.OFF, spawn=procs, pid=PID)

    keep.start()

    assert procs.spawned == []


def test_set_mode_off_terminates_caffeinate(state_file: Path) -> None:
    procs = FakeProcs()
    keep = KeepAwake(StateFile(state_file), AwakeMode.AC, spawn=procs, pid=PID)
    keep.start()

    result = keep.set_mode(AwakeMode.OFF)

    assert (result, procs.running) == (Awake(mode=AwakeMode.OFF, active=False), [])


def test_switching_mode_replaces_caffeinate_flags(state_file: Path) -> None:
    procs = FakeProcs()
    keep = KeepAwake(StateFile(state_file), AwakeMode.AC, spawn=procs, pid=PID)
    keep.start()

    keep.set_mode(AwakeMode.ALWAYS)

    assert procs.running == [["/usr/bin/caffeinate", "-i", "-s", "-w", "4242"]]


def test_set_mode_persists_across_restart(state_file: Path) -> None:
    KeepAwake(StateFile(state_file), AwakeMode.OFF, spawn=FakeProcs(), pid=PID).set_mode(AwakeMode.AC)

    reloaded = KeepAwake(StateFile(state_file), AwakeMode.OFF, spawn=FakeProcs(), pid=PID)

    assert reloaded.status().mode is AwakeMode.AC


def test_state_file_keeps_other_keys(state_file: Path) -> None:
    state_file.write_text(json.dumps({"other": 1}))
    keep = KeepAwake(StateFile(state_file), AwakeMode.OFF, spawn=FakeProcs(), pid=PID)

    keep.set_mode(AwakeMode.ALWAYS)

    assert json.loads(state_file.read_text()) == {"other": 1, "keepAwake": "always"}


def test_corrupt_state_file_falls_back_to_default(state_file: Path) -> None:
    state_file.write_text("{not json")

    keep = KeepAwake(StateFile(state_file), AwakeMode.AC, spawn=FakeProcs(), pid=PID)

    assert keep.status().mode is AwakeMode.AC


def test_status_restarts_caffeinate_that_was_killed(state_file: Path) -> None:
    procs = FakeProcs()
    keep = KeepAwake(StateFile(state_file), AwakeMode.AC, spawn=procs, pid=PID)
    keep.start()
    procs.spawned[0].returncode = -9  # someone ran `killall caffeinate`

    result = keep.status()

    assert (result.active, len(procs.spawned), len(procs.running)) == (True, 2, 1)


def test_spawn_failure_reports_inactive(state_file: Path) -> None:
    def broken(argv: list[str]) -> FakeProcs:
        raise OSError("no caffeinate")

    keep = KeepAwake(StateFile(state_file), AwakeMode.AC, spawn=broken, pid=PID)

    result = keep.status()

    assert result == Awake(mode=AwakeMode.AC, active=False)


# ---------------------------------------------------------------- API
def test_health_reports_keep_awake_from_config(make_client: MakeClient) -> None:
    c = make_client(keep_awake="ac")

    awake = c.get("/api/health", headers=AUTH).json()["awake"]

    assert awake == {"mode": "ac", "active": True}


def test_fleetd_shutdown_stops_caffeinate(make_client: MakeClient) -> None:
    procs = FakeProcs()
    c = make_client(keep_awake="ac", procs=procs)

    c.__exit__(None, None, None)

    assert procs.running == []


def test_unknown_keep_awake_in_config_means_off(make_client: MakeClient) -> None:
    procs = FakeProcs()
    c = make_client(keep_awake="forever", procs=procs)

    awake = c.get("/api/health", headers=AUTH).json()["awake"]

    assert (awake, procs.spawned) == ({"mode": "off", "active": False}, [])


def test_post_awake_changes_mode(make_client: MakeClient) -> None:
    procs = FakeProcs()
    c = make_client(procs=procs)

    r = c.post("/api/m/home/awake", headers=WRITE, json={"mode": "always"})

    assert (r.json(), len(procs.running)) == ({"mode": "always", "active": True}, 1)


def test_post_awake_requires_csrf_header(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/awake", headers=AUTH, json={"mode": "ac"})

    assert r.status_code == 403


def test_post_awake_rejects_unknown_mode(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/awake", headers=WRITE, json={"mode": "forever"})

    assert r.status_code == 422


def test_post_awake_on_peer_is_forwarded(make_client: MakeClient) -> None:
    seen: list[tuple[str, str | None, str | None, bytes]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.headers.get("authorization"), req.headers.get("x-fleet-request"),
                     req.content))
        return httpx.Response(200, json={"mode": "ac", "active": True})

    c = make_client(peers=(Peer(name="work", url="https://work.ts.net", label="W"),), peer_handler=handler)

    r = c.post("/api/m/work/awake", headers=WRITE, json={"mode": "ac"})

    assert (r.json(), seen) == ({"mode": "ac", "active": True},
                                [("/api/awake", f"Bearer {TOKEN}", "1", b'{"mode":"ac"}')])
