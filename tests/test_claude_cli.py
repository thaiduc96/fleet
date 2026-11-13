from pathlib import Path

import pytest

from fleetd.claude_cli import ClaudeCLI, CLIError, clean_terminal, normalize_state
from fleetd.models import SessionState
from tests.conftest import FakeClaude


@pytest.mark.parametrize(("entry", "expected"), [
    ({"state": "done"}, SessionState.DONE),
    ({"state": "blocked"}, SessionState.NEEDS_INPUT),
    ({"state": "needs_input"}, SessionState.NEEDS_INPUT),
    ({"state": "working"}, SessionState.WORKING),
    ({"state": "working", "status": "busy"}, SessionState.WORKING),
    # After Esc the daemon keeps state "working" while the TUI waits for input (2.1.323).
    ({"state": "working", "status": "idle"}, SessionState.IDLE),
    ({"state": "failed"}, SessionState.FAILED),
    ({"state": "stopped"}, SessionState.STOPPED),
    ({"state": "idle"}, SessionState.IDLE),
    ({"state": "brand_new_state"}, SessionState.UNKNOWN),
    ({"status": "busy"}, SessionState.WORKING),
    ({}, SessionState.IDLE),
])
def test_normalize_state_maps_cli_state_to_dashboard_group(entry: dict[str, object],
                                                          expected: SessionState) -> None:
    result = normalize_state(entry)

    assert result == expected


def test_clean_terminal_strips_ansi_and_collapses_blank_lines() -> None:
    result = clean_terminal("\x1b[1mhello\x1b[0m\r\n\n\n\nworld")

    assert result == "hello\n\nworld"


def test_clean_terminal_drops_spinner_frames_and_repeated_lines() -> None:
    raw = "Spelunking…\n✻Spelunking…\n4\n✳8\n⏺\nReading file\nReading file\nDone: 3 files"

    result = clean_terminal(raw)

    assert result == "Reading file\nDone: 3 files"


def test_clean_terminal_turns_cursor_forward_into_spaces() -> None:
    result = clean_terminal("Xoá\x1b[1Cnhánh\x1b[3Cxong\x1b[C!")

    assert result == "Xoá nhánh   xong !"


def test_agents_parses_json_from_cli(claude_home: Path) -> None:
    cli = FakeClaude(claude_home, agents=[{"id": "abc", "state": "working"}])

    result = cli.agents()

    assert result == [{"id": "abc", "state": "working"}]


def test_agents_raises_cli_error_when_cli_fails(claude_home: Path) -> None:
    cli = FakeClaude(claude_home, error="boom")

    with pytest.raises(CLIError, match="boom"):
        cli.agents()


def test_is_running_true_while_daemon_still_lists_worker(claude_home: Path) -> None:
    (claude_home / "daemon").mkdir()
    (claude_home / "daemon/roster.json").write_text('{"workers": {"558c6df2": {"pid": 1}}}')
    cli = FakeClaude(claude_home, agents=[{"id": "558c6df2", "state": "done"}])

    result = ClaudeCLI.is_running(cli, "558c6df2", None)

    assert result is True


def test_is_running_false_once_worker_and_pid_are_gone(claude_home: Path) -> None:
    (claude_home / "daemon").mkdir()
    (claude_home / "daemon/roster.json").write_text('{"workers": {}}')
    cli = FakeClaude(claude_home, agents=[{"id": "558c6df2", "state": "done"}])

    result = ClaudeCLI.is_running(cli, "558c6df2", None)

    assert result is False


def test_job_state_reads_state_json(claude_home: Path) -> None:
    cli = FakeClaude(claude_home)

    result = cli.job_state("558c6df2")

    assert result["detail"] == "Đã xoá worktree"


@pytest.mark.parametrize("bad_id", ["../558c6df2", "", "a/b"])
def test_job_state_returns_empty_for_unsafe_id(claude_home: Path, bad_id: str) -> None:
    cli = FakeClaude(claude_home)

    result = cli.job_state(bad_id)

    assert result == {}
