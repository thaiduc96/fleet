# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Fleet shows and controls Claude Code sessions across several Macs. Each Mac runs **Fleet.app** (Swift menu bar app) which supervises **fleetd** (Python/FastAPI) as a child process. fleetd reads Claude Code's local state, drives the `claude` CLI, and serves a dashboard. Machines reach each other only over Tailscale (`tailscale serve`, HTTPS inside the tailnet); fleetd itself binds to `127.0.0.1`.

The app UI is in Vietnamese; README and code docs are in English.

## Commands

Python is managed with `uv` (Python 3.12+). No GitHub Actions; `scripts/test.sh` is the full check.

```bash
scripts/test.sh                          # pytest -q, ruff check fleetd tests, tsc --noEmit, swift test
uv run pytest -q                         # Python tests only
uv run pytest tests/test_attach.py -k choose   # single file / test
uv run ruff check fleetd tests           # lint (line length 110, rules E,F,W,I,B,UP,N,RUF)
npm run check                            # type-check the dashboard (npx typescript@5.9.3, no node_modules)
npm run build                            # recompile fleetd/web/app.js after editing fleetd/web/src/app.ts
swift test --package-path app            # FleetCore tests (test.sh adds flags when only CLT is installed)
scripts/build-app.sh 1.2.3-dev           # dist/Fleet.app + dist/Fleet-<ver>.zip + checksums.txt
swift scripts/make-icons.swift           # re-render app/Fleet.icns + fleetd/web/icons (both committed)
uv run fleetd                            # run the backend (config from FLEET_CONFIG or ~/.fleet/config.toml)
```

Dev builds next to an installed Fleet: `FLEET_DEV=1` (no LaunchAgents/notifications/updates) plus `FLEET_HOME=<dir>` with a different `port` in that dir's `config.toml`. `Fleet --render-previews <dir>` renders the app windows to PNGs.

Releasing: bump `VERSION`, merge to `main`, then `scripts/release.sh` on the machine holding `~/.fleet/release-signing-key` (runs all checks, builds, Ed25519-signs, tags, publishes). Apps only install signed releases; the public key is in `app/Sources/Fleet/Env.swift`.

## Architecture

- `app/` — Swift package. `FleetCore` is pure, unit-tested logic (invite codes, versions, release parsing, update signature, fleet summary). `Fleet` is the menu bar app: `Backend.swift` runs the bundled Python fleetd as a child (so macOS privacy prompts are attributed to Fleet.app), plus onboarding, invites, Keychain token, self-updater, LaunchAgent `com.fleet.app`, and a WKWebView dashboard window.
- `fleetd/main.py` — `create_app(...)` builds the FastAPI app. All I/O dependencies (claude CLI, peer HTTP client, clocks, sleep, process spawn, git, attacher, health) are injected parameters; tests pass fakes through this factory.
- **Claude Code internals are isolated** in `claude_cli.py` (`claude agents --json`, `~/.claude/jobs/*/state.json`, roster), `transcripts.py` (`~/.claude/projects/**/<sid>.jsonl`, parsed defensively, tail only) and `handoff.py` (on-disk session layout). A Claude Code change should only need fixes there.
- `snapshot.py` builds this machine's snapshot; `peers.py` fetches other machines' snapshots, caches the last good one in `~/.fleet/cache/` so offline machines still show, and gossips peer lists. `/api/fleet` merges them. Every `/api/m/<machine>/…` route proxies the same operation to another machine.
- `actions.py` — stop/respawn/remove/message. Messaging: `claude --bg --resume <id> "msg"` continues the *same* session only if it is not running, otherwise it starts a copy; see the module docstring for the state table. `claude stop` returns before the process exits, so actions wait for it.
- `attach.py` + `vt.py` + `prompts.py` — answer a running background session by `claude attach` in a pty, emulating the screen and parsing menus/mode. Rules are deliberately loose and fall back to "no prompt"; only keys for an option currently on screen are pressed, typed text has control characters stripped, one attach per session.
- `markdown.py` / `tool_calls.py` — transcripts become typed `Block`/`Inline`/`ToolCall` models; the browser builds DOM from them and never uses `innerHTML`.
- `state.py` — `~/.fleet/state.json` (keep-awake mode, peers, label, own URL). fleetd is the only writer; Fleet.app changes settings through the HTTP API, never the file.
- `awake.py` — runs `caffeinate … -w <fleetd pid>` so the assertion dies with fleetd.
- `models.py` — Pydantic API models: snake_case attributes, camelCase JSON aliases.
- `fleetd/web/` — dashboard. `src/app.ts` (strict TS, `module: none`, single file) compiles to `app.js`, which **is committed** so installing fleetd needs no Node. Always run `npm run build` and commit both. `index.html` is served with `?v=<content hash>` on assets plus no-cache headers so a browser never pairs a new page with a stale `app.js`.
- `bin/fleet`, `install.sh`, `scripts/install.sh` — the older script/LaunchAgent (`com.fleet.fleetd`) install path; Fleet.app migrates away from it.

## Conventions and gotchas

- **Bump `fleetd.API_LEVEL`** whenever fleetd gains an endpoint other machines rely on; the dashboard uses it to detect peers on an older Fleet.
- Security invariants: every API call needs the token (bearer or cookie); write endpoints use `require_write`, which also requires `X-Fleet-Request: 1` (CSRF guard). Peers must be `https://<name>.ts.net` in the same tailnet (no IPs, no plain HTTP). Remote session control is limited to folders under `~` and permission modes that still prompt. The token goes to `security` via stdin, never argv.
- Tests fake only process boundaries: the `claude` binary, the filesystem (`tmp_path`) and peer HTTP (`httpx.MockTransport`). Builders live in `tests/conftest.py`; recorded TUI screens for the attach/prompt parser are in `tests/fixtures/screens/*.raw`.
- Changes must work identically on the web dashboard, inside Fleet.app, and on an iPhone (PWA). Check mobile layouts at 390px width.
- The repo lives in iCloud-synced `~/Documents`: `build-app.sh` assembles and signs in a temp dir and zips without xattrs because iCloud re-adds xattrs and breaks `codesign`. Keep virtualenvs out of the repo (`UV_PROJECT_ENVIRONMENT=~/.fleet/venv-dev`).
- uv may reuse a stale wheel of `fleet`; use `--reinstall-package fleet` when an installed fleetd does not pick up changes.
- `--render-previews` must draw via `NSHostingView` + `cacheDisplay` (`ImageRenderer` cannot draw AppKit controls).
