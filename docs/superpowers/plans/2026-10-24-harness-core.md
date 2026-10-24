# Mises Harness Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A macOS Python core service that runs a Claude-Code-style agent harness for *any* model (Anthropic, OpenAI, Gemini, OpenRouter, Ollama, LM Studio) and for external ACP agents, with every rule enforced in the core: gate pipeline, content-hashed approvals, hash-chained step log, Onshape call ledger, and CAD tools on a local build123d mirror.

**Architecture:** One Python package `mises` in `core/`. All tools live in one `ToolRegistry`; every call — from Mises's own agent loop, from the MCP server, or from an ACP agent — goes through one `Gate`. Model vendors sit behind one `ModelProvider` streaming interface with two real adapters (Anthropic Messages, OpenAI-compatible chat) that together cover every vendor. A FastAPI server on 127.0.0.1 exposes runs, approvals, an event WebSocket and the `mises` MCP endpoint; a `mises` CLI is the first UI (the Mac app is Plan 2).

**Tech Stack:** Python 3.13 (uv), pydantic 2, sqlite3 (stdlib, WAL), anthropic 2.x, openai 3.x, mcp 2.x, agent-client-protocol 0.12, build123d 0.13, httpx, FastAPI + uvicorn, rich + prompt_toolkit, pytest + pytest-asyncio.

