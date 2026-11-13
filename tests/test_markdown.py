from __future__ import annotations

import pytest

from fleetd import markdown
from fleetd.models import Block, Inline

COMPARISON = """Máy công ty đã cài thành công.

| | 🏠 Nhà | 💼 Công ty |
|---|:---:|---:|
| Fleet.app | 1.0.0 | **1.0.0** |
| `apiLevel` | 6 | 6 |

Mình đã kiểm tra:"""


def plain(inlines: list[Inline]) -> str:
    return "".join(i.text if i.type in ("text", "code") else plain(i.children) for i in inlines)


def only(blocks: list[Block], kind: str) -> Block:
    found = [b for b in blocks if b.type == kind]
    assert len(found) == 1, blocks
    return found[0]


def test_table_from_a_real_reply_has_header_rows_and_alignment() -> None:
    table = only(markdown.parse(COMPARISON), "table")
    assert [plain(c) for c in table.header] == ["", "🏠 Nhà", "💼 Công ty"]
    assert table.align == [None, "center", "right"]
    assert [[plain(c) for c in row] for row in table.rows] == [
        ["Fleet.app", "1.0.0", "1.0.0"], ["apiLevel", "6", "6"]]
    assert table.rows[0][2][0].type == "strong"
    assert table.rows[1][0][0].type == "code"


def test_text_around_a_table_stays_paragraphs() -> None:
    kinds = [b.type for b in markdown.parse(COMPARISON)]
    assert kinds == ["paragraph", "table", "paragraph"]


def test_pipe_inside_code_or_escaped_does_not_split_a_cell() -> None:
    table = only(markdown.parse("| a | b |\n|---|---|\n| `x|y` | c \\| d |"), "table")
    assert [plain(c) for c in table.rows[0]] == ["x|y", "c | d"]


def test_short_and_long_table_rows_are_padded_and_cut_to_header_width() -> None:
    table = only(markdown.parse("| a | b |\n|---|---|\n| 1 |\n| 1 | 2 | 3 |"), "table")
    assert [[plain(c) for c in r] for r in table.rows] == [["1", ""], ["1", "2"]]


@pytest.mark.parametrize(("source", "lang"), [("```python\nx = 1\n```", "python"), ("```\nx = 1\n```", None)])
def test_fenced_code_keeps_text_and_language(source: str, lang: str | None) -> None:
    code = only(markdown.parse(source), "code")
    assert (code.lang, code.text) == (lang, "x = 1")


def test_markdown_inside_a_code_fence_is_not_parsed() -> None:
    code = only(markdown.parse("```\n| a | b |\n|---|---|\n**x**\n```"), "code")
    assert code.text == "| a | b |\n|---|---|\n**x**"


def test_unclosed_fence_keeps_everything_to_the_end() -> None:
    assert only(markdown.parse("```sh\nls\npwd"), "code").text == "ls\npwd"


def test_nested_list_items_hold_their_own_list() -> None:
    lst = only(markdown.parse("- one\n  - inner\n- two"), "list")
    assert not lst.ordered and len(lst.items) == 2
    assert [b.type for b in lst.items[0]] == ["paragraph", "list"]


def test_ordered_list_keeps_its_start_number() -> None:
    lst = only(markdown.parse("3. c\n4. d"), "list")
    assert (lst.ordered, lst.start, len(lst.items)) == (True, 3, 2)


def test_headings_quotes_and_rules() -> None:
    kinds = [(b.type, b.level) for b in markdown.parse("## Title\n\n> quoted\n\n---")]
    assert kinds == [("heading", 2), ("quote", None), ("rule", None)]


def test_nested_inline_emphasis() -> None:
    (para,) = markdown.parse("**bold *and italic***")
    strong = para.inlines[0]
    assert strong.type == "strong" and strong.children[-1].type == "em"


def test_snake_case_and_lone_stars_stay_text() -> None:
    (para,) = markdown.parse("call update_all_machines with 2 * 3 = 6")
    assert [i.type for i in para.inlines] == ["text"]
    assert plain(para.inlines) == "call update_all_machines with 2 * 3 = 6"


def test_links_with_safe_schemes_are_kept() -> None:
    (para,) = markdown.parse("see [release](https://example.com/r) or https://example.com/x.")
    links = [i for i in para.inlines if i.type == "link"]
    assert [link.href for link in links] == ["https://example.com/r", "https://example.com/x"]
    assert plain(para.inlines).endswith(".")


@pytest.mark.parametrize("href", ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd"])
def test_unsafe_link_schemes_keep_only_the_label(href: str) -> None:
    (para,) = markdown.parse(f"[click]({href})")
    assert [i.type for i in para.inlines] == ["text"] and plain(para.inlines) == "click"


@pytest.mark.parametrize(
    "source", ["**not closed", "`open code", "[label](", "| lonely | pipes |", "<b>html</b>"])
def test_broken_markdown_keeps_all_text(source: str) -> None:
    (para,) = markdown.parse(source)
    assert plain(para.inlines) == source


def test_deep_nesting_is_bounded() -> None:
    blocks = markdown.parse("> " * 50 + "deep")
    assert blocks and blocks[0].type == "quote"


def test_to_plain_strips_markers_for_previews() -> None:
    text = markdown.to_plain(COMPARISON)
    assert "|" not in text and "**" not in text and "`" not in text
    assert "Fleet.app · 1.0.0 · 1.0.0" in text
