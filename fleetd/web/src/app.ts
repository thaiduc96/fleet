// Fleet dashboard. `npm run build` compiles this (tsc, strict) to fleetd/web/app.js,
// which is committed so installing fleetd needs no Node. Edit this file, not app.js.
// Plain script (no modules / bundler) so fleetd can serve it as a static file.

const REFRESH_MS = 5000;
const WRITE_HEADER = "X-Fleet-Request";

// ---------------------------------------------------------------- types
const STATES = ["needs_input", "working", "failed", "done", "idle", "stopped", "unknown", "ended"] as const;
type SessionState = (typeof STATES)[number];
type Tab = "transcript" | "logs";
type Role = "user" | "assistant" | "tool";

interface Battery { percent: number | null; onAC: boolean }
const AWAKE_MODES = ["off", "ac", "always"] as const;
type AwakeMode = (typeof AWAKE_MODES)[number];
interface Awake { mode: AwakeMode; active: boolean }
interface Health { battery: Battery; sleepPrevented: boolean; holders: string[]; awake: Awake | null }
interface Session {
  id: string; sessionId: string | null; name: string | null; cwd: string | null;
  kind: string | null; state: SessionState; rawState: string | null; pid: number | null;
  startedAt: number | null; updatedAt: string | null; lastPrompt: string | null;
  lastReply: string | null; costUSD: number | null; gitBranch: string | null;
  detail: string | null; result: string | null; live: boolean;
}
interface Machine {
  machine: string; label: string; online: boolean; lastSeen: number | null;
  claudeVersion: string | null; health: Health | null; sessions: Session[];
  history: Session[]; error: string | null;
  apiLevel: number; isSelf: boolean; appVersion: string | null;
}
interface Fleet { machines: Machine[]; webVersion: string }
/** Markdown parsed by fleetd (fleetd/markdown.py); rendered with DOM nodes, never innerHTML. */
type Inline =
  | { type: "text" | "code"; text: string }
  | { type: "strong" | "em"; children: Inline[] }
  | { type: "link"; href: string; children: Inline[] };
type Align = "left" | "center" | "right" | null;
type Block =
  | { type: "heading"; level: number; inlines: Inline[] }
  | { type: "paragraph"; inlines: Inline[] }
  | { type: "code"; lang: string | null; text: string }
  | { type: "list"; ordered: boolean; start: number | null; items: Block[][] }
  | { type: "quote"; children: Block[] }
  | { type: "table"; align: Align[]; header: Inline[][]; rows: Inline[][][] }
  | { type: "rule" };
interface Question { question: string; header: string | null; options: string[] }
type TodoStatus = "pending" | "in_progress" | "completed";
interface Todo { content: string; status: TodoStatus }
/** A tool call as Claude Code's terminal shows it; result is null while the tool still runs. */
interface ToolCall {
  name: string; detail: string | null; result: string | null; isError: boolean;
  questions: Question[]; todos: Todo[]; plan: Block[] | null;
}
interface Message { id: string | null; role: Role; text: string; ts: string | null; blocks: Block[] | null; tool: ToolCall | null }
type PromptKind = "plan" | "permission" | "question" | "menu";
interface PromptOption { key: string; label: string; detail: string | null; needsText: boolean; selected: boolean }
interface SessionPrompt { kind: PromptKind; title: string; body: string | null; options: PromptOption[] }
type SessionMode = "default" | "manual" | "auto" | "acceptEdits" | "plan" | "bypassPermissions" | "dontAsk";
type SettableMode = "manual" | "auto" | "acceptEdits" | "plan";
/** What the session's terminal shows (fleetd attaches to it). */
interface SessionScreen { text: string; mode: SessionMode | null; prompt: SessionPrompt | null }
type SessionInput =
  | { action: "choose"; option: string; title: string; label: string; text?: string }
  | { action: "key"; key: "esc" }
  | { action: "mode"; mode: SettableMode }
  | { action: "type"; text: string };
interface Transcript { version: string; unchanged: boolean; model: string | null; contextTokens: number | null; messages: Message[] }
interface ActionResult { id: string | null; action: string; output: string }

/** A session as shown in the list: plus which machine it belongs to. */
interface Row extends Session { machine: string; mlabel: string; stale: boolean }

const GROUPS: ReadonlyArray<readonly [SessionState, string]> = [
  ["needs_input", "Cần bạn trả lời"], ["working", "Đang chạy"], ["failed", "Lỗi"],
  ["done", "Xong"], ["idle", "Idle"], ["stopped", "Đã dừng"], ["unknown", "Khác"],
  ["ended", "Lịch sử (đã đóng)"],
];
const STATE_LABEL: Record<SessionState, string> = {
  needs_input: "Cần input", working: "Đang chạy", failed: "Lỗi", done: "Xong",
  idle: "Idle", stopped: "Đã dừng", unknown: "?", ended: "Đã đóng",
};
const NOTIFY_ON: ReadonlySet<SessionState> = new Set<SessionState>(["needs_input", "done", "failed"]);
const ROLE_LABEL: Record<Role, string> = { user: "Bạn", assistant: "Claude", tool: "công cụ" };

// ---------------------------------------------------------------- parsing (API data is untrusted)
type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const obj = (v: unknown): Obj => (isObj(v) ? v : {});
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const bool = (v: unknown): boolean => v === true;
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const STATE_SET: ReadonlySet<unknown> = new Set(STATES);
const isState = (v: unknown): v is SessionState => STATE_SET.has(v);
const isRole = (v: unknown): v is Role => v === "user" || v === "assistant" || v === "tool";

function parseSession(v: unknown): Session {
  const o = obj(v);
  return {
    id: str(o.id) ?? "", sessionId: str(o.sessionId), name: str(o.name), cwd: str(o.cwd),
    kind: str(o.kind), state: isState(o.state) ? o.state : "unknown", rawState: str(o.rawState),
    pid: num(o.pid), startedAt: num(o.startedAt), updatedAt: str(o.updatedAt),
    lastPrompt: str(o.lastPrompt), lastReply: str(o.lastReply), costUSD: num(o.costUSD),
    gitBranch: str(o.gitBranch), detail: str(o.detail), result: str(o.result), live: bool(o.live),
  };
}

const AWAKE_SET: ReadonlySet<unknown> = new Set(AWAKE_MODES);
const isAwakeMode = (v: unknown): v is AwakeMode => AWAKE_SET.has(v);

function parseHealth(h: Obj, b: Obj): Health {
  const a = isObj(h.awake) ? h.awake : null;
  // one label per holding process: "fleetd", "homecam.supervisor", "claude", "backupd"…
  const holders = [...new Set(list(h.holders).flatMap((x) => {
    const o = obj(x);
    const label = str(o.owner) ?? str(o.process);
    return label ? [label] : [];
  }))];
  return {
    battery: { percent: num(b.percent), onAC: bool(b.onAc) },
    sleepPrevented: bool(h.sleepPrevented),
    holders,
    awake: a && isAwakeMode(a.mode) ? { mode: a.mode, active: bool(a.active) } : null,
  };
}

function parseMachine(v: unknown): Machine {
  const o = obj(v);
  const h = isObj(o.health) ? o.health : null;
  const b = obj(h?.battery);
  return {
    machine: str(o.machine) ?? "?", label: str(o.label) ?? str(o.machine) ?? "?",
    online: bool(o.online), lastSeen: num(o.lastSeen), claudeVersion: str(o.claudeVersion),
    apiLevel: num(o.apiLevel) ?? 0, isSelf: bool(o.isSelf), appVersion: str(o.appVersion),
    health: h ? parseHealth(h, b) : null,
    sessions: list(o.sessions).map(parseSession), history: list(o.history).map(parseSession),
    error: str(o.error),
  };
}

const parseFleet = (v: unknown): Fleet => ({ machines: list(obj(v).machines).map(parseMachine), webVersion: str(obj(v).webVersion) ?? "" });

const SAFE_HREF = /^(https?:|mailto:)/i;

function parseInlines(v: unknown): Inline[] {
  return list(v).flatMap((x): Inline[] => {
    const o = obj(x);
    const text = str(o.text) ?? "";
    switch (o.type) {
      case "text": case "code": return [{ type: o.type, text }];
      case "strong": case "em": return [{ type: o.type, children: parseInlines(o.children) }];
      case "link": {
        const href = str(o.href);
        const children = parseInlines(o.children);
        // fleetd already drops unsafe schemes; an old or odd peer must not get past here either.
        return href && SAFE_HREF.test(href) ? [{ type: "link", href, children }] : children;
      }
      default: return text ? [{ type: "text", text }] : [];
    }
  });
}

const parseAlign = (v: unknown): Align => (v === "left" || v === "center" || v === "right" ? v : null);

function parseBlocks(v: unknown): Block[] {
  return list(v).flatMap((x): Block[] => {
    const o = obj(x);
    switch (o.type) {
      case "heading": return [{ type: "heading", level: Math.min(6, Math.max(1, num(o.level) ?? 3)), inlines: parseInlines(o.inlines) }];
      case "paragraph": return [{ type: "paragraph", inlines: parseInlines(o.inlines) }];
      case "code": return [{ type: "code", lang: str(o.lang), text: str(o.text) ?? "" }];
      case "list": return [{ type: "list", ordered: bool(o.ordered), start: num(o.start), items: list(o.items).map(parseBlocks) }];
      case "quote": return [{ type: "quote", children: parseBlocks(o.children) }];
      case "table": return [{
        type: "table", align: list(o.align).map(parseAlign), header: list(o.header).map(parseInlines),
        rows: list(o.rows).map((r) => list(r).map(parseInlines)),
      }];
      case "rule": return [{ type: "rule" }];
      default: return [];
    }
  });
}

