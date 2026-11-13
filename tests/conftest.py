"""Shared builders. Only process boundaries are faked: the `claude` binary
(subprocess), the filesystem (tmp_path) and peer HTTP (httpx.MockTransport)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fleetd import config
from fleetd.attach import Attacher
from fleetd.claude_cli import ClaudeCLI, CLIError
from fleetd.main import create_app
from fleetd.models import Awake, Battery, Health

SID = "558c6df2-236b-4346-b459-524522eae490"
HIST_SID = "db2a212b-0306-457d-a6cd-2735c5483264"
TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def agent(**fields: object) -> dict[str, object]:
    """One `claude agents --json` entry (shape from Claude Code 2.1.323)."""
    base: dict[str, object] = {
        "pid": 32471, "id": "558c6df2", "cwd": "/Users/me/code", "kind": "background",
        "startedAt": 1794573363444, "sessionId": SID, "name": "home-camera-setup",
        "status": "busy", "state": "done",
    }
    return {**base, **fields}


def rec(**fields: object) -> str:
    return json.dumps(fields, ensure_ascii=False)


def write_transcript(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def standard_transcript(title: str) -> list[str]:
    return [
        rec(type="mode", mode="normal"),
        "{not json",
        rec(type="user", message={"role": "user", "content": "xin chào"}, origin={"kind": "human"},
            timestamp="2026-11-13T12:00:00Z", cwd="/Users/me/code", sessionKind="bg"),
        rec(type="assistant", timestamp="2026-11-13T12:00:05Z", message={"role": "assistant", "content": [
            {"type": "text", "text": "Chào bạn"},
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "ls", "description": "List files"}},
        ]}),
        rec(type="user", timestamp="2026-11-13T12:00:06Z",
            message={"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "a b"}]}),
        rec(type="assistant", isSidechain=True, message={"content": [{"type": "text", "text": "subagent"}]}),
        rec(type="ai-title", aiTitle=title),
        rec(type="cost-state", totalCostUSD=0.42),
    ]


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    home = tmp_path / "claude"
    project = home / "projects/-Users-me-code"
    write_transcript(project / f"{SID}.jsonl", standard_transcript("Camera nhà"))
    write_transcript(project / f"{HIST_SID}.jsonl", standard_transcript("bật menu iterm2"))
    job = home / "jobs/558c6df2"
    job.mkdir(parents=True)
    (job / "state.json").write_text(json.dumps({
        "state": "done", "detail": "Đã xoá worktree", "intent": "Triển khai camera",
        "output": {"result": "Xong rồi"}, "updatedAt": "2026-11-13T15:02:04Z", "tokens": 10,
    }))
    return home


PAST_TENSE = {"stop": "stopped", "respawn": "respawned", "rm": "removed"}


class FakeClaude(ClaudeCLI):
    """Replaces the subprocess call to the `claude` binary."""

    def __init__(self, home: Path, agents: list[dict[str, object]] | None = None,
                 error: str | None = None, untrusted: tuple[Path, ...] = (),
                 stop_lag: int | None = 0, copy_id: str | None = None) -> None:
        """stop_lag: how many `claude agents` polls a stopped process stays alive
        (None = never exits). copy_id: make `--resume` start a copy with this id."""
        super().__init__("claude", home)
        self._agents = [dict(a) for a in ([agent()] if agents is None else agents)]
        self._error = error
        self._untrusted = untrusted
        self._stop_lag = stop_lag
        self._copy_id = copy_id
        self._exiting: dict[str, int | None] = {}
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []

    def _tick_exits(self) -> None:
        for a in self._agents:
            ident = str(a.get("id"))
            if ident not in self._exiting:
                continue
            left = self._exiting[ident]
            if left is None:
                continue
            if left <= 0:
                a.pop("pid", None)
                a.pop("status", None)
                del self._exiting[ident]
            else:
                self._exiting[ident] = left - 1

    def run(self, *args: str, timeout: float | None = None, cwd: Path | None = None) -> str:
        if self._error:
            raise CLIError(self._error)
        if args[:1] not in (("agents",), ("--version",)):
            self.calls.append((args, cwd))
        known = {str(a.get("id")) for a in self._agents}
        if args[:1] in (("stop",), ("respawn",), ("rm",)):
            if args[1] not in known:
                raise CLIError(f"`claude {args[0]}` exited 1: No job matching '{args[1]}'")
            if args[0] == "stop":
                self._exiting[args[1]] = self._stop_lag
            return f"{PAST_TENSE[args[0]]} {args[1]}\n"
        if args[:1] == ("--bg",):
            if cwd in self._untrusted:
                raise CLIError("`claude --bg` exited 1: Workspace not trusted. Run `claude` once.")
            if "--resume" in args:
                new_id = self._copy_id or args[args.index("--resume") + 1][:8]
            else:
                new_id = "new12345"
            name = args[args.index("-n") + 1] if "-n" in args else "auto"
            return f"backgrounded · {new_id} · {name}\n  claude agents   list sessions\n"
        if args[:1] == ("agents",):
            self._tick_exits()
        outputs = {
            ("agents", "--json", "--all"): json.dumps(self._agents),
            ("logs", "558c6df2"): "\x1b[1mhello\x1b[0m\r\n\n\n\nworld",
            ("--version",): "2.1.323 (Claude Code)\n",
        }
        if args not in outputs:
            raise CLIError(f"unexpected call {args}")
        return outputs[args]

    def is_running(self, short_id: str, pid: int | None) -> bool:
        """Simulated: alive until `stop_lag` polls after `stop` (no real os.kill)."""
        self._tick_exits()
        entry = next((a for a in self._agents if a.get("id") == short_id), None)
        return bool(entry and entry.get("pid"))

    @property
    def commands(self) -> list[tuple[str, ...]]:
        return [args for args, _ in self.calls]


class FakeClock:
    """monotonic() + sleep() that only move when slept on."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeProc:
    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else 0


