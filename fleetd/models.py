"""API data models. Attributes are snake_case; JSON uses camelCase aliases."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class SessionState(StrEnum):
    NEEDS_INPUT = "needs_input"
    WORKING = "working"
    IDLE = "idle"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"
    ENDED = "ended"  # transcript only, no live process
    UNKNOWN = "unknown"


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True,
                              serialize_by_alias=True, extra="ignore")


class Battery(ApiModel):
    percent: int | None = None
    state: str | None = None
    on_ac: bool = False


class AwakeMode(StrEnum):
    OFF = "off"
    AC = "ac"          # caffeinate -s: no effect on battery
    ALWAYS = "always"  # caffeinate -i -s: also on battery


class Awake(ApiModel):
    """fleetd's own keep-awake assertion."""

    mode: AwakeMode = AwakeMode.OFF
    active: bool = False


class SleepHolder(ApiModel):
    """A long-lived power assertion that keeps the Mac from sleeping."""

    pid: int
    process: str
    kind: str                 # PreventSystemSleep | PreventUserIdleSystemSleep
    owner: str | None = None  # for caffeinate: who it asserts on behalf of


class Health(ApiModel):
    uptime_sec: int | None = None
    battery: Battery = Battery()
    sleep_prevented: bool = False
    holders: list[SleepHolder] = []
    awake: Awake | None = None


class AwakeBody(ApiModel):
    mode: AwakeMode


# ---- Phase 4: handoff --------------------------------------------------------
class GitInfo(ApiModel):
    root: str | None = None
    remote: str | None = None
    branch: str | None = None
    head: str | None = None
    dirty: int = 0  # files with uncommitted changes (they do not travel)


class BundleFile(ApiModel):
    rel: str   # "transcript.jsonl", "session/subagents/x.jsonl", "file-history/…"
    data: str  # base64


class HandoffBundle(ApiModel):
    session_id: str
    source_machine: str
    home: str
    cwd: str
    title: str | None = None
    git: GitInfo | None = None
    size: int = 0
    files: list[BundleFile] = []


class ImportBody(ApiModel):
    bundle: HandoffBundle
    target_cwd: str | None = None
    dry_run: bool = False
    overwrite: bool = False


class ImportResult(ApiModel):
    session_id: str
    cwd: str
    dry_run: bool
    written: list[str] = []
    warnings: list[str] = []


class ReleaseBody(ApiModel):
    force: bool = False
    dry_run: bool = False


class ReleaseResult(ApiModel):
    session_id: str
    was_live: bool
    stopped: bool


class HandoffBody(ApiModel):
    source: str
    target: str
    session_id: str = Field(min_length=36, max_length=36)
    target_cwd: str | None = None
    message: str | None = Field(default=None, max_length=20000)
    force: bool = False
    overwrite: bool = False
    dry_run: bool = False


class HandoffResult(ApiModel):
    session_id: str
    source: str
    target: str
    cwd: str
    dry_run: bool
    stopped_source: bool = False
    resumed_id: str | None = None
    warnings: list[str] = []


class TranscriptSummary(ApiModel):
    session_id: str
    path: str
    title: str | None = None
    last_prompt: str | None = None
    last_reply: str | None = None
    updated_at: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    session_kind: str | None = None
    cost_usd: float | None = Field(default=None, alias="costUSD")
    continued_in: str | None = None


class Inline(ApiModel):
    """A span inside a paragraph, heading or table cell.

    text/code carry `text`; strong/em/link carry `children`; link also `href`
    (only http, https and mailto ever reach the dashboard).
    """

    type: Literal["text", "code", "strong", "em", "link"]
    text: str = ""
    children: list[Inline] = []
    href: str | None = None


Align = Literal["left", "center", "right"] | None


class Block(ApiModel):
    """One Markdown block, parsed server-side so the dashboard only builds DOM nodes."""

    type: Literal["heading", "paragraph", "code", "list", "quote", "table", "rule"]
    level: int | None = None              # heading 1-6
    inlines: list[Inline] = []            # heading, paragraph
    lang: str | None = None               # code
    text: str | None = None               # code
    ordered: bool = False                 # list
    start: int | None = None              # ordered list first number
    items: list[list[Block]] = []         # list: blocks of each item
    children: list[Block] = []            # quote
    align: list[Align] = []               # table
    header: list[list[Inline]] = []       # table
    rows: list[list[list[Inline]]] = []   # table


class Question(ApiModel):
    """One question Claude asked with AskUserQuestion."""

    question: str
    header: str | None = None
    options: list[str] = []


TodoStatus = Literal["pending", "in_progress", "completed"]


class Todo(ApiModel):
    content: str
    status: TodoStatus = "pending"


class ToolCall(ApiModel):
    """What a tool call did, for the expandable line in the conversation."""

    name: str
    detail: str | None = None        # command, diff, file content… (clipped)
    result: str | None = None        # the tool's output (clipped); None while it is still running
    is_error: bool = False
    questions: list[Question] = []   # AskUserQuestion
    todos: list[Todo] = []           # TodoWrite
    plan: list[Block] | None = None  # ExitPlanMode: the plan as Markdown


class Message(ApiModel):
    role: str  # "user" | "assistant" | "tool"
    text: str  # tool: the one-line title (older dashboards show only this)
    ts: str | None = None
    id: str | None = None              # stable across reads: record uuid + block index
    blocks: list[Block] | None = None  # assistant replies: parsed Markdown; None for plain text
    tool: ToolCall | None = None       # role "tool" only


class Transcript(ApiModel):
    session_id: str
    summary: TranscriptSummary
    messages: list[Message]
    version: str = ""        # changes whenever the transcript file does; pass back as ?since=
    unchanged: bool = False  # ?since= matched: messages is empty, keep what you have
    model: str | None = None
    context_tokens: int | None = None  # prompt size of the latest reply (input + cache)


