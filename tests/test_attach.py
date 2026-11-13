"""Answering menus through the session's terminal: exactly which keys get pressed, and when not."""

from pathlib import Path

import pytest

from fleetd.attach import ENTER, ESC, MAX_MODE_STEPS, PASTE_END, PASTE_START, SHIFT_TAB, Attacher, AttachError
from fleetd.prompts import PROMPT_MARK

FIXTURES = Path(__file__).parent / "fixtures" / "screens"
CLEAR = "\x1b[2J\x1b[H"
IDLE_AUTO = (FIXTURES / "idle-auto.raw").read_bytes()
IDLE_MANUAL = (FIXTURES / "idle-manual.raw").read_bytes()
PLAN = (FIXTURES / "plan.raw").read_bytes()
QUESTION = (FIXTURES / "question.raw").read_bytes()


def screen(*lines: str) -> bytes:
    return (CLEAR + "\r\n".join(lines)).encode()


QUESTION_ON_2 = screen("Chọn màu?", "  1. Đỏ", f"{PROMPT_MARK} 2. Xanh", "  3. Type something.")


class FakeTty:
    """Plays back a screen on attach, and another after each key that has a scripted reply."""

    def __init__(self, initial: bytes, replies: dict[str, bytes] | None = None) -> None:
        self.pending = [initial]
        self.replies = replies or {}
        self.written: list[str] = []
        self.closed = False

    def read(self, timeout: float) -> bytes:
        return self.pending.pop(0) if self.pending else b""

    def write(self, data: bytes) -> None:
        keys = data.decode()
        self.written.append(keys)
        if keys in self.replies:
            self.pending.append(self.replies[keys])

    def close(self) -> None:
        self.closed = True


def attacher(tty: FakeTty) -> Attacher:
    return Attacher(lambda short_id: tty)


def test_screen_reads_mode_and_closes_the_terminal() -> None:
    tty = FakeTty(IDLE_AUTO)

    view = attacher(tty).screen("5099ea80")

    assert (view.mode, view.prompt, tty.written, tty.closed) == ("auto", None, [], True)


def test_approving_a_plan_presses_only_its_digit() -> None:
    tty = FakeTty(PLAN, {"2": CLEAR.encode() + IDLE_MANUAL})

    view = attacher(tty).choose("a199faa5", "2", None)

    assert (tty.written, view.prompt) == (["2"], None)


def test_a_digit_that_only_moves_the_highlight_is_confirmed_with_enter() -> None:
    tty = FakeTty(QUESTION, {"2": QUESTION_ON_2, ENTER: CLEAR.encode() + IDLE_AUTO})

    attacher(tty).choose("5099ea80", "2", None)

    assert tty.written == ["2", ENTER]


def test_feedback_is_typed_after_the_digit_then_entered() -> None:
    tty = FakeTty(PLAN)

    attacher(tty).choose("a199faa5", "3", "Thêm bước:\x1b[Z kiểm tra\nlại")

    assert tty.written == ["3", "Thêm bước:[Z kiểm tra lại", ENTER]


def test_an_option_that_needs_text_refuses_an_empty_answer() -> None:
    tty = FakeTty(PLAN)

    with pytest.raises(AttachError) as err:
        attacher(tty).choose("a199faa5", "3", "  ")

    assert (err.value.status, tty.written, tty.closed) == (400, [], True)


def test_an_option_no_longer_on_screen_is_never_pressed() -> None:
    tty = FakeTty(PLAN)

    with pytest.raises(AttachError) as err:
        attacher(tty).choose("a199faa5", "4", None)

    assert (err.value.status, tty.written) == (409, [])


def test_choosing_when_nothing_is_asked_is_refused() -> None:
    tty = FakeTty(IDLE_AUTO)

    with pytest.raises(AttachError) as err:
        attacher(tty).choose("5099ea80", "1", None)

    assert (err.value.status, tty.written) == (409, [])


def test_interrupt_presses_escape() -> None:
    tty = FakeTty(IDLE_AUTO)

    attacher(tty).interrupt("5099ea80")

    assert tty.written == [ESC]


def test_switching_mode_cycles_until_the_footer_shows_it() -> None:
    tty = FakeTty(IDLE_AUTO, {SHIFT_TAB: CLEAR.encode() + IDLE_MANUAL})

    view = attacher(tty).set_mode("5099ea80", "manual")

    assert (tty.written, view.mode) == ([SHIFT_TAB], "manual")


def test_switching_to_the_current_mode_presses_nothing() -> None:
    tty = FakeTty(IDLE_AUTO)

    attacher(tty).set_mode("5099ea80", "auto")

    assert tty.written == []


def test_a_mode_that_never_shows_up_is_reported_after_a_full_cycle() -> None:
    tty = FakeTty(IDLE_AUTO)

    with pytest.raises(AttachError) as err:
        attacher(tty).set_mode("5099ea80", "plan")

    assert (err.value.status, tty.written) == (409, [SHIFT_TAB] * MAX_MODE_STEPS)


def test_mode_is_not_switched_while_a_menu_is_open() -> None:
    tty = FakeTty(PLAN)

    with pytest.raises(AttachError):
        attacher(tty).set_mode("a199faa5", "auto")

    assert tty.written == []


def test_a_typed_message_is_pasted_then_sent() -> None:
    tty = FakeTty(IDLE_AUTO)

    attacher(tty).type_message("5099ea80", "dòng 1\ndòng 2\x07")

    assert tty.written == [f"{PASTE_START}dòng 1\ndòng 2{PASTE_END}", ENTER]


def test_typing_while_a_menu_is_open_is_refused() -> None:
    tty = FakeTty(QUESTION)

    with pytest.raises(AttachError):
        attacher(tty).type_message("5099ea80", "hi")

    assert tty.written == []


def test_a_character_split_across_reads_is_decoded_whole() -> None:
    raw = screen("Chọn màu?", f"{PROMPT_MARK} 1. Đỏ", "  2. Xanh")
    cut = raw.index("Đ".encode()) + 1
    tty = FakeTty(raw[:cut])
    tty.pending.append(raw[cut:])

    view = attacher(tty).screen("5099ea80")

    assert view.prompt is not None and view.prompt.options[0].label == "Đỏ"
