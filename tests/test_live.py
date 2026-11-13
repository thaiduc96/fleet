"""The open conversation is polled: unchanged transcripts cost a stat, not a parse."""

from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from fleetd.config import Peer
from tests.conftest import AUTH, SID, rec

MakeClient = Callable[..., TestClient]
URL = f"/api/m/home/sessions/{SID}/transcript"


def test_poll_with_the_current_version_returns_unchanged(make_client: MakeClient) -> None:
    c = make_client()
    first = c.get(URL, headers=AUTH).json()

    again = c.get(URL, params={"since": first["version"]}, headers=AUTH).json()

    assert (again["unchanged"], again["messages"], again["version"]) == (True, [], first["version"])


def test_poll_after_the_transcript_grew_returns_the_new_message(
        make_client: MakeClient, claude_home: Path) -> None:
    c = make_client()
    old = c.get(URL, headers=AUTH).json()
    path = next(claude_home.glob(f"projects/*/{SID}.jsonl"))
    with path.open("a") as f:
        prompt = rec(type="user", message={"role": "user", "content": "tiếp đi"}, origin={"kind": "human"})
        f.write(prompt + "\n")

    new = c.get(URL, params={"since": old["version"]}, headers=AUTH).json()

    assert (new["unchanged"], new["messages"][-1]["text"]) == (False, "tiếp đi")


def test_transcript_reports_model_and_context_size(make_client: MakeClient, claude_home: Path) -> None:
    c = make_client()
    path = next(claude_home.glob(f"projects/*/{SID}.jsonl"))
    with path.open("a") as f:
        f.write(rec(type="assistant", message={"model": "claude-opus-5-5", "content": [], "usage": {
            "input_tokens": 2, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 98}}) + "\n")

    t = c.get(URL, headers=AUTH).json()

    assert (t["model"], t["contextTokens"]) == ("claude-opus-5-5", 1100)


def test_poll_on_a_peer_forwards_since(make_client: MakeClient) -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.query.decode())
        return httpx.Response(200, json={"sessionId": SID, "summary": {}, "messages": [], "unchanged": True})

    c = make_client(peers=(Peer(name="work", url="https://work.ts.net"),), peer_handler=handler)

    c.get(f"/api/m/work/sessions/{SID}/transcript", params={"limit": 5, "since": "1-2"}, headers=AUTH)

    assert seen == ["limit=5&since=1-2"]