class Session(ApiModel):
    id: str | None = None
    session_id: str | None = None
    name: str | None = None
    cwd: str | None = None
    kind: str | None = None  # "background" | "interactive" | "history"
    session_kind: str | None = None
    state: SessionState = SessionState.UNKNOWN
    raw_state: str | None = None
    status: str | None = None
    pid: int | None = None
    started_at: int | None = None
    updated_at: str | None = None
    last_prompt: str | None = None
    last_reply: str | None = None
    cost_usd: float | None = Field(default=None, alias="costUSD")
    git_branch: str | None = None
    continued_in: str | None = None
    detail: str | None = None
    intent: str | None = None
    result: str | None = None
    worktree: str | None = None
    tokens: int | None = None
    live: bool = False


MACHINE_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$"
FLEET_URL = r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?/?$"


def _strip_slash(v: str | None) -> str | None:
    return v.rstrip("/") if isinstance(v, str) else v


class PeerInfo(ApiModel):
    machine: str = Field(pattern=MACHINE_ID)
    label: str | None = Field(default=None, max_length=80)
    url: str | None = Field(default=None, pattern=FLEET_URL)

    _norm_url = field_validator("url")(_strip_slash)


class JoinBody(PeerInfo):
    url: str = Field(pattern=FLEET_URL)


class Roster(ApiModel):
    """Reply to a join: who the joined machine is, and everyone it knows."""

    machine: str
    label: str
    url: str | None = None
    peers: list[PeerInfo] = []


class Hello(ApiModel):
    """Unauthenticated identification: nothing secret in here."""

    fleet: bool = True
    machine: str
    label: str
    api_level: int
    app_version: str | None = None


class SettingsBody(ApiModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    url: str | None = Field(default=None, pattern=FLEET_URL)

    _norm_url = field_validator("url")(_strip_slash)


class UpdateResult(ApiModel):
    requested: bool
    app_managed: bool


class MachineSnapshot(ApiModel):
    machine: str
    label: str | None = None
    online: bool = True
    is_self: bool = False
    generated_at: float | None = None
    fetched_at: float | None = None
    last_seen: float | None = None
    claude_version: str | None = None
    api_level: int = 0  # fleetd.API_LEVEL of that machine; 0 = Fleet older than this field
    app_version: str | None = None  # Fleet.app version; None = not running inside the app
    url: str | None = None          # this machine's own tailnet URL, if known
    peers: list[PeerInfo] = []      # machines this one knows (gossiped to the others)
    health: Health | None = None
    sessions: list[Session] = []
    history: list[Session] = []
    error: str | None = None


class Fleet(ApiModel):
    self_machine: str
    machines: list[MachineSnapshot]


class HealthResponse(Health):
    machine: str
    label: str
    claude_version: str | None = None


class Logs(ApiModel):
    id: str
    logs: str


class LoginBody(BaseModel):
    token: str


class NewSessionBody(ApiModel):
    cwd: str = Field(min_length=1, max_length=1024)
    prompt: str = Field(min_length=1, max_length=20000)
    name: str | None = Field(default=None, max_length=80)
    permission_mode: str | None = None


class MessageBody(ApiModel):
    text: str = Field(min_length=1, max_length=20000)
    force: bool = False  # stop a session that is still working


PromptKind = Literal["plan", "permission", "question", "menu"]
SessionMode = Literal["default", "manual", "auto", "acceptEdits", "plan", "bypassPermissions", "dontAsk"]
SettableMode = Literal["manual", "auto", "acceptEdits", "plan"]  # remote callers may not skip prompts


class PromptOption(ApiModel):
    key: str                    # the digit to press
    label: str
    detail: str | None = None   # the grey line under a choice
    needs_text: bool = False    # "Tell Claude what to change", "Type something."
    selected: bool = False


class Prompt(ApiModel):
    """A menu a background session waits on, read off its terminal screen."""

    kind: PromptKind
    title: str
    body: str | None = None
    options: list[PromptOption]


class SessionScreen(ApiModel):
    """What `claude attach` shows right now."""

    id: str
    text: str
    mode: SessionMode | None = None  # None: not visible (a menu covers the footer)
    prompt: Prompt | None = None


class ChooseInput(ApiModel):
    """An answer names the menu it was meant for, so a menu that changed meanwhile is never answered."""

    action: Literal["choose"]
    option: str = Field(pattern=r"^[1-9]$")
    title: str = Field(max_length=2000)
    label: str = Field(min_length=1, max_length=2000)
    text: str | None = Field(default=None, max_length=4000)


class KeyInput(ApiModel):
    action: Literal["key"]
    key: Literal["esc"]


class ModeInput(ApiModel):
    action: Literal["mode"]
    mode: SettableMode


class TypeInput(ApiModel):
    action: Literal["type"]
    text: str = Field(min_length=1, max_length=20000)


class SessionInputBody(ApiModel):
    input: ChooseInput | KeyInput | ModeInput | TypeInput = Field(discriminator="action")


class Upload(ApiModel):
    """A file attached from the dashboard, saved on the machine that runs the session."""

    path: str
    name: str
    size: int


class Folders(ApiModel):
    """Subfolders of one directory inside home, for picking a new session's cwd."""

    path: str                # "~/Documents/code"
    parent: str | None       # None at home itself: the browser cannot leave it
    folders: list[str]       # names, not paths; hidden ones left out
    truncated: bool = False  # more than folders.MAX_FOLDERS


class ActionResult(ApiModel):
    ok: bool = True
    id: str | None = None
    action: str
    output: str = ""
