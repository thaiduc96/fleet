"""Tool calls shown like Claude Code's terminal: what ran, and what came back."""

import pytest

from fleetd.models import Question, Todo
from fleetd.tool_calls import RESULT_CHARS, describe, result_text, title


def test_bash_shows_the_command_and_its_output() -> None:
    call = describe({"name": "Bash", "input": {"command": "ls -la", "description": "List"}},
                    {"content": "total 0"})

    assert (call.detail, call.result, call.is_error) == ("ls -la", "total 0", False)


def test_edit_shows_removed_and_added_lines() -> None:
    edit = {"file_path": "/a.py", "old_string": "x = 1", "new_string": "x = 2\ny = 3"}

    call = describe({"name": "Edit", "input": edit}, None)

    assert call.detail == "/a.py\n- x = 1\n+ x = 2\n+ y = 3"


def test_a_running_tool_has_no_result_yet() -> None:
    assert describe({"name": "Bash", "input": {"command": "sleep 9"}}, None).result is None


def test_a_failed_tool_is_marked_as_error() -> None:
    assert describe({"name": "Bash", "input": {}}, {"content": "boom", "is_error": True}).is_error


def test_long_output_is_clipped() -> None:
    call = describe({"name": "Bash", "input": {}}, {"content": "x" * (RESULT_CHARS * 2)})

    assert call.result is not None and len(call.result) == RESULT_CHARS and call.result.endswith("…")


def test_ask_user_question_lists_questions_and_options() -> None:
    call = describe({"name": "AskUserQuestion", "input": {"questions": [
        {"question": "Chọn cách nào?", "header": "Cách", "options": [{"label": "A"}, {"label": "B"}]},
    ]}}, None)

    expected = Question(question="Chọn cách nào?", header="Cách", options=["A", "B"])
    assert (call.questions, call.detail) == ([expected], None)


def test_todo_write_lists_todos_with_a_safe_status() -> None:
    call = describe({"name": "TodoWrite", "input": {"todos": [
        {"content": "viết test", "status": "completed"}, {"content": "sửa lỗi", "status": "weird"},
    ]}}, None)

    assert call.todos == [Todo(content="viết test", status="completed"), Todo(content="sửa lỗi")]


def test_exit_plan_mode_carries_the_plan_as_markdown() -> None:
    call = describe({"name": "ExitPlanMode", "input": {"plan": "# Plan\n- bước 1"}}, None)

    assert call.plan is not None and [b.type for b in call.plan] == ["heading", "list"]


def test_unknown_tools_show_their_input_as_json() -> None:
    assert describe({"name": "mcp__x__y", "input": {"q": "cat"}}, None).detail == '{\n "q": "cat"\n}'


def test_result_with_text_and_image_blocks() -> None:
    assert result_text([{"type": "text", "text": "ok"}, {"type": "image", "source": {}}]) == "ok\n[ảnh]"


@pytest.mark.parametrize(("inp", "expected"), [
    ({"description": "List files", "command": "ls"}, "Bash: List files"),
    ({"command": "a\nb"}, "Bash: a b"),
    ({}, "Bash"),
])
def test_title_is_one_line(inp: dict[str, object], expected: str) -> None:
    assert title("Bash", inp) == expected
