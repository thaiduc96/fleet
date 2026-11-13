"""Phase 4: move a session between Macs with different home directories."""

import base64
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fleetd.actions import ActionError
from fleetd.config import Peer
from fleetd.handoff import Handoff, encode_project, normalize_remote, remap, remap_many
from fleetd.models import BundleFile, GitInfo, HandoffBundle, ImportBody
from fleetd.transcripts import TranscriptIndex
from tests.conftest import AUTH, SID, TOKEN, FakeClaude, agent, rec, write_transcript

MakeClient = Callable[..., TestClient]
WRITE = {**AUTH, "X-Fleet-Request": "1"}
WORK = Peer(name="work", url="https://work.ts.net", label="💼 Công ty")
HEAD = "a" * 40


@pytest.fixture
def src(tmp_path: Path) -> Handoff:
    """Source Mac: home /…/src-home (think /Users/alice), repo at ~/code/app."""
    home = tmp_path / "src-home"
    (home / "code/app").mkdir(parents=True)
    claude = home / ".claude"
    cwd = str(home / "code/app")
    project = claude / "projects" / encode_project(cwd)
    write_transcript(
        project / f"{SID}.jsonl",
        [
            rec(
                type="user",
                message={"role": "user", "content": f"sửa {cwd}/main.py"},
                origin={"kind": "human"},
                cwd=cwd,
                timestamp="2026-11-13T12:00:00Z",
            ),
            rec(
                type="assistant",
                message={"content": [{"type": "text", "text": f"đã đọc {home}/notes.md"}]},
                cwd=cwd,
                timestamp="2026-11-13T12:00:05Z",
            ),
            rec(type="ai-title", aiTitle="Sửa app"),
        ],
    )
    (project / SID / "subagents").mkdir(parents=True)
    (project / SID / "subagents/a.jsonl").write_text(rec(cwd=cwd) + "\n")
    (claude / "file-history" / SID).mkdir(parents=True)
    (claude / "file-history" / SID / "v1").write_bytes(b"\x00\x01binary")
    return Handoff(claude, TranscriptIndex(claude), home, "home", git=fake_git({}))


@pytest.fixture
def dst(tmp_path: Path) -> Handoff:
    """Target Mac: a different home (think /Users/bob) with the repo cloned."""
    home = tmp_path / "dst-home"
    (home / "code/app").mkdir(parents=True)
    claude = home / ".claude"
    return Handoff(claude, TranscriptIndex(claude), home, "work", git=fake_git({}))


def fake_git(answers: dict[tuple[str, ...], str | None]) -> Callable[..., str | None]:
    """Stands in for the `git` binary; unknown questions answer "not a repo"."""

    def git(cwd: Path, *args: str) -> str | None:
        return answers.get(args)

    return git


def src_git(dirty: int = 0) -> GitInfo:
    return GitInfo(root="/x", remote="git@github.com:me/app.git", branch="main", head=HEAD, dirty=dirty)


def dst_repo(head: str = HEAD, has_commit: bool = True, remote: str = "https://github.com/me/app") -> dict:
    return {
        ("rev-parse", "--show-toplevel"): "/y",
        ("status", "--porcelain"): "",
        ("remote", "get-url", "origin"): remote,
        ("rev-parse", "--abbrev-ref", "HEAD"): "main",
        ("rev-parse", "HEAD"): head,
        ("cat-file", "-e", f"{HEAD}^{{commit}}"): "" if has_commit else None,
    }


# ---------------------------------------------------------------- helpers
def test_encode_project_matches_claude_code_naming() -> None:
    result = encode_project("/Users/alice/Documents/code/fleet/.claude/worktrees/phase1")

    assert result == "-Users-alice-Documents-code-fleet--claude-worktrees-phase1"