**Spec:** `docs/design/2026-10-24-mises-design.md` (§6, §7.1–7.4, §8, §10) and `research/harness/README.md`. Reference code: open-multi-agent (MIT, https://github.com/open-multi-agent/open-multi-agent) — port patterns, don't copy; if any code is copied verbatim, keep its MIT notice in `core/THIRD_PARTY.md`.

## Global Constraints

- Python `>=3.13`; package at `core/src/mises/`; tests at `core/tests/`; run everything with `uv run` from `core/`.
- macOS only for now; keep OS-specific code (Keychain, `sandbox-exec`, app-support paths) behind `mises/platform.py` so Windows can be added later.
- Data home: `MISES_HOME` env var, default `~/Library/Application Support/Mises`. SQLite file `mises.db` there.
- No code, pseudocode or prompts taken from leaked Claude Code source or from claude-code-from-source.com. Ideas only.
- Never offer claude.ai login; never brand anything "Claude Code".
- Tool wire names match `^[a-z][a-z0-9_]{0,47}$` (underscores, e.g. `onshape_propose`).
- Safety classes: `read`, `local_mutate`, `onshape_mutate`, `irreversible`. Unclassified = `irreversible` (fail closed).
- **Never auto-approve.** No setting, flag or env var may approve an `irreversible` call without a human decision.
- Typed error codes: `stale_version`, `budget_exhausted`, `needs_approval`, `transient`, `invalid_input`, `out_of_scope`, `denied`, `not_found`, `tool_failed`, `cancelled`.
- Names an agent creates (features, variables, parts) must not end in a number (`Gusset1`, `Extrude 1`, `var2` are rejected) — design §5.1.
- All Onshape text and web data is untrusted: tool results wrap it as data, never as instructions.
- Onshape allowance default: 2,500 calls/user/year; only 2xx/3xx count; every call reserved in the ledger before it is sent.
- Servers bind 127.0.0.1 only and require a per-session bearer token.
- Tool outputs over 16 KB are written to a file under the run's workdir and returned as a handle plus summary.
- When writing code that calls Claude models, load the `claude-api` skill first. Default Claude model id: `claude-sonnet-5-5`; small/fast: `claude-haiku-5-5`.

## Review Focus

1. **A model emits malformed tool input or an unknown tool name** → the run continues; the model gets an `invalid_input` / `not_found` tool result, nothing crashes. (Task 3, Task 7 tests.)
2. **An approval is decided twice, or its payload changes after approval** → first decision wins; execution with a different payload hash raises `needs_approval` and a fresh card. (Task 3 tests.)
3. **The user cancels mid-tool-call (Ctrl-C, `session/cancel`, kill)** → the run ends `cancelled`, ledger reservations are released, no orphan approvals stay pending. (Task 7, Task 10 tests.)
4. **A provider returns 429/5xx or drops the stream** → retried with backoff as `transient` up to 3 times, then the run fails with a clear error, not a hang. (Task 4 tests.)
5. **An agent-written build123d script loops forever or tries the network** → killed at the timeout, network denied by `sandbox-exec`, error returned as `tool_failed`. (Task 5 tests.)

---

## File Structure

```
core/
  pyproject.toml                 deps, scripts: mises = "mises.cli.main:main"
  THIRD_PARTY.md
  src/mises/
    __init__.py
    types.py                     enums, errors, message blocks, ToolOutput
    platform.py                  data home, keychain, sandbox-exec profile
    naming.py                    no-trailing-number rule, display names
    events.py                    in-process EventBus
    store.py                     SQLite: runs, policies, steps, approvals, ledger, tool cache
    approvals.py                 ApprovalService
    tools/registry.py            ToolDef, @tool, ToolRegistry, ToolContext
    gate.py                      the gate pipeline
    providers/base.py            ModelRequest, StreamEvent, ModelProvider
    providers/anthropic_provider.py
    providers/openai_provider.py
    providers/fake.py            scripted provider for tests
    providers/registry.py        "vendor/model" refs → provider; key lookup
    cad/worker.py                runs inside the sandboxed subprocess
    cad/runner.py                spawns worker under sandbox-exec, timeout
    cad/tools.py                 mirror_* tools; register_mirror_tools()
    onshape/client.py            HMAC-signed httpx client + ledger
    onshape/adapter.py           documents, features, variables, branches, merge
    onshape/tools.py             onshape_* tools; register_onshape_tools()
    agent/definitions.py         markdown+frontmatter agent definitions
    agent/prompts.py             stable-first system prompt builder
    agent/loop.py                AgentLoop
    agent/builtin/*.md           designer, planner, verifier, sim
    orchestrator/plan.py         Plan schema + validate_plan
    orchestrator/scheduler.py    DAG scheduler with cascade
    orchestrator/orchestrator.py Orchestrator + agent_spawn tool
    ui_tools.py                  ui_ask_user, ui_note
    runtime.py                   Runtime: wires store/registry/gate/events; start_run()
    server/app.py                FastAPI app, WS events, MCP mount
    server/mcp_server.py         MCP server over the registry
    server/stdio_proxy.py        stdio MCP → HTTP MCP proxy
    acp_host/specs.py            known ACP agents (command lines)
    acp_host/session.py          AcpSession (client side)
    cli/main.py                  argparse entry: chat, serve, mcp-stdio, acp, models, agents
    cli/repl.py                  interactive REPL
  tests/ ... mirrors src layout; tests/fixtures/fake_acp_agent.py
```

Waves (tasks in a wave are independent and may run in parallel worktrees): **W1** Task 1 · **W2** Tasks 2, 3, 4, 5, 6 · **W3** Tasks 7, 8 · **W4** Tasks 9, 10, 11.

---

### Task 1: Scaffold, types, store, events, naming, platform

**Files:**
- Create: `core/pyproject.toml`, `core/src/mises/{__init__,types,platform,naming,events,store}.py`, `core/THIRD_PARTY.md`, `.gitignore`
- Test: `core/tests/test_types.py`, `test_store.py`, `test_naming.py`, `test_events.py`

**Interfaces — Produces:**
```python
# types.py (pydantic BaseModel unless noted)
class SafetyClass(StrEnum): READ="read"; LOCAL_MUTATE="local_mutate"; ONSHAPE_MUTATE="onshape_mutate"; IRREVERSIBLE="irreversible"
class ErrorCode(StrEnum): STALE_VERSION="stale_version"; BUDGET_EXHAUSTED="budget_exhausted"; NEEDS_APPROVAL="needs_approval"; TRANSIENT="transient"; INVALID_INPUT="invalid_input"; OUT_OF_SCOPE="out_of_scope"; DENIED="denied"; NOT_FOUND="not_found"; TOOL_FAILED="tool_failed"; CANCELLED="cancelled"
class MisesError(Exception): __init__(code: ErrorCode, message: str, *, hint: str|None=None); retryable -> bool (TRANSIENT only); to_dict() -> {"error": code, "message", "hint", "retryable"}
class StepKind(StrEnum): MESSAGE, PLAN, TOOL_CALL, TOOL_RESULT, DIFF, DECISION, ERROR, ONSHAPE_CALL, SIM_RUN, MEASURE, VERIFY, COMPACTION  (values lowercase)
class TextBlock(type: Literal["text"], text: str)
class ImageBlock(type: Literal["image"], media_type: str, data_b64: str)
class ToolUseBlock(type: Literal["tool_use"], id: str, name: str, input: dict)
class ToolResultBlock(type: Literal["tool_result"], tool_use_id: str, content: str, is_error: bool=False)
Block = Annotated[TextBlock|ImageBlock|ToolUseBlock|ToolResultBlock, Field(discriminator="type")]
class Message(role: Literal["user","assistant"], content: list[Block])
class ToolOutput(ok: bool=True, summary: str, data: dict={}, handles: list[str]=[], warnings: list[str]=[], step_kind: StepKind=StepKind.TOOL_RESULT)
class Caps(onshape_calls_run: int=200, onshape_calls_year: int=2500, max_turns: int=40, usd_run: float=5.0)
def canonical_json(obj) -> str            # sort_keys, separators=(",",":"), ensure_ascii=False
def sha256_hex(s: str) -> str

# platform.py
def data_home() -> Path                    # MISES_HOME or ~/Library/Application Support/Mises; mkdir
def keychain_get(service: str, account: str) -> str|None   # `security find-generic-password -s -a -w`; None if missing
def keychain_set(service: str, account: str, secret: str) -> None
def sandbox_profile(write_dir: Path) -> str   # SBPL: (version 1)(allow default)(deny network*)(deny file-write*)(allow file-write* (subpath write_dir) (subpath "/private/var/folders") (literal "/dev/null"))

# naming.py
def check_name(name: str) -> None          # raises MisesError(INVALID_INPUT) if re.search(r"\d+\s*$") or empty
def display_name(feature_type: str, name: str, params: dict|None=None) -> str  # "Type · purpose"; Onshape defaults like "Extrude 1" → "Extrude · <purpose from params or 'unnamed'>"

# events.py
class Event(type: str, run_id: str|None, data: dict, ts: float)
class EventBus: publish(event: Event) -> None (non-blocking); subscribe(run_id: str|None=None) -> AsyncIterator[Event]  (per-subscriber asyncio.Queue maxsize 1000; drop-oldest on overflow)
# event types used by later tasks: run_started, text_delta, tool_call, tool_result, approval_requested, approval_decided, step, run_finished, question_asked

# store.py — sqlite3, WAL, check_same_thread=False, one threading.Lock; all writes in transactions
class AgentRun(id, parent_id: str|None, agent: str, provider: str, model: str, task: str, status: Literal["running","done","failed","cancelled"], branch: str|None, onshape_calls: int, input_tokens: int, output_tokens: int, usd: float, created_at, updated_at)
class AgentPolicy(run_id, version: int, edit_scope: list[str], read_scope: list[str], caps: Caps, allowed_classes: list[SafetyClass], created_at)
class Step(id: int, run_id, ts, kind: StepKind, payload: dict, evidence_hash: str, prev_hash: str|None, collapsed: bool)
class Approval(id, run_id, action: str, payload: dict, payload_hash: str, microversion: str|None, deltas: dict, status: Literal["pending","approved","rejected","expired","executed"], expires_at: float, decided_by: str|None, decided_at: float|None, edited_payload: dict|None)
class LedgerEntry(id, user: str, run_id: str|None, endpoint: str, estimate: int, idempotency_key: str, status_code: int|None, counted: bool, state: Literal["reserved","settled","released"], ts)
class Store:
  __init__(path: Path | str)                           # ":memory:" allowed
  create_run(agent, provider, model, task, parent_id=None) -> AgentRun
  get_run(run_id) -> AgentRun; update_run(run_id, **fields) -> AgentRun; list_runs(limit=50) -> list[AgentRun]
  add_usage(run_id, *, input_tokens=0, output_tokens=0, usd=0.0, onshape_calls=0) -> None
  create_policy(run_id, edit_scope, read_scope, caps, allowed_classes) -> AgentPolicy   # version = prev+1
  current_policy(run_id) -> AgentPolicy
  append_step(run_id, kind, payload, collapsed=False) -> Step   # evidence_hash = sha256(canonical_json({run_id, kind, payload, prev_hash}))
  steps(run_id, kinds: list[StepKind]|None=None, after_id: int=0) -> list[Step]
  get_step(step_id) -> Step; verify_chain(run_id) -> bool
  insert_approval(a: Approval) -> Approval; get_approval(id) -> Approval; pending_approvals(run_id=None) -> list[Approval]
  cas_approval(id, *, from_status, to_status, **fields) -> bool   # UPDATE ... WHERE id=? AND status=?
  ledger_reserve(user, run_id, endpoint, estimate, idempotency_key) -> LedgerEntry
  ledger_settle(entry_id, status_code) -> LedgerEntry   # counted = 200<=code<400
  ledger_release(entry_id) -> None; release_run_reservations(run_id) -> int
  ledger_used(user, *, since: float, run_id=None) -> int   # counted settled + reserved estimates
  cache_get(key) -> dict|None; cache_put(key, value: dict) -> None   # tool-result checkpoints
```

- [ ] **Step 1:** `git init` at repo root `Mises/` (if not already), add `.gitignore` (`.venv/`, `__pycache__/`, `*.db`, `.superpowers/`, `.DS_Store`, `core/.pytest_cache/`). Write `core/pyproject.toml` (hatchling, src layout) with deps: `pydantic>=2.12`, `anthropic>=2.3`, `openai>=3.37`, `mcp>=2.3`, `agent-client-protocol>=0.12`, `build123d>=0.13`, `httpx>=0.28`, `fastapi`, `uvicorn`, `rich`, `prompt_toolkit`, `pyyaml`; dev: `pytest`, `pytest-asyncio` (asyncio_mode=auto). Run `brew install uv` if missing, then `cd core && uv sync`. Expected: resolves and installs.
- [ ] **Step 2: Write failing tests**
  - `test_error_to_dict`: `MisesError(ErrorCode.TRANSIENT,"x").to_dict()["retryable"] is True`; INVALID_INPUT → False.
  - `test_message_roundtrip`: a `Message` with all four block types survives `model_dump_json` → `model_validate_json`.
  - `test_step_chain`: three `append_step` calls; step 2 `prev_hash == step1.evidence_hash`; `verify_chain` True; after `UPDATE steps SET payload='{}' WHERE id=2` via raw sql, `verify_chain` False.
  - `test_policy_versions`: second `create_policy` → version 2, `current_policy` returns it.
  - `test_cas_approval_first_wins`: two `cas_approval(from_status="pending", to_status=...)` → first True, second False.
  - `test_ledger_counts_only_2xx_3xx`: reserve 3 entries, settle with 200, 302, 500 → `ledger_used` == 2; a reserved-unsettled entry with estimate 4 counts 4 until released.
  - `test_release_run_reservations`: two reserved entries for run R → returns 2, `ledger_used` drops.
  - `test_check_name`: rejects `"Gusset1"`, `"Extrude 1"`, `"var2"`, `""`; accepts `"gusset_upright"`, `"plate_thickness"`, `"M4_hole"`.
  - `test_display_name`: `display_name("Extrude","Extrude 1",{"depth":"5 mm"})` starts with `"Extrude · "` and contains no digit-suffix name; `display_name("Gusset","Gusset · upright")` unchanged.
  - `test_eventbus_filters_by_run`: subscriber for run A receives A's events only; overflow beyond 1000 drops oldest without raising.
- [ ] **Step 3:** Run `uv run pytest -q` → FAIL (imports missing).
- [ ] **Step 4:** Implement the interfaces above.
- [ ] **Step 5:** `uv run pytest -q` → all pass.
- [ ] **Step 6:** Commit `feat(core): scaffold, types, store, events, naming`.

---

### Task 2: Tool registry, approvals, gate pipeline

**Files:**
- Create: `core/src/mises/tools/__init__.py`, `tools/registry.py`, `approvals.py`, `gate.py`
- Test: `core/tests/test_registry.py`, `test_approvals.py`, `test_gate.py`

**Interfaces — Consumes:** Task 1 (`Store`, `EventBus`, `MisesError`, `ToolOutput`, `SafetyClass`, `canonical_json`, `sha256_hex`).

**Produces:**
```python
# tools/registry.py
@dataclass
class ToolContext: run_id: str; user: str; store: Store; events: EventBus; workdir: Path; policy: AgentPolicy; call_id: str; services: dict[str, Any]   # services: "onshape", "orchestrator", ...
@dataclass(frozen=True)
class ToolDef: name: str; description: str; input_model: type[BaseModel]; safety: SafetyClass; handler: Callable[[ToolContext, BaseModel], Awaitable[ToolOutput]]; onshape_cost: int = 0; scope_arg: str|None = None; timeout_s: float = 120
def tool(name: str, *, description: str, safety: SafetyClass = SafetyClass.IRREVERSIBLE, onshape_cost: int = 0, scope_arg: str|None = None, timeout_s: float = 120)  # decorator on `async def f(ctx, args: SomeModel) -> ToolOutput`; infers input_model from the annotation; returns ToolDef
class ToolRegistry:
  register(t: ToolDef) -> None            # validates name regex; duplicate → ValueError
  get(name) -> ToolDef                    # MisesError(NOT_FOUND)
  list(names: list[str]|None=None, classes: set[SafetyClass]|None=None) -> list[ToolDef]
  schema(t: ToolDef) -> dict               # {"name","description","input_schema"} — JSON schema from pydantic, $defs inlined

# approvals.py
class ApprovalService:
  __init__(store: Store, events: EventBus)
  async request(run_id, action: str, payload: dict, *, microversion=None, deltas=None, ttl_s=1800) -> Approval   # publishes approval_requested
  async wait(approval_id, timeout_s: float|None=None) -> Approval        # returns when status != pending; on ttl → CAS to expired
  decide(approval_id, payload_hash: str, approve: bool, decided_by: str, edited_payload: dict|None=None) -> Approval
      # hash mismatch → MisesError(STALE_VERSION); already decided → returns current (first wins); edited_payload → stored, and payload_hash of the edit becomes the one consume() accepts
  consume(approval_id, payload_hash: str) -> dict   # must be approved, unexpired, hash matches → CAS approved→executed; returns payload to execute (edited if any); else MisesError(NEEDS_APPROVAL)
  cancel_run(run_id) -> int                          # pending → rejected(decided_by="system:cancel")

# gate.py
class Gate:
  __init__(registry: ToolRegistry, store: Store, approvals: ApprovalService, events: EventBus, *, user: str = "local", services: dict|None=None)
  async call(run_id: str, tool_name: str, raw_input: dict, *, call_id: str, workdir: Path) -> ToolOutput   # raises MisesError
```

Gate pipeline, in order (records `tool_call` Step before execute and `tool_result` or `error` Step after; publishes `tool_call`/`tool_result` events):
1. `registry.get` → `not_found`. 2. `input_model.model_validate(raw_input)` → `invalid_input` with pydantic's message. 3. Safety class ∉ `policy.allowed_classes` → `denied`. 4. If `scope_arg`: value ∈ `edit_scope` for mutating classes, ∈ `edit_scope ∪ read_scope` for `read`; `"*"` in a scope matches all → else `out_of_scope` (hint: ask the user to widen scope). 5. If `onshape_cost > 0`: `ledger_used(user, since=year_start) + cost > caps.onshape_calls_year` or run's `onshape_calls + cost > caps.onshape_calls_run` → `budget_exhausted`. 6. If `irreversible`: `approvals.request(... payload=validated.model_dump())`, `wait`, rejected/expired → `denied`; `consume` right before execute; execute with the returned payload. 7. Idempotency key `sha256(canonical_json([run_id, name, payload]))`; for non-`read` tools a `cache_get` hit returns the saved ToolOutput without executing. 8. Execute with `asyncio.wait_for(timeout_s)`; timeout → `transient`; non-Mises exception → `tool_failed` (message = `type: str(e)`, no traceback to the model). 9. Output JSON > 16 KB → write `workdir/outputs/<call_id>.json`, return `ToolOutput(summary=..., handles=[path], data={})`. 10. `cache_put` for non-read tools; Step kind = `output.step_kind`.

- [ ] **Step 1: Write failing tests**
  - `test_tool_decorator_infers_model` / `test_bad_name_rejected` (`"onshape.propose"` → ValueError) / `test_schema_has_properties`.
  - `test_decide_hash_mismatch_is_stale`; `test_first_decision_wins`; `test_consume_requires_approval`; `test_consume_twice_fails` (second → `needs_approval`); `test_edited_payload_executes_edit`; `test_expiry` (ttl 0.05s → wait returns expired); `test_cancel_run_rejects_pending`.
  - Gate (use a fresh `Store(":memory:")`, tools defined inline): `test_unknown_tool_not_found`, `test_invalid_input`, `test_class_not_allowed_denied`, `test_out_of_scope`, `test_star_scope_allows`, `test_budget_exhausted` (caps.onshape_calls_run=3, tool cost 4), `test_irreversible_waits_for_human` (task decides approve after 0.05s → executes once), `test_irreversible_rejected_denied` (handler never called), `test_idempotent_replay` (local_mutate tool called twice with same input → handler ran once, outputs equal), `test_timeout_is_transient`, `test_exception_is_tool_failed`, `test_large_output_becomes_handle` (20 KB data → handle file exists), `test_steps_recorded` (tool_call + tool_result steps, chain verifies).
- [ ] **Step 2:** `uv run pytest tests/test_registry.py tests/test_approvals.py tests/test_gate.py -q` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass; full suite passes.
- [ ] **Step 5:** Commit `feat(core): tool registry, approvals, gate pipeline`.

---

### Task 3: Model providers (any model)

**Files:**
- Create: `core/src/mises/providers/{__init__,base,anthropic_provider,openai_provider,fake,registry}.py`
- Test: `core/tests/providers/test_fake.py`, `test_anthropic.py`, `test_openai.py`, `test_registry.py`

Load the `claude-api` skill before writing `anthropic_provider.py`.

**Interfaces — Consumes:** Task 1 message types, `MisesError`, `platform.keychain_get`.

**Produces:**
```python
# base.py
class ToolSchema(name: str, description: str, input_schema: dict)
class ModelRequest(model: str, system: str, messages: list[Message], tools: list[ToolSchema] = [], max_tokens: int = 8192, temperature: float|None = None)
class TextDelta(type: Literal["text_delta"], text: str)
class ToolCall(type: Literal["tool_call"], id: str, name: str, input: dict, raw: str|None = None)   # raw = unparsable args string; input={} then
class Usage(type: Literal["usage"], input_tokens: int, output_tokens: int, cache_read_tokens: int = 0)
class Done(type: Literal["done"], stop_reason: Literal["end_turn","tool_use","max_tokens","refusal","error"])
StreamEvent = TextDelta | ToolCall | Usage | Done
class ModelProvider(Protocol):
  name: str
  def stream(self, req: ModelRequest) -> AsyncIterator[StreamEvent]   # always ends with exactly one Done
async def with_retries(fn, *, attempts=3, base_delay=1.0)   # retries MisesError(TRANSIENT) with exponential backoff + jitter
# Error mapping in both adapters: 429, 5xx, timeouts, connection drops → MisesError(TRANSIENT); 401/403 → DENIED with hint naming the key source; 400 → INVALID_INPUT

# anthropic_provider.py
class AnthropicProvider: __init__(api_key: str, base_url: str|None = None)  # messages.stream; cache_control ephemeral on system and on the last tool; tool_use blocks → ToolCall at content_block_stop
# openai_provider.py
class OpenAICompatProvider: __init__(name: str, api_key: str|None, base_url: str|None, *, supports_tools: bool = True)
  # chat.completions stream; accumulate tool_calls deltas by index; parse JSON args at end (bad JSON → ToolCall(input={}, raw=...));
  # messages: ToolUseBlock → assistant.tool_calls, ToolResultBlock → role "tool"; ImageBlock → image_url data URI
# fake.py
class FakeProvider: __init__(turns: list[list[StreamEvent]]); name="fake"; requests: list[ModelRequest]  # each stream() call yields the next scripted turn; appends Done(end_turn) if missing
# registry.py
VENDORS: dict[str, VendorSpec]   # VendorSpec(kind: "anthropic"|"openai", base_url: str|None, env_key: str|None)
#   anthropic: anthropic, None, ANTHROPIC_API_KEY
#   openai:    openai, None, OPENAI_API_KEY
#   gemini:    openai, "https://generativelanguage.googleapis.com/v1beta/openai/", GEMINI_API_KEY
#   openrouter:openai, "https://openrouter.ai/api/v1", OPENROUTER_API_KEY
#   ollama:    openai, "http://localhost:11434/v1", None
#   lmstudio:  openai, "http://localhost:1234/v1", None
def resolve(model_ref: str) -> tuple[ModelProvider, str]   # "vendor/model-id"; bare id starting "claude-" → anthropic; key from env, then keychain_get("mises", vendor); missing key → MisesError(DENIED, hint="set ANTHROPIC_API_KEY or run `mises keys set anthropic`")
DEFAULT_MODEL = "anthropic/claude-sonnet-5-5"
```

- [ ] **Step 1: Write failing tests** (no network: use `httpx.MockTransport` via the SDKs' `http_client=` parameter with recorded SSE bodies)
  - `test_fake_scripts_turns`; `test_anthropic_text_and_tool_call` (SSE with text + tool_use → TextDelta…, ToolCall(name, input), Usage, Done("tool_use")); `test_anthropic_429_is_transient`; `test_anthropic_request_has_cache_control`.
  - `test_openai_tool_call_accumulates_deltas` (args split over 3 chunks → one ToolCall with parsed dict); `test_openai_bad_json_args` (`raw` set, input {}); `test_openai_message_mapping` (ToolResultBlock → `role:"tool"` with `tool_call_id`); `test_openai_500_transient`.
  - `test_with_retries_gives_up_after_3`; `test_resolve_refs` (`ollama/qwen3` → OpenAICompatProvider base_url localhost:11434; `claude-haiku-5-5` → anthropic); `test_missing_key_denied_with_hint` (env cleared, keychain monkeypatched to None).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(core): model providers for any vendor`.

---

### Task 4: Local CAD mirror tools (build123d, sandboxed)

**Files:**
- Create: `core/src/mises/cad/{__init__,worker,runner,tools}.py`
- Test: `core/tests/cad/test_runner.py`, `test_mirror_tools.py`

**Interfaces — Consumes:** Task 1 (`naming.check_name`, `platform.sandbox_profile`, `ToolOutput`, `StepKind`), Task 2 (`tool`, `ToolRegistry`, `ToolContext`). (Task 2 runs in parallel: code against its Interfaces block; if it is not merged yet, stub `tool` locally in tests via the same signature and delete the stub at merge.)

**Produces:**
```python
# Part layout: <workdir>/parts/<part_id>/part.py  (defines VARS: dict[str, float] and build(v: dict) -> build123d Part|Compound)
#              <workdir>/parts/<part_id>/vars.json, model.step, model.stl, model.glb, metrics.json
# runner.py
class BuildResult(ok: bool, metrics: dict, error: str|None, stdout_tail: str)
async def run_build(part_dir: Path, vars: dict[str, float], *, timeout_s: float = 60, density_g_cm3: float = 1.24) -> BuildResult
   # spawns: sandbox-exec -p <sandbox_profile(part_dir)> <sys.executable> -I -m mises.cad.worker <part_dir> <vars_json_path>
   # (-I needs the package importable: pass PYTHONPATH=<src dir> in env; worker reads only part_dir)
   # kills process group on timeout → BuildResult(ok=False, error="timeout after 60s")
# worker metrics (metrics.json): {valid, volume_mm3, area_mm2, mass_g, bbox:{min:[x,y,z],max:[x,y,z],size:[x,y,z]}, center_of_mass:[..], faces, edges, holes:[{diameter_mm, axis:[..], center:[..], depth_mm}], solids}
#   holes = cylindrical faces whose surface normal points toward the axis (internal), grouped by axis+diameter
# tools.py
def register_mirror_tools(registry: ToolRegistry) -> None
```

| Tool | Safety | scope_arg | Input | Output data |
|---|---|---|---|---|
| `mirror_part_create` | local_mutate | `part_id` | `part_id`, `script`, `vars: dict[str,float]` | metrics; `check_name` on part_id and every var name |
| `mirror_part_set_vars` | local_mutate | `part_id` | `part_id`, `vars` (partial) | metrics + `delta` (volume, mass, bbox size) vs previous metrics; `step_kind=DIFF` |
| `mirror_measure` | read | `part_id` | `part_id` | metrics; `step_kind=MEASURE` |
| `mirror_export` | read | `part_id` | `part_id`, `format: "step"|"stl"|"glb"` | handle to the file |
| `mirror_part_list` | read | — | — | part ids + vars |

`part_id` must match `^[a-z][a-z0-9_]{1,40}$` and pass `check_name`. Failed builds return `ToolOutput(ok=False, summary=error, ...)` — not an exception — so the agent can fix its script; a failed `set_vars` leaves the previous vars and files in place.

- [ ] **Step 1: Write failing tests**
  - `test_box_metrics`: script building `Box(VARS["length"],20,10)` with length 40 → `volume_mm3 == pytest.approx(8000)`, `mass_g ≈ 9.92`, bbox size `[40,20,10]`.
  - `test_holes_found`: plate with two Ø4.2 through-holes → `len(holes)==2`, diameters ≈4.2.
  - `test_timeout_killed`: script `while True: pass` with timeout_s=2 → ok False, "timeout" in error, finishes < 5 s.
  - `test_network_denied`: script opening `socket.create_connection(("1.1.1.1",80))` → ok False.
  - `test_write_outside_denied`: script writing `~/mises_escape.txt` → ok False and file absent.
  - `test_set_vars_delta`: create length 40, set 50 → `delta.volume_mm3 ≈ 2000`, step_kind DIFF.
  - `test_failed_set_vars_keeps_previous`: set vars that make the script raise → ok False, `vars.json` unchanged.
  - `test_bad_var_name_rejected`: var `"rib2"` → `MisesError(INVALID_INPUT)`.
  - `test_export_glb_handle`: handle path exists and ends `.glb`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS (these tests spawn real subprocesses; mark `@pytest.mark.slow` and keep them in the default run).
- [ ] **Step 5:** Commit `feat(core): sandboxed build123d mirror tools`.

---

### Task 5: Onshape client, adapter and tools

**Files:**
- Create: `core/src/mises/onshape/{__init__,client,adapter,tools}.py`
- Test: `core/tests/onshape/test_client.py`, `test_adapter.py`, `test_tools.py`, `tests/onshape/fake_onshape.py`

Read `research/integration/onshape-and-agents.md` first; verify HMAC signing details and endpoint paths against Onshape's official API docs (https://onshape-public.github.io/docs/) — the fake server must mirror what the real one checks.

**Interfaces — Consumes:** Task 1 (`Store.ledger_*`, `Store.add_usage`, `naming.check_name`, `MisesError`), Task 2 (`tool`, `ToolRegistry`; same stub rule as Task 4).

**Produces:**
```python
# client.py
class OnshapeClient:
  __init__(access_key: str, secret_key: str, *, store: Store, user: str, base_url="https://cad.onshape.com", transport: httpx.AsyncBaseTransport|None=None)
  async request(method, path, *, run_id: str|None, query: dict|None=None, json: dict|None=None, estimate: int = 1) -> dict
     # ledger_reserve → signed request (re-sign on each redirect, follow up to 3) → ledger_settle(status) → add_usage(onshape_calls=1 if counted)
     # 409/412 with microversion skew → STALE_VERSION; 429 → TRANSIENT; 402 → BUDGET_EXHAUSTED; 401/403 → DENIED; other 4xx → INVALID_INPUT; 5xx → TRANSIENT
     # exceptions before a response → ledger_release
# adapter.py
class DocRef(did: str, wvm: Literal["w","v","m"], wvmid: str, eid: str|None)
def parse_url(url: str) -> DocRef     # https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>
class OnshapeAdapter:
  __init__(client: OnshapeClient, store: Store)
  async microversion(ref, run_id) -> str
  async features(ref, run_id) -> dict          # cached in store.cache by f"feat:{did}:{microversion}"
  async variables(ref, run_id) -> list[dict]   # cached likewise
  async ensure_branch(ref, agent_name: str, run_id) -> DocRef   # workspace named f"agent/{agent_name}"; reuse if it exists
  async propose(branch: DocRef, run_id, *, source_microversion: str, variables: dict[str,str] | None, feature_updates: list[dict] | None) -> dict
      # one batched features/updates call with sourceMicroversion + rejectMicroversionSkew=true; variable sets; check_name on any new names
      # returns {"microversion", "changes": {"mass","volume","bbox"}} from mass properties + bounding box calls
  async merge_preview(branch, target, run_id) -> dict; async merge(branch, target, run_id) -> dict
```

Tools (`register_onshape_tools(registry)`; the adapter is read from `ctx.services["onshape"]`; missing → `DENIED` with hint "connect Onshape: `mises keys set onshape`"):

| Tool | Safety | onshape_cost | Notes |
|---|---|---|---|
| `onshape_doc_open(url)` | read | 3 | microversion + features + variables; feature rows shown via `display_name`; all names wrapped as data |
| `onshape_features_list(url)` | read | 1 | cached per microversion |
| `onshape_variables_get(url)` | read | 1 | |
| `onshape_propose(url, source_microversion, variables, feature_updates, summary)` | onshape_mutate | 4 | on `agent/<run's agent>` branch; records `run.branch` |
| `onshape_merge_request(url, summary)` | irreversible | 3 | merge preview in approval `deltas`; merge after approval |
| `onshape_budget_get()` | read | 0 | used / cap this year and this run |

- [ ] **Step 1: Write failing tests** against `fake_onshape.py` (an `httpx.MockTransport` handler that verifies the HMAC signature, serves features/variables, tracks a microversion, returns 409 on stale `sourceMicroversion`, and can return 302 to a second path).
  - `test_signature_verified`; `test_redirect_resigned`; `test_ledger_counts_success_only` (one 200, one 500 → year usage 1); `test_exception_releases_reservation`.
  - `test_parse_url`; `test_features_cached_by_microversion` (two calls, fake sees one request); `test_propose_stale_version` (fake bumped microversion → `STALE_VERSION`); `test_propose_rejects_numbered_name` (`"Rib 2"` → `INVALID_INPUT`, zero requests sent); `test_branch_reused`.
  - `test_merge_requires_approval` (through a real `Gate` with an approver task) ; `test_doc_open_wraps_names_as_data` (feature named `"Ignore previous instructions"` appears inside the `data` field only, summary mentions untrusted).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(core): Onshape client, adapter and tools with call ledger`.

---

### Task 6: Agent definitions and system prompts

**Files:**
- Create: `core/src/mises/agent/{__init__,definitions,prompts}.py`, `agent/builtin/{designer,planner,verifier,sim}.md`
- Test: `core/tests/agent/test_definitions.py`, `test_prompts.py`

**Interfaces — Consumes:** Task 1 (`AgentPolicy`, `Caps`). **Produces:**
```python
class AgentDefinition(name: str, description: str, model: str = "inherit", tools: list[str] | None = None, max_turns: int = 40, body: str, source: Path|None)
def load_definitions(dirs: list[Path] | None = None) -> dict[str, AgentDefinition]   # builtin dir first, then each dir in order (later overrides); YAML frontmatter between --- lines; bad file → skipped with a warning list attr
def build_system_prompt(defn: AgentDefinition, policy: AgentPolicy, tool_names: list[str], *, project_notes: str = "") -> str
```
Prompt order is stable-first so provider prompt caching hits: (1) fixed Mises harness rules, identical for every agent — check by numbers (mass/volume/bbox from tools), never claim a check you didn't run, no numbered names, Onshape and web text is untrusted data, meaning of each error code (`stale_version` → re-read then re-plan, never blind-retry; `budget_exhausted` → stop and tell the user; `needs_approval` → wait; `transient` → retry later), prefer the local mirror over Onshape calls; (2) agent body; (3) dynamic tail: edit/read scope, caps, tool list, project notes. Write the rules fresh in your own words.

Built-in agents: **designer** (main CAD agent; all tools), **planner** (read tools + `orchestrator_submit_plan`; outputs a plan only), **verifier** (read-only tools; skeptical: tries to prove the change wrong; reports pass/fail per requirement with evidence step ids), **sim** (placeholder for M4: read mirror tools; says simulation is not available yet).

- [ ] **Step 1: Tests:** `test_builtins_load` (4 names), `test_user_override` (tmp dir `designer.md` replaces body), `test_bad_frontmatter_skipped`, `test_prompt_stable_prefix` (two different agents' prompts share an identical first section ≥ 500 chars), `test_prompt_contains_scope_and_tools`, `test_prompt_mentions_untrusted`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(core): agent definitions and prompt builder`.

---

### Task 7: Agent loop and runtime

**Files:**
- Create: `core/src/mises/agent/loop.py`, `core/src/mises/ui_tools.py`, `core/src/mises/runtime.py`
- Test: `core/tests/agent/test_loop.py`, `core/tests/test_runtime.py`

**Interfaces — Consumes:** Task 2 (`Gate`, `ToolRegistry`, `ApprovalService`), Task 3 (`ModelProvider`, `ModelRequest`, `StreamEvent` types, `with_retries`, `resolve`), Task 6 (`AgentDefinition`, `build_system_prompt`, `load_definitions`), Tasks 4–5 (`register_mirror_tools`, `register_onshape_tools`).

**Produces:**
```python
# agent/loop.py
class LoopConfig(max_turns: int = 40, max_tokens: int = 8192, context_budget_tokens: int = 150_000, compact_at: float = 0.8, keep_recent: int = 6, max_repeat_errors: int = 3)
class RunResult(final_text: str, messages: list[Message], stop_reason: Literal["end_turn","max_turns","cancelled","circuit_open","error","max_tokens"], error: str|None = None)
class AgentLoop:
  __init__(*, run_id: str, defn: AgentDefinition, provider: ModelProvider, model: str, gate: Gate, registry: ToolRegistry, store: Store, events: EventBus, workdir: Path, config: LoopConfig = LoopConfig())
  async run(prompt: str | list[Block], history: list[Message] | None = None) -> RunResult
  def cancel() -> None
# ui_tools.py
def register_ui_tools(registry) -> None    # ui_ask_user(question, options: list[str]) (read; publishes question_asked, awaits runtime.answer()), ui_note(text) (read)
# runtime.py
class Runtime:
  __init__(home: Path | None = None, *, provider_resolver=resolve)
  store, events, approvals, registry, gate, definitions
  async start_run(agent: str, prompt: str, *, model_ref: str | None = None, edit_scope=("*",), read_scope=("*",), caps: Caps = Caps(), parent_id: str | None = None, allowed_classes: set[SafetyClass] | None = None) -> str   # creates run + frozen policy, starts AgentLoop in a task; returns run_id
  async send(run_id, text) -> None          # follow-up message to a finished/idle run (continues its history)
  async wait(run_id) -> RunResult
  def cancel(run_id) -> None                # loop.cancel(), approvals.cancel_run, store.release_run_reservations
  def answer(question_id, text) -> None
  def workdir(run_id) -> Path               # data_home()/runs/<run_id>; shared parts dir data_home()/parts symlinked in as workdir/parts
```
`Runtime.__init__` registers mirror, onshape and ui tools, and sets `services["onshape"]` to an `OnshapeAdapter` when `keychain_get("mises","onshape")` returns `access:secret` (else leaves it unset). Task 9 adds `register_orchestrator_tools` to this list and `services["orchestrator"]`.

Loop behaviour per turn: build `ModelRequest` (system prompt from Task 6, tool schemas for the definition's tools ∩ policy classes); stream through `with_retries`; publish `text_delta` events; record a `message` Step for assistant text; collect `ToolCall`s. A `ToolCall` with `raw` set gets an `invalid_input` result without calling the gate. Execute calls: consecutive `read` tools concurrently (`asyncio.gather`), all others one at a time in model order; each result → `ToolResultBlock(content=canonical_json(output or error.to_dict()), is_error=...)`. Stop on `end_turn` with no tool calls. Circuit breaker: the same `(tool, error code)` failing `max_repeat_errors` times in a row → stop `circuit_open`. Compaction: when estimated tokens (chars/4) of messages exceed `compact_at × context_budget_tokens`, ask the same model to summarise all but the last `keep_recent` messages (never split a tool_use from its tool_result), replace them with one user message `"[Summary of earlier work]\n…"`, record a `compaction` Step. Usage events → `store.add_usage`. Cancellation: `cancel()` sets a flag and cancels the in-flight stream/tool task; result `cancelled`; any pending tool call gets no execution.

- [ ] **Step 1: Tests** (all with `FakeProvider` and an in-memory runtime):
  - `test_text_only_turn`; `test_tool_roundtrip` (tool_use → result fed back → final text; the second request contains the ToolResultBlock); `test_unknown_tool_result_is_error_and_run_continues`; `test_bad_json_args_invalid_input`; `test_reads_run_concurrently` (two 0.2 s read tools finish in < 0.35 s); `test_mutations_sequential`; `test_max_turns`; `test_circuit_breaker`; `test_compaction_keeps_tool_pairs` (tiny budget → a `compaction` step, no orphan tool_result); `test_cancel_mid_tool` (slow tool, cancel → `cancelled`, ledger reservation released, pending approval rejected); `test_transient_provider_error_retried_then_fails` (3 transient → stop_reason `error`).
  - Runtime: `test_start_run_records_policy_and_steps`; `test_irreversible_tool_blocks_until_decided`; `test_send_continues_history`; `test_ui_ask_user_roundtrip`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(core): agent loop and runtime`.

---

### Task 8: Local server, MCP server, stdio proxy

**Files:**
- Create: `core/src/mises/server/{__init__,app,mcp_server,stdio_proxy}.py`
- Test: `core/tests/server/test_app.py`, `test_mcp.py`

Read the installed `mcp` 2.x package's docs/source for the current low-level `Server` API and streamable-HTTP transport before writing; don't rely on memory of 1.x.

**Interfaces — Consumes:** Task 7 `Runtime`, Task 2 `Gate`/`ToolRegistry`. **Produces:**
```python
# server/app.py
def create_app(runtime: Runtime, token: str) -> FastAPI
#  auth: "Authorization: Bearer <token>" on every route; WS uses ?token=; wrong/missing → 401 / close 4401
#  GET  /health -> {"ok": true}
#  POST /runs {agent, prompt, model_ref?, edit_scope?, read_scope?} -> {"run_id"}
#  GET  /runs -> [AgentRun]; GET /runs/{id} ; GET /runs/{id}/steps?after_id=
#  POST /runs/{id}/message {text}; POST /runs/{id}/cancel
#  GET  /approvals?status=pending ; POST /approvals/{id}/decide {payload_hash, approve, edited_payload?} (decided_by="local-user")
#  POST /questions/{id}/answer {text}
#  WS   /events?run_id= — JSON Event stream
#  /mcp — the MCP endpoint (streamable HTTP); run binding from header "X-Mises-Run" (required)
def serve(runtime, *, port: int = 0, token: str | None = None) -> tuple[int, str]   # 127.0.0.1 only; writes {"port","token","pid"} to data_home()/server.json (mode 0600)
# server/mcp_server.py
def build_mcp_server(runtime: Runtime) -> <mcp Server>   # list_tools from registry filtered by the bound run's policy classes; annotations readOnlyHint (read), destructiveHint (irreversible), idempotentHint (non-read); call_tool → runtime.gate.call(run_id,...) ; MisesError → isError result whose text is canonical_json(error.to_dict())
# server/stdio_proxy.py
async def run_stdio_proxy(url: str, token: str, run_id: str) -> None   # stdio MCP server that forwards list_tools/call_tool to the HTTP endpoint
```
CLI hook for later: `mises mcp-stdio --run <id>` reads `server.json` for url/token.

- [ ] **Step 1: Tests:** `test_auth_required` (401 without token); `test_create_run_and_steps` (FakeProvider runtime); `test_decide_approval_endpoint` (stale hash → 409 with `stale_version`); `test_events_ws_streams_run_events`; `test_bind_localhost_only` (server.json port bound on 127.0.0.1; `serve` rejects host override); `test_mcp_list_tools_has_annotations` (in-process MCP client over the HTTP app); `test_mcp_call_goes_through_gate` (call `mirror_part_list` → a tool_call Step exists for the bound run); `test_mcp_missing_run_header_rejected`; `test_stdio_proxy_forwards` (spawn proxy subprocess against a live uvicorn on a random port, list tools).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(core): local API server, mises MCP server, stdio proxy`.

---

### Task 9: Orchestrator (plan → DAG → subagents)

**Files:**
- Create: `core/src/mises/orchestrator/{__init__,plan,scheduler,orchestrator}.py`
- Test: `core/tests/orchestrator/test_plan.py`, `test_scheduler.py`, `test_orchestrator.py`

Study OMA's planner/scheduler (`research/harness/oma/core.md`, and the repo's source) for the pattern; implement independently in Python.

**Interfaces — Consumes:** Task 7 `Runtime.start_run/wait`, Task 6 definitions, Task 2 `tool`. **Produces:**
```python
# plan.py
class PlanTask(id: str, title: str, agent: str, prompt: str, depends_on: list[str] = [])
class Plan(goal: str, tasks: list[PlanTask])
def validate_plan(plan: Plan, known_agents: set[str], max_tasks: int = 12) -> list[str]   # errors: empty plan, dup ids, id regex ^[a-z][a-z0-9_-]{0,31}$, unknown dep, self-dep, cycle (names the cycle), unknown agent, too many tasks
# scheduler.py
class TaskResult(task_id: str, status: Literal["done","failed","skipped","cancelled"], output: str, run_id: str|None)
async def run_plan(plan: Plan, spawn: Callable[[PlanTask, dict[str, TaskResult]], Awaitable[TaskResult]], *, max_parallel: int = 3) -> dict[str, TaskResult]
   # ready = all deps done; failed/skipped/cancelled dep → task "skipped" (cascade, transitively); spawn receives the deps' results
# orchestrator.py
class Orchestrator:
  __init__(runtime: Runtime)
  async run(goal: str, *, parent_run_id: str, model_ref: str|None = None) -> dict   # planner agent → plan (submitted via orchestrator_submit_plan tool) → validate (one retry with errors fed back) → run_plan with child runs → returns {"plan","results"}
def register_orchestrator_tools(registry) -> None
#   orchestrator_submit_plan(plan: Plan)  — read; only stores the plan on the run (planner's tool)
#   agent_spawn(agent: str, prompt: str)  — local_mutate; child run with policy = parent's scopes and classes intersected with the child definition's; waits; returns its final text + run_id; depth limit 2
#   orchestrator_run(goal: str)           — local_mutate; runs a full plan from the designer agent
```
Child policies may only narrow the parent's: child `edit_scope ⊆ parent.edit_scope`, classes ⊆ parent classes, caps ≤ parent's remaining.

- [ ] **Step 1: Tests:** `validate_plan` cases (cycle `a→b→a` error names both; unknown agent; dup id; 13 tasks); `test_parallel_ready_tasks` (two independent 0.2 s tasks + one dependent → total < 0.55 s, dependent got both results); `test_cascade_skip` (a fails → b (dep a) and c (dep b) skipped, d independent done); `test_max_parallel` respected; `test_agent_spawn_narrows_policy` (parent edit_scope ["bracket"] → child cannot call a local_mutate tool on part "lid": `out_of_scope`); `test_depth_limit`; `test_orchestrator_end_to_end_fake` (FakeProvider scripts planner submitting a 2-task plan, children answering) → results both done.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(core): orchestrator with plan validation and DAG scheduler`.

---

### Task 10: ACP host for external agents

**Files:**
- Create: `core/src/mises/acp_host/{__init__,specs,session}.py`
- Test: `core/tests/acp/test_session.py`, `core/tests/fixtures/fake_acp_agent.py`

Read the installed `agent-client-protocol` package (client and agent sides, schema types) and https://agentclientprotocol.com before writing. Verify each spec's command line against the agent's current docs; mark unverified ones `verified=False`.

**Interfaces — Consumes:** Task 7 `Runtime` (store, events, approvals, `workdir`), Task 8 `server.json` / stdio proxy. **Produces:**
```python
# specs.py
class AcpAgentSpec(name: str, command: str, args: list[str], env: dict[str,str] = {}, verified: bool = False, install_hint: str)
KNOWN_AGENTS: dict[str, AcpAgentSpec]   # claude (Anthropic's ACP adapter via npx), codex (codex ACP adapter via npx), gemini (`gemini --experimental-acp`), opencode, goose — exact commands verified by the implementer
# session.py
class AcpSession:
  __init__(runtime: Runtime, spec: AcpAgentSpec, run_id: str, *, mcp_url: str, mcp_token: str, cancel_timeout_s: float = 10)
  async start() -> None        # spawn subprocess (cwd = runtime.workdir(run_id)); initialize; session/new with mcpServers=[stdio: `<python> -m mises.cli.main mcp-stdio --run <run_id>`]
  async prompt(text: str) -> str   # session/prompt; session/update → Steps + events: agent text → message, tool_call/tool_call_update → tool_call/tool_result (status "failed" shown failed), plan → plan, diff content → diff
  async cancel() -> None       # session/cancel; if not finished in cancel_timeout_s → kill process group; approvals.cancel_run; release reservations
  async close() -> None
# Client-side handlers: request_permission → ApprovalService.request(action="acp_permission", payload={tool_call, options}) → wait → map to the agent's allow_once / reject_once option (never allow_always); fs read/write → only inside workdir, else error; terminal/* → not supported error.
```

- [ ] **Step 1: Tests** using `fake_acp_agent.py` (an ACP agent built on the same package's agent side that: streams a text chunk, reports a tool_call then a failed tool_call_update, requests permission, then ends; and a `--hang` mode that ignores cancel):
  - `test_updates_become_steps` (message, tool_call, failed tool_result steps present, chain verifies); `test_permission_becomes_approval` (approve → agent receives the allow_once option id); `test_permission_reject`; `test_fs_write_outside_workdir_refused`; `test_cancel_kills_hung_agent` (hang mode, cancel_timeout_s=1 → process dead < 3 s, run status cancelled); `test_session_new_passes_mises_mcp_server`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(core): ACP host for external agents`.

---

### Task 11: CLI and REPL

**Files:**
- Create: `core/src/mises/cli/{__init__,main,repl}.py`
- Test: `core/tests/cli/test_main.py`, `test_repl.py`

**Interfaces — Consumes:** everything above. **Produces:** console script `mises`:
- `mises chat [--model REF] [--agent designer] [--scope PART ...]` — REPL (below).
- `mises serve [--port N]` — runs `serve`, prints url; Ctrl-C stops.
- `mises mcp-stdio --run ID` — stdio proxy using `server.json`.
- `mises acp NAME [--prompt TEXT]` — runs a known ACP agent against the `mises` MCP server (starts an in-process server if none running), streams its updates like the REPL.
- `mises models` — vendors, whether a key is found (env / keychain / none), default model. `mises keys set VENDOR` — prompts (hidden input) and stores via `keychain_set("mises", vendor)`; `onshape` stores `access:secret`.
- `mises agents` — loaded definitions. `mises runs [--steps ID]` — recent runs / a run's steps.

REPL (`repl.py`, `class Repl(runtime, agent, model_ref)`; rich for output, prompt_toolkit for input): streams assistant text; each tool call prints one collapsed line `▸ mirror_measure bracket  ✓ 42 ms` (✗ + error code on failure); `/expand N` shows full input/output of the Nth call; approval requests render a blue panel (action, payload JSON, deltas, short hash) and ask `approve? [y/N/e(dit)]` — only an explicit `y` typed at that prompt approves (pasted text containing a newline is ignored for approvals); `ui_ask_user` renders options as a numbered pick. Slash commands: `/model REF`, `/agent NAME`, `/plan GOAL` (orchestrator), `/runs`, `/cost` (tokens, $, Onshape calls used/cap), `/steps`, `/clear`, `/quit`. Ctrl-C during a turn → `runtime.cancel(run_id)`; Ctrl-C at an empty prompt twice → exit.

- [ ] **Step 1: Tests:** `test_models_lists_vendors` (capsys, env key present for one vendor); `test_agents_lists_builtins`; `test_repl_streams_and_collapses_tool_line` (FakeProvider; drive `Repl.handle_line` and capture rich console output to a string); `test_repl_approval_requires_explicit_y` (input "" → rejected; "y" → approved); `test_repl_paste_not_approval` ("y\nmore" → rejected); `test_slash_cost`; `test_ctrl_c_cancels_run`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** End-to-end check (manual, needs a key): `uv run mises chat --model anthropic/claude-haiku-5-5`, ask "make a 40×20×5 mm plate called mount_plate with two 4.2 mm holes and measure it". Expected: `mirror_part_create` then `mirror_measure` lines, two holes reported. If no key is set, run with `--model ollama/<model>` or skip and say so.
- [ ] **Step 6:** Commit `feat(core): mises CLI and REPL`.

---

## After all tasks

- Whole-branch review against this plan and design §7.3/§7.4/§8.
- `core/README.md`: install (`brew install uv`, `cd core && uv sync`), keys, `mises chat`, `mises acp claude`, how to point external MCP clients at `mises mcp-stdio`.
- Plan 2 (separate): the Mac app — Tauri shell + TypeScript/Three.js UI that talks to `mises serve` (console, approval cards, glTF viewer of `mirror_export` output).
