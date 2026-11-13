"""Phase 2: stop / respawn / rm / start / message, locally and via a peer."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fleetd.config import Peer
from tests.conftest import AUTH, HIST_SID, SID, TOKEN, FakeClaude, agent

MakeClient = Callable[..., TestClient]
WRITE = {**AUTH, "X-Fleet-Request": "1"}
WORK = Peer(name="work", url="https://work.ts.net", label="💼 Công ty")


@pytest.fixture
def project(user_home: Path) -> Path:
    return user_home / "proj"


# ---------------------------------------------------------------- guards
def test_write_returns_401_without_token(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/m/home/sessions/558c6df2/stop", headers={"X-Fleet-Request": "1"})

    assert r.status_code == 401


def test_write_returns_403_without_csrf_header(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/m/home/sessions/558c6df2/stop", headers=AUTH)

    assert r.status_code == 403


def test_unknown_action_returns_404(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/m/home/sessions/558c6df2/explode", headers=WRITE)

    assert r.status_code == 404


# ---------------------------------------------------------------- stop / respawn / rm
@pytest.mark.parametrize(("method", "path", "command"), [
    ("POST", "/api/m/home/sessions/558c6df2/stop", ("stop", "558c6df2")),
    ("POST", "/api/m/home/sessions/558c6df2/respawn", ("respawn", "558c6df2")),
    ("DELETE", "/api/m/home/sessions/558c6df2", ("rm", "558c6df2")),
])
def test_lifecycle_action_runs_cli_command(claude_home: Path, make_client: MakeClient,
                                           method: str, path: str, command: tuple[str, ...]) -> None:
    fake = FakeClaude(claude_home)
    c = make_client(fake=fake)

    r = c.request(method, path, headers=WRITE)

    assert (r.status_code, fake.commands) == (200, [command])


def test_stop_returns_cli_output(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/sessions/558c6df2/stop", headers=WRITE)

    assert r.json() == {"ok": True, "id": "558c6df2", "action": "stop", "output": "stopped 558c6df2"}


def test_stop_returns_404_when_session_does_not_exist(make_client: MakeClient) -> None:
    c = make_client()

    r = c.post("/api/sessions/zzzz9999/stop", headers=WRITE)

    assert r.status_code == 404


# ---------------------------------------------------------------- start
def test_start_runs_bg_session_in_project_dir(claude_home: Path, make_client: MakeClient,
                                              project: Path) -> None:
    fake = FakeClaude(claude_home)
    c = make_client(fake=fake)

    r = c.post("/api/m/home/sessions", headers=WRITE,
               json={"cwd": str(project), "prompt": "fix tests", "name": "fix", "permissionMode": "auto"})

    assert (r.json()["id"], fake.calls) == ("new12345", [
        (("--bg", "-n", "fix", "--permission-mode", "auto", "--", "fix tests"), project.resolve()),
    ])


def test_start_passes_prompt_after_double_dash(claude_home: Path, make_client: MakeClient,
                                               project: Path) -> None:
    fake = FakeClaude(claude_home)
    c = make_client(fake=fake)

    body = {"cwd": str(project), "prompt": "--dangerously-skip-permissions"}

    c.post("/api/sessions", headers=WRITE, json=body)

    assert fake.commands == [("--bg", "--", "--dangerously-skip-permissions")]


@pytest.mark.parametrize(("cwd", "status"), [
    ("/etc", 400),            # outside home
    ("relative/path", 400),   # not absolute
    ("{home}/missing", 404),  # does not exist
    ("{home}/proj/../..", 400),  # escapes home after resolving
])
def test_start_rejects_bad_cwd(make_client: MakeClient, user_home: Path, cwd: str, status: int) -> None:
    c = make_client()

    r = c.post("/api/sessions", headers=WRITE, json={"cwd": cwd.format(home=user_home), "prompt": "hi"})

    assert r.status_code == status


@pytest.mark.parametrize("mode", ["bypassPermissions", "dontAsk", "whatever"])
def test_start_rejects_permission_mode_that_skips_prompts(make_client: MakeClient, project: Path,
                                                          mode: str) -> None:
    c = make_client()

    r = c.post("/api/sessions", headers=WRITE,
               json={"cwd": str(project), "prompt": "hi", "permissionMode": mode})

    assert r.status_code == 400


def test_start_rejects_empty_prompt(make_client: MakeClient, project: Path) -> None:
    c = make_client()

    r = c.post("/api/sessions", headers=WRITE, json={"cwd": str(project), "prompt": ""})

    assert r.status_code == 422


def test_start_returns_409_with_hint_when_workspace_untrusted(claude_home: Path, make_client: MakeClient,
                                                              project: Path) -> None:
    c = make_client(fake=FakeClaude(claude_home, untrusted=(project.resolve(),)))

    r = c.post("/api/sessions", headers=WRITE, json={"cwd": str(project), "prompt": "hi"})

    assert (r.status_code, "trust" in r.json()["detail"]) == (409, True)


# ---------------------------------------------------------------- message
def test_message_to_history_session_resumes_it(claude_home: Path, make_client: MakeClient,
                                               project: Path) -> None:
    fake = FakeClaude(claude_home, agents=[])
    c = make_client(fake=fake)
    hist = claude_home / f"projects/-Users-me-code/{HIST_SID}.jsonl"
    hist.write_text(hist.read_text() + json.dumps({"type": "system", "cwd": str(project)}) + "\n")

    r = c.post(f"/api/m/home/sessions/{HIST_SID}/message", headers=WRITE, json={"text": "tiếp tục"})

    assert (r.status_code, fake.calls) == (200, [
        (("--bg", "--resume", HIST_SID, "--", "tiếp tục"), project.resolve()),
    ])


def test_message_to_finished_live_session_stops_then_resumes(claude_home: Path, make_client: MakeClient,
                                                             project: Path) -> None:
    fake = FakeClaude(claude_home, agents=[agent(cwd=str(project), state="done")])
    c = make_client(fake=fake)

    c.post("/api/sessions/558c6df2/message", headers=WRITE, json={"text": "thêm test"})

    assert fake.commands == [("stop", "558c6df2"), ("--bg", "--resume", SID, "--", "thêm test")]


def test_message_to_stopped_session_resumes_without_stop(claude_home: Path, make_client: MakeClient,
                                                         project: Path) -> None:
    stopped = agent(cwd=str(project), state="stopped", pid=None, status=None)
    fake = FakeClaude(claude_home, agents=[stopped])
    c = make_client(fake=fake)

    c.post("/api/sessions/558c6df2/message", headers=WRITE, json={"text": "tiếp"})

    assert fake.commands == [("--bg", "--resume", SID, "--", "tiếp")]


def test_message_to_working_session_returns_409(claude_home: Path, make_client: MakeClient,
                                                project: Path) -> None:
    fake = FakeClaude(claude_home, agents=[agent(cwd=str(project), state="working")])
    c = make_client(fake=fake)

    r = c.post("/api/sessions/558c6df2/message", headers=WRITE, json={"text": "dừng lại"})

    assert (r.status_code, fake.commands) == (409, [])


def test_message_with_force_interrupts_working_session(claude_home: Path, make_client: MakeClient,
                                                       project: Path) -> None:
    fake = FakeClaude(claude_home, agents=[agent(cwd=str(project), state="working")])
    c = make_client(fake=fake)

    c.post("/api/sessions/558c6df2/message", headers=WRITE, json={"text": "đổi hướng", "force": True})

    assert fake.commands == [("stop", "558c6df2"), ("--bg", "--resume", SID, "--", "đổi hướng")]


def test_message_waits_for_stopped_process_to_exit_before_resuming(claude_home: Path, make_client: MakeClient,
                                                                   project: Path) -> None:
    fake = FakeClaude(claude_home, agents=[agent(cwd=str(project), state="done")], stop_lag=3)
    c = make_client(fake=fake)

    r = c.post("/api/sessions/558c6df2/message", headers=WRITE, json={"text": "tiếp"})

    assert (r.json()["id"], r.json()["output"], fake.commands) == (
        "558c6df2", "", [("stop", "558c6df2"), ("--bg", "--resume", SID, "--", "tiếp")])


def test_message_returns_504_when_process_never_exits(claude_home: Path, make_client: MakeClient,
                                                      project: Path) -> None:
    fake = FakeClaude(claude_home, agents=[agent(cwd=str(project), state="done")], stop_lag=None)
    c = make_client(fake=fake)

    r = c.post("/api/sessions/558c6df2/message", headers=WRITE, json={"text": "tiếp"})

    assert (r.status_code, fake.commands) == (504, [("stop", "558c6df2")])


def test_message_reports_when_cli_started_a_copy(claude_home: Path, make_client: MakeClient,
                                                 project: Path) -> None:
    fake = FakeClaude(claude_home, agents=[agent(cwd=str(project), state="done")], copy_id="ae6100f7")
    c = make_client(fake=fake)

    r = c.post("/api/sessions/558c6df2/message", headers=WRITE, json={"text": "tiếp"})

    assert (r.json()["id"], "bản sao" in r.json()["output"]) == ("ae6100f7", True)


def test_message_to_open_interactive_session_returns_409(claude_home: Path, make_client: MakeClient,
                                                         project: Path) -> None:
    fake = FakeClaude(claude_home, agents=[agent(cwd=str(project), kind="interactive", state="idle")])
    c = make_client(fake=fake)

    r = c.post("/api/sessions/558c6df2/message", headers=WRITE, json={"text": "hi"})

    assert (r.status_code, fake.commands) == (409, [])


def test_message_to_unknown_session_returns_404(claude_home: Path, make_client: MakeClient) -> None:
    c = make_client(fake=FakeClaude(claude_home, agents=[]))

    r = c.post("/api/sessions/zzzz9999/message", headers=WRITE, json={"text": "hi"})

    assert r.status_code == 404


# ---------------------------------------------------------------- via peer
def test_message_on_peer_is_forwarded_with_token_csrf_header_and_body(make_client: MakeClient) -> None:
    seen: list[tuple[str, str, str | None, str | None, dict[str, object]]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, req.headers.get("authorization"),
                     req.headers.get("x-fleet-request"), json.loads(req.content)))
        return httpx.Response(200, json={"ok": True, "id": "w1", "action": "message", "output": ""})

    c = make_client(peers=(WORK,), peer_handler=handler)

    r = c.post("/api/m/work/sessions/w1/message", headers=WRITE, json={"text": "hi"})

    assert (r.status_code, seen) == (200, [
        ("POST", "/api/sessions/w1/message", f"Bearer {TOKEN}", "1", {"text": "hi", "force": False}),
    ])


def test_remove_on_peer_relays_peer_error_status(make_client: MakeClient) -> None:
    c = make_client(peers=(WORK,), peer_handler=lambda r: httpx.Response(404, json={"detail": "nope"}))

    r = c.delete("/api/m/work/sessions/w1", headers=WRITE)

    assert (r.status_code, r.json()) == (404, {"detail": "nope"})


def test_action_on_unreachable_peer_returns_504(make_client: MakeClient) -> None:
    c = make_client(peers=(WORK,))

    r = c.post("/api/m/work/sessions/w1/stop", headers=WRITE)

    assert r.status_code == 504