@pytest.mark.parametrize(
    "url", ["git@github.com:Me/App.git", "https://github.com/me/app", "ssh://git@github.com/me/app.git"]
)
def test_normalize_remote_treats_ssh_and_https_alike(url: str) -> None:
    result = normalize_remote(url)

    assert result == "github.com/me/app"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('"cwd":"/Users/alice/code"', '"cwd":"/Users/bob/code"'),
        ('"cwd":"/Users/alice"', '"cwd":"/Users/bob"'),
        ("xem /Users/alice/a.txt nhé", "xem /Users/bob/a.txt nhé"),
        ('"/Users/alicebook/x"', '"/Users/alicebook/x"'),  # different user, untouched
    ],
)
def test_remap_replaces_whole_path_prefix_only(text: str, expected: str) -> None:
    result = remap(text, "/Users/alice", "/Users/bob")

    assert result == expected


def test_remap_many_does_not_rewrite_its_own_output_when_target_is_inside_source() -> None:
    # Found in the e2e run: target home /Users/me/tmp/fake lies under source home /Users/me.
    text = '"cwd":"/Users/me/code" "f":"/Users/me/notes.md"'

    result = remap_many(
        text, [("/Users/me/code", "/Users/me/tmp/fake/code"), ("/Users/me", "/Users/me/tmp/fake")]
    )

    assert result == '"cwd":"/Users/me/tmp/fake/code" "f":"/Users/me/tmp/fake/notes.md"'


def test_import_into_home_nested_under_source_home_rewrites_cwd_once(src: Handoff) -> None:
    nested_home = src.user_home / "tmp/fake"
    (nested_home / "code/app").mkdir(parents=True)
    claude = nested_home / ".claude"
    target = Handoff(claude, TranscriptIndex(claude), nested_home, "work", git=fake_git({}))

    target.import_(ImportBody(bundle=src.export(SID)))

    summary = target.idx.summary_for(SID)
    assert summary.cwd == str((nested_home / "code/app").resolve())


# ---------------------------------------------------------------- export
def test_export_bundles_transcript_subagents_and_file_history(src: Handoff) -> None:
    b = src.export(SID)

    assert (b.title, sorted(f.rel for f in b.files)) == (
        "Sửa app",
        ["file-history/v1", "session/subagents/a.jsonl", "transcript.jsonl"],
    )


def test_export_meta_only_has_no_files(src: Handoff) -> None:
    b = src.export(SID, meta_only=True)

    assert (b.files, b.size > 0, b.cwd.endswith("code/app")) == ([], True, True)


def test_export_unknown_session_is_404(src: Handoff) -> None:
    with pytest.raises(ActionError) as e:
        src.export("00000000-0000-0000-0000-000000000000")

    assert e.value.status == 404


def test_export_rejects_bundle_over_limit(src: Handoff) -> None:
    small = Handoff(src.claude_home, src.idx, src.user_home, "home", git=fake_git({}), max_bytes=10)

    with pytest.raises(ActionError) as e:
        small.export(SID)

    assert e.value.status == 413


# ---------------------------------------------------------------- import
def test_import_rewrites_home_and_writes_into_target_project(src: Handoff, dst: Handoff) -> None:
    bundle = src.export(SID)

    result = dst.import_(ImportBody(bundle=bundle))

    target_cwd = str((dst.user_home / "code/app").resolve())  # tmp_path lives under /var -> /private/var
    transcript = dst.claude_home / "projects" / encode_project(target_cwd) / f"{SID}.jsonl"
    text = transcript.read_text()
    assert (result.cwd, str(src.user_home) in text, f'"cwd": "{target_cwd}"' in text) == (
        target_cwd,
        False,
        True,
    )


def test_imported_transcript_is_found_by_index(src: Handoff, dst: Handoff) -> None:
    dst.import_(ImportBody(bundle=src.export(SID)))

    summary = dst.idx.summary_for(SID)

    assert (summary.title, summary.last_prompt) == ("Sửa app", f"sửa {dst.user_home}/code/app/main.py")


