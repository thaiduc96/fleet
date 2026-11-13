"""The emulator turns a TUI's redraw stream into the text a person sees."""

from pathlib import Path

import pytest

from fleetd.prompts import PROMPT_MARK
from fleetd.vt import Screen, cell_width

FIXTURES = Path(__file__).parent / "fixtures" / "screens"


def render(raw: str, cols: int = 20, rows: int = 5) -> list[str]:
    screen = Screen(cols, rows)
    screen.feed(raw)
    return screen.lines()


def test_cursor_positioning_and_forward_moves_build_words() -> None:
    assert render("\x1b[2;3Hab\x1b[2Ccd")[1] == "  ab  cd"


def test_erase_line_and_display() -> None:
    assert render("hello\x1b[1;3H\x1b[K") == ["he", "", "", "", ""]
    assert render("a\r\nb\x1b[2J") == ["", "", "", "", ""]


def test_line_feed_at_the_bottom_scrolls() -> None:
    assert render("1\r\n2\r\n3", rows=2) == ["2", "3"]


def test_wide_characters_take_two_columns() -> None:
    assert render("🏠x\x1b[1;4Hy") == ["🏠xy", "", "", "", ""]


def test_combining_marks_join_the_previous_letter() -> None:
    assert render("é!")[0] == "é!"


@pytest.mark.parametrize(("ch", "width"), [("a", 1), ("Đ", 1), ("😀", 2), ("中", 2), ("́", 0)])
def test_cell_width(ch: str, width: int) -> None:
    assert cell_width(ch) == width


def test_colours_and_unknown_sequences_leave_no_trace() -> None:
    assert render("\x1b[38;2;1;2;3mred\x1b[0m\x1b]0;title\x07\x1b[?25l!")[0] == "red!"


def test_a_real_claude_code_plan_prompt_renders_as_text() -> None:
    lines = render((FIXTURES / "plan.raw").read_bytes().decode(), cols=100, rows=40)

    assert any(f"{PROMPT_MARK} 1. Yes, and use auto mode" in ln for ln in lines)
    assert any("Plan: Đọc số phiên bản Fleet" in ln for ln in lines)


@pytest.mark.parametrize("cut", ["a\x1b", "a\x1b[", "a\x1b[2", "a\x1b[23"])
def test_an_escape_sequence_split_across_reads_still_applies(cut: str) -> None:
    whole = "a\x1b[23Gb"
    screen = Screen(30, 2)

    screen.feed(cut)
    screen.feed(whole[len(cut):])

    assert screen.lines()[0] == "a" + " " * 21 + "b"
