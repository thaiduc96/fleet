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
from pathlib import Path

from fleetd.models import Message, TranscriptSummary

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SUMMARY_TAIL = 512 * 1024
TRANSCRIPT_TAIL = 4 * 1024 * 1024

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


def _tool_line(block: dict[str, object]) -> str:
    name = str(block.get("name", "tool"))
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    hint = next((inp[k] for k in ("description", "command", "file_path", "prompt") if inp.get(k)), "")
    hint = str(hint).replace("\n", " ")
    return f"{name}: {hint[:140]}" if hint else name


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
            s.last_reply = _text_of(_content(r)).strip() or s.last_reply
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


def messages(path: Path, limit: int = 60) -> list[Message]:
    """Human prompts, assistant text and one-line tool calls, newest last."""
    out: list[Message] = []
    for r in _read_tail(path, TRANSCRIPT_TAIL):
        if r.get("isSidechain"):
            continue
        ts = _str(r.get("timestamp"))
        if _is_human(r):
            out.append(Message(role="user", text=clip(_text_of(_content(r)), 4000) or "", ts=ts))
            continue
        content = _content(r)
        if r.get("type") != "assistant" or not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and str(b.get("text", "")).strip():
                out.append(Message(role="assistant", text=clip(str(b["text"]), 6000) or "", ts=ts))
            elif b.get("type") == "tool_use":
                out.append(Message(role="tool", text=_tool_line(b), ts=ts))
    return out[-limit:]


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