def test_import_copies_binary_file_history_unchanged(src: Handoff, dst: Handoff) -> None:
    dst.import_(ImportBody(bundle=src.export(SID)))

    data = (dst.claude_home / "file-history" / SID / "v1").read_bytes()

    assert data == b"\x00\x01binary"


def test_import_dry_run_writes_nothing(src: Handoff, dst: Handoff) -> None:
    result = dst.import_(ImportBody(bundle=src.export(SID), dry_run=True))

    assert (result.written, (dst.claude_home / "projects").exists()) == ([], False)


def test_import_404_with_hint_when_repo_not_cloned_on_target(src: Handoff, dst: Handoff) -> None:
    (dst.user_home / "code/app").rmdir()

    with pytest.raises(ActionError) as e:
        dst.import_(ImportBody(bundle=src.export(SID)))

    assert (e.value.status, "clone" in e.value.message) == (404, True)


def test_import_409_when_target_already_has_the_session(src: Handoff, dst: Handoff) -> None:
    dst.import_(ImportBody(bundle=src.export(SID)))

    with pytest.raises(ActionError) as e:
        dst.import_(ImportBody(bundle=src.export(SID)))

    assert e.value.status == 409


def test_import_overwrite_replaces_existing(src: Handoff, dst: Handoff) -> None:
    dst.import_(ImportBody(bundle=src.export(SID)))

    result = dst.import_(ImportBody(bundle=src.export(SID), overwrite=True))

    assert len(result.written) == 3


def test_import_honours_explicit_target_cwd(src: Handoff, dst: Handoff) -> None:
    (dst.user_home / "elsewhere").mkdir()

    result = dst.import_(ImportBody(bundle=src.export(SID), target_cwd=str(dst.user_home / "elsewhere")))

    assert result.cwd == str((dst.user_home / "elsewhere").resolve())


@pytest.mark.parametrize("rel", ["../../.ssh/authorized_keys", "/etc/passwd", "session/../../x", "other/x"])
def test_import_rejects_unsafe_or_unknown_paths(src: Handoff, dst: Handoff, rel: str) -> None:
    bundle = src.export(SID)
    evil = bundle.model_copy(update={"files": [*bundle.files, BundleFile(rel=rel, data="")]})

    with pytest.raises(ActionError) as e:
        dst.import_(ImportBody(bundle=evil))

    assert e.value.status == 400


def test_import_rejects_bundle_without_transcript(dst: Handoff) -> None:
    bundle = HandoffBundle(
        session_id=SID,
        source_machine="home",
        home="/Users/alice",
        cwd=str(dst.user_home / "code/app"),
        files=[],
    )

    with pytest.raises(ActionError) as e:
        dst.import_(ImportBody(bundle=bundle))

    assert e.value.status == 400


# ---------------------------------------------------------------- warnings
def warnings_for(dst: Handoff, src_info: GitInfo | None, answers: dict) -> list[str]:
    target = Handoff(dst.claude_home, dst.idx, dst.user_home, "work", git=fake_git(answers))
    bundle = HandoffBundle(
        session_id=SID,
        source_machine="home",
        home="/Users/alice",
        cwd=str(dst.user_home / "code/app"),
        git=src_info,
    )
    return target.import_(ImportBody(bundle=bundle, dry_run=True)).warnings


def test_no_warnings_when_target_repo_matches(dst: Handoff) -> None:
    result = warnings_for(dst, src_git(), dst_repo())

    assert result == []


def test_warns_about_uncommitted_changes_on_source(dst: Handoff) -> None:
    result = warnings_for(dst, src_git(dirty=3), dst_repo())

    assert result == ["Máy nguồn còn 3 file chưa commit — các thay đổi đó KHÔNG được chuyển."]


def test_warns_when_target_lacks_the_commit(dst: Handoff) -> None:
    result = warnings_for(dst, src_git(), dst_repo(head="b" * 40, has_commit=False))

    assert result == [
        "Máy đích chưa có commit aaaaaaa (nhánh main): hãy push ở máy nguồn rồi git pull ở đây."
    ]


