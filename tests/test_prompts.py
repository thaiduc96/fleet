"""Reading menus and the mode off real Claude Code screens (captured from 2.1.323, paths anonymised)."""

from pathlib import Path

import pytest

from fleetd import prompts
from fleetd.prompts import PROMPT_MARK
from fleetd.vt import Screen

FIXTURES = Path(__file__).parent / "fixtures" / "screens"


def parse(name: str) -> prompts.Parsed:
    screen = Screen(100, 40)
    screen.feed((FIXTURES / name).read_bytes().decode())
    return prompts.parse(screen.lines())


def test_plan_approval_prompt() -> None:
    p = parse("plan.raw").prompt

    assert p is not None and p.kind == "plan"
    assert [(o.key, o.label, o.needs_text) for o in p.options] == [
        ("1", "Yes, and use auto mode", False), ("2", "Yes, manually approve edits", False),
        ("3", "Tell Claude what to change", True)]
    assert p.body is not None and "Plan: Đọc số phiên bản Fleet" in p.body


def test_permission_prompt_shows_the_command() -> None:
    p = parse("permission.raw").prompt

    assert p is not None and (p.kind, p.title) == ("permission", "Do you want to proceed?")
    assert [o.label for o in p.options][0::3] == ["Yes", "No"]
    assert p.body is not None and "touch /Users/jdoe1/" in p.body


def test_question_prompt_with_choice_details() -> None:
    p = parse("question.raw").prompt

    assert p is not None and (p.kind, p.title) == ("question", "Chọn màu?")
    assert [(o.label, o.detail, o.needs_text) for o in p.options] == [
        ("Đỏ", "Chọn màu đỏ", False), ("Xanh", "Chọn màu xanh", False),
        ("Type something.", None, True), ("Chat about this", None, False)]
    assert p.options[0].selected


@pytest.mark.parametrize(("name", "mode"), [("idle-auto.raw", "auto"), ("idle-manual.raw", "manual")])
def test_idle_screen_has_a_mode_and_no_prompt(name: str, mode: str) -> None:
    parsed = parse(name)

    assert (parsed.mode, parsed.prompt) == (mode, None)


def test_numbered_list_in_a_reply_above_the_input_box_is_not_a_menu() -> None:
    reply = ["⏺ Các bước:", "  1. Đọc file", "  2. Sửa lỗi"]
    lines = [*reply, "─" * 40, PROMPT_MARK, "─" * 40, "  ⏸ plan mode on"]

    assert prompts.parse(lines) == prompts.Parsed(mode="plan", prompt=None)


def test_options_that_do_not_start_at_one_are_not_a_menu() -> None:
    assert prompts.parse(["Pick", "  2. B", "  3. C"]).prompt is None


@pytest.mark.parametrize(("footer", "mode"), [
    ("  ⏵⏵ accept edits on (shift+tab to cycle)", "acceptEdits"),
    ("  ? for shortcuts", "default"),
    ("  nothing here", None),
])
def test_mode_from_the_footer(footer: str, mode: str | None) -> None:
    assert prompts.parse([PROMPT_MARK, footer]).mode == mode


def test_a_numbered_reply_above_a_tall_footer_is_not_a_menu() -> None:
    lines = ["⏺ Plan:", "  1. Do A", "  2. Do B", "─" * 40, PROMPT_MARK, "─" * 40,
             "  ⏵⏵ auto mode on", "  ⎿ 1 shell", "  ⎿ 2 agents", "  agent a", "  agent b", "  agent c"]

    assert prompts.parse(lines).prompt is None
