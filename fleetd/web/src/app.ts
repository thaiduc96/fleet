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
interface Fleet { machines: Machine[] }
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
interface Message { role: Role; text: string; ts: string | null; blocks: Block[] | null }
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

const parseFleet = (v: unknown): Fleet => ({ machines: list(obj(v).machines).map(parseMachine) });

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

function parseMessages(v: unknown): Message[] {
  return list(obj(v).messages).flatMap((m) => {
    const o = obj(m);
    const text = str(o.text);
    const blocks = Array.isArray(o.blocks) ? parseBlocks(o.blocks) : null;
    return isRole(o.role) && text !== null ? [{ role: o.role, text, ts: str(o.ts), blocks }] : [];
  });
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
    fleet = f;
    notifyChanges(f);
    render();
    focusFromHash(f);
    el("updated", HTMLSpanElement).textContent = `cập nhật ${new Date().toLocaleTimeString("vi-VN")}`;
  } catch (e) {
    if (!(e instanceof Unauthorized)) el("updated", HTMLSpanElement).textContent = `lỗi: ${String(e)}`;
  }
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
    const fresh = all.find((s) => s.machine === cur.row.machine && s.id === cur.row.id);
    if (fresh) { cur.row = fresh; renderDrawerHead(); }
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

function renderCompose(s: Row): void {
  const form = el("d-compose", HTMLFormElement);
  const note = el("d-compose-note", HTMLSpanElement);
  const forceWrap = el("d-force-wrap", HTMLLabelElement);
  const openInTerminal = s.kind === "interactive" && s.pid != null;
  form.hidden = s.stale || !s.sessionId || openInTerminal;
  forceWrap.hidden = s.state !== "working";
  note.textContent = s.state === "working" ? "Session đang chạy dở." :
    s.pid != null ? "Session sẽ được dừng rồi tiếp tục với tin nhắn này." :
    "Session sẽ được mở lại ở chế độ nền với tin nhắn này.";
}