def test_warns_when_target_is_on_another_commit(dst: Handoff) -> None:
    result = warnings_for(dst, src_git(), dst_repo(head="b" * 40))

    assert result == ["Máy đích đang ở bbbbbbb (main), session dừng ở aaaaaaa (main)."]


def test_warns_on_different_remote(dst: Handoff) -> None:
    result = warnings_for(dst, src_git(), dst_repo(remote="https://github.com/other/app"))

    assert result == ["Remote khác nhau: nguồn git@github.com:me/app.git, đích https://github.com/other/app."]


def test_warns_when_target_dir_is_untrusted(dst: Handoff) -> None:
    cwd = str((dst.user_home / "code/app").resolve())
    (dst.user_home / ".claude.json").write_text(
        json.dumps({"projects": {cwd: {"hasTrustDialogAccepted": False}}})
    )

    result = warnings_for(dst, None, {})

    assert result == [
        f"Thư mục {cwd} chưa được trust trên máy này: mở `claude` ở đó một lần trước khi tiếp tục session."
    ]


# ---------------------------------------------------------------- orchestration API
def bundle_json(src: Handoff) -> dict:
    return src.export(SID).model_dump(by_alias=True)


def test_handoff_local_to_peer_stops_exports_imports_and_resumes(
    claude_home: Path, make_client: MakeClient, user_home: Path
) -> None:
    calls: list[tuple[str, str]] = []
    bodies: dict[str, dict] = {}

    def work(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        bodies[req.url.path] = json.loads(req.content)
        if req.url.path == "/api/import":
            return httpx.Response(
                200,
                json={
                    "sessionId": SID,
                    "cwd": "/Users/bob/proj",
                    "dryRun": False,
                    "written": ["x"],
                    "warnings": ["w1"],
                },
            )
        return httpx.Response(200, json={"ok": True, "id": SID[:8], "action": "message", "output": ""})

    live = agent(cwd=str(user_home / "proj"), state="done")
    fake = FakeClaude(claude_home, agents=[live])
    c = make_client(fake=fake, peers=(WORK,), peer_handler=work)

    r = c.post(
        "/api/handoff",
        headers=WRITE,
        json={"source": "home", "target": "work", "sessionId": SID, "message": "tiếp ở công ty"},
    )

    files = [f["rel"] for f in bodies["/api/import"]["bundle"]["files"]]
    assert (
        r.status_code,
        r.json()["stoppedSource"],
        r.json()["resumedId"],
        r.json()["warnings"],
        fake.commands,
        calls,
        files,
        bodies[f"/api/sessions/{SID}/message"]["text"],
    ) == (
        200,
        True,
        SID[:8],
        ["w1"],
        [("stop", "558c6df2")],
        [("POST", "/api/import"), ("POST", f"/api/sessions/{SID}/message")],
        ["transcript.jsonl"],
        "tiếp ở công ty",
    )


def test_handoff_peer_to_local_writes_transcript_here(
    src: Handoff, claude_home: Path, make_client: MakeClient, user_home: Path
) -> None:
    (user_home / "code/app").mkdir(parents=True)

    def work(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/release"):
            return httpx.Response(200, json={"sessionId": SID, "wasLive": False, "stopped": False})
        return httpx.Response(200, json=bundle_json(src))

    c = make_client(fake=FakeClaude(claude_home, agents=[]), peers=(WORK,), peer_handler=work)
    (claude_home / "projects/-Users-me-code" / f"{SID}.jsonl").unlink()  # not here yet

    r = c.post("/api/handoff", headers=WRITE, json={"source": "work", "target": "home", "sessionId": SID})

    target = (
        claude_home / "projects" / encode_project(str((user_home / "code/app").resolve())) / f"{SID}.jsonl"
    )
    assert (r.status_code, r.json()["cwd"], target.exists()) == (
        200,
        str((user_home / "code/app").resolve()),
        True,
    )


def test_handoff_dry_run_changes_nothing_and_warns_about_live_session(
    claude_home: Path, make_client: MakeClient, user_home: Path
) -> None:
    seen: list[dict] = []

    def work(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"sessionId": SID, "cwd": "/c", "dryRun": True, "warnings": []})

    fake = FakeClaude(claude_home, agents=[agent(cwd=str(user_home / "proj"), state="done")])
    c = make_client(fake=fake, peers=(WORK,), peer_handler=work)

    r = c.post(
        "/api/handoff",
        headers=WRITE,
        json={"source": "home", "target": "work", "sessionId": SID, "dryRun": True},
    )

    assert (r.json()["warnings"], fake.commands, seen[0]["dryRun"], seen[0]["bundle"]["files"]) == (
        ["Session đang chạy ở home và sẽ được dừng trước khi chuyển."],
        [],
        True,
        [],
    )


def test_handoff_refuses_working_session_without_force(
    claude_home: Path, make_client: MakeClient, user_home: Path
) -> None:
    fake = FakeClaude(claude_home, agents=[agent(cwd=str(user_home / "proj"), state="working")])
    c = make_client(fake=fake, peers=(WORK,))

    r = c.post("/api/handoff", headers=WRITE, json={"source": "home", "target": "work", "sessionId": SID})

    assert (r.status_code, fake.commands) == (409, [])


def test_handoff_failure_after_stop_says_conversation_is_safe(
    claude_home: Path, make_client: MakeClient, user_home: Path
) -> None:
    fake = FakeClaude(claude_home, agents=[agent(cwd=str(user_home / "proj"), state="done")])
    c = make_client(
        fake=fake,
        peers=(WORK,),
        peer_handler=lambda r: httpx.Response(404, json={"detail": "Máy này chưa có thư mục X"}),
    )

    r = c.post("/api/handoff", headers=WRITE, json={"source": "home", "target": "work", "sessionId": SID})

    assert (
        r.status_code,
        "💼 Công ty: Máy này chưa có thư mục X" in r.json()["detail"],
        "hội thoại vẫn còn nguyên" in r.json()["detail"],
    ) == (404, True, True)


@pytest.mark.parametrize(("source", "target", "status"), [("home", "home", 400), ("home", "nowhere", 404)])
def test_handoff_rejects_bad_machines(make_client: MakeClient, source: str, target: str, status: int) -> None:
    c = make_client()

    r = c.post("/api/handoff", headers=WRITE, json={"source": source, "target": target, "sessionId": SID})

    assert r.status_code == status


def test_export_endpoint_requires_token(make_client: MakeClient) -> None:
    c = make_client()

    r = c.get(f"/api/sessions/{SID}/export")

    assert r.status_code == 401


def test_export_endpoint_returns_base64_transcript(make_client: MakeClient) -> None:
    c = make_client()

    b = c.get(f"/api/sessions/{SID}/export", headers=AUTH).json()

    assert base64.b64decode(b["files"][0]["data"]).decode().count('"type": "ai-title"') == 1


def test_peer_receives_token_on_import(make_client: MakeClient) -> None:
    auth_seen: list[str | None] = []

    def work(req: httpx.Request) -> httpx.Response:
        auth_seen.append(req.headers.get("authorization"))
        return httpx.Response(200, json={"sessionId": SID, "cwd": "/c", "dryRun": True, "warnings": []})

    c = make_client(fake=FakeClaude(Path("/nonexistent"), agents=[]), peers=(WORK,), peer_handler=work)

    c.post(
        "/api/handoff",
        headers=WRITE,
        json={"source": "home", "target": "work", "sessionId": SID, "dryRun": True},
    )

    assert auth_seen == [f"Bearer {TOKEN}"]
