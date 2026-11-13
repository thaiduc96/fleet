"""Read a Claude Code terminal screen the way a person would: which menu is open, which mode is on.

The TUI is not an API, so every rule here is deliberately loose and falls back to
"no prompt" / "mode unknown" instead of guessing. Fleet only ever presses a key
for an option it actually found on the screen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fleetd.models import Prompt, PromptKind, PromptOption, SessionMode

PROMPT_MARK = "\u276f"  # the TUI's cursor/prompt glyph, drawn before the highlighted choice
OPTION_RE = re.compile(rf"^\s*(?:│\s*)?({PROMPT_MARK}\s*)?(\d{{1,2}})\.\s+(\S.*?)\s*$")
RULE_RE = re.compile(r"^\s*[─━▔▁╌]{12,}")
INPUT_BOX_RE = re.compile(rf"^{PROMPT_MARK}(\s.*)?$")
NEEDS_TEXT_RE = re.compile(r"(?i)^(tell claude what to change|type something\.?|no, and tell claude)")
MODE_RE = re.compile(r"(?i)\b(auto|manual|plan|accept edits|bypass permissions|don't ask)(?: mode)? on\b")
MODE_IDS: dict[str, SessionMode] = {
    "auto": "auto", "manual": "manual", "plan": "plan", "accept edits": "acceptEdits",
    "bypass permissions": "bypassPermissions", "don't ask": "dontAsk",
}
DEFAULT_MODE_HINT = "? for shortcuts"
MAX_BODY_LINES = 60
INPUT_BOX_LOOKBACK = 6


@dataclass(frozen=True)
class Parsed:
    mode: SessionMode | None
    prompt: Prompt | None


def parse(lines: list[str]) -> Parsed:
    return Parsed(mode=_mode(lines), prompt=_prompt(lines))


def _mode(lines: list[str]) -> SessionMode | None:
    for line in reversed(lines):
        if m := MODE_RE.search(line):
            return MODE_IDS[m.group(1).lower()]
        if DEFAULT_MODE_HINT in line:
            return "default"
    return None


def _has_input_box(lines: list[str]) -> bool:
    """An input box line (PROMPT_MARK alone) means Claude waits for a message, not a menu answer."""
    tail = [ln.strip() for ln in lines if ln.strip()][-INPUT_BOX_LOOKBACK:]
    return any(INPUT_BOX_RE.match(ln) and not OPTION_RE.match(ln) for ln in tail)


def _option_group(lines: list[str]) -> list[int] | None:
    """Line numbers of the bottom-most run of options numbered 1, 2, …, n."""
    found = [(k, int(m.group(2))) for k, ln in enumerate(lines) if (m := OPTION_RE.match(ln))]
    if not found:
        return None
    group: list[int] = []
    expected = found[-1][1]
    for k, number in reversed(found):
        if number != expected:
            continue
        group.append(k)
        if number == 1:
            return sorted(group)
        expected -= 1
    return None


def _clean(line: str) -> str:
    return line.strip().lstrip("│").strip()


def _kind(title: str, body: str, labels: list[str]) -> PromptKind:
    text = f"{title}\n{body}".lower()
    if "ready to execute" in text or "claude's plan" in text:
        return "plan"
    if "do you want to" in text:
        return "permission"
    if any(lb.lower().startswith("type something") for lb in labels):
        return "question"
    return "menu"


def _prompt(lines: list[str]) -> Prompt | None:
    if _has_input_box(lines):
        return None
    group = _option_group(lines)
    if not group:
        return None
    options = [_option(lines, k, group) for k in group]
    title_at = next((j for j in range(group[0] - 1, -1, -1)
                     if lines[j].strip() and not RULE_RE.match(lines[j])), None)
    title = _clean(lines[title_at]) if title_at is not None else ""
    body = _body(lines, title_at) if title_at is not None else None
    kind = _kind(title, body or "", [o.label for o in options])
    return Prompt(kind=kind, title=title, body=body, options=options)


def _is_section_rule(line: str) -> bool:
    """A solid rule ends a section; dashed ╌ rules sit inside a plan or command preview."""
    return bool(RULE_RE.match(line)) and not line.lstrip().startswith("╌")


def _option(lines: list[str], k: int, group: list[int]) -> PromptOption:
    m = OPTION_RE.match(lines[k])
    assert m is not None  # group only holds option lines
    label = m.group(3)
    end = next((j for j in group if j > k), len(lines))
    detail = None
    for j in range(k + 1, end):  # the grey line under a choice, never past a rule
        if RULE_RE.match(lines[j]):
            break
        if lines[j].strip():
            detail = _clean(lines[j])
            break
    return PromptOption(key=m.group(2), label=label, detail=detail if end < len(lines) else None,
                        selected=bool(m.group(1)), needs_text=bool(NEEDS_TEXT_RE.match(label)))


def _body(lines: list[str], title_at: int) -> str | None:
    """The section above the title: the command, the plan, the question header."""
    j = title_at - 1
    while j >= 0 and (not lines[j].strip() or _is_section_rule(lines[j])):
        j -= 1
    end = j + 1
    while j >= 0 and not _is_section_rule(lines[j]):
        j -= 1
    kept = [_clean(ln) for ln in lines[j + 1:end]]
    body_lines = [ln for ln in kept if ln and not set(ln) <= set("╌─")][-MAX_BODY_LINES:]
    return "\n".join(body_lines) or None
