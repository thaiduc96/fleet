"""Read Claude Code transcripts (~/.claude/projects/<project>/<session>.jsonl).

The format is internal to Claude Code and may change, so every field is read
defensively and unknown records are skipped. Only the tail of each file is
parsed; that is where the freshest title / prompt / reply live.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fleetd import markdown, tool_calls
from fleetd.models import Message, TranscriptSummary

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SUMMARY_TAIL = 512 * 1024
TRANSCRIPT_TAIL = 8 * 1024 * 1024  # long sessions log big request records between messages
USER_CHARS = 20000
ASSISTANT_CHARS = 50000

Record = dict[str, object]


def _read_tail(path: Path, nbytes: int) -> list[Record]:
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > nbytes:
            f.seek(size - nbytes)
            f.readline()  # drop partial line
        raw = f.read()
    out: list[Record] = []
    for line in raw.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _content(rec: Record) -> object:
    msg = rec.get("message")
    return msg.get("content") if isinstance(msg, dict) else None


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(b.get("text", "")) for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


def _is_human(rec: Record) -> bool:
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return False
    origin = rec.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return False
    content = _content(rec)
    if isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    ):
        return False
    return bool(_text_of(content).strip())


def _str(v: object) -> str | None:
    return v if isinstance(v, str) and v else None


def clip(s: str | None, n: int) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    return s if len(s) <= n else s[: n - 1] + "…"


def summarize(path: Path) -> TranscriptSummary:
    s = TranscriptSummary(session_id=path.stem, path=str(path))
    for r in _read_tail(path, SUMMARY_TAIL):
        t = r.get("type")
        if t in ("ai-title", "agent-name", "custom-title"):
            title = _str(r.get("aiTitle")) or _str(r.get("agentName")) or _str(r.get("customTitle"))
            s.title = title or s.title
        elif t == "last-prompt" and _str(r.get("lastPrompt")):
            s.last_prompt = _str(r.get("lastPrompt"))
        elif t == "cost-state" and isinstance(r.get("totalCostUSD"), (int, float)):
            s.cost_usd = float(r["totalCostUSD"])
        elif t == "continued-in":
            s.continued_in = _str(r.get("continuedInSessionId"))
        elif t == "assistant" and not r.get("isSidechain"):
            s.last_reply = markdown.to_plain(_text_of(_content(r))).strip() or s.last_reply
        elif _is_human(r):
            s.last_prompt = _text_of(_content(r))
        s.cwd = _str(r.get("cwd")) or s.cwd
        s.git_branch = _str(r.get("gitBranch")) or s.git_branch
        s.session_kind = _str(r.get("sessionKind")) or s.session_kind
        s.updated_at = _str(r.get("timestamp")) or s.updated_at
    if not s.updated_at:
        s.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))
    s.last_prompt = clip(s.last_prompt, 300)
    s.last_reply = clip(s.last_reply, 400)
    return s


@dataclass(frozen=True)
class Conversation:
    messages: list[Message]
    model: str | None = None
    context_tokens: int | None = None  # prompt size of the latest reply


def _tool_results(records: list[Record]) -> dict[str, Record]:
    """tool_use_id → its tool_result block (they arrive in the next user record)."""
    out: dict[str, Record] = {}
    for r in records:
        content = _content(r)
        if r.get("type") != "user" or not isinstance(content, list):
            continue
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                continue
            if isinstance(use_id := b.get("tool_use_id"), str):
                out[use_id] = b
    return out


def _context_tokens(usage: object) -> int | None:
    if not isinstance(usage, dict):
        return None
    keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    total = sum(v for k in keys if isinstance(v := usage.get(k), int))
    return total or None


def conversation(path: Path, limit: int = 60) -> Conversation:
    """Human prompts, assistant text and tool calls (with their results), newest last."""
    records = [r for r in _read_tail(path, TRANSCRIPT_TAIL) if not r.get("isSidechain")]
    results = _tool_results(records)
    out: list[Message] = []
    model: str | None = None
    context: int | None = None
    for n, r in enumerate(records):
        ts = _str(r.get("timestamp"))
        rid = _str(r.get("uuid")) or f"r{n}"
        if _is_human(r):
            text = clip(_text_of(_content(r)), USER_CHARS) or ""
            out.append(Message(role="user", id=rid, ts=ts, text=text))
            continue
        content = _content(r)
        if r.get("type") != "assistant" or not isinstance(content, list):
            continue
        msg = r.get("message")
        if isinstance(msg, dict):
            model = _str(msg.get("model")) or model
            context = _context_tokens(msg.get("usage")) or context
        for k, b in enumerate(content):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and str(b.get("text", "")).strip():
                text = clip(str(b["text"]), ASSISTANT_CHARS) or ""
                out.append(Message(role="assistant", id=f"{rid}:{k}", ts=ts, text=text,
                                   blocks=markdown.parse(text)))
            elif b.get("type") == "tool_use":
                inp = b.get("input")
                name = _str(b.get("name")) or "tool"
                result = results.get(use_id) if isinstance(use_id := b.get("id"), str) else None
                out.append(Message(role="tool", id=f"{rid}:{k}", ts=ts,
                                   text=tool_calls.title(name, inp if isinstance(inp, dict) else {}),
                                   tool=tool_calls.describe(b, result)))
    return Conversation(messages=out[-limit:], model=model, context_tokens=context)


def messages(path: Path, limit: int = 60) -> list[Message]:
    return conversation(path, limit).messages


def version(path: Path) -> str:
    """Changes whenever the file does; cheap enough to check on every poll."""
    st = path.stat()
    return f"{st.st_mtime_ns}-{st.st_size}"


class TranscriptIndex:
    """Finds and summarizes transcripts, caching by (mtime, size)."""

    def __init__(self, claude_home: Path) -> None:
        self.projects: Path = claude_home / "projects"
        self._cache: dict[Path, tuple[float, int, TranscriptSummary]] = {}
        self._lock = threading.Lock()

    def find(self, session_id: str) -> Path | None:
        if not UUID_RE.match(session_id or ""):
            return None
        return next(iter(self.projects.glob(f"*/{session_id}.jsonl")), None)

    def summary(self, path: Path) -> TranscriptSummary:
        st = path.stat()
        with self._lock:
            hit = self._cache.get(path)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
        info = summarize(path)
        with self._lock:
            self._cache[path] = (st.st_mtime, st.st_size, info)
        return info

    def summary_for(self, session_id: str | None) -> TranscriptSummary | None:
        p = self.find(session_id or "")
        return self.summary(p) if p else None

    def recent(self, days: float = 7, limit: int = 200) -> list[TranscriptSummary]:
        cutoff = time.time() - days * 86400
        files: list[tuple[float, Path]] = []
        for p in self.projects.glob("*/*.jsonl"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m >= cutoff and UUID_RE.match(p.stem):
                files.append((m, p))
        files.sort(reverse=True)
        out: list[TranscriptSummary] = []
        for _, p in files[:limit]:
            try:
                out.append(self.summary(p))
            except OSError:
                continue
        return out
