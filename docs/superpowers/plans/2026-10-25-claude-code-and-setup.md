# Claude Code Workflow + Setup & Profile Implementation Plan (Plan 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a user run their own, unmodified Claude Code (signed in with their own Claude subscription or key) as an agent inside Mises — prompted by Mises, fed Mises's data, working only through the `mises` tools and the Gate — and give the app a first-run setup screen plus a Profile screen where every connection and default is set.

**Architecture:** A new core package `mises/claude_code/` detects the user's `claude` binary and sign-in state, launches Anthropic's own sign-in flow, and runs Claude Code in print mode with stream-json in/out as an *external run* (Runtime.create_external_run), sandboxed like ACP agents, with built-in tools disabled and only the `mises` MCP server attached. Runtime routes `model_ref` values `claude-code/<alias>` to it, so the existing console, approvals and step log work unchanged. A new core profile module stores non-secret settings in `profile.json` and secrets in the Keychain behind admin-token routes. The app gains a Setup wizard (shown until setup is complete) and a Profile screen.

**Spec:** `docs/design/2026-10-24-mises-design.md` (§7.3 agent host policy, §10 security); Anthropic terms: https://code.claude.com/docs/en/legal-and-compliance (hosting the unmodified Claude Code binary with the end user's own sign-in is permitted; Mises must never offer claude.ai login itself, collect/store/intermediate Claude credentials, or pay for/resell the user's usage); CLI flags verified 2026-10-25 against https://code.claude.com/docs/en/cli-reference.

## Global Constraints

