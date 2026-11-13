"""HTTP routes that drive a session's terminal, locally and through a peer."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fleetd.attach import Attacher
from fleetd.config import Peer
from tests.conftest import AUTH, TOKEN, agent
from tests.test_attach import IDLE_AUTO, PLAN, FakeTty

MakeClient = Callable[..., TestClient]
WRITE = {**AUTH, "X-Fleet-Request": "1"}
WORK = Peer(name="work", url="https://work.ts.net", label="💼 Công ty")
FIXTURES = Path(__file__).parent / "fixtures" / "screens"


def client_with(make_client: MakeClient, tty: FakeTty, **kw: object) -> TestClient:
    return make_client(attacher=Attacher(lambda short_id: tty), **kw)


def test_screen_of_a_live_background_session(make_client: MakeClient) -> None:
    c = client_with(make_client, FakeTty(PLAN))

    body = c.get("/api/m/home/sessions/558c6df2/screen", headers=AUTH).json()

    assert (body["id"], body["prompt"]["kind"], len(body["prompt"]["options"])) == ("558c6df2", "plan", 3)


def test_choosing_an_option_answers_the_menu(make_client: MakeClient) -> None:
    tty = FakeTty(PLAN, {"1": b"\x1b[2J\x1b[H" + IDLE_AUTO})
    c = client_with(make_client, tty)

    choose = {"action": "choose", "option": "1", "title": PLAN_TITLE, "label": "Yes, and use auto mode"}

    r = c.post("/api/m/home/sessions/558c6df2/input", headers=WRITE, json={"input": choose})

    assert (r.status_code, r.json()["prompt"], tty.written) == (200, None, ["1"])


@pytest.mark.parametrize("payload", [
    {"action": "choose", "option": "0"},
    {"action": "choose", "option": "12"},
    {"action": "key", "key": "enter"},
    {"action": "mode", "mode": "bypassPermissions"},
    {"action": "type", "text": ""},
    {"action": "shell", "text": "rm -rf ~"},
])
def test_inputs_outside_the_allowed_set_are_rejected(
        make_client: MakeClient, payload: dict[str, object]) -> None:
    tty = FakeTty(PLAN)
    c = client_with(make_client, tty)

    r = c.post("/api/m/home/sessions/558c6df2/input", headers=WRITE, json={"input": payload})

    assert (r.status_code, tty.written) == (422, [])


def test_input_needs_the_csrf_header(make_client: MakeClient) -> None:
    c = client_with(make_client, FakeTty(PLAN))

    r = c.post("/api/sessions/558c6df2/input", headers=AUTH, json={"input": {"action": "key", "key": "esc"}})

    assert r.status_code == 403


@pytest.mark.parametrize(("entry", "why"), [
    (agent(pid=None), "không chạy"),
    (agent(kind="interactive"), "terminal"),
])
def test_only_running_background_sessions_can_be_driven(
        make_client: MakeClient, entry: dict[str, object], why: str) -> None:
    tty = FakeTty(PLAN)
    c = client_with(make_client, tty, agents=[entry])

    r = c.get("/api/sessions/558c6df2/screen", headers=AUTH)

    assert (r.status_code, why in r.json()["detail"], tty.closed) == (409, True, False)


def test_menu_errors_come_back_as_http_errors(make_client: MakeClient) -> None:
    c = client_with(make_client, FakeTty(IDLE_AUTO))
    choose = {"input": {"action": "choose", "option": "1", "title": PLAN_TITLE,
                        "label": "Yes, and use auto mode"}}

    r = c.post("/api/sessions/558c6df2/input", headers=WRITE, json=choose)

    assert r.status_code == 409


def test_screen_and_input_on_a_peer_are_forwarded(make_client: MakeClient) -> None:
    seen: list[tuple[str, str, str | None, object]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        seen.append((req.method, req.url.path, req.headers.get("authorization"), body))
        return httpx.Response(200, json={"id": "w1", "text": "", "mode": "auto"})

    c = make_client(peers=(WORK,), peer_handler=handler)

    c.get("/api/m/work/sessions/w1/screen", headers=AUTH)
    c.post("/api/m/work/sessions/w1/input", headers=WRITE, json={"input": {"action": "mode", "mode": "plan"}})

    assert seen == [
        ("GET", "/api/sessions/w1/screen", f"Bearer {TOKEN}", None),
        ("POST", "/api/sessions/w1/input", f"Bearer {TOKEN}", {"input": {"action": "mode", "mode": "plan"}}),
    ]


PLAN_TITLE = "Claude has written up a plan and is ready to execute. Would you like to proceed?"


def test_mode_is_only_switched_while_claude_is_idle(make_client: MakeClient) -> None:
    tty = FakeTty(IDLE_AUTO)
    c = client_with(make_client, tty, agents=[agent(state="working", status="busy")])

    mode = {"input": {"action": "mode", "mode": "plan"}}

    r = c.post("/api/sessions/558c6df2/input", headers=WRITE, json=mode)

    assert (r.status_code, tty.written, tty.closed) == (409, [], False)


def test_choose_must_name_the_menu_it_answers(make_client: MakeClient) -> None:
    c = client_with(make_client, FakeTty(PLAN))

    unnamed = {"input": {"action": "choose", "option": "1"}}

    r = c.post("/api/sessions/558c6df2/input", headers=WRITE, json=unnamed)

    assert r.status_code == 422
