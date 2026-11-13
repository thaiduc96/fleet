from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from tests.conftest import AUTH, HIST_SID, SID, TOKEN, agent

MakeClient = Callable[..., TestClient]


@pytest.mark.parametrize("path", [
    "/api/local", "/api/fleet", "/api/health",
    f"/api/sessions/{SID}/transcript", "/api/sessions/558c6df2/logs",
    f"/api/m/home/sessions/{SID}/transcript",
])
def test_returns_401_without_token(make_client: MakeClient, path: str) -> None:
    c = make_client()

    r = c.get(path)

    assert r.status_code == 401


def test_returns_401_with_wrong_token(make_client: MakeClient) -> None:
    c = make_client()

    r = c.get("/api/local", headers={"Authorization": "Bearer nope"})

    assert r.status_code == 401


def test_dashboard_page_is_public(make_client: MakeClient) -> None:
    c = make_client()

    r = c.get("/")

    assert (r.status_code, "Fleet" in r.text) == (200, True)


def test_login_with_wrong_token_returns_401(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/login", json={"token": "nope"})

    assert r.status_code == 401


def test_login_sets_httponly_cookie(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/login", json={"token": TOKEN})

    assert "httponly" in r.headers["set-cookie"].lower()


def test_cookie_from_login_authorizes_api(make_client: MakeClient) -> None:
    c = make_client()
    c.post("/api/login", json={"token": TOKEN})

    r = c.get("/api/local")

    assert r.status_code == 200


def test_local_merges_agent_job_state_and_transcript(make_client: MakeClient) -> None:
    c = make_client()

    s = c.get("/api/local", headers=AUTH).json()["sessions"][0]

    assert {k: s[k] for k in ("id", "state", "detail", "result", "lastReply", "costUSD", "live")} == {
        "id": "558c6df2", "state": "done", "detail": "Đã xoá worktree", "result": "Xong rồi",
        "lastReply": "Chào bạn", "costUSD": 0.42, "live": True,
    }


def test_local_names_untitled_interactive_session_by_id(make_client: MakeClient) -> None:
    c = make_client(agents=[agent(id="aaaa1111", kind="interactive", sessionId=None,
                                  name=None, state="blocked")])

    s = c.get("/api/local", headers=AUTH).json()["sessions"][0]

    assert (s["name"], s["state"]) == ("aaaa1111", "needs_input")


def test_local_history_excludes_live_sessions(make_client: MakeClient) -> None:
    c = make_client()

    history = c.get("/api/local", headers=AUTH).json()["history"]

    assert [(h["sessionId"], h["state"]) for h in history] == [(HIST_SID, "ended")]


def test_local_reports_cli_failure_instead_of_crashing(make_client: MakeClient) -> None:
    c = make_client(error="boom")

    snap = c.get("/api/local", headers=AUTH).json()

    assert (snap["sessions"], snap["error"]) == ([], "boom")


def test_transcript_route_returns_newest_messages(make_client: MakeClient) -> None:
    c = make_client()

    t = c.get(f"/api/m/home/sessions/{SID}/transcript?limit=2", headers=AUTH).json()

    assert [m["role"] for m in t["messages"]] == ["assistant", "tool"]


def test_logs_route_returns_clean_terminal_text(make_client: MakeClient) -> None:
    c = make_client()

    r = c.get("/api/m/home/sessions/558c6df2/logs", headers=AUTH)

    assert r.json() == {"id": "558c6df2", "logs": "hello\n\nworld"}


def test_returns_404_when_transcript_does_not_exist(make_client: MakeClient) -> None:
    c = make_client()

    r = c.get("/api/sessions/00000000-0000-0000-0000-000000000000/transcript", headers=AUTH)

    assert r.status_code == 404


@pytest.mark.parametrize("path", [
    "/api/m/home/sessions/558c6df2/other",
    "/api/m/nowhere/sessions/558c6df2/logs",
    "/api/m/home/sessions/..%2F..%2Fetc/logs",
])
def test_machine_route_returns_404_for_unknown_target(make_client: MakeClient, path: str) -> None:
    c = make_client()

    r = c.get(path, headers=AUTH)

    assert r.status_code == 404


def test_logs_returns_502_when_cli_fails(make_client: MakeClient) -> None:
    c = make_client(error="no such session")

    r = c.get("/api/sessions/558c6df2/logs", headers=AUTH)

    assert r.status_code == 502


def test_fleet_with_no_peers_lists_only_this_machine(make_client: MakeClient) -> None:
    c = make_client()

    d = c.get("/api/fleet", headers=AUTH).json()

    assert (d["selfMachine"], [m["machine"] for m in d["machines"]], d["machines"][0]["isSelf"]) == (
        "home", ["home"], True)