const parseTodoStatus = (v: unknown): TodoStatus => (v === "in_progress" || v === "completed" ? v : "pending");

function parseTool(v: unknown): ToolCall | null {
  if (!isObj(v)) return null;
  return {
    name: str(v.name) ?? "tool", detail: str(v.detail), result: str(v.result), isError: bool(v.isError),
    questions: list(v.questions).flatMap((q) => {
      const o = obj(q);
      const question = str(o.question);
      return question ? [{ question, header: str(o.header), options: list(o.options).flatMap((x) => str(x) ?? []) }] : [];
    }),
    todos: list(v.todos).flatMap((t) => {
      const o = obj(t);
      const content = str(o.content);
      return content ? [{ content, status: parseTodoStatus(o.status) }] : [];
    }),
    plan: Array.isArray(v.plan) ? parseBlocks(v.plan) : null,
  };
}

function parseMessages(v: unknown): Message[] {
  return list(obj(v).messages).flatMap((m) => {
    const o = obj(m);
    const text = str(o.text);
    const blocks = Array.isArray(o.blocks) ? parseBlocks(o.blocks) : null;
    return isRole(o.role) && text !== null
      ? [{ id: str(o.id), role: o.role, text, ts: str(o.ts), blocks, tool: parseTool(o.tool) }] : [];
  });
}

const PROMPT_KINDS: ReadonlySet<unknown> = new Set(["plan", "permission", "question", "menu"]);
const isPromptKind = (v: unknown): v is PromptKind => PROMPT_KINDS.has(v);
const MODES: ReadonlySet<unknown> = new Set(["default", "manual", "auto", "acceptEdits", "plan", "bypassPermissions", "dontAsk"]);
const isMode = (v: unknown): v is SessionMode => MODES.has(v);

function parsePrompt(v: unknown): SessionPrompt | null {
  if (!isObj(v) || !isPromptKind(v.kind)) return null;
  const options = list(v.options).flatMap((x): PromptOption[] => {
    const o = obj(x);
    const key = str(o.key);
    const label = str(o.label);
    return key && label ? [{ key, label, detail: str(o.detail), needsText: bool(o.needsText), selected: bool(o.selected) }] : [];
  });
  return options.length ? { kind: v.kind, title: str(v.title) ?? "", body: str(v.body), options } : null;
}

function parseScreen(v: unknown): SessionScreen {
  const o = obj(v);
  return { text: str(o.text) ?? "", mode: isMode(o.mode) ? o.mode : null, prompt: parsePrompt(o.prompt) };
}

function parseTranscript(v: unknown): Transcript {
  const o = obj(v);
  return { version: str(o.version) ?? "", unchanged: bool(o.unchanged), model: str(o.model),
    contextTokens: num(o.contextTokens), messages: parseMessages(v) };
}

function parseAction(v: unknown): ActionResult {
  const o = obj(v);
  return { id: str(o.id), action: str(o.action) ?? "", output: str(o.output) ?? "" };
}

// ---------------------------------------------------------------- DOM helpers
function el<T extends HTMLElement>(id: string, type: new () => T): T {
  const e = document.getElementById(id);
  if (!(e instanceof type)) throw new Error(`#${id} missing or not a ${type.name}`);
  return e;
}

type Child = Node | string | number | null | undefined | false;
type Attrs = Record<string, string | boolean | null | undefined | ((e: Event) => void)>;

function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...kids: (Child | Child[])[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "class") node.className = String(v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    node.append(kid instanceof Node ? kid : String(kid));
  }
  return node;
}