class FakeProcs:
    """Stands in for subprocess.Popen of /usr/bin/caffeinate."""

    def __init__(self) -> None:
        self.spawned: list[FakeProc] = []

    def __call__(self, argv: list[str]) -> FakeProc:
        p = FakeProc(argv)
        self.spawned.append(p)
        return p

    @property
    def running(self) -> list[list[str]]:
        return [p.argv for p in self.spawned if p.returncode is None]


def fake_health(awake: Awake | None, self_pid: int | None) -> Health:
    return Health(uptime_sec=100, battery=Battery(percent=80, state="AC attached", on_ac=True),
                  sleep_prevented=bool(awake and awake.active), awake=awake)


def no_git(cwd: Path, *args: str) -> str | None:
    """Stands in for the `git` binary: every directory is "not a repo"."""
    return None


Handler = Callable[[httpx.Request], httpx.Response]


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.fixture
def user_home(tmp_path: Path) -> Path:
    """Stands in for ~ ; contains one project directory."""
    (tmp_path / "home/proj").mkdir(parents=True)
    return tmp_path / "home"


@pytest.fixture
def make_client(claude_home: Path, tmp_path: Path, user_home: Path) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def build(peers: tuple[config.Peer, ...] = (), peer_handler: Handler = unreachable,
              fake: FakeClaude | None = None, procs: FakeProcs | None = None,
              keep_awake: str = "off", git: Callable[..., str | None] = no_git,
              app_version: str | None = None, attacher: Attacher | None = None,
              **fake_kw: object) -> TestClient:
        cfg = config.Config(machine="home", label="🏠 Nhà", claude_home=claude_home,
                            peers=peers, home=tmp_path / "fleet", user_home=user_home,
                            keep_awake=keep_awake)
        clock = FakeClock()
        app = create_app(cfg, token=TOKEN, cli=fake or FakeClaude(claude_home, **fake_kw),
                         peer_http=httpx.AsyncClient(transport=httpx.MockTransport(peer_handler)),
                         clock=clock, sleep=clock.sleep, spawn=procs or FakeProcs(),
                         health_fn=fake_health, git=git, app_version=app_version,
                         attacher=attacher)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield build
    for c in clients:
        c.__exit__(None, None, None)
