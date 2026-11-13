"""Build this machine's snapshot: live sessions + recent history + health."""

from __future__ import annotations

import time
from dataclasses import dataclass

from fleetd import API_LEVEL
from fleetd.claude_cli import AgentEntry, ClaudeCLI, CLIError, normalize_state
from fleetd.config import Config
from fleetd.models import Health, MachineSnapshot, PeerInfo, Session, SessionState, TranscriptSummary
from fleetd.transcripts import TranscriptIndex, clip


def _opt_str(v: object) -> str | None:
    return v if isinstance(v, str) and v else None


def _opt_int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def to_session(a: AgentEntry, tr: TranscriptSummary | None, job: dict[str, object]) -> Session:
    output = job.get("output")
    result = output.get("result") if isinstance(output, dict) else None
    tr = tr or TranscriptSummary(session_id="", path="")
    ident = _opt_str(a.get("id"))
    return Session(
        id=ident,
        session_id=_opt_str(a.get("sessionId")),
        name=_opt_str(a.get("name")) or tr.title or ident,
        cwd=_opt_str(a.get("cwd")) or tr.cwd,
        kind=_opt_str(a.get("kind")),
        session_kind=tr.session_kind,
        state=normalize_state(a),
        raw_state=_opt_str(a.get("state")),
        status=_opt_str(a.get("status")),
        pid=_opt_int(a.get("pid")),
        started_at=_opt_int(a.get("startedAt")),
        updated_at=_opt_str(job.get("updatedAt")) or tr.updated_at,
        last_prompt=tr.last_prompt,
        last_reply=tr.last_reply,
        cost_usd=tr.cost_usd,
        git_branch=tr.git_branch,
        detail=clip(_opt_str(job.get("detail")), 300),
        intent=clip(_opt_str(job.get("intent")), 300),
        result=clip(_opt_str(result), 600),
        worktree=_opt_str(job.get("worktreePath")),
        tokens=_opt_int(job.get("tokens")),
        live=True,
    )


def history_session(h: TranscriptSummary) -> Session:
    return Session(
        id=h.session_id[:8],
        session_id=h.session_id,
        name=h.title or clip(h.last_prompt, 60) or h.session_id[:8],
        cwd=h.cwd,
        kind="history",
        session_kind=h.session_kind,
        state=SessionState.ENDED,
        updated_at=h.updated_at,
        last_prompt=h.last_prompt,
        last_reply=h.last_reply,
        cost_usd=h.cost_usd,
        git_branch=h.git_branch,
        continued_in=h.continued_in,
    )


def live_sessions(cli: ClaudeCLI, idx: TranscriptIndex) -> tuple[list[Session], str | None]:
    try:
        agents = cli.agents()
    except CLIError as e:
        return [], str(e)
    out: list[Session] = []
    for a in agents:
        sid = _opt_str(a.get("sessionId"))
        is_bg = a.get("kind") == "background"
        job = cli.job_state(str(a.get("id", ""))) if is_bg else {}
        out.append(to_session(a, idx.summary_for(sid), job))
    return out, None


def history(idx: TranscriptIndex, exclude: set[str], days: float, limit: int) -> list[Session]:
    recent = idx.recent(days=days, limit=limit + len(exclude))
    return [history_session(h) for h in recent if h.session_id not in exclude][:limit]


@dataclass(frozen=True)
class Identity:
    """How this machine presents itself (label/url may be changed at runtime)."""

    label: str
    url: str | None
    peers: list[PeerInfo]
    app_version: str | None


def build(cfg: Config, cli: ClaudeCLI, idx: TranscriptIndex, claude_version: str,
          health: Health, me: Identity, history_days: float = 3, history_limit: int = 40) -> MachineSnapshot:
    sessions, err = live_sessions(cli, idx)
    seen = {s.session_id for s in sessions if s.session_id}
    return MachineSnapshot(
        machine=cfg.machine,
        label=me.label,
        online=True,
        is_self=True,
        generated_at=time.time(),
        claude_version=claude_version,
        api_level=API_LEVEL,
        app_version=me.app_version,
        url=me.url,
        peers=me.peers,
        health=health,
        sessions=sessions,
        history=history(idx, seen, history_days, history_limit),
        error=err,
    )
