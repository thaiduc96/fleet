"""Readable details of a tool call (and its result) for the conversation view.

Claude Code's terminal shows what each tool did: the command, the diff, the
output. The transcript records the same thing as tool_use / tool_result blocks;
this module turns one pair into a `ToolCall`. Unknown tools still show their
input as JSON, so nothing new is hidden.
"""

from __future__ import annotations

import json

from fleetd import markdown
from fleetd.models import Question, Todo, TodoStatus, ToolCall

DETAIL_CHARS = 6000
RESULT_CHARS = 8000
TITLE_CHARS = 140

Json = dict[str, object]


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _s(v: object) -> str:
    return v if isinstance(v, str) else ""


def title(name: str, inp: Json) -> str:
    """One line, as the conversation showed tool calls before: 'Bash: List files'."""
    hint = next((inp[k] for k in ("description", "command", "file_path", "pattern", "prompt", "subject")
                 if isinstance(inp.get(k), str) and inp.get(k)), "")
    hint = str(hint).replace("\n", " ")
    return f"{name}: {hint[:TITLE_CHARS]}" if hint else name


def _prefixed(text: str, mark: str) -> str:
    return "\n".join(f"{mark} {line}" for line in text.split("\n"))


def _edit(path: str, old: str, new: str) -> str:
    return f"{path}\n{_prefixed(old, '-')}\n{_prefixed(new, '+')}"


def detail(name: str, inp: Json) -> str | None:
    """The interesting part of the input, the way a person would want to read it."""
    path = _s(inp.get("file_path")) or _s(inp.get("notebook_path"))
    if name == "Bash":
        out = _s(inp.get("command"))
    elif name == "Edit":
        out = _edit(path, _s(inp.get("old_string")), _s(inp.get("new_string")))
    elif name == "MultiEdit" and isinstance(inp.get("edits"), list):
        edits = [e for e in inp["edits"] if isinstance(e, dict)]
        out = "\n".join(_edit(path, _s(e.get("old_string")), _s(e.get("new_string"))) for e in edits)
    elif name == "Write":
        out = f"{path}\n{_s(inp.get('content'))}"
    elif name == "Read":
        extra = [f"{k}={inp[k]}" for k in ("offset", "limit", "pages") if inp.get(k) is not None]
        out = " ".join([path, *extra])
    elif name in ("Grep", "Glob"):
        out = " ".join(f"{k}={inp[k]}" for k in ("pattern", "path", "glob", "type") if inp.get(k))
    elif name in ("Agent", "Task"):
        out = _s(inp.get("prompt"))
    elif name in ("AskUserQuestion", "TodoWrite", "ExitPlanMode"):
        return None  # shown as questions / todos / plan instead
    else:
        out = json.dumps(inp, ensure_ascii=False, indent=1) if inp else ""
    return _clip(out, DETAIL_CHARS) if out.strip() else None


def _questions(inp: Json) -> list[Question]:
    out: list[Question] = []
    for q in inp.get("questions") or []:
        if not isinstance(q, dict) or not _s(q.get("question")):
            continue
        options = [_s(o.get("label")) if isinstance(o, dict) else _s(o) for o in q.get("options") or []]
        out.append(Question(question=_s(q.get("question")), header=_s(q.get("header")) or None,
                            options=[o for o in options if o]))
    return out


def _todo_status(v: object) -> TodoStatus:
    if v == "in_progress":
        return "in_progress"
    if v == "completed":
        return "completed"
    return "pending"


def _todos(inp: Json) -> list[Todo]:
    out: list[Todo] = []
    for t in inp.get("todos") or []:
        if isinstance(t, dict) and _s(t.get("content")):
            out.append(Todo(content=_s(t["content"]), status=_todo_status(t.get("status"))))
    return out


def result_text(content: object) -> str:
    """tool_result content is a string or a list of text/image blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            parts.append(_s(b.get("text")))
        elif b.get("type") == "image":
            parts.append("[ảnh]")
    return "\n".join(p for p in parts if p)


def describe(block: Json, result: Json | None) -> ToolCall:
    """A tool_use block plus its tool_result block (None while the tool is running)."""
    name = _s(block.get("name")) or "tool"
    raw_input = block.get("input")
    inp: Json = raw_input if isinstance(raw_input, dict) else {}
    plan = _s(inp.get("plan")) if name == "ExitPlanMode" else ""
    return ToolCall(
        name=name,
        detail=detail(name, inp),
        result=_clip(result_text(result.get("content")), RESULT_CHARS) if result is not None else None,
        is_error=bool(result and result.get("is_error") is True),
        questions=_questions(inp) if name == "AskUserQuestion" else [],
        todos=_todos(inp) if name == "TodoWrite" else [],
        plan=markdown.parse(plan) if plan else None,
    )