function renderDrawerHead(): void {
  if (!drawer) return;
  const s = drawer.row;
  el("d-title", HTMLHeadingElement).textContent = s.name ?? s.id;
  el("d-meta", HTMLDivElement).textContent = [s.mlabel, STATE_LABEL[s.state], shortPath(s.cwd),
    s.costUSD != null ? `$${s.costUSD.toFixed(2)}` : null, ago(s.updatedAt ?? s.startedAt)]
    .filter(Boolean).join(" · ");
  const acts: Node[] = [...actionButtons(s)];
  if (s.kind === "background") acts.push(copyBtn("Copy attach", `claude attach ${s.id}`));
  if (s.sessionId) acts.push(copyBtn("Copy resume", `cd ${shortPath(s.cwd)} && claude --resume ${s.sessionId}`));
  acts.push(...handoffButtons(s));
  acts.push(h("button", { onclick: () => { void loadTab(); } }, "Tải lại"));
  acts.push(h("a", { href: "https://claude.ai/code", target: "_blank", rel: "noopener" }, "claude.ai/code ↗"));
  el("d-actions", HTMLDivElement).replaceChildren(...acts);
  for (const b of document.querySelectorAll<HTMLButtonElement>(".tabs button")) {
    b.classList.toggle("active", b.dataset.tab === drawer.tab);
    if (b.dataset.tab === "logs") b.hidden = s.kind !== "background";
  }
  renderCompose(s);
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

function messageNode(m: Message): HTMLDivElement {
  if (m.role === "tool") return h("div", { class: "msg tool" }, `→ ${m.text}`);
  const who = h("div", { class: "who" }, `${ROLE_LABEL[m.role]} · ${ago(m.ts)}`);
  // Old fleetd peers send no blocks: keep their plain text.
  if (m.blocks && m.blocks.length) return h("div", { class: `msg ${m.role} md` }, who, renderBlocks(m.blocks));
  return h("div", { class: `msg ${m.role}` }, who, m.text);
}

async function loadTab(): Promise<void> {
  if (!drawer) return;
  const { row: s, tab } = drawer;
  const body = el("d-body", HTMLDivElement);
  body.replaceChildren(h("p", { class: "muted" }, "Đang tải…"));
  try {
    switch (tab) {
      case "logs": {
        const d = obj(await api(`${mpath(s.machine)}/${encodeURIComponent(s.id)}/logs`));
        body.replaceChildren(h("pre", { class: "logs" }, str(d.logs) || "(trống)"));
        break;
      }
      case "transcript": {
        if (!s.sessionId) { body.replaceChildren(h("p", { class: "muted" }, "Không có transcript.")); return; }
        const msgs = parseMessages(await api(`${mpath(s.machine)}/${encodeURIComponent(s.sessionId)}/transcript?limit=120`));
        body.replaceChildren(...(msgs.length ? msgs.map(messageNode) : [h("p", { class: "muted" }, "Chưa có tin nhắn.")]));
        break;
      }
      default: {
        const never: never = tab;
        throw new Error(`unknown tab ${String(never)}`);
      }
    }
    body.scrollTop = body.scrollHeight;
  } catch (e) {
    if (!(e instanceof Unauthorized)) body.replaceChildren(h("p", { class: "error" }, `Không tải được: ${String(e)}`));
  }
}

function openDrawer(row: Row): void {
  drawer = { row, tab: "transcript" };
  el("drawer", HTMLDivElement).hidden = false;
  renderDrawerHead();
  void loadTab();
}
function closeDrawer(): void { drawer = null; el("drawer", HTMLDivElement).hidden = true; }

async function sendMessage(e: Event): Promise<void> {
  e.preventDefault();
  if (!drawer) return;
  const s = drawer.row;
  const text = el("d-text", HTMLTextAreaElement);
  const force = el("d-force", HTMLInputElement);
  const target = s.pid != null ? s.id : (s.sessionId ?? s.id);
  const submit = el("d-compose", HTMLFormElement).querySelector("button[type=submit]");
  if (submit instanceof HTMLButtonElement) submit.disabled = true;
  try {
    const r = await write("POST", `${mpath(s.machine)}/${encodeURIComponent(target)}/message`,
      { text: text.value, force: force.checked });
    text.value = ""; force.checked = false;
    toast(`Đã gửi tới ${r.id ?? s.id}`);
    await refresh();
    void loadTab();
  } catch (err) {
    if (!(err instanceof Unauthorized)) toast(`Gửi lỗi: ${err instanceof Error ? err.message : String(err)}`, true);
  } finally {
    if (submit instanceof HTMLButtonElement) submit.disabled = false;
  }
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
  el("n-prompt", HTMLTextAreaElement).focus();
}

function fillCwds(): void {
  const m = fleet?.machines.find((x) => x.machine === el("n-machine", HTMLSelectElement).value);
  const cwds = [...new Set([...(m?.sessions ?? []), ...(m?.history ?? [])].map((s) => shortPath(s.cwd)).filter(Boolean))];
  el("n-cwds", HTMLDataListElement).replaceChildren(...cwds.map((c) => h("option", { value: c })));
  const cwd = el("n-cwd", HTMLInputElement);
  if (!cwd.value && cwds[0]) cwd.value = cwds[0];
}

async function startSession(e: Event): Promise<void> {
  e.preventDefault();
  const machine = el("n-machine", HTMLSelectElement).value;
  const prompt = el("n-prompt", HTMLTextAreaElement);
  try {
    const r = await write("POST", mpath(machine), {
      cwd: el("n-cwd", HTMLInputElement).value.trim(), prompt: prompt.value,
      name: el("n-name", HTMLInputElement).value.trim() || null,
      permissionMode: el("n-mode", HTMLSelectElement).value || null,
    });
    prompt.value = ""; el("n-name", HTMLInputElement).value = "";
    el("modal", HTMLDivElement).hidden = true;
    toast(`Đã tạo session ${r.id ?? ""}`);
    await refresh();
  } catch (err) {
    if (!(err instanceof Unauthorized)) toast(`Không tạo được: ${err instanceof Error ? err.message : String(err)}`, true);
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
  el("drawer", HTMLDivElement).addEventListener("click", (e) => { if (e.target === e.currentTarget) closeDrawer(); });
  el("d-compose", HTMLFormElement).addEventListener("submit", (e) => { void sendMessage(e); });
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
  el("new-form", HTMLFormElement).addEventListener("submit", (e) => { void startSession(e); });
  el("q", HTMLInputElement).addEventListener("input", render);
  const hist = el("show-history", HTMLInputElement);
  hist.checked = store.get("fleet.history", "0") === "1";
  hist.addEventListener("change", () => { store.set("fleet.history", hist.checked ? "1" : "0"); render(); });
  el("notify", HTMLButtonElement).addEventListener("click", () => { void enableNotifications(); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!el("handoff", HTMLDivElement).hidden) el("handoff", HTMLDivElement).hidden = true;
    else if (!el("modal", HTMLDivElement).hidden) el("modal", HTMLDivElement).hidden = true;
    else if (drawer) closeDrawer();
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && timer) void refresh(); });
}

wire();
start();
