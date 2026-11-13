"""A peer running an older Fleet: clear error messages and a dashboard hint."""

from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from fleetd import API_LEVEL
from fleetd.config import Peer
from fleetd.peers import is_outdated
from tests.conftest import AUTH, SID, FakeClaude

MakeClient = Callable[..., TestClient]
WRITE = {**AUTH, "X-Fleet-Request": "1"}
WORK = Peer(name="work", url="https://work.ts.net", label="💼 Công ty")
OLD_HINT = ("💼 Công ty đang chạy Fleet bản cũ, chưa có chức năng này. "
            "Cập nhật Fleet trên máy đó (hoặc bấm 'Cập nhật tất cả máy').")


def old_fleet(req: httpx.Request) -> httpx.Response:
    """What an older fleetd answers for an endpoint it does not have (FastAPI default)."""
    return httpx.Response(404, json={"detail": "Not Found"})


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        (404, "Not Found", True),
        (405, "Method Not Allowed", True),
        (404, "transcript not found", False),  # our own 404: a real answer
        (404, "Máy này chưa có thư mục X", False),
        (500, "Not Found", False),
    ],
)
def test_is_outdated_only_for_framework_route_missing(status: int, detail: str, expected: bool) -> None:
    r = httpx.Response(status, json={"detail": detail})

    result = is_outdated(r)

    assert result is expected


def test_handoff_to_old_peer_explains_update(claude_home: object, make_client: MakeClient) -> None:
    c = make_client(fake=FakeClaude(claude_home, agents=[]), peers=(WORK,), peer_handler=old_fleet)

    r = c.post(
        "/api/handoff",
        headers=WRITE,
        json={"source": "home", "target": "work", "sessionId": SID, "dryRun": True},
    )

    assert (r.status_code, r.json()["detail"]) == (404, OLD_HINT)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/m/work/sessions/w1/stop", None),
        ("POST", "/api/m/work/awake", {"mode": "ac"}),
        ("GET", f"/api/m/work/sessions/{SID}/transcript", None),
    ],
)
def test_proxied_calls_to_old_peer_explain_update(
    make_client: MakeClient, method: str, path: str, body: dict | None
) -> None:
    c = make_client(peers=(WORK,), peer_handler=old_fleet)

    r = c.request(method, path, headers=WRITE, json=body)

    assert r.json() == {"detail": OLD_HINT}


def test_peer_own_404_is_passed_through_unchanged(make_client: MakeClient) -> None:
    c = make_client(
        peers=(WORK,), peer_handler=lambda r: httpx.Response(404, json={"detail": "transcript not found"})
    )

    r = c.get(f"/api/m/work/sessions/{SID}/transcript", headers=AUTH)

    assert (r.status_code, r.json()) == (404, {"detail": "transcript not found"})


def test_snapshot_reports_api_level(make_client: MakeClient) -> None:
    c = make_client()

    snap = c.get("/api/local", headers=AUTH).json()

    assert snap["apiLevel"] == API_LEVEL


def test_peer_snapshot_without_api_level_counts_as_zero(make_client: MakeClient) -> None:
    old_snapshot = {"machine": "work", "sessions": [], "history": [], "generatedAt": 1.0}
    c = make_client(peers=(WORK,), peer_handler=lambda r: httpx.Response(200, json=old_snapshot))

    work = c.get("/api/fleet", headers=AUTH).json()["machines"][1]

    assert (work["online"], work["apiLevel"]) == (True, 0)