- Everything from Plans 1–2 still binds (Gate on every call, never auto-approve, untrusted text as data, colors/fonts/square corners/no auto-numbered names, admin token only in memory, 127.0.0.1 only).
- **Claude Code compliance (hard rules):**
  - Run the user's installed `claude` binary unmodified; never bundle, patch or wrap its auth.
  - Sign-in happens only through `claude auth login` (Anthropic's flow, opens the browser). Mises shows status from `claude auth status` (JSON; exit 0 = signed in) and never reads, copies, stores, logs or transmits Claude tokens or `~/.claude` credential files.
  - Mises never names a feature "Claude Code"; plain-text "Runs your Claude Code" / provider label "Claude Code (your account)" is allowed. No Claude Code logo or look-alike art.
  - Usage is the user's own; Mises only prompts Claude Code for work the user started (a run the user created or a message the user sent). Mises never uses Claude Code as a hidden model backend for other features.
- **Claude Code invocation (exact flags):** `claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages --tools "" --strict-mcp-config --mcp-config <file> --allowedTools "mcp__mises__*" --append-system-prompt-file <mises-rules.md> --agents <agents.json> --session-id <uuid> --model <alias> --name <run label>`; resume with `--resume <session-id>` instead of `--session-id`. The MCP config file defines one stdio server `mises` = `<python> -m mises.server.stdio_proxy --run <run_id>` with env `MISES_MCP_URL`, `MISES_MCP_TOKEN` (MCP-only token), `MISES_HOME`. cwd = the run's workdir. Run under `platform.agent_sandbox_profile` like ACP agents, but the profile must allow Claude Code's own config dir read/write (path from `claude auth status` `configDirectory`, default `~/.claude`) and the network.
- **Secrets:** API keys and Onshape keys go webview → `POST /keys/{vendor}` (admin token) → Keychain. Never returned by any route (status only: `{vendor, present, source: "env"|"keychain"|"none"}`), never logged, never in profile.json, inputs are `type=password` with no echo.
- "Login" = local setup on this Mac. No Mises cloud account in this plan (paid tier later).

## Review Focus

1. A Claude Code run can only act through `mises` tools: no built-in Bash/Edit/Write/WebFetch, no user MCP servers, no user hooks reaching files outside the workdir; every Onshape write/merge still goes through the Gate and an approval card.
2. No Claude credential ever passes through Mises (no reads of `~/.claude/.credentials*`, Keychain items named for Claude, or token env vars into logs/steps/events).
3. Claude Code not installed / not signed in / signed out mid-run / process crash / `claude` update changes output → clear status in Setup, Profile and the run (red error step), no hang, process group killed.
4. Secrets: keys never echoed to the UI, logs, steps, events, profile.json or server responses; wrong Onshape keys show a clear error without consuming more than one ledgered call.
5. Setup wizard: re-entrant (closing mid-way keeps what's saved), never blocks local-only use (a "skip — local models only" path), and reappears only while required pieces are missing.

---

## File Structure

```
core/src/mises/claude_code/
  detect.py        find_claude() -> ClaudeInfo; auth_status() -> AuthStatus; start_login() -> LoginProcess
  session.py       ClaudeCodeSession: start/prompt/cancel/close; stream-json ↔ Steps/events
  prompts.py       mises-rules.md text + agents.json builder (designer, verifier) + context block
core/src/mises/profile.py           Profile model, load/save profile.json, setup_state()
core/src/mises/server/profile_api.py  /profile, /keys, /setup, /claude-code/*, /onshape/test routes
core/src/mises/context_tools.py     mises_context_get tool (read): project + UI selection + latest metrics
core/src/mises/runtime.py           (modify) route claude-code/<alias>; apply profile defaults; context store
core/src/mises/server/ui_api.py     (modify) /models lists claude-code entries with signed-in status
app/src/api/{types,schemas,client}.ts   (modify) new routes + shapes
app/src/ui/setup/   SetupWizard.tsx steps/*.tsx setup.css           (Task 4)
app/src/ui/profile/ ProfilePane.tsx sections/*.tsx profile.css     (Task 5)
app/src/ui/topbar/  (modify) profile button + setup gate             (Task 6)
app/src/ui/dialogs/NewRunDialog.tsx (modify) claude-code models     (Task 6)
```

Waves: **W1** Tasks 1, 2 (core, parallel) · **W2** Tasks 3 (core wiring), 4, 5 (app, parallel; app tasks code against Task 1–2 Interfaces + fake core) · **W3** Task 6 (app integration + e2e + real-binary smoke).

---

### Task 1: Profile, keys and setup state (core)

**Files:** Create `core/src/mises/profile.py`, `core/src/mises/server/profile_api.py`; modify `server/app.py` (include router, admin-token only); tests `core/tests/test_profile.py`, `core/tests/server/test_profile_api.py`.

**Produces:**
```python
class Profile(BaseModel):  # stored at data_home()/profile.json (0600), never secrets
    display_name: str = ""
    units: Literal["mm"] = "mm"
    default_material: str = "PLA"
    default_agent: str = "designer"
    default_model: str | None = None            # e.g. "claude-code/sonnet", "anthropic/claude-sonnet-5-5", "ollama/qwen3"
    onshape_calls_year_cap: int = 2500
    onshape_calls_run_cap: int = 200
    max_parallel_agents: int = 2
    local_endpoints: dict[str, str] = {}        # {"ollama": "http://localhost:11434/v1", "lmstudio": ...}
    setup_completed_at: float | None = None
def load_profile(home: Path) -> Profile; def save_profile(home: Path, p: Profile) -> None   # atomic write
class SetupState(BaseModel): complete: bool; has_model: bool; has_onshape: bool; missing: list[Literal["model","onshape","profile"]]
def setup_state(home, keys: KeyStatusFn, claude: ClaudeStatusFn) -> SetupState
   # has_model = any API key present OR claude-code signed in OR a local endpoint answering; complete = has_model and setup_completed_at set
   # (onshape optional for completeness: missing lists it, but complete doesn't require it — local-only use allowed)
```
Routes (all admin token; MCP token → 403):
- `GET /profile` → Profile; `PUT /profile` (partial merge, validated) → Profile
- `GET /keys` → `[{vendor, present, source}]` for VENDORS ∪ {"onshape"}; `POST /keys/{vendor}` `{secret}` (onshape: `{access_key, secret_key}`) → status; `DELETE /keys/{vendor}` → status. Secrets via `platform.keychain_set/get`; delete via a new `platform.keychain_delete`.
- `POST /onshape/test` → `{ok, user_name?, error?}` — exactly one ledgered GET of Onshape's session-info endpoint (verify path in Onshape docs); wrong keys → `{ok:false, error:"denied"}`.
- `GET /setup` → SetupState; `POST /setup/complete` → sets setup_completed_at.
Runtime applies profile caps/defaults when a run doesn't pass its own.

- [ ] Tests first: profile round-trip + 0600 + atomic; partial PUT; validation errors 422; `GET /keys` never contains secret material (assert the secret string absent from the whole response body and logs via caplog); POST/DELETE keys hit a monkeypatched keychain; MCP token gets 403 on every new route; onshape test makes exactly one ledgered call against the fake Onshape and maps 401 → denied; setup_state truth table; runtime uses profile caps.
- [ ] Implement; full core suite pristine; commit `feat(core): profile, key and setup routes`.

### Task 2: Claude Code host (core)

**Files:** Create `core/src/mises/claude_code/{__init__,detect,session,prompts}.py`, `core/src/mises/context_tools.py`; tests `core/tests/claude_code/test_detect.py`, `test_session.py`, `tests/fixtures/fake_claude.py` (a fake `claude` executable speaking the stream-json protocol, `auth status`, `auth login`).

**Produces:**
```python
class ClaudeInfo(path: str|None, version: str|None)
def find_claude() -> ClaudeInfo          # PATH, ~/.local/bin, /opt/homebrew/bin, /usr/local/bin, ~/.claude/local; `claude --version`
class AuthStatus(signed_in: bool, auth_method: str, email: str|None, config_dir: str|None, raw_ok: bool)
async def auth_status(info) -> AuthStatus  # runs `claude auth status` (JSON); keep only non-secret fields: authMethod, email/account display if present, configDirectory
async def start_login(info, *, console: bool=False) -> LoginHandle   # runs `claude auth login [--console]`; Anthropic's flow opens the browser; Mises only waits for exit and re-checks status; never reads its stdout for tokens (discard stdout; keep a 2 KB stderr tail with any token-looking strings redacted)
class ClaudeCodeSession:
  __init__(runtime, run_id, *, model_alias: str, info: ClaudeInfo, mcp_url, mcp_token, resume_session_id: str|None=None)
  async start() -> None; async prompt(text: str, *, context: dict|None=None) -> str; async cancel() -> None; async close() -> None
  session_id: str
```
Stream-json mapping: `system/init` → decision Step (model, tools list — must contain only `mcp__mises__*`; if any other tool appears, cancel the run with an error Step "unexpected tools"); `assistant` text → message Step + text_delta events (partial messages); tool_use / tool_result for `mcp__mises__*` are NOT duplicated as Steps (the Gate already records them via the MCP binding) — only publish lightweight `tool_call`/`tool_result` events for live UI; `result` → run_finished (done | failed with `is_error`), usage → store.add_usage tokens (no $). stderr → bounded tail; non-JSON stdout lines ignored with a warning.
`prompts.py`: `mises_rules_md()` reuses `agent/prompts.py` fixed rules (same text, written for a tool-only agent); `agents_json()` → `{"designer": {...}, "verifier": {...}}` with descriptions/prompts from `agent/builtin/*.md` and `tools: ["mcp__mises__*"]`; `context_block(ctx)` → a delimited, labelled data block (selected part id, metrics, vars, doc url, recent sim) prepended to the user's first prompt as data.
`context_tools.py`: `mises_context_get()` (read) returns the same context dict live (selection set by the app via `PUT /context` — add that route here: `{selected_part_id, selected_run_id, picks:[]}`, admin token).
Sandbox: `platform.claude_sandbox_profile(home, workdir, claude_config_dir)` = agent profile + allow read/write of claude_config_dir and the Claude binary's install dir read; deny the Mises home except workdir (server.json, mises.db unreadable). If sandbox-exec is unavailable → refuse to start.
Cancel: write nothing more to stdin, SIGTERM the process group, 5 s, SIGKILL; finish the external run cancelled; release reservations.

- [ ] Tests (all with `fake_claude.py` on PATH via env, no real network): find/version/status parsing (signed in, signed out exit 1, malformed JSON → signed_in False raw_ok False); login waits and re-checks, stdout never stored; session start passes EXACTLY the global-constraints flags (assert argv); init with an unexpected tool → run failed; text streaming → message Step + deltas; a fake mises tool call goes through the real stdio proxy → Gate tool_call Step exists for the run; result → done; crash mid-run → failed with stderr tail, process group dead; cancel kills a hung fake; resume uses --resume; context block delimited and appears only in the first prompt; no credential paths touched (monkeypatch open/Path.read_* to fail on `~/.claude` credential files).
- [ ] Implement; core suite pristine; commit `feat(core): host the user's Claude Code as a Mises agent`.

### Task 3: Runtime + routes wiring (core)

**Files:** modify `runtime.py`, `server/ui_api.py`, `server/profile_api.py` (from Task 1), `cli/main.py`; tests.
- `Runtime.start_run(agent, prompt, model_ref="claude-code/<alias>")` → create_external_run(agent, provider="claude-code", model=alias, …) + ClaudeCodeSession (needs the in-process MCP URL + an MCP-only token from the running server; when no server is running, start_run raises DENIED with hint) and drives it; `send()` → session.prompt; `cancel()` → session.cancel (cancel hooks from Plan 1 M8); on Runtime start, external claude-code runs from a previous process are resumable: `POST /runs/{id}/resume` re-spawns with `--resume <session_id>` (store session_id on the run — add a nullable `session_ref` column, migration like `external`).
- `GET /models` adds `claude-code` vendor with models `sonnet`, `opus`, `haiku`, `fable` and `key: "signed_in"|"signed_out"|"not_installed"`.
- `GET /claude-code/status` → `{installed, version, signed_in, auth_method}`; `POST /claude-code/login` → starts `claude auth login` (returns immediately; status polled); `POST /claude-code/logout` → `claude auth logout`.
- CLI: `mises chat --model claude-code/sonnet` works; `mises claude status|login`.
- [ ] Tests (fake_claude): start_run with claude-code model creates an external run and finishes done; send continues; cancel; resume after a new Runtime; /models entries; status/login/logout routes admin-only. Commit `feat(core): route claude-code models through the runtime`.

### Task 4: Setup wizard (app)

**Files:** `app/src/ui/setup/**`, `app/test/fake-core/` additions for the new routes (fake core owns no logic beyond shapes), `app/test/e2e/setup.spec.ts`; `api/types.ts`, `schemas.ts`, `client.ts` additions for: `GET/PUT /profile`, `GET /keys`, `POST/DELETE /keys/{vendor}`, `POST /onshape/test`, `GET /setup`, `POST /setup/complete`, `GET /claude-code/status`, `POST /claude-code/login|logout`, `PUT /context`.
Full-screen wizard on the drafting look, shown when `GET /setup` → complete=false (and re-openable from Profile):
1. **Welcome** — what Mises is, "everything stays on this Mac".
2. **Connect a model** — three cards: *Use my Claude Code* (status: not installed → link text + `npm`/installer hint; installed + signed out → **Sign in** button = POST /claude-code/login, then poll status every 2 s for 2 min, text "finish signing in in your browser"); *API key* (vendor select + password field + Save → POST /keys); *Local model* (Ollama/LM Studio endpoint test). Continue enabled once has_model.
3. **Onshape** (optional, "Skip for now") — access key + secret key (password fields) → Save → Test (one call; shows user name or error).
4. **Profile basics** — display name, default material, default model (from /models, only available ones), parallel agents (1–4).
5. **Done** → POST /setup/complete.
- [ ] Tests: wizard shows only when incomplete; keys never rendered after save (field clears, shows "saved ●●●● from Keychain"); sign-in polling stops on success/timeout; skip Onshape path; re-entrant (reload mid-way → resumes at first missing step); e2e full path against the fake core. Commit `feat(app): first-run setup wizard`.

### Task 5: Profile screen (app)

**Files:** `app/src/ui/profile/**`, `app/test/e2e/profile.spec.ts` (uses Task 4's client additions — if run in parallel, code against the same route shapes; Task 6 reconciles).
Panel opened from a top-bar profile button (Task 6 wires the button); sections: **Profile** (name, units, default material), **Models** (Claude Code card with status/version/auth method + Sign in/Sign out; API keys table: vendor, status dot, source, Replace, Remove; local endpoints with Test), **Onshape** (keys present?, Replace/Remove, Test, usage this year vs cap with bar, caps editable), **Agents & budgets** (default agent/model, max parallel agents, per-run Onshape cap), **Data** (data folder path, read-only, "Show in Finder" via Tauri opener if available else copy path), **Setup** (re-run setup wizard), **About** (versions of app, core, claude).
- [ ] Tests: each section reads/writes the right route; secrets never displayed; remove asks a confirm inside the panel (no window.confirm); blue only for selected section/needs-you; e2e edit-and-persist. Commit `feat(app): profile screen`.

### Task 6: Integration (app + smoke)

- Top bar: profile button (display name initials or "Profile"), setup gate on boot; New-run dialog lists `claude-code/*` models (signed-out → disabled with "sign in in Profile"); roster/provider label "Claude Code (your account)"; the app sends `PUT /context` when selection changes so `mises_context_get` and the context block see it.
- Reconcile client/types from Tasks 4–5; update fake core; contract test covers new routes (fixtures dumped from the real core).
- e2e: first-run → sign-in (fake) → new claude-code run → tool rows → approval → done.
- Real-binary smoke (if `claude` is installed and signed in on this Mac): run `mises chat --model claude-code/haiku` with "list my parts" and record output; if not signed in, record status only. Never sign in on the user's behalf.
- [ ] All suites pristine (lint, vitest, playwright chromium+webkit approvals, contract, cargo, core). Rebuild dev app + headless launch check. Commit.
