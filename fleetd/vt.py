"""A tiny terminal screen emulator: turns a TUI's output stream into the text on screen.

`claude attach` redraws Claude Code's interface with cursor movement and partial
updates, so the raw stream is not readable text. Fleet needs to *read the screen*
(which menu is open, which mode is on) the way a person looking at the terminal
would. Only what that TUI uses is handled: cursor movement, erasing, wide
characters, scrolling. Colours and other attributes are ignored.
"""

from __future__ import annotations

import re
import unicodedata

CSI_RE = re.compile(r"\x1b\[([?>=!]?)([0-9;:]*)([ -/]*)([@-~])")
OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
ESC_OTHER_RE = re.compile(r"\x1b([@-Z\\-_]|[()][0-9A-Za-z])")
TAB = 8
Row = list[str | None]  # None: right half of a wide character
ALT_SCREEN_MODES = ("1049", "47", "1047")


def cell_width(ch: str) -> int:
    """Columns a character occupies: 2 for wide (CJK, most emoji), 0 for combining marks."""
    if unicodedata.combining(ch) or ch in "‍️︎":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class Screen:
    def __init__(self, cols: int = 100, rows: int = 40) -> None:
        self.cols = cols
        self.rows = rows
        self.grid: list[Row] = [self._blank() for _ in range(rows)]
        self.x = 0
        self.y = 0
        self.saved = (0, 0)

    def _blank(self) -> Row:
        return [" "] * self.cols

    # ------------------------------------------------------------ output
    def lines(self) -> list[str]:
        """The screen as text, one string per row, trailing spaces removed."""
        return ["".join(c for c in row if c is not None).rstrip() for row in self.grid]

    def text(self) -> str:
        return "\n".join(self.lines()).rstrip("\n")

    # ------------------------------------------------------------ input
    def feed(self, data: str) -> None:
        i, n = 0, len(data)
        while i < n:
            ch = data[i]
            if ch == "\x1b":
                i = self._escape(data, i)
                continue
            i += 1
            if ch == "\r":
                self.x = 0
            elif ch == "\n":
                self._line_feed()
            elif ch == "\b":
                self.x = max(0, self.x - 1)
            elif ch == "\t":
                self.x = min(self.cols - 1, (self.x // TAB + 1) * TAB)
            elif ch >= " ":
                self._put(ch)

    # ------------------------------------------------------------ helpers
    def _put(self, ch: str) -> None:
        width = cell_width(ch)
        if width == 0:  # combining mark: attach to the previous cell
            row = self.grid[self.y]
            px = self.x - 1
            while px >= 0 and row[px] is None:
                px -= 1
            cell = row[px] if px >= 0 else None
            if cell is not None:
                row[px] = cell + ch
            return
        if self.x + width > self.cols:
            self.x = 0
            self._line_feed()
        self.grid[self.y][self.x] = ch
        if width == 2:
            self.grid[self.y][self.x + 1] = None
        self.x += width

    def _line_feed(self) -> None:
        if self.y == self.rows - 1:
            self.grid.pop(0)
            self.grid.append(self._blank())
        else:
            self.y += 1

    def _clear(self) -> None:
        self.grid = [self._blank() for _ in range(self.rows)]

    def _erase_line(self, mode: int) -> None:
        row = self.grid[self.y]
        spans = {0: (self.x, self.cols), 1: (0, self.x + 1), 2: (0, self.cols)}
        start, end = spans.get(mode, spans[0])
        for k in range(start, min(end, self.cols)):
            row[k] = " "

    def _erase_display(self, mode: int) -> None:
        if mode in (2, 3):
            self._clear()
            return
        self._erase_line(mode)
        rows = range(self.y + 1, self.rows) if mode == 0 else range(0, self.y)
        for r in rows:
            self.grid[r] = self._blank()

    def _move(self, x: int, y: int) -> None:
        self.x = min(max(x, 0), self.cols - 1)
        self.y = min(max(y, 0), self.rows - 1)

    def _escape(self, data: str, i: int) -> int:
        if m := CSI_RE.match(data, i):
            self._csi(m.group(1), m.group(2), m.group(4))
            return m.end()
        if m := OSC_RE.match(data, i):
            return m.end()
        if data.startswith("\x1b7", i):
            self.saved = (self.x, self.y)
            return i + 2
        if data.startswith("\x1b8", i):
            self._move(*self.saved)
            return i + 2
        if m := ESC_OTHER_RE.match(data, i):
            return m.end()
        return i + 1  # lone or unknown ESC: drop it

    def _csi(self, private: str, raw: str, final: str) -> None:
        params = [int(p) if p.isdigit() else 0 for p in raw.replace(":", ";").split(";")] if raw else []

        def arg(k: int = 0, default: int = 1) -> int:
            return params[k] if len(params) > k and params[k] else default

        if private == "?":
            if final in "hl" and any(p in ALT_SCREEN_MODES for p in raw.split(";")):
                self._clear()
                self._move(0, 0)
            return
        if final == "A":
            self._move(self.x, self.y - arg())
        elif final == "B" or final == "e":
            self._move(self.x, self.y + arg())
        elif final == "C" or final == "a":
            self._move(self.x + arg(), self.y)
        elif final == "D":
            self._move(self.x - arg(), self.y)
        elif final == "E":
            self._move(0, self.y + arg())
        elif final == "F":
            self._move(0, self.y - arg())
        elif final in "G`":
            self._move(arg() - 1, self.y)
        elif final == "d":
            self._move(self.x, arg() - 1)
        elif final in "Hf":
            self._move(arg(1) - 1, arg(0) - 1)
        elif final == "J":
            self._erase_display(arg(0, 0))
        elif final == "K":
            self._erase_line(arg(0, 0))
        elif final == "X":
            row = self.grid[self.y]
            for k in range(self.x, min(self.x + arg(), self.cols)):
                row[k] = " "
        elif final == "s":
            self.saved = (self.x, self.y)
        elif final == "u":
            self._move(*self.saved)
        # m (colours), r (scroll region), h/l (modes), etc.: no effect on the text