const store = {
  get(k: string, d: string): string { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k: string, v: string): void { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

function ago(t: string | number | null): string {
  if (t == null) return "";
  const ms = typeof t === "number" ? (t < 1e12 ? t * 1000 : t) : Date.parse(t);
  if (!ms) return "";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return "vừa xong";
  if (s < 3600) return `${Math.floor(s / 60)} phút trước`;
  if (s < 86400) return `${Math.floor(s / 3600)} giờ trước`;
  return `${Math.floor(s / 86400)} ngày trước`;
}
const shortPath = (p: string | null): string => (p ?? "").replace(/^\/Users\/[^/]+/, "~");

/** For cards: "~/Documents/code/app/.claude/worktrees/feat-x" -> "~/…/app ⎇wt feat-x". */
function compactPath(p: string | null): string {
  const s = shortPath(p);
  const wt = s.match(/^(.*)\/\.claude\/worktrees\/([^/]+)$/);
  const base = wt?.[1] ?? s;
  const parts = base.split("/");
  const head = parts.length > 3 ? `~/…/${parts.slice(-1).join("/")}` : base;
  return wt?.[2] ? `${head} ⎇wt ${wt[2]}` : head;
}
const timeOf = (s: Session): number => Date.parse(s.updatedAt ?? "") || s.startedAt || 0;

let toastTimer = 0;
function toast(msg: string, isError = false): void {
  const t = el("toast", HTMLDivElement);
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { t.hidden = true; }, isError ? 7000 : 3500);
}

// ---------------------------------------------------------------- API
class Unauthorized extends Error {}

async function api(path: string, init: RequestInit = {}): Promise<unknown> {
  const r = await fetch(path, { credentials: "same-origin", ...init });
  if (r.status === 401) { showLogin(); throw new Unauthorized("unauthorized"); }
  const body: unknown = await r.json().catch(() => null);
  if (!r.ok) throw new Error(str(obj(body).detail) ?? `${r.status} ${r.statusText}`);
  return body;
}

async function send(method: "POST" | "DELETE", path: string, body?: Obj): Promise<unknown> {
  const headers: Record<string, string> = { [WRITE_HEADER]: "1" };
  if (body) headers["Content-Type"] = "application/json";
  return api(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
}

async function write(method: "POST" | "DELETE", path: string, body?: Obj): Promise<ActionResult> {
  return parseAction(await send(method, path, body));
}

const mpath = (machine: string): string => `/api/m/${encodeURIComponent(machine)}/sessions`;

// ---------------------------------------------------------------- attachments
// Like Claude Code's own pasted images: the file is saved on the machine that runs the
// session and its path goes into the message, so Claude reads it with its Read tool.
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;  // fleetd/uploads.py MAX_UPLOAD_BYTES

class Attachments {
  private files: File[] = [];

  constructor(private readonly chips: HTMLDivElement, private readonly picker: HTMLInputElement,
              private readonly busyNote: HTMLSpanElement) {}

  add(files: Iterable<File>): void {
    for (const f of files) {
      if (f.size > MAX_UPLOAD_BYTES) { toast(`"${f.name}" quá lớn (${size(f.size)}, tối đa ${size(MAX_UPLOAD_BYTES)}).`, true); continue; }
      this.files.push(f);
    }
    this.render();
  }

  clear(): void { this.files = []; this.picker.value = ""; this.render(); }

  get isEmpty(): boolean { return !this.files.length; }

  /** Upload every file to `machine`; returns their saved paths, or throws naming the file that failed. */
  async upload(machine: string): Promise<string[]> {
    const paths: string[] = [];
    for (const [k, f] of this.files.entries()) {
      this.busyNote.textContent = `Đang tải lên ${k + 1}/${this.files.length}: ${f.name}…`;
      try {
        const saved = obj(await api(`/api/m/${encodeURIComponent(machine)}/uploads?name=${encodeURIComponent(f.name || "file")}`, {
          method: "POST", headers: { [WRITE_HEADER]: "1", "Content-Type": "application/octet-stream" }, body: f,
        }));
        const path = str(saved.path);
        if (!path) throw new Error("máy đích không trả về đường dẫn");
        paths.push(path);
      } catch (err) {
        if (err instanceof Unauthorized) throw err;
        throw new Error(`tải "${f.name}" lên thất bại: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    return paths;
  }

  private render(): void {
    this.chips.hidden = !this.files.length;
    this.chips.replaceChildren(...this.files.map((f, k) => h("span", { class: "chip" },
      h("span", { class: "chip-name", title: f.name }, f.name || "file"), h("span", { class: "muted" }, size(f.size)),
      h("button", { type: "button", class: "ghost", "aria-label": `Bỏ ${f.name}`, onclick: () => {
        this.files.splice(k, 1); this.render();
      } }, "✕"))));
  }
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** The message as Claude Code's CLI would send it: text, then one attached path per line. */
const withPaths = (text: string, paths: string[]): string => (paths.length ? `${text}\n\n${paths.join("\n")}` : text);

/** 📎 button, paste and drag-and-drop for one form. */
function wireAttachments(prefix: "d" | "n", target: HTMLElement, text: HTMLTextAreaElement, noteId: string): Attachments {
  const picker = el(`${prefix}-file`, HTMLInputElement);
  const files = new Attachments(el(`${prefix}-files`, HTMLDivElement), picker, el(noteId, HTMLSpanElement));
  el(`${prefix}-attach`, HTMLButtonElement).addEventListener("click", () => picker.click());
  picker.addEventListener("change", () => { files.add(picker.files ?? []); picker.value = ""; });
  text.addEventListener("paste", (e) => {
    const pasted = e.clipboardData?.files;
    if (!pasted?.length) return;
    files.add(pasted);
    // A copied image also carries text (its name/URL): keep only the file.
    e.preventDefault();
  });
  target.addEventListener("dragover", (e) => {
    if (!e.dataTransfer?.types.includes("Files")) return;
    e.preventDefault(); target.classList.add("dropping");
  });
  target.addEventListener("dragleave", () => target.classList.remove("dropping"));
  target.addEventListener("drop", (e) => {
    target.classList.remove("dropping");
    if (!e.dataTransfer?.files.length) return;
    e.preventDefault(); files.add(e.dataTransfer.files);
  });
  return files;
}

let messageFiles: Attachments | null = null;
let sessionFiles: Attachments | null = null;

// ---------------------------------------------------------------- state
let fleet: Fleet | null = null;
let prevStates: Map<string, SessionState> | null = null;
let drawer: { row: Row; tab: Tab } | null = null;
let timer = 0;

// ---------------------------------------------------------------- login
function showLogin(): void {
  clearInterval(timer); timer = 0;
  el("app", HTMLDivElement).hidden = true;
  el("login", HTMLDivElement).hidden = false;
  el("token", HTMLInputElement).focus();
}

async function login(e: Event): Promise<void> {
  e.preventDefault();
  const err = el("login-error", HTMLParagraphElement);
  err.hidden = true;
  const r = await fetch("/api/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: el("token", HTMLInputElement).value }),
  });
  if (!r.ok) { err.textContent = "Sai token."; err.hidden = false; return; }
  el("token", HTMLInputElement).value = "";
  start();
}

// ---------------------------------------------------------------- data
function rows(): Row[] {
  const showHist = el("show-history", HTMLInputElement).checked;
  const q = el("q", HTMLInputElement).value.trim().toLowerCase();
  const out: Row[] = (fleet?.machines ?? []).flatMap((m) =>
    [...m.sessions, ...(showHist ? m.history : [])].map((s) => ({ ...s, machine: m.machine, mlabel: m.label, stale: !m.online })));
  if (!q) return out;
  return out.filter((s) => [s.name, s.cwd, s.lastPrompt, s.detail, s.mlabel, s.id]
    .some((v) => (v ?? "").toLowerCase().includes(q)));
}

function notifyChanges(f: Fleet): void {
  const cur = new Map<string, { s: Session; m: Machine }>();
  for (const m of f.machines) {
    if (!m.online) continue;
    for (const s of m.sessions) cur.set(`${m.machine}/${s.id}`, { s, m });
  }
  if (prevStates && "Notification" in window && Notification.permission === "granted") {
    for (const [k, { s, m }] of cur) {
      const before = prevStates.get(k);
      if (before && before !== s.state && NOTIFY_ON.has(s.state)) {
        new Notification(`${m.label}: ${STATE_LABEL[s.state]}`, {
          body: `${s.name ?? s.id}\n${s.detail ?? s.lastReply ?? ""}`.slice(0, 180), tag: k,
        });
      }
    }
  }
  prevStates = new Map([...cur].map(([k, v]) => [k, v.s.state]));
}

/** "#s=<machine>/<id>" (used by Fleet.app's menu and notifications) opens that session once. */
function focusFromHash(f: Fleet): void {
  const m = location.hash.match(/^#s=([^/]+)\/(.+)$/);
  if (!m) return;
  history.replaceState(null, "", location.pathname);  // consume it first: a bad hash must not stick
  let machine = "", id = "";
  try {
    [machine, id] = [decodeURIComponent(m[1] ?? ""), decodeURIComponent(m[2] ?? "")];
  } catch {
    return;  // malformed %-escape
  }
  const mach = f.machines.find((x) => x.machine === machine);
  const s = mach && [...mach.sessions, ...mach.history].find((x) => x.id === id || x.sessionId === id);
  if (mach && s) openDrawer({ ...s, machine: mach.machine, mlabel: mach.label, stale: !mach.online });
}

async function refresh(): Promise<void> {
  try {
    const f = parseFleet(await api("/api/fleet"));
    reloadIfOutdated(f.webVersion);
    fleet = f;
    notifyChanges(f);
    render();
    focusFromHash(f);
    el("updated", HTMLSpanElement).textContent = `cập nhật ${new Date().toLocaleTimeString("vi-VN")}`;
  } catch (e) {
    if (!(e instanceof Unauthorized)) el("updated", HTMLSpanElement).textContent = `lỗi: ${String(e)}`;
  }
}

// ---------------------------------------------------------------- reload after an update
// fleetd serves a new page after an update, but an open tab, and above all the iPhone
// app (iOS keeps it in memory for days), keeps running the old script. Reload as soon as
// nothing typed would be lost; until then each poll tries again.
const PAGE_VERSION = document.querySelector<HTMLMetaElement>('meta[name="fleet-web-version"]')?.content ?? "";
const RELOADED_FOR_KEY = "fleet.reloadedFor";

function reloadIfOutdated(serverVersion: string): void {
  if (!PAGE_VERSION || !serverVersion || serverVersion === PAGE_VERSION) return;
  // Already reloaded for this version and still different (a proxy serving an old page): stop, never loop.
  if (store.get(RELOADED_FOR_KEY, "") === serverVersion) return;
  if (hasUnsavedInput()) return;
  store.set(RELOADED_FOR_KEY, serverVersion);
  // Reopen the session being read after the reload (focusFromHash consumes this).
  const open = drawer?.row;
  if (open) history.replaceState(null, "", `#s=${encodeURIComponent(open.machine)}/${encodeURIComponent(open.id)}`);
  location.reload();
}

function hasUnsavedInput(): boolean {
  const dialogOpen = !el("modal", HTMLDivElement).hidden || !el("handoff", HTMLDivElement).hidden;
  const draft = el("d-text", HTMLTextAreaElement).value.trim() !== "";
  const files = !(messageFiles?.isEmpty ?? true) || !(sessionFiles?.isEmpty ?? true);
  return dialogOpen || draft || files;
}

// ---------------------------------------------------------------- render: list
const AWAKE_LABEL: Record<AwakeMode, string> = { off: "Tắt", ac: "Khi cắm sạc", always: "Luôn luôn" };

/** "giữ thức bởi …" line plus warnings, so you know whether the Mac stays reachable. */
function awakeLine(m: Machine): HTMLDivElement | null {
  const hl = m.health;
  if (!m.online || !hl) return null;
  const warn: string[] = [];
  if (hl.awake?.mode === "ac" && !hl.battery.onAC) warn.push("đang chạy pin: chế độ 'Khi cắm sạc' không có tác dụng");
  if (hl.awake && hl.awake.mode !== "off" && !hl.awake.active) warn.push("caffeinate không chạy được");
  const who = hl.holders.length ? `giữ thức bởi: ${hl.holders.join(", ")}` : "⚠ có thể ngủ khi rảnh";
  return h("div", { class: `awake${hl.holders.length ? "" : " warn"}` }, [who, ...warn].join(" · "));
}

function awakeSelect(m: Machine): HTMLLabelElement | null {
  const a = m.health?.awake;
  if (!m.online || !a) return null;
  const sel = h("select", { "aria-label": `Giữ ${m.label} thức` },
    AWAKE_MODES.map((mode) => h("option", { value: mode, selected: mode === a.mode }, AWAKE_LABEL[mode])));
  sel.addEventListener("change", () => {
    const mode = sel.value;
    if (!isAwakeMode(mode)) return;
    void act(`Giữ thức (${m.label})`, () => setAwake(m.machine, mode));
  });
  return h("label", { class: "awake-select" }, "Giữ thức ", sel);
}

async function setAwake(machine: string, mode: AwakeMode): Promise<ActionResult> {
  const r = obj(await api(`/api/m/${encodeURIComponent(machine)}/awake`, {
    method: "POST", headers: { [WRITE_HEADER]: "1", "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  }));
  return { id: null, action: "awake", output: bool(r.active) ? "đang giữ thức" : "không giữ thức" };
}

/** A peer whose fleetd is older than the one serving this page lacks newer buttons. */
function outdatedLine(m: Machine, selfLevel: number): HTMLDivElement | null {
  if (!m.online || m.isSelf || m.apiLevel >= selfLevel) return null;
  return h("div", { class: "awake warn" }, "⚠ Fleet bản cũ — bấm 'Cập nhật tất cả máy' hoặc cập nhật Fleet trên máy này");
}

function renderMachines(f: Fleet): void {
  const box = el("machines", HTMLElement);
  const selfLevel = f.machines.find((m) => m.isSelf)?.apiLevel ?? 0;
  box.replaceChildren(...f.machines.map((m) => {
    const bits: string[] = [];
    if (!m.online) bits.push(`offline · lần cuối ${ago(m.lastSeen) || "chưa từng"}`);
    else {
      bits.push(`${m.sessions.length} session`);
      const b = m.health?.battery;
      if (b?.percent != null) bits.push(`pin ${b.percent}%${b.onAC ? " ⚡" : ""}`);
      if (m.claudeVersion) bits.push(`Claude ${m.claudeVersion.replace(" (Claude Code)", "")}`);
      bits.push(m.appVersion ? `Fleet ${m.appVersion}` : "Fleet (chưa cài app)");
    }
    if (m.error) bits.push(m.error);
    return h("div", { class: `machine${m.online ? "" : " offline"}`, title: m.error ?? "" },
      h("span", { class: "dot" }),
      h("div", { class: "machine-body" },
        h("div", { class: "machine-head" }, h("b", {}, m.label), awakeSelect(m)),
        h("div", { class: "muted" }, bits.join(" · ")),
        awakeLine(m),
        outdatedLine(m, selfLevel)));
  }), ...updateAllButton(f));
}

/** Ask every online machine to update itself (Fleet.app picks the request up). */
function updateAllButton(f: Fleet): HTMLButtonElement[] {
  const targets = f.machines.filter((m) => m.online);
  if (!targets.length) return [];
  const btn = h("button", { class: "ghost update-all" }, "↻ Cập nhật tất cả máy");
  btn.addEventListener("click", () => { void updateAll(btn, targets); });
  return [btn];
}

async function updateAll(btn: HTMLButtonElement, targets: Machine[]): Promise<void> {
  btn.disabled = true;
  const results = await Promise.allSettled(targets.map(async (m) => {
    const r = obj(await send("POST", `/api/m/${encodeURIComponent(m.machine)}/update`));
    return { label: m.label, app: bool(r.appManaged) };
  }));
  const ok = results.flatMap((r) => (r.status === "fulfilled" && r.value.app ? [r.value.label] : []));
  const noApp = results.flatMap((r) => (r.status === "fulfilled" && !r.value.app ? [r.value.label] : []));
  // a rejected call carries the server's explanation (e.g. "… đang chạy Fleet bản cũ …")
  const errors = results.flatMap((r) =>
    r.status === "rejected" && !(r.reason instanceof Unauthorized)
      ? [r.reason instanceof Error ? r.reason.message : String(r.reason)] : []);
  const parts = [
    ok.length ? `Đang cập nhật: ${ok.join(", ")}` : "",
    noApp.length ? `Chưa cài app (cập nhật bằng tay): ${noApp.join(", ")}` : "",
    ...errors,
  ].filter(Boolean);
  toast(parts.join(" · ") || "Không có máy nào để cập nhật", errors.length > 0);
  btn.disabled = false;
}

function card(s: Row): HTMLButtonElement {
  const active = s.state === "working" || s.state === "needs_input";
  const line = active ? (s.detail ?? s.lastReply ?? s.lastPrompt) : (s.result ?? s.detail ?? s.lastReply ?? s.lastPrompt);
  const kind = s.kind === "background" ? "bg" : s.kind === "history" ? null : s.kind;
  return h("button", { class: `card${s.stale ? " stale" : ""}`, onclick: () => openDrawer(s) },
    h("div", { class: "row" },
      h("span", { class: "name" }, s.name ?? s.id),
      h("span", { class: `badge st-${s.state}` }, s.state === "unknown" ? (s.rawState ?? "?") : STATE_LABEL[s.state]),
      h("span", { class: "badge m" }, s.mlabel)),
    line ? h("div", { class: "line" }, line) : null,
    h("div", { class: "muted", title: shortPath(s.cwd) },
      [compactPath(s.cwd), s.gitBranch && s.gitBranch !== "HEAD" ? `⎇ ${s.gitBranch}` : null, kind,
        ago(s.updatedAt ?? s.startedAt)].filter(Boolean).join(" · ")));
}

function render(): void {
  if (!fleet) return;
  renderMachines(fleet);
  const all = rows();
  const sections = GROUPS.flatMap(([key, label]) => {
    const items = all.filter((s) => s.state === key).sort((a, b) => timeOf(b) - timeOf(a));
    return items.length ? [h("section", { class: "group" },
      h("h2", {}, label, h("span", { class: "n" }, items.length)),
      h("div", { class: "cards" }, items.map(card)))] : [];
  });
  el("groups", HTMLElement).replaceChildren(...(sections.length ? sections : [h("p", { class: "empty" }, "Không có session nào.")]));
  if (drawer) {
    const cur = drawer;
    const fresh = all.find((s) => s.machine === cur.row.machine && s.id === cur.row.id)
      ?? all.find((s) => s.machine === cur.row.machine && cur.row.sessionId != null && s.sessionId === cur.row.sessionId);
    if (fresh) {
      const stateChanged = fresh.state !== cur.row.state;
      cur.row = fresh;
      renderDrawerHead();
      if (stateChanged && live?.loaded && cur.tab === "transcript") renderConversation(live, false);  // the "working" line
    }
  }
}

// ---------------------------------------------------------------- render: drawer
function copyBtn(label: string, text: string): HTMLButtonElement {
  const b = h("button", { title: text }, label);
  b.addEventListener("click", () => { void copy(b, label, text); });
  return b;
}

async function copy(b: HTMLButtonElement, label: string, text: string): Promise<void> {
  try { await navigator.clipboard.writeText(text); b.textContent = "Đã copy ✓"; }
  catch { window.prompt("Copy lệnh này:", text); }
  window.setTimeout(() => { b.textContent = label; }, 1500);
}

/** Run a lifecycle action from the drawer; refreshes the list afterwards. */
async function act(label: string, run: () => Promise<ActionResult>, confirmText?: string): Promise<void> {
  if (confirmText && !window.confirm(confirmText)) return;
  try {
    const r = await run();
    toast(`${label}: xong${r.output ? ` — ${r.output}` : ""}`);
    await refresh();
  } catch (e) {
    if (!(e instanceof Unauthorized)) toast(`${label} lỗi: ${e instanceof Error ? e.message : String(e)}`, true);
  }
}

function actionButtons(s: Row): HTMLButtonElement[] {
  const base = `${mpath(s.machine)}/${encodeURIComponent(s.id)}`;
  const btn = (label: string, cls: string, fn: () => void): HTMLButtonElement =>
    h("button", { class: cls, onclick: fn, disabled: s.stale }, label);
  if (s.kind !== "background") return [];
  const out: HTMLButtonElement[] = [];
  if (s.pid != null) out.push(btn("Dừng", "", () => { void act("Dừng", () => write("POST", `${base}/stop`)); }));
  out.push(btn("Restart", "", () => { void act("Restart", () => write("POST", `${base}/respawn`)); }));
  out.push(btn("Xoá", "danger", () => {
    void act("Xoá", () => write("DELETE", base),
      `Xoá session "${s.name ?? s.id}" khỏi danh sách và xoá worktree của nó?\nHội thoại vẫn được giữ (claude --resume).`);
  }));
  return out;
}

/** Secondary drawer actions behind one button, so the row fits a phone without hidden sideways scrolling. */
function moreMenu(items: Node[], isOpen: boolean): HTMLDetailsElement {
  return h("details", { class: "more", open: isOpen },
    h("summary", {}, "Thêm"),
    h("div", { class: "menu" }, items));
}

function closeMoreMenus(except?: Node): void {
  for (const d of document.querySelectorAll<HTMLDetailsElement>("details.more[open]")) {
    if (!except || !d.contains(except)) d.open = false;
  }
}

function renderCompose(s: Row): void {
  const form = el("d-compose", HTMLFormElement);
  const note = el("d-compose-note", HTMLSpanElement);
  const forceWrap = el("d-force-wrap", HTMLLabelElement);
  const openInTerminal = s.kind === "interactive" && s.pid != null;
  form.hidden = s.stale || !s.sessionId || openInTerminal;
  forceWrap.hidden = s.state !== "working" || canDrive(s);
  if (canDrive(s)) {
    note.textContent = s.state === "working" ? "Gửi thẳng vào session; Claude đọc khi xong bước đang làm." :
      s.state === "needs_input" ? "Claude đang chờ bạn chọn ở trên." : "Gửi thẳng vào session.";
    return;
  }
  note.textContent = s.state === "working" ? "Session đang chạy dở." :
    s.pid != null ? "Session sẽ được dừng rồi tiếp tục với tin nhắn này." :
    "Session sẽ được mở lại ở chế độ nền với tin nhắn này.";
}

function renderDrawerHead(): void {
  if (!drawer) return;
  const s = drawer.row;
  el("d-title", HTMLHeadingElement).textContent = s.name ?? s.id;
  el("d-meta", HTMLDivElement).textContent = [s.mlabel, STATE_LABEL[s.state], shortPath(s.cwd),
    s.costUSD != null ? `$${s.costUSD.toFixed(2)}` : null,
    live?.model ? live.model.replace(/^claude-/, "") : null,
    live?.contextTokens ? `${Math.round(live.contextTokens / 1000)}k token context` : null,
    ago(s.updatedAt ?? s.startedAt)]
    .filter(Boolean).join(" · ");
  const extras: Node[] = [];
  if (s.kind === "background") extras.push(copyBtn("Copy attach", `claude attach ${s.id}`));
  if (s.sessionId) extras.push(copyBtn("Copy resume", `cd ${shortPath(s.cwd)} && claude --resume ${s.sessionId}`));
  extras.push(...handoffButtons(s));
  extras.push(h("a", { href: "https://claude.ai/code", target: "_blank", rel: "noopener" }, "claude.ai/code ↗"));
  const actions = el("d-actions", HTMLDivElement);
  // Every refresh rebuilds this row; keep the menu open if the user had opened it.
  const isMenuOpen = actions.querySelector<HTMLDetailsElement>("details.more")?.open ?? false;
  actions.replaceChildren(...actionButtons(s), moreMenu(extras, isMenuOpen));
  for (const b of document.querySelectorAll<HTMLButtonElement>(".tabs button")) {
    b.classList.toggle("active", b.dataset.tab === drawer.tab);
    if (b.dataset.tab === "logs") b.hidden = s.kind !== "background";
  }
  renderCompose(s);
  renderControl();
}

function renderInlines(inlines: Inline[]): Node[] {
  return inlines.map((i): Node => {
    switch (i.type) {
      case "text": return document.createTextNode(i.text);
      case "code": return h("code", {}, i.text);
      case "strong": return h("strong", {}, renderInlines(i.children));
      case "em": return h("em", {}, renderInlines(i.children));
      case "link": return h("a", { href: i.href, target: "_blank", rel: "noopener noreferrer" }, renderInlines(i.children));
    }
  });
}

const HEADING_TAGS = ["h3", "h3", "h4", "h5", "h6", "h6"] as const;  // h1/h2 belong to the page

function cell(tag: "th" | "td", inlines: Inline[], align: Align | undefined): HTMLTableCellElement {
  return h(tag, { style: align ? `text-align:${align}` : null }, renderInlines(inlines));
}

function renderBlock(b: Block): Node {
  switch (b.type) {
    case "heading": return h(HEADING_TAGS[b.level - 1] ?? "h6", { class: "md-h" }, renderInlines(b.inlines));
    case "paragraph": return h("p", {}, renderInlines(b.inlines));
    case "code": return h("pre", { class: "md-code", "data-lang": b.lang }, h("code", {}, b.text));
    case "list": {
      const items = b.items.map((item) => h("li", {}, renderBlocks(item)));
      return b.ordered ? h("ol", { start: b.start != null && b.start !== 1 ? String(b.start) : null }, items) : h("ul", {}, items);
    }
    case "quote": return h("blockquote", {}, renderBlocks(b.children));
    case "table": return h("div", { class: "md-table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, b.header.map((c, k) => cell("th", c, b.align[k])))),
      h("tbody", {}, b.rows.map((r) => h("tr", {}, r.map((c, k) => cell("td", c, b.align[k])))))));
    case "rule": return h("hr");
  }
}

function renderBlocks(blocks: Block[]): Node[] {
  return blocks.map(renderBlock);
}

const TODO_MARK: Record<TodoStatus, string> = { pending: "☐", in_progress: "◐", completed: "☑" };
const DIFF_TOOLS: ReadonlySet<string> = new Set(["Edit", "MultiEdit"]);

/** Edit diffs: "- " lines red, "+ " lines green, like the terminal. */
function detailNode(t: ToolCall): HTMLPreElement | null {
  if (!t.detail) return null;
  if (!DIFF_TOOLS.has(t.name)) return h("pre", { class: "tool-detail" }, t.detail);
  return h("pre", { class: "tool-detail" }, t.detail.split("\n").map((line) => h("span", {
    class: line.startsWith("+ ") ? "add" : line.startsWith("- ") ? "del" : null,
  }, `${line}\n`)));
}

function toolNode(m: Message): HTMLDivElement {
  const t = m.tool;
  if (!t) return h("div", { class: "msg tool" }, `→ ${m.text}`);
  const mark = t.result === null ? " …" : t.isError ? " ✗" : "";
  const always: Node[] = [];
  if (t.questions.length) {
    always.push(h("div", { class: "tool-questions" }, t.questions.map((q) => h("div", {},
      h("strong", {}, q.question), q.options.length ? h("ul", {}, q.options.map((o) => h("li", {}, o))) : null))));
  }
  if (t.todos.length) always.push(h("ul", { class: "todos" }, t.todos.map((td) => h("li", { class: td.status }, `${TODO_MARK[td.status]} ${td.content}`))));
  if (t.plan?.length) always.push(h("div", { class: "plan md" }, renderBlocks(t.plan)));
  const output = t.result === null ? h("p", { class: "muted" }, "đang chạy…")
    : h("pre", { class: `tool-result${t.isError ? " error" : ""}` }, t.result || "(không có output)");
  return h("div", { class: `msg tool${t.isError ? " error" : ""}` },
    h("details", {}, h("summary", {}, `→ ${m.text}${mark}`), detailNode(t), output), always);
}

function messageNode(m: Message): HTMLDivElement {
  if (m.role === "tool") return toolNode(m);
  const who = h("div", { class: "who" }, `${ROLE_LABEL[m.role]} · ${ago(m.ts)}`);
  // Old fleetd peers send no blocks: keep their plain text.
  if (m.blocks && m.blocks.length) return h("div", { class: `msg ${m.role} md` }, who, renderBlocks(m.blocks));
  return h("div", { class: `msg ${m.role}` }, who, m.text);
}

// ---------------------------------------------------------------- live drawer
// The open tab polls its machine: fast while Claude works or a sent message has not
// shown up yet, slower otherwise. Transcript polls send ?since=<version>, so an
// unchanged transcript costs fleetd one stat. Only changed messages are rebuilt,
// so open tool details and the scroll position survive.
const LIVE_FAST_MS = 1500;
const LIVE_SLOW_MS = 4000;
const PENDING_TTL_MS = 3 * 60 * 1000;
const TRANSCRIPT_LIMIT = 200;
const NEAR_BOTTOM_PX = 80;

/** A message sent from here that the transcript does not show yet; `after` = last message id at send. */
interface Pending { text: string; at: number; after: string | null }
interface Live {
  key: string; version: string; timer: number; busy: boolean; loaded: boolean;
  messages: Message[]; nodes: Map<string, { sig: string; node: HTMLElement }>;
  pending: Pending[]; model: string | null; contextTokens: number | null; logs: string | null;
  screen: SessionScreen | null; screenAt: number; screenBusy: boolean; screenError: string | null;
  promptCard: { sig: string; node: HTMLElement } | null;
}
let live: Live | null = null;

const liveKey = (s: Row, tab: Tab): string => `${s.machine}/${s.sessionId ?? s.id}/${tab}`;
const sameText = (a: string, b: string): boolean => a.trim().startsWith(b.trim().slice(0, 200));
/** Has `p` shown up? Only messages newer than the send count, so a repeated "ok" is not mistaken for it. */
function arrived(msgs: Message[], p: Pending): boolean {
  const from = p.after ? msgs.findIndex((m) => m.id === p.after) : -1;  // -1: scrolled out, all are newer
  return msgs.slice(from + 1).some((m) => m.role === "user" && sameText(m.text, p.text));
}

function stopLive(): void {
  if (live) clearTimeout(live.timer);
  live = null;
}

/** Open (or force-refresh) the drawer's current tab. */
async function loadTab(): Promise<void> {
  if (!drawer) return;
  const key = liveKey(drawer.row, drawer.tab);
  if (live?.key !== key) {
    stopLive();
    live = { key, version: "", timer: 0, busy: false, loaded: false, messages: [], nodes: new Map(),
      pending: [], model: null, contextTokens: null, logs: null,
      screen: null, screenAt: 0, screenBusy: false, screenError: null, promptCard: null };
    el("d-body", HTMLDivElement).replaceChildren(h("p", { class: "muted" }, "Đang tải…"));
  } else {
    live.version = "";
  }
  await pollLive();
}

async function pollLive(): Promise<void> {
  const cur = live;
  if (!cur || !drawer) return;
  clearTimeout(cur.timer);
  if (!document.hidden && !cur.busy) {
    cur.busy = true;
    try {
      const row = drawer.row;
      if (wantsScreen(cur, row)) void fetchScreen(cur, row);  // slow (attach): never blocks the transcript
      if (drawer.tab === "logs" && canDrive(row)) renderScreenTab(cur);
      else if (drawer.tab === "logs") await pollLogs(cur, row);
      else await pollTranscript(cur, row);
    } catch (e) {
      if (!(e instanceof Unauthorized) && live === cur && !cur.loaded) {
        el("d-body", HTMLDivElement).replaceChildren(h("p", { class: "error" }, `Không tải được: ${String(e)}`));
      }
    } finally {
      cur.busy = false;
    }
  }
  if (live !== cur) return;
  const fast = drawer?.row.state === "working" || cur.pending.length > 0;
  cur.timer = window.setTimeout(() => { void pollLive(); }, fast ? LIVE_FAST_MS : LIVE_SLOW_MS);
}

/** Run `update`, then stay pinned to the bottom if the reader was there; else keep their place. */
function keepScroll(update: () => void, toBottom: boolean): void {
  const body = el("d-body", HTMLDivElement);
  const atBottom = toBottom || body.scrollHeight - body.scrollTop - body.clientHeight < NEAR_BOTTOM_PX;
  const top = body.scrollTop;
  update();
  body.scrollTop = atBottom ? body.scrollHeight : top;
}

async function pollLogs(cur: Live, s: Row): Promise<void> {
  const d = obj(await api(`${mpath(s.machine)}/${encodeURIComponent(s.id)}/logs`));
  const logs = str(d.logs) || "(trống)";
  if (live !== cur || logs === cur.logs) return;
  const first = !cur.loaded;
  cur.logs = logs; cur.loaded = true;
  keepScroll(() => el("d-body", HTMLDivElement).replaceChildren(h("pre", { class: "logs" }, logs)), first);
}

async function pollTranscript(cur: Live, s: Row): Promise<void> {
  if (!s.sessionId) {
    cur.loaded = true;
    el("d-body", HTMLDivElement).replaceChildren(h("p", { class: "muted" }, "Không có transcript."));
    return;
  }
  const q = new URLSearchParams({ limit: String(TRANSCRIPT_LIMIT) });
  if (cur.version) q.set("since", cur.version);
  const t = parseTranscript(await api(`${mpath(s.machine)}/${encodeURIComponent(s.sessionId)}/transcript?${q.toString()}`));
  if (live !== cur) return;
  if (!t.unchanged) {
    cur.version = t.version; cur.messages = t.messages;
    cur.model = t.model ?? cur.model; cur.contextTokens = t.contextTokens ?? cur.contextTokens;
  }
  const first = !cur.loaded;
  cur.loaded = true;
  renderConversation(cur, first);
  if (first || !t.unchanged) renderDrawerHead();
  // The session's state (working → done) lives in the fleet list, polled every REFRESH_MS.
  // Claude's daemon flips it a few seconds after the last reply, so while someone watches
  // a working session, follow it at the drawer's pace instead.
  if (!first && (!t.unchanged || s.state === "working")) void refresh();
}

/** The "Claude is working" line: which tool runs right now, if any. */
function workingNode(msgs: Message[]): HTMLDivElement | null {
  if (drawer?.row.state !== "working") return null;
  const last = msgs[msgs.length - 1];
  const doing = last?.role === "tool" && last.tool?.result === null ? `đang chạy ${last.text}` : "đang làm việc";
  return h("div", { class: "msg working", "aria-live": "polite" }, h("span", { class: "dots" }), `Claude ${doing}…`);
}

function renderConversation(cur: Live, toBottom: boolean): void {
  const msgs = cur.messages;
  const now = Date.now();
  cur.pending = cur.pending.filter((p) => now - p.at < PENDING_TTL_MS && !arrived(msgs, p));
  const next = new Map<string, { sig: string; node: HTMLElement }>();
  const nodes: Node[] = msgs.map((m, k) => {
    const id = m.id ?? `i${k}`;
    const sig = JSON.stringify(m);
    const old = cur.nodes.get(id);
    const node = old?.sig === sig ? old.node : messageNode(m);
    const who = node.querySelector(":scope > .who");
    if (node === old?.node && who && m.role !== "tool") who.textContent = `${ROLE_LABEL[m.role]} · ${ago(m.ts)}`;
    if (old && old.node !== node && old.node.querySelector("details")?.open) {
      node.querySelector("details")?.setAttribute("open", "");
    }
    next.set(id, { sig, node });
    return node;
  });
  cur.nodes = next;
  nodes.push(...cur.pending.map((p) => h("div", { class: "msg user pending" },
    h("div", { class: "who" }, `${ROLE_LABEL.user} · đã gửi, chờ Claude nhận…`), p.text)));
  const working = workingNode(msgs);
  if (working) nodes.push(working);
  const card = promptCard(cur);
  if (card) nodes.push(card);
  if (!nodes.length) nodes.push(h("p", { class: "muted" }, "Chưa có tin nhắn."));
  keepScroll(() => el("d-body", HTMLDivElement).replaceChildren(...nodes), toBottom);
}

function openDrawer(row: Row): void {
  if (drawer && liveKey(drawer.row, drawer.tab) !== liveKey(row, "transcript")) stopLive();  // no stale model/tokens in the head
  drawer = { row, tab: "transcript" };
  el("drawer", HTMLDivElement).hidden = false;
  renderDrawerHead();
  void loadTab();
}
function closeDrawer(): void { drawer = null; stopLive(); messageFiles?.clear(); el("drawer", HTMLDivElement).hidden = true; }

async function sendMessage(e: Event): Promise<void> {
  e.preventDefault();
  if (!drawer) return;
  const s = drawer.row;
  const text = el("d-text", HTMLTextAreaElement);
  const force = el("d-force", HTMLInputElement);
  const target = s.pid != null ? s.id : (s.sessionId ?? s.id);
  const submit = el("d-compose", HTMLFormElement).querySelector("button[type=submit]");
  const note = el("d-compose-note", HTMLSpanElement);
  const noteText = note.textContent;
  if (submit instanceof HTMLButtonElement) submit.disabled = true;
  // Show the message at once, as a terminal would; stopping/resuming the session takes seconds.
  const typed = text.value;
  const shownIn = live && drawer.tab === "transcript" ? live : null;
  const bubble: Pending = { text: typed, at: Date.now(), after: shownIn?.messages.at(-1)?.id ?? null };
  if (shownIn) { shownIn.pending.push(bubble); renderConversation(shownIn, true); }
  try {
    const paths = await messageFiles?.upload(s.machine) ?? [];
    const message = withPaths(typed, paths);
    const r = canDrive(s)
      ? await typeInto(s, message)
      : await write("POST", `${mpath(s.machine)}/${encodeURIComponent(target)}/message`, { text: message, force: force.checked });
    text.value = ""; force.checked = false; messageFiles?.clear();
    // A resume can give the job a new short id; keep the drawer on it.
    if (drawer?.row === s && r.id && r.id !== s.id) drawer.row = { ...s, id: r.id };
    toast(`Đã gửi tới ${r.id ?? s.id}`);
    await refresh();
    void pollLive();
  } catch (err) {
    if (shownIn) {
      shownIn.pending = shownIn.pending.filter((p) => p !== bubble);
      if (live === shownIn) renderConversation(shownIn, false);
    }
    if (!(err instanceof Unauthorized)) toast(`Gửi lỗi: ${err instanceof Error ? err.message : String(err)}`, true);
  } finally {
    note.textContent = noteText;
    if (submit instanceof HTMLButtonElement) submit.disabled = false;
  }
}

// ---------------------------------------------------------------- session terminal (answer, mode, esc)
// fleetd attaches to a running background session and presses keys as a person would,
// so everything the terminal offers (plan approval, permission prompts, questions,
// shift+tab modes, esc) works from the web dashboard, Fleet.app and the phone alike.
const MIN_DRIVE_API = 8;
const SCREEN_MS = 3000;
const SETTABLE_MODES: readonly SettableMode[] = ["manual", "auto", "acceptEdits", "plan"];
const MODE_LABEL: Record<SessionMode, string> = {
  default: "Mặc định", manual: "Manual: hỏi trước mọi thao tác", auto: "Auto: tự quyết, hỏi khi rủi ro",
  acceptEdits: "Accept edits: tự sửa file", plan: "Plan: chỉ lập plan", bypassPermissions: "Bypass permissions",
  dontAsk: "Don't ask",
};
const PROMPT_LABEL: Record<PromptKind, string> = {
  plan: "Claude xin duyệt plan", permission: "Claude xin quyền", question: "Claude hỏi bạn", menu: "Claude đang chờ bạn chọn",
};

const apiLevelOf = (machine: string): number => fleet?.machines.find((m) => m.machine === machine)?.apiLevel ?? 0;

/** A running background session on a Fleet new enough to attach to it. */
const canDrive = (s: Row): boolean => s.kind === "background" && s.pid != null && !s.stale && apiLevelOf(s.machine) >= MIN_DRIVE_API;

function wantsScreen(cur: Live, s: Row): boolean {
  if (!canDrive(s) || cur.screenBusy) return false;
  if (cur.screenAt === 0) return true;  // once on open: the mode
  const watching = s.state === "needs_input" || drawer?.tab === "logs";
  return watching && Date.now() - cur.screenAt >= SCREEN_MS;
}

const inputPath = (s: Row): string => `${mpath(s.machine)}/${encodeURIComponent(s.id)}/input`;

async function fetchScreen(cur: Live, s: Row): Promise<void> {
  cur.screenBusy = true;
  try {
    const v = parseScreen(await api(`${mpath(s.machine)}/${encodeURIComponent(s.id)}/screen`));
    if (live !== cur) return;
    cur.screen = v; cur.screenError = null;
  } catch (e) {
    if (live === cur && !(e instanceof Unauthorized)) cur.screenError = e instanceof Error ? e.message : String(e);
  } finally {
    cur.screenAt = Date.now();
    cur.screenBusy = false;
  }
  showScreen(cur);
}

function showScreen(cur: Live): void {
  if (live !== cur || !drawer) return;
  renderControl();
  if (drawer.tab === "logs") renderScreenTab(cur);
  else if (cur.loaded) renderConversation(cur, false);
}

function renderScreenTab(cur: Live): void {
  const text = cur.screen?.text ?? (cur.screenError ? `Không đọc được màn hình: ${cur.screenError}` : "Đang đọc màn hình…");
  const first = !cur.loaded;
  cur.loaded = true;
  keepScroll(() => el("d-body", HTMLDivElement).replaceChildren(h("pre", { class: "logs screen" }, text)), first);
}

/** Send one input; the reply is the screen afterwards. */
async function sendInput(s: Row, input: SessionInput, done: string): Promise<boolean> {
  const cur = live;
  cur?.promptCard?.node.classList.add("busy");
  try {
    const v = parseScreen(await send("POST", inputPath(s), { input }));
    if (cur && live === cur) { cur.screen = v; cur.screenAt = Date.now(); }
    toast(done);
    await refresh();
    void pollLive();
    return true;
  } catch (err) {
    cur?.promptCard?.node.classList.remove("busy");
    if (!(err instanceof Unauthorized)) toast(`Không gửi được: ${err instanceof Error ? err.message : String(err)}`, true);
    return false;
  } finally {
    if (cur) showScreen(cur);
  }
}

async function typeInto(s: Row, text: string): Promise<ActionResult> {
  const cur = live;  // the drawer may switch sessions while this is in flight
  const v = parseScreen(await send("POST", inputPath(s), { input: { action: "type", text } }));
  if (cur && live === cur) { cur.screen = v; cur.screenAt = Date.now(); }
  return { id: s.id, action: "type", output: "" };
}

/** The menu Claude waits on, as buttons; rebuilt only when the menu itself changes (typed feedback survives polls). */
function promptCard(cur: Live): HTMLElement | null {
  const s = drawer?.row;
  const p = cur.screen?.prompt;
  if (!s || !p || s.state !== "needs_input" || !canDrive(s)) return null;
  const sig = JSON.stringify(p);
  if (cur.promptCard?.sig !== sig) cur.promptCard = { sig, node: buildPromptCard(p, s) };
  return cur.promptCard.node;
}

function buildPromptCard(p: SessionPrompt, s: Row): HTMLElement {
  let chosen: PromptOption | null = null;
  const feedback = h("textarea", { rows: "3", placeholder: "Nội dung gửi kèm…" });
  const sendBtn = h("button", { type: "button", class: "primary" }, "Gửi");
  const textBox = h("div", { class: "prompt-text", hidden: true }, feedback,
    h("div", { class: "prompt-text-actions" },
      h("button", { type: "button", onclick: () => { textBox.hidden = true; chosen = null; } }, "Huỷ"), sendBtn));
  sendBtn.addEventListener("click", () => {
    const text = feedback.value.trim();
    if (!chosen || !text) { feedback.focus(); return; }
    void sendInput(s, { action: "choose", option: chosen.key, title: p.title, label: chosen.label, text },
      `Đã gửi: ${chosen.label}`);
  });
  const options = p.options.map((o) => h("button", {
    type: "button", class: `prompt-option${o.selected ? " selected" : ""}`,
    onclick: () => {
      if (o.needsText) {
        chosen = o; textBox.hidden = false; sendBtn.textContent = `Gửi · ${o.label}`; feedback.focus();
        return;
      }
      void sendInput(s, { action: "choose", option: o.key, title: p.title, label: o.label }, `Đã chọn: ${o.label}`);
    },
  }, h("span", { class: "key" }, o.key), h("span", { class: "label" }, o.label),
  o.detail ? h("span", { class: "detail" }, o.detail) : null));
  return h("div", { class: `msg prompt ${p.kind}`, role: "group", "aria-label": PROMPT_LABEL[p.kind] },
    h("div", { class: "who" }, PROMPT_LABEL[p.kind]),
    p.title ? h("div", { class: "prompt-title" }, p.title) : null,
    p.body ? h("pre", { class: "prompt-body" }, p.body) : null,
    h("div", { class: "prompt-options" }, options), textBox);
}

let controlSig = "";

/** Mode picker and Esc for a running background session. */
function renderControl(): void {
  const box = el("d-control", HTMLDivElement);
  const s = drawer?.row;
  if (!s || !canDrive(s)) { box.hidden = true; box.replaceChildren(); controlSig = ""; return; }
  const mode = live?.screen?.mode ?? null;
  const sig = [s.machine, s.id, s.state, mode, live?.screenError ?? ""].join("|");
  if (!box.hidden && sig === controlSig) return;  // rebuilding would close an open picker
  controlSig = sig;
  const menuOpen = s.state === "needs_input";
  const sel = h("select", { "aria-label": "Mode của session" },
    mode === null ? h("option", { value: "", selected: true, disabled: true },
      menuOpen ? "Trả lời câu hỏi trước" : live?.screenError ? "Không rõ" : "Đang đọc mode…") : null,
    mode !== null && !SETTABLE_MODES.some((m) => m === mode) ? h("option", { value: mode, selected: true, disabled: true }, MODE_LABEL[mode]) : null,
    SETTABLE_MODES.map((m) => h("option", { value: m, selected: m === mode }, MODE_LABEL[m])));
  const working = s.state === "working";
  sel.disabled = menuOpen || working || mode === null;
  sel.title = menuOpen ? "Trả lời câu hỏi đang chờ trước, rồi mới đổi mode"
    : working ? "Chỉ đổi mode khi Claude đang rảnh: đợi xong hoặc bấm Esc" : "Như Shift+Tab trong terminal";
  sel.addEventListener("change", () => {
    const target = SETTABLE_MODES.find((m) => m === sel.value);
    if (target) void sendInput(s, { action: "mode", mode: target }, `Đã chuyển sang ${MODE_LABEL[target]}`);
  });
  const esc = h("button", {
    type: "button", title: "Như phím Esc: ngắt Claude đang làm, hoặc đóng câu hỏi đang chờ",
    onclick: () => { void sendInput(s, { action: "key", key: "esc" }, "Đã gửi Esc"); },
  }, "⎋ Ngắt (Esc)");
  esc.disabled = s.state !== "working" && !menuOpen;
  box.hidden = false;
  const problem = live?.screenError ? [h("span", { class: "muted" }, "Không đọc được màn hình")] : [];
  box.replaceChildren(h("label", { class: "mode" }, "Mode", sel), esc, ...problem);
}

// ---------------------------------------------------------------- new session modal
function openNewSession(): void {
  const machines = (fleet?.machines ?? []).filter((m) => m.online);
  if (!machines.length) { toast("Không có máy nào online.", true); return; }
  const sel = el("n-machine", HTMLSelectElement);
  sel.replaceChildren(...machines.map((m) => h("option", { value: m.machine }, m.label)));
  const preferred = drawer?.row.machine;
  if (preferred && machines.some((m) => m.machine === preferred)) sel.value = preferred;
  fillCwds();
  el("modal", HTMLDivElement).hidden = false;
  // On a phone, focusing opens the keyboard over the form before the user has
  // picked a machine or folder; they tap the prompt themselves when ready.
  if (!isTouchScreen()) el("n-prompt", HTMLTextAreaElement).focus();
}

function isTouchScreen(): boolean {
  return window.matchMedia("(pointer: coarse)").matches;
}

function fillCwds(): void {
  const m = fleet?.machines.find((x) => x.machine === el("n-machine", HTMLSelectElement).value);
  const cwds = [...new Set([...(m?.sessions ?? []), ...(m?.history ?? [])].map((s) => shortPath(s.cwd)).filter(Boolean))];
  el("n-cwds", HTMLDataListElement).replaceChildren(...cwds.map((c) => h("option", { value: c })));
  const cwd = el("n-cwd", HTMLInputElement);
  if (!cwd.value && cwds[0]) cwd.value = cwds[0];
  closeFolderBrowser();
  const browse = el("n-browse", HTMLButtonElement);
  browse.disabled = !m || apiLevelOf(m.machine) < MIN_FOLDERS_API;
  browse.title = browse.disabled ? "Máy này chạy Fleet bản cũ: cập nhật để chọn thư mục" : "Duyệt thư mục trên máy đã chọn";
}

// ---------------------------------------------------------------- folder picker (new session)
const MIN_FOLDERS_API = 9;
interface Folders { path: string; parent: string | null; folders: string[]; truncated: boolean }

function parseFolders(v: unknown): Folders {
  const o = obj(v);
  return {
    path: str(o.path) ?? "~", parent: str(o.parent), truncated: bool(o.truncated),
    folders: list(o.folders).flatMap((f) => (typeof f === "string" ? [f] : [])),
  };
}

let browseSeq = 0;  // a slow reply for a folder already left must not replace the newer listing

function closeFolderBrowser(): void {
  browseSeq++;
  el("n-browser", HTMLDivElement).hidden = true;
  el("n-browse", HTMLButtonElement).setAttribute("aria-expanded", "false");
}

function toggleFolderBrowser(): void {
  if (!el("n-browser", HTMLDivElement).hidden) { closeFolderBrowser(); return; }
  el("n-browse", HTMLButtonElement).setAttribute("aria-expanded", "true");
  void browseFolder(el("n-cwd", HTMLInputElement).value.trim());
}

async function browseFolder(path: string): Promise<void> {
  const box = el("n-browser", HTMLDivElement);
  const machine = el("n-machine", HTMLSelectElement).value;
  const seq = ++browseSeq;
  box.hidden = false;
  box.replaceChildren(h("div", { class: "fb-note muted" }, "Đang tải…"));
  try {
    const query = new URLSearchParams({ path });
    const folders = parseFolders(await api(`/api/m/${encodeURIComponent(machine)}/folders?${query}`));
    if (seq === browseSeq) renderFolders(box, folders);
  } catch (err) {
    if (seq !== browseSeq || err instanceof Unauthorized) return;
    const msg = err instanceof Error ? err.message : String(err);
    // A half-typed path usually does not exist: offer home instead of a dead end.
    box.replaceChildren(h("div", { class: "fb-note error" }, `Không mở được ${path || "~"}: ${msg}`),
      ...(path ? [h("div", { class: "fb-note" },
        h("button", { type: "button", onclick: () => { void browseFolder(""); } }, "Mở thư mục home (~)"))] : []));
  }
}

function renderFolders(box: HTMLDivElement, f: Folders): void {
  const parent = f.parent;
  const up = h("button", {
    type: "button", class: "ghost", "aria-label": "Lên thư mục cha", title: parent ?? "Đang ở thư mục home",
    onclick: () => { if (parent) void browseFolder(parent); },
  }, "↑");
  up.disabled = parent === null;
  const choose = h("button", {
    type: "button", class: "primary",
    onclick: () => { el("n-cwd", HTMLInputElement).value = f.path; closeFolderBrowser(); },
  }, "Chọn");
  const items = f.folders.map((name) => h("li", {},
    h("button", { type: "button", onclick: () => { void browseFolder(`${f.path}/${name}`); } }, `📁 ${name}`)));
  box.replaceChildren(
    h("div", { class: "fb-head" }, up, h("code", { class: "fb-path" }, f.path), choose),
    items.length ? h("ul", { "aria-label": `Thư mục con của ${f.path}` }, items)
      : h("div", { class: "fb-note muted" }, "Không có thư mục con."),
    ...(f.truncated ? [h("div", { class: "fb-note muted" }, `Chỉ hiện ${f.folders.length} thư mục đầu tiên.`)] : []));
}

async function startSession(e: Event): Promise<void> {
  e.preventDefault();
  const machine = el("n-machine", HTMLSelectElement).value;
  const prompt = el("n-prompt", HTMLTextAreaElement);
  const submit = el("new-form", HTMLFormElement).querySelector("button[type=submit]");
  if (submit instanceof HTMLButtonElement) submit.disabled = true;
  try {
    const paths = await sessionFiles?.upload(machine) ?? [];
    const r = await write("POST", mpath(machine), {
      cwd: el("n-cwd", HTMLInputElement).value.trim(), prompt: withPaths(prompt.value, paths),
      name: el("n-name", HTMLInputElement).value.trim() || null,
      permissionMode: el("n-mode", HTMLSelectElement).value || null,
    });
    prompt.value = ""; el("n-name", HTMLInputElement).value = ""; sessionFiles?.clear();
    el("modal", HTMLDivElement).hidden = true;
    toast(`Đã tạo session ${r.id ?? ""}`);
    await refresh();
  } catch (err) {
    if (!(err instanceof Unauthorized)) toast(`Không tạo được: ${err instanceof Error ? err.message : String(err)}`, true);
  } finally {
    el("n-note", HTMLSpanElement).textContent = "";
    if (submit instanceof HTMLButtonElement) submit.disabled = false;
  }
}

// ---------------------------------------------------------------- wiring
async function enableNotifications(): Promise<void> {
  const btn = el("notify", HTMLButtonElement);
  if (!("Notification" in window)) { toast("Trình duyệt không hỗ trợ thông báo (cần HTTPS / Add to Home Screen trên iPhone).", true); return; }
  const p = await Notification.requestPermission();
  btn.title = p === "granted" ? "Đã bật thông báo" : "Thông báo bị chặn";
  btn.style.opacity = p === "granted" ? "1" : ".5";
}

function start(): void {
  el("login", HTMLDivElement).hidden = true;
  el("app", HTMLDivElement).hidden = false;
  void refresh();
  clearInterval(timer);
  timer = window.setInterval(() => { void refresh(); }, REFRESH_MS);
}

// ---------------------------------------------------------------- handoff (Phase 4)
interface HandoffPreview { cwd: string; warnings: string[] }
let handoffCtx: { row: Row; target: Machine } | null = null;

function handoffButtons(s: Row): HTMLButtonElement[] {
  const sid = s.sessionId;
  if (!sid || s.stale || (s.kind === "interactive" && s.pid != null)) return [];
  return (fleet?.machines ?? [])
    .filter((m) => m.online && m.machine !== s.machine)
    .map((m) => h("button", { onclick: () => openHandoff(s, m) }, `Chuyển sang ${m.label}`));
}

function handoffBody(dryRun: boolean): Obj {
  if (!handoffCtx) return {};
  const cwd = el("h-cwd", HTMLInputElement).value.trim();
  const message = el("h-message", HTMLTextAreaElement).value.trim();
  return {
    source: handoffCtx.row.machine, target: handoffCtx.target.machine, sessionId: handoffCtx.row.sessionId,
    targetCwd: cwd || null, message: message || null, dryRun,
    force: el("h-force", HTMLInputElement).checked, overwrite: el("h-overwrite", HTMLInputElement).checked,
  };
}

function parsePreview(v: unknown): HandoffPreview {
  const o = obj(v);
  return { cwd: str(o.cwd) ?? "", warnings: list(o.warnings).flatMap((w) => (typeof w === "string" ? [w] : [])) };
}

async function previewHandoff(): Promise<void> {
  if (!handoffCtx) return;
  const box = el("h-preview", HTMLDivElement);
  const go = el("h-go", HTMLButtonElement);
  box.replaceChildren(h("p", { class: "muted" }, "Đang kiểm tra hai máy…"));
  go.disabled = true;
  try {
    const p = parsePreview(await send("POST", "/api/handoff", handoffBody(true)));
    const cwd = el("h-cwd", HTMLInputElement);
    if (!cwd.value) cwd.value = p.cwd;
    box.replaceChildren(...(p.warnings.length
      ? p.warnings.map((w) => h("div", { class: "warn" }, `⚠ ${w}`))
      : [h("div", { class: "ok" }, `✓ Sẵn sàng: sẽ ghi vào ${p.cwd} trên ${handoffCtx.target.label}.`)]));
    go.disabled = false;
  } catch (e) {
    if (e instanceof Unauthorized) return;
    const msg = e instanceof Error ? e.message : String(e);
    // let the user opt in to the two recoverable refusals, then re-check
    if (msg.includes("đang chạy dở")) el("h-force-wrap", HTMLLabelElement).hidden = false;
    if (msg.includes("đã có transcript")) el("h-overwrite-wrap", HTMLLabelElement).hidden = false;
    box.replaceChildren(h("div", { class: "err" }, msg));
  }
}

function openHandoff(row: Row, target: Machine): void {
  handoffCtx = { row, target };
  el("h-route", HTMLParagraphElement).textContent =
    `${row.name ?? row.id}: ${row.mlabel} → ${target.label}`;
  el("h-cwd", HTMLInputElement).value = "";
  el("h-message", HTMLTextAreaElement).value = "";
  el("h-force", HTMLInputElement).checked = false;
  el("h-overwrite", HTMLInputElement).checked = false;
  el("h-force-wrap", HTMLLabelElement).hidden = row.state !== "working";
  el("h-overwrite-wrap", HTMLLabelElement).hidden = true;
  el("handoff", HTMLDivElement).hidden = false;
  void previewHandoff();
}

async function runHandoff(e: Event): Promise<void> {
  e.preventDefault();
  if (!handoffCtx) return;
  const { target } = handoffCtx;
  const go = el("h-go", HTMLButtonElement);
  go.disabled = true;
  try {
    const r = obj(await send("POST", "/api/handoff", handoffBody(false)));
    el("handoff", HTMLDivElement).hidden = true;
    const resumed = str(r.resumedId);
    toast(resumed ? `Đã chuyển sang ${target.label} và chạy tiếp (${resumed}).`
                  : `Đã chuyển sang ${target.label}. Gửi tin nhắn ở đó để chạy tiếp.`);
    closeDrawer();
    await refresh();
  } catch (err) {
    if (!(err instanceof Unauthorized)) {
      el("h-preview", HTMLDivElement).replaceChildren(
        h("div", { class: "err" }, err instanceof Error ? err.message : String(err)));
    }
  } finally {
    go.disabled = false;
  }
}

function wire(): void {
  el("handoff-form", HTMLFormElement).addEventListener("submit", (e) => { void runHandoff(e); });
  el("h-close", HTMLButtonElement).addEventListener("click", () => { el("handoff", HTMLDivElement).hidden = true; });
  el("h-recheck", HTMLButtonElement).addEventListener("click", () => { void previewHandoff(); });
  for (const id of ["h-force", "h-overwrite"]) {
    el(id, HTMLInputElement).addEventListener("change", () => { void previewHandoff(); });
  }
  el("login-form", HTMLFormElement).addEventListener("submit", (e) => { void login(e); });
  el("d-close", HTMLButtonElement).addEventListener("click", closeDrawer);
  el("d-reload", HTMLButtonElement).addEventListener("click", () => { void loadTab(); });
  document.addEventListener("click", (e) => { closeMoreMenus(e.target instanceof Node ? e.target : undefined); });
  el("drawer", HTMLDivElement).addEventListener("click", (e) => { if (e.target === e.currentTarget) closeDrawer(); });
  el("d-compose", HTMLFormElement).addEventListener("submit", (e) => { void sendMessage(e); });
  messageFiles = wireAttachments("d", el("d-compose", HTMLFormElement), el("d-text", HTMLTextAreaElement), "d-compose-note");
  sessionFiles = wireAttachments("n", el("new-form", HTMLFormElement), el("n-prompt", HTMLTextAreaElement), "n-note");
  for (const b of document.querySelectorAll<HTMLButtonElement>(".tabs button")) {
    b.addEventListener("click", () => {
      const tab = b.dataset.tab;
      if (!drawer || (tab !== "transcript" && tab !== "logs")) return;
      drawer.tab = tab;
      renderDrawerHead();
      void loadTab();
    });
  }
  el("new-session", HTMLButtonElement).addEventListener("click", openNewSession);
  el("n-close", HTMLButtonElement).addEventListener("click", () => { el("modal", HTMLDivElement).hidden = true; });
  el("n-machine", HTMLSelectElement).addEventListener("change", fillCwds);
  el("n-browse", HTMLButtonElement).addEventListener("click", toggleFolderBrowser);
  el("new-form", HTMLFormElement).addEventListener("submit", (e) => { void startSession(e); });
  el("q", HTMLInputElement).addEventListener("input", render);
  const hist = el("show-history", HTMLInputElement);
  hist.checked = store.get("fleet.history", "0") === "1";
  hist.addEventListener("change", () => { store.set("fleet.history", hist.checked ? "1" : "0"); render(); });
  el("notify", HTMLButtonElement).addEventListener("click", () => { void enableNotifications(); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (document.querySelector("details.more[open]")) closeMoreMenus();
    else if (!el("handoff", HTMLDivElement).hidden) el("handoff", HTMLDivElement).hidden = true;
    else if (!el("modal", HTMLDivElement).hidden) el("modal", HTMLDivElement).hidden = true;
    else if (drawer) closeDrawer();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !timer) return;
    void refresh();
    void pollLive();  // back from the background (iPhone): catch up at once
  });
}

wire();
start();
