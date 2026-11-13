from pathlib import Path

from fleetd.models import Block, Inline, Message
from fleetd.transcripts import TranscriptIndex, messages, summarize
from tests.conftest import HIST_SID, SID, rec, write_transcript

PROJECT = "projects/-Users-me-code"


def test_summarize_extracts_title_prompt_reply_and_cost(claude_home: Path) -> None:
    path = claude_home / PROJECT / f"{SID}.jsonl"

    s = summarize(path)

    assert (s.title, s.last_prompt, s.last_reply, s.cost_usd, s.session_kind, s.cwd) == (
        "Camera nhà", "xin chào", "Chào bạn", 0.42, "bg", "/Users/me/code")


def test_summarize_of_empty_file_falls_back_to_mtime(tmp_path: Path) -> None:
    path = write_transcript(tmp_path / f"{SID}.jsonl", [])

    s = summarize(path)

    assert (s.title, s.last_prompt, s.updated_at is not None) == (None, None, True)


def test_summarize_clips_long_prompt_to_300_chars(tmp_path: Path) -> None:
    path = write_transcript(tmp_path / f"{SID}.jsonl", [rec(type="last-prompt", lastPrompt="x" * 1000)])

    s = summarize(path)

    assert s.last_prompt == "x" * 299 + "…"


def test_messages_skips_tool_results_and_sidechains(claude_home: Path) -> None:
    path = claude_home / PROJECT / f"{SID}.jsonl"

    result = messages(path)

    assert result == [
        Message(role="user", text="xin chào", ts="2026-11-13T12:00:00Z"),
        Message(role="assistant", text="Chào bạn", ts="2026-11-13T12:00:05Z",
                blocks=[Block(type="paragraph", inlines=[Inline(type="text", text="Chào bạn")])]),
        Message(role="tool", text="Bash: List files", ts="2026-11-13T12:00:05Z"),
    ]


def test_messages_limit_keeps_newest(claude_home: Path) -> None:
    path = claude_home / PROJECT / f"{SID}.jsonl"

    result = messages(path, limit=1)

    assert [m.text for m in result] == ["Bash: List files"]


def test_index_find_returns_transcript_path(claude_home: Path) -> None:
    idx = TranscriptIndex(claude_home)

    result = idx.find(SID)

    assert result == claude_home / PROJECT / f"{SID}.jsonl"


def test_index_find_rejects_non_uuid(claude_home: Path) -> None:
    idx = TranscriptIndex(claude_home)

    result = idx.find("../../etc/passwd")

    assert result is None


def test_index_recent_lists_all_transcripts(claude_home: Path) -> None:
    idx = TranscriptIndex(claude_home)

    result = idx.recent(days=36500)

    assert sorted(s.session_id for s in result) == sorted([SID, HIST_SID])


def test_index_recent_with_cutoff_in_future_is_empty(claude_home: Path) -> None:
    idx = TranscriptIndex(claude_home)

    result = idx.recent(days=-1)

    assert result == []
