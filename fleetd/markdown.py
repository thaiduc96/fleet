"""Markdown → blocks for the dashboard.

Claude answers in GitHub-flavoured Markdown (tables, fenced code, lists…). The
dashboard used to print it verbatim, so a table showed up as rows of "|". This
module parses the subset Claude uses into `Block`/`Inline` models; the browser
only builds DOM nodes from them (never innerHTML), so nothing in a transcript
can inject markup.

Not full CommonMark. The rule that matters: text is never lost — whatever is
not recognised stays plain text.
"""

from __future__ import annotations

import re

from fleetd.models import Align, Block, Inline

MAX_DEPTH = 4  # nested lists/quotes deeper than this are kept as plain text
SAFE_SCHEMES = ("http://", "https://", "mailto:")

FENCE_RE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})\s*([\w.+#-]*)")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
RULE_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|(\d{1,9})[.)])\s+(.*)$")
QUOTE_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
DELIM_CELL_RE = re.compile(r"^\s*(:?)-{1,}(:?)\s*$")
BARE_URL_RE = re.compile(r"https?://[^\s<>()\[\]`]+[^\s<>()\[\]`.,;:!?'\"]")
# href allows one level of balanced parentheses: [x](https://en.wikipedia.org/wiki/A_(b))
LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*((?:[^()\s]|\([^()\s]*\))+)(?:\s+\"[^\"]*\")?\s*\)")


# ---------------------------------------------------------------- inline
def _text(s: str) -> Inline:
    return Inline(type="text", text=s)


def _merge(parts: list[Inline]) -> list[Inline]:
    out: list[Inline] = []
    for p in parts:
        if p.type == "text" and out and out[-1].type == "text":
            out[-1] = _text(out[-1].text + p.text)
        elif p.type != "text" or p.text:
            out.append(p)
    return out


def _closing(s: str, start: int, delim: str) -> int:
    """Index of the closing `delim` for an emphasis span opened just before `start`."""
    i = start
    while True:
        j = s.find(delim, i)
        if j <= start:
            return -1
        if not s[j - 1].isspace() and s[j - 1] != "\\":
            # In "***" closing "**", the inner "*" closes first: take the run's last delimiter.
            while j + len(delim) < len(s) and s[j + len(delim)] == delim[0]:
                j += 1
            return j
        i = j + 1


def parse_inlines(s: str, depth: int = 0) -> list[Inline]:
    out: list[Inline] = []
    buf: list[str] = []
    i, n = 0, len(s)

    def flush() -> None:
        if buf:
            out.append(_text("".join(buf)))
            buf.clear()

    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and not s[i + 1].isalnum():  # escaped punctuation
            buf.append(s[i + 1])
            i += 2
            continue
        if c == "`":
            run = len(s[i:]) - len(s[i:].lstrip("`"))
            end = s.find("`" * run, i + run)
            if end != -1:
                flush()
                out.append(Inline(type="code", text=s[i + run:end].strip(" ") or s[i + run:end]))
                i = end + run
                continue
        if c == "[" and depth < MAX_DEPTH:
            m = LINK_RE.match(s, i)
            if m:
                flush()
                label, href = m.group(1), m.group(2)
                kids = parse_inlines(label, depth + 1) or [_text(href)]
                if href.lower().startswith(SAFE_SCHEMES):
                    out.append(Inline(type="link", href=href, children=kids))
                else:
                    out.extend(kids)  # unsafe scheme (javascript:, data:…): keep the label only
                i = m.end()
                continue
        if c in "*_" and depth < MAX_DEPTH:
            double = s.startswith(c * 2, i)
            delim = c * 2 if double else c
            prev = s[i - 1] if i else " "
            nxt = s[i + len(delim)] if i + len(delim) < n else " "
            # "_" inside a word (snake_case) is never emphasis; an opener must touch text.
            if not nxt.isspace() and not (c == "_" and prev.isalnum()):
                end = _closing(s, i + len(delim), delim)
                after = s[end + len(delim)] if end != -1 and end + len(delim) < n else " "
                if end != -1 and not (c == "_" and after.isalnum()):
                    flush()
                    inner = parse_inlines(s[i + len(delim):end], depth + 1)
                    out.append(Inline(type="strong" if double else "em", children=inner))
                    i = end + len(delim)
                    continue
        if c == "h" and (i == 0 or not s[i - 1].isalnum()):
            m = BARE_URL_RE.match(s, i)
            if m:
                flush()
                out.append(Inline(type="link", href=m.group(0), children=[_text(m.group(0))]))
                i = m.end()
                continue
        buf.append(c)
        i += 1
    flush()
    return _merge(out)


# ---------------------------------------------------------------- tables
def split_row(line: str) -> list[str]:
    """Cells of a table row; '|' inside `code` or escaped as \\| does not split."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if c == "`":
            in_code = not in_code
        if c == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _delimiter(line: str) -> list[Align] | None:
    if "-" not in line or ("|" not in line and line.count("-") < 3):
        return None
    cells = split_row(line)
    aligns: list[Align] = []
    for cell in cells:
        m = DELIM_CELL_RE.match(cell)
        if not m:
            return None
        left, right = bool(m.group(1)), bool(m.group(2))
        aligns.append("center" if left and right else "right" if right else "left" if left else None)
    return aligns


def _is_table_start(lines: list[str], i: int) -> bool:
    return "|" in lines[i] and i + 1 < len(lines) and _delimiter(lines[i + 1]) is not None


def _table(lines: list[str], i: int) -> tuple[Block, int]:
    header = split_row(lines[i])
    aligns = _delimiter(lines[i + 1]) or []
    width = len(header)
    aligns = (aligns + [None] * width)[:width]
    rows: list[list[list[Inline]]] = []
    j = i + 2
    while j < len(lines) and lines[j].strip() and "|" in lines[j]:
        cells = (split_row(lines[j]) + [""] * width)[:width]
        rows.append([parse_inlines(c) for c in cells])
        j += 1
    return Block(type="table", align=aligns, header=[parse_inlines(c) for c in header], rows=rows), j


# ---------------------------------------------------------------- blocks
def _starts_block(lines: list[str], i: int) -> bool:
    line = lines[i]
    return bool(FENCE_RE.match(line) or HEADING_RE.match(line) or RULE_RE.match(line)
                or LIST_RE.match(line) or QUOTE_RE.match(line) or _is_table_start(lines, i))


def _code(lines: list[str], i: int) -> tuple[Block, int]:
    m = FENCE_RE.match(lines[i])
    assert m is not None
    fence = m.group(2)
    body: list[str] = []
    j = i + 1
    while j < len(lines):
        stripped = lines[j].strip()
        if stripped.startswith(fence[0] * len(fence)) and set(stripped) <= {fence[0]}:
            j += 1
            break
        body.append(lines[j])
        j += 1
    return Block(type="code", lang=m.group(3) or None, text="\n".join(body)), j


def _list(lines: list[str], i: int, depth: int) -> tuple[Block, int]:
    first = LIST_RE.match(lines[i])
    assert first is not None
    base = len(first.group(1))
    ordered = first.group(3) is not None
    start = int(first.group(3)) if ordered and first.group(3) else None
    items: list[list[str]] = []
    j = i
    while j < len(lines):
        line = lines[j]
        m = LIST_RE.match(line)
        if m and len(m.group(1)) == base and (m.group(3) is not None) == ordered:
            items.append([m.group(4)])
            j += 1
            continue
        if not line.strip():
            nxt = next((k for k in range(j + 1, len(lines)) if lines[k].strip()), None)
            if nxt is None:
                break
            nm = LIST_RE.match(lines[nxt])
            indent = len(lines[nxt]) - len(lines[nxt].lstrip())
            if (nm and len(nm.group(1)) == base) or indent > base:
                items[-1].append("")
                j += 1
                continue
            break
        indent = len(line) - len(line.lstrip())
        if indent > base:
            items[-1].append(line[min(indent, base + 2):] if indent >= base + 2 else line.lstrip())
            j += 1
            continue
        if _starts_block(lines, j):
            break
        items[-1].append(line.strip())  # lazy continuation of the item's paragraph
        j += 1
    parsed = [parse_blocks("\n".join(item), depth + 1) for item in items]
    return Block(type="list", ordered=ordered, start=start, items=parsed), j


def _paragraph(lines: list[str], i: int) -> tuple[Block, int]:
    body = [lines[i].strip()]
    j = i + 1
    while j < len(lines) and lines[j].strip() and not _starts_block(lines, j):
        body.append(lines[j].strip())
        j += 1
    return Block(type="paragraph", inlines=parse_inlines("\n".join(body))), j


def parse_blocks(text: str, depth: int = 0) -> list[Block]:
    lines = text.replace("\r\n", "\n").replace("\t", "    ").split("\n")
    if depth > MAX_DEPTH:
        return [Block(type="paragraph", inlines=[_text(text)])] if text.strip() else []
    out: list[Block] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if FENCE_RE.match(line):
            block, i = _code(lines, i)
        elif m := HEADING_RE.match(line):
            block = Block(type="heading", level=len(m.group(1)), inlines=parse_inlines(m.group(2)))
            i += 1
        elif RULE_RE.match(line):
            block = Block(type="rule")
            i += 1
        elif _is_table_start(lines, i):
            block, i = _table(lines, i)
        elif QUOTE_RE.match(line):
            quoted: list[str] = []
            while i < len(lines) and (q := QUOTE_RE.match(lines[i])):
                quoted.append(q.group(1))
                i += 1
            block = Block(type="quote", children=parse_blocks("\n".join(quoted), depth + 1))
        elif LIST_RE.match(line):
            block, i = _list(lines, i, depth)
        else:
            block, i = _paragraph(lines, i)
        out.append(block)
    return out


def parse(text: str) -> list[Block]:
    """Blocks for a whole message."""
    return parse_blocks(text)


# ---------------------------------------------------------------- plain text
def _plain_inlines(inlines: list[Inline]) -> str:
    return "".join(i.text if i.type in ("text", "code") else _plain_inlines(i.children) for i in inlines)


def _plain_block(b: Block) -> str:
    if b.type in ("heading", "paragraph"):
        return _plain_inlines(b.inlines)
    if b.type == "code":
        return b.text or ""
    if b.type == "list":
        return "\n".join("• " + " ".join(_plain_block(x) for x in item) for item in b.items)
    if b.type == "quote":
        return "\n".join(_plain_block(x) for x in b.children)
    if b.type == "table":
        rows = [b.header, *b.rows]
        return "\n".join(" · ".join(_plain_inlines(c) for c in row) for row in rows)
    return ""


def to_plain(text: str) -> str:
    """Readable one-paragraph-per-line text without Markdown markers (for previews)."""
    return "\n".join(p for p in (_plain_block(b) for b in parse(text)) if p.strip())
