# open-multi-agent (OMA) core — baseline review

Source: `github.com/open-multi-agent/open-multi-agent`, shallow clone at commit `009bdbe` (2026-10-24), `@open-multi-agent/core` v1.22.0, MIT. Read only; no install, build or tests were run. All paths are relative to `packages/core/src/` unless noted.
Out of scope here (another agent covers them): approvals, run journal/verifyRun, governance, egress policy, ACP/process backends, observability.

Note on the design doc: `docs/design/2026-10-24-mises-design.md` has no sections 2a or 7a. Its numbering is 2 Intent, 6 Architecture, 7.1–7.9 Components. It also names the MCP server `tandem`, not `cad-harness`. I mapped against §2, §6/6.1, §7.1, §7.3 and §7.4.

## 1. Three execution modes

All three are methods on `OpenMultiAgent` (`orchestrator/orchestrator.ts:481`).

| Mode | Entry | Path |
|---|---|---|
| `runAgent(config, input)` | `orchestrator.ts:1042` | Fills in default config (`applyAgentDefaults`, `applyDefaultToolPreset`, `:1058`). Adds a consequential-confirmation wrapper if configured (`:1063`). Then `buildAgent` → `agent.run()` (`:1103`). Cost accounting runs afterwards (`:1121`). No coordinator, queue or delegation tool. |
| `runTasks(team, specs)` | `orchestrator.ts:2751` | You supply the DAG. `loadSpecsIntoQueue` (`coordinator.ts:711`) → `executeExplicitTaskQueue` (`orchestrator.ts:3066`) → `executeQueue` (`task-execution.ts:750`). No synthesis. |
| `runTeam(team, goal)` | `orchestrator.ts:1201` | Steps: (a) an optional fixed governance topology (`:1221-1234`); (b) a routing decision plus a **short-circuit** to one agent for "simple" goals (`:1549`, heuristic in `short-circuit.ts:146`: length ≤200 "information units" and no complexity regex); (c) coordinator decomposition (`:1699`); (d) parse/validate (`:1783`); (e) auto-assign (`:1949`); (f) `executeQueue` (`:2131`); (g) coordinator synthesis (`:2175`). |

Related entry points: `runFromPlan` (`:2283`), `restore` (`:2344-2358` overloads), `runConsensus` (`:2807`).

## 2. runTeam in detail

### 2.1 Planner (goal → task graph)
- **Coordinator config.** `buildCoordinatorBaseConfig` (`coordinator.ts:503`) builds an ephemeral agent named `coordinator` with `maxTurns: 3` by default (`:529`). It uses the same model defaults as the orchestrator and can be overridden per run via `options.coordinator`.
- **System prompt.** `buildCoordinatorSystemPrompt` (`coordinator.ts:377`) has three parts:
  - A roster section (`:427`) containing a *bounded JSON manifest* of each agent: a role summary capped at 140 chars (not the full system prompt), up to 20 capabilities and up to 24 tools (`:328-331`, `:349`).
  - An output-format section (`:441`) asking for a JSON array of `{title, description, assignee, dependsOn[], requires?, verify?}`, plus "Dependency Guidance" that pushes for minimal `dependsOn` to keep parallelism.
  - A synthesis section (`:477`).
- **User prompt.** `buildDecompositionPrompt` (`:486`): "Decompose the following goal… Return ONLY the JSON task array."
- **Schema.** Zod `coordinatorTaskSpecSchema` (`coordinator.ts:95`). It is `.strict()`, has at most 200 tasks (`:82`) and at most 64 requirements per task. `buildCoordinatorTaskSpecsSchema` (`:124`) adds a `superRefine` with runtime checks:
  - unknown assignee (only when `strictAssignees`)
  - unknown or ambiguous `dependsOn` title
  - duplicate titles
  - DFS cycle detection (`:173-194`)
  This schema is passed as the coordinator's `outputSchema` (`orchestrator.ts:1722`).
- **Repair.** `Agent.validateStructuredOutput` (`agent/agent.ts:703`) runs `extractJSON` + Zod. On failure it re-sends the full context plus the error text **once** (`:723-748`). Usage from both attempts is merged and budget-checked.
- **Fail closed.** If the plan is still invalid after repair, `runTeam` stops with `COORDINATOR_PLAN_INVALID` (`orchestrator.ts:1893-1920`). The code comment says why: "A coordinator plan is an execution boundary. Do not turn an invalid plan into a different topology: that could duplicate side effects". Earlier OMA versions fell back to one task per agent; that fallback is gone.
- **Assignee handling.** Invalid assignees are cleared and rescheduled, with a warning (`:1851-1862`). Under `strictAssignees` they are rejected instead (`:1815`).
- **Loading the plan.** `loadSpecsIntoQueue` (`coordinator.ts:711`) works in two passes:
  1. Create tasks to get stable IDs, and map normalized title → ID (`:745-770`).
  2. Resolve `dependsOn` by ID or title.
  It then validates the whole graph *before* adding anything (`validateTaskDependencies`, `:808`). Unresolved references fail the individual task rather than the whole plan (`:816-822`).
- **Requirements check.** `validateTaskRequirements` (`orchestrator.ts:1925`) checks hard `requires` (tools, capabilities, backend `llm|process|acp`, provider) against the roster before anything is dispatched.

### 2.2 Deterministic scheduler and executor
- **Queue** (`task/queue.ts:64`). States: pending, blocked, in_progress, completed, failed, skipped. `add` decides the initial state (`resolveInitialStatus`, `:709`). `complete` → `unblockDependents` fires `task:ready` (`:433`, `:726`). `fail` and `skip` cascade transitively to dependents (`cascadeFailure` `:514`, `cascadeSkip` `:533`). Independent branches keep running. The queue also supports plan patches and revisions (`:163-386`) for "repairable" recovery (`orchestrator/recovery.ts:20`, defaults of 3 revisions and 20 added tasks, `:28-29`).
- **Dispatch loop** (`executeQueue`, `task-execution.ts:750`). This is the default "pipeline" mode: event driven, with no rounds.
  - A `readyTaskIds` set is fed by `task:ready` events and reconciled with queue state on every loop iteration (`:963-968`).
  - One ready task is admitted per iteration (`:980-997`). A dependent never starts while its predecessor's promise is still in flight (`:984`).
  - Terminal gates (abort, lost lease, budget) go through one helper, `requestTerminalGateStop` (`:805`).
  - On a stop it **drains, then skips**: in-flight tasks finish, then everything else is skipped (`:945-955`).
  - On suspension it checkpoints and exits (`:934-942`).
  - A legacy round mode exists only for the `onApproval` callback (`:755`, `:1090-1100`).
- **Ordering and assignment** (`orchestrator/scheduler.ts:142`).
  - Strategies: `round-robin | least-busy | capability-match | dependency-first (default) | composite` (`:44`). Composite weights default to fit 0.7, load 0.3 (`:52-64`).
  - `orderReadyTasks` (`:260`) sorts ready tasks by how many blocked tasks depend on them (critical-path-ish).
  - `scheduleTask` (`:223`) assigns unassigned tasks lazily at dispatch time (`task-execution.ts:1000-1017`).
- **Concurrency.** `AgentPool` (`agent/pool.ts:58`) has a global `Semaphore(maxConcurrency=5)` plus a **per-agent mutex** `Semaphore(1)` (`pool.ts:62-68`, `:55`), so one named agent never runs two tasks at once. The queue itself has no separate concurrency knob; capacity is exposed as a dispatch gate (`task-execution.ts:973`).
- **Retries** (`orchestrator/retry.ts:46`, called at `task-execution.ts:1423`).
  - Off by default (`maxRetries: 0`). Set per task: `maxRetries`, `retryDelayMs` (default 1000 ms) and `retryBackoff` (default 2), capped at 30 s (`:13`).
  - Backoff uses "equal jitter", sleeping between nominal/2 and nominal (`:84-90`).
  - Retries are error-aware: auth, validation, abort and most 4xx errors are terminal, except 408/409/429 (`:108-115`).
  - A suspended result is never retried (`:100`).
  - Token usage is summed across attempts. On each new attempt the task's in-flight checkpoint is dropped, so the retry starts fresh (`task-execution.ts:1424-1428`).
- **Per-task context.** `buildTaskPrompt` (`task-execution.ts:1789`) controls what each task sees:
  - With `memoryScope: 'dependencies'` (default), it gets only its dependencies' outputs. The `dependencyPayload` mode is `output | structured | both`, capped at 64 KiB per dependency (`:1889`).
  - With `memoryScope: 'all'`, it gets the full shared-memory summary.
  - It also gets team-bus messages addressed to its assignee.
- **On success** (`task-execution.ts:1574-1614`): optional judge verification (§5) → write `task:<id>:result` to shared memory → `queue.complete` → checkpoint.
- **On failure** (`:1636-1640`): run recovery hook → `queue.fail` (cascades) → error event.

### 2.3 Synthesis
`runCoordinatorSynthesis` (`coordinator.ts:559`) is skipped if the run was aborted or is already over its token or cost budget (`:581-593`). In that case `runTeam` returns raw task outputs. Otherwise `buildSynthesisPrompt` (`:660`) builds a prompt from the original goal, the completed task outputs, FAILED sections, SKIPPED sections and the shared-memory summary, ending with "note any gaps". The prompt runs on the same coordinator config, with a separate model-routing phase `synthesis`. Its usage is charged to the run budget (`:621-645`).

## 3. Single-agent loop (`agent/runner.ts`, class `AgentRunner` `:574`)
- **Layering.** `Agent` (`agent/agent.ts`) owns the following, then delegates to `AgentRunner.stream()`:
  - state and history (`messageHistory`, `:117`)
  - the `beforeRun`/`afterRun` hooks (`:455`, `:609`)
  - the whole-run `timeoutMs` (`:474`)
  - structured-output instructions appended to the system prompt (`:209-222`)
  `run()` just collects the stream's events (`runner.ts:1020`).
- **Message assembly.**
  - `baseChatOptions` (`runner.ts:1172`) carries the model, tools (from `resolveTools()` `:995`, default-deny for built-ins), sampling parameters, thinking/reasoning flags, system prompt and abort signal.
  - Before each call, two optional steps run:
    1. `compressToolResults` shrinks tool results the model has already consumed (`:1449`). No LLM call.
    2. `contextStrategy` (`:1460`, `applyContextStrategy` `:931`) is one of four kinds:
       - `sliding-window` (`:599`)
       - `summarize` (`:808`): an LLM summary of old turns, cached by signature, with images stripped
       - `compact`
       - `custom`
  - The provider call is `tracedChat` (`:1500`), which supports a per-call `callTimeoutMs` (`:686-703`).
- **Tool execution: parallel, no cap.** All tool calls from one assistant turn run through `Promise.all` (`runner.ts:1255`), calling `toolExecutor.execute` (`:1872`). `ToolExecutor` has a `Semaphore(maxConcurrency=4)` (`tool/executor.ts:38-44`), but only `executeBatch` (`:148`) uses it. `execute()` (`:112`) does not, so in the agent loop concurrency is effectively unbounded within a turn. The model-side `parallelToolCalls` flag (`runner.ts:125`) is passed through to OpenAI-style providers only; the Anthropic adapter ignores it.
- **Tool errors are values.** Zod failures, gate denials, thrown exceptions and ungranted tools all become `ToolResult{isError:true}` (`executor.ts:189-200`, `runner.ts:1843`). LLM API errors propagate.
- **Stop conditions.**
  1. The model returns no `tool_use` → final output (`:1626-1666`).
  2. `turns >= maxTurns`, default 10 (`:592`, `:1439`). The last text becomes the output (`:1700`).
  3. Abort signal (`:1433`).
  4. Token budget: `maxTokenBudget` is checked after each response. The stop is deferred until the tool results are appended, so history never holds an unmatched `tool_use` (`:1541-1555`).
  5. Loop detection (`:1568-1619`, `agent/loop-detector.ts`) catches repeated tool calls or text. Action is `warn | inject | terminate | continue`. A second detection after a warning forces termination.
  6. Whole-run timeout.
- **Recovery and checkpoints.**
  - The runner keeps an explicit phase: `awaiting_model | executing_tools | completed`.
  - It calls `persistCheckpoint()` at every boundary: after the assistant turn and *before* any side effect (`:1679`), and after each tool result.
  - On restore, a tool call that already has a `commit` record is replayed from the checkpoint instead of re-executed (`runner.ts:1256-1258`, type `ToolCallCommitCheckpoint` `types.ts:2654`). Its result is returned to the model without running the tool again.
  - The first-turn "tools given but none used" warning is aimed at local models (`:1652`).

## 4. Tools, schemas, MCP, providers
- **Tool API.** `defineTool({name, description, inputSchema: Zod, outputSchema?, llmInputSchema?, consequential?, maxOutputChars?, execute})` (`tool/framework.ts:71`).
  - The JSON Schema sent to the model comes from Zod via `zodToJsonSchema` (`:279`), unless `llmInputSchema` is given.
  - `ToolResult` (`types.ts:767`) has two parts: `data` is app-owned and can be typed; `modelOutput` is what the model sees (text, image or file parts, `types.ts:137-163`).
  - Validation order in `runTool` (`executor.ts:180+`): input Zod → abort check → `onToolCall` gate → execute → optional `outputSchema` check → truncation (`truncateToolOutput` `:499`).
  - The gate receives `ToolCallContext{toolName, input, agentName, consequential, runId, taskId, toolCallId}` (`types.ts:683`). It is a clean per-agent hook point.
  - Built-in tools: bash, file read/write/edit, glob, grep, and `delegate` (max depth 3, `tool/built-in/delegate.ts:69-73`).
- **MCP client** (`tool/mcp.ts`, `connectMCPTools` `:398`). It is minimal:
  - stdio transport only (no Streamable HTTP or SSE anywhere in `src`)
  - tools only: `tools/list` with pagination (`:381`) and `tools/call` (`:436`)
  - no resources, prompts, sampling or MCP Tasks
  - each tool is wrapped with `inputSchema: z.any()` (`:432`), so no client-side validation; the server's JSON Schema is forwarded to the model
  - wrapped tools are never marked `consequential`
  - optional `namePrefix`; 60 s request timeout; `structuredContent` is passed through as JSON (`:212`)
- **Provider adapters.** The interface is `LLMAdapter{name, chat(), stream(), reasoning-echo policy?}` (`types.ts:3153`). There are two ways to add a provider:
  - Pass an `adapter` instance on `AgentConfig` (`types.ts:1033`), with no registry change.
  - Add a case to `createAdapter` (`llm/adapter.ts:164`) and a name to `SupportedProvider` (`:49`); SDKs load lazily via `import()`. OpenAI-compatible vendors are tiny subclasses: `GrokAdapter extends OpenAIAdapter` is 32 lines (`llm/grok.ts`).
  - Adapters include Anthropic, OpenAI, Azure, Bedrock, Gemini, Copilot, DeepSeek and several Chinese vendors, plus a Vercel `ai` SDK bridge (`llm/ai-sdk.ts`).

## 5. Memory, checkpoints, persistence, consensus, replay
- **SharedMemory** (`memory/shared.ts:69`). Keys are namespaced `<agent>/<key>`. Any agent can read, but an agent writes only its own namespace (`write` `:205`). It supports turn-based TTL (`writeExpiring` `:246`, `advanceTurn` `:114`), a markdown digest `getSummary` (`:338`) for prompt injection, and snapshot/restore (`:138`, `:166`). Reserved prefixes stop agents from overwriting checkpoint or approval keys (`:30`).
- **MemoryStore interface** (`types.ts:3065`): `get/set/list/delete/clear`, plus optional `compareAndSet` (required for durable approvals) and `setWithExpiry`. Implementations:
  - `InMemoryStore` (`memory/store.ts:31`)
  - `FileStore` (`memory/file-store.ts:80`): one JSON file, atomic temp+rename, serialized write chain
  - `RedactingStore`
  There is no SQLite store, but the interface is small enough to add one.
- **Checkpoints** (`memory/checkpoint.ts:45`). Stored under the key `__oma_checkpoint__/<runId|latest>`. The schema is now v5 (`types.ts:2738-2790`) and contains:
  - the queue snapshot
  - completed results
  - shared memory
  - per-task `InFlightTaskCheckpoint` (`types.ts:2678`): full conversation, runner phase, and pending tool calls with commit records
  - pending approvals and decisions
  - a journal watermark
  `saveRunCheckpoint` (`task-execution.ts:189`) is best-effort: a failed store write is reported but does not fail the run. `restore()` rebuilds the queue and memory, skips completed tasks, and re-runs synthesis for `runTeam` (doc comment `orchestrator.ts:2329-2342`). Single ownership needs an optional `runStore` lease (`acquireRunLease` `:3325`); without one, two processes can restore the same snapshot.
- **Consensus with judges** (`orchestrator/consensus.ts`).
  - Top level: `runConsensus` (`orchestrator.ts:2807`). Per task: `verify` (`runTaskVerify` `consensus.ts:336`), which runs before the task is finalized.
  - Judges run **one after another** so quorum or budget can stop the rest early (`:5`, `runConsensusCore` `:196`, quorum exit `:282`).
  - Modes: `refute` (skeptic prompt, `:51`) and `lens` (each judge gets one of six review angles round-robin, `:57`).
  - Verdict contract: `{"accept":bool,"critique":str}` (`:67`). Unparseable output counts as a reject (`parseJudgeVerdict` `:126`).
  - Default quorum is ceil(judges/2) (`:374`).
  - `onDissent` is `revise` (default; the proposer gets the critiques, `buildRevisePrompt` `:111`), `reject` or `keep` (`:296-315`).
  - Judge usage counts against the parent budget.
- **Plan replay.** `runTeam(..., {planOnly:true})` → `createPlanArtifact` (`orchestrator.ts:2241`, `version:1`, tasks with IDs, deps, assignees and retry settings) → `runFromPlan` (`:2283`). The replay uses the stored IDs, assignees and descriptions exactly, never calls the coordinator, does no synthesis, and requires `recovery: 'fixed'` (`:2289`). Together these give a plan → human review → execute-exactly flow.

## 6. Code quality and adoptability
- **Size.** `src/` is about 49.3k LOC across 16 subsystems. The parts that matter here (agent, orchestrator, task, tool, memory, llm, team) total about 27.9k, plus `types.ts` at 3.2k.
  - `orchestrator.ts` is a 3,800-line god class.
  - `task-execution.ts` (1,954) and `runner.ts` (2,290) are dense state machines, full of journal, trace and approval branches tied into the core flow.
  - Roughly half of core is enterprise surface: eval, journal, observability, dashboard, run store, approval, governance.
- **Tests.** 163 test files, about 52.3k LOC, about 2,190 `it/test` cases, run with vitest; provider SDKs are mocked. Plus 6 opt-in e2e files and a CI matrix on Node 20/22/24 (`AGENTS.md`). The tests are heavily invariant-driven (checkpoint v5, adaptive recovery, scheduler, task retry). I counted the tests but did not run them.
- **Dependencies.** Runtime: `@anthropic-ai/sdk ^0.52`, `openai ^6`. Peer: `zod` (v3 or v4). Optional peers: MCP SDK, ACP SDK, Bedrock, Gemini, `ai`. Requires Node ≥20 and is ESM only.
- **Release cadence.** Very fast (v1.22, a commit dated today). Expect API churn: there are already checkpoint schemas v1–v5 and two approval mechanisms.
- **Library vs patterns.** Adopting it as a library means running a Node process and accepting its abstractions: its own `Team`, `AgentConfig`, its MCP client and its token-only budgets. Its most valuable machinery, the runner tool loop and in-flight checkpoints, **does not apply to ACP agents** ("External backends replace the LLM runner", `AGENTS.md` invariants). The patterns, by contrast, are small, well isolated and easy to port. Overall: borrow patterns.

## Mapping to cad-harness

| OMA mechanism | Verdict | Why |
|---|---|---|
| Library as a whole | **Skip** | The design (§6, §7.3) puts the agent host in the Python core service, with ACP agents as the default and the Claude Agent SDK as the orchestrator. OMA would add a third agent runtime that duplicates the Agent SDK's loop and MCP client, and ACP agents bypass its loop anyway. |
| `runAgent` loop (runner.ts) | **Skip**, borrow two details | The Agent SDK owns the loop. Borrow: (1) defer the budget stop until tool results are appended, so history never has an unmatched `tool_use` (`runner.ts:1541`); (2) loop detection with warn → terminate (`:1568`). A looping agent that repeats `onshape.verify` burns real API budget. |
| Unbounded parallel tool calls in a turn (`runner.ts:1255`) | **Avoid** | Metered Onshape tools must be serialized and checked against the ledger *before* the call. Enforce this in the MCP server, per agent and per document, because ACP agents get no OMA loop to enforce it for them. |
| Coordinator planner prompt + Zod/superRefine DAG schema + one repair attempt + fail-closed | **Copy pattern** (Pydantic in Python) | Fits "user asks for a change → orchestrator spawns SIM/MAT subagents". Keep: bounded roster manifest, title-based `dependsOn`, cycle/duplicate checks inside the schema, and "an invalid plan is an execution boundary". Add CAD-specific fields: `editScope` (part), `onshapeCallEstimate`, `requires: sim|mirror`. |
| Short-circuit for simple goals | Copy (cheap) | Most edits ("make the wall 3 mm") need one agent. That saves a decompose call and a synthesis call. |
| Event-driven ready-set executor, drain-then-skip, cascade fail/skip, blocked-dependents priority | **Copy pattern** | A natural fit for an asyncio TaskGroup with a semaphore. Cascade-skip matches "sim failed → don't propose". |
| Pool semaphore + per-agent mutex | Copy | Maps to "one ACP session per agent branch" (§7.1 branch per agent): never two concurrent writes from the same agent/branch. |
| Error-aware jittered retry | Copy for LLM/sim; **not** for Onshape writes | Onshape writes use `rejectMicroversionSkew`. A 409 or skew means re-plan, not retry. OMA treats 409 as retryable (`retry.ts`), which would be wrong here and would cost budget. |
| Token/cost budgets (`budget.ts`) | Copy shape only | OMA meters tokens and dollars only; it has no generic resource quota. The Onshape ledger (§7.1: per user, agent, day, year; 2xx/3xx only) must be our own. Hook it at the MCP tool boundary, the equivalent of OMA's `onToolCall` gate with `agentName`/`taskId`/`runId` (`types.ts:683`), with a hard stop before the call. |
| Tool API (`defineTool`, separate `data` vs `modelOutput`, `outputSchema`, `consequential`) | **Copy pattern** in the `tandem` MCP server | Matches §7.4 "every mutation returns `{ok, version, changes, warnings}`": a typed `data` for the harness, plus a compact model view and image parts for renders. A `consequential` flag maps to merge/print/spend, which always need approval. |
| MCP client (`tool/mcp.ts`) | **Skip** | It supports stdio and tools only, does no input validation and has no MCP Tasks. §7.4 needs HTTP+stdio and MCP Tasks for long jobs. The Agent SDK and ACP agents bring their own clients. |
| Provider adapter layer | **Skip** | Models come via ACP agents or the Agent SDK. Not our problem to abstract. |
| SharedMemory (namespaced, agent-attributed) | Borrow idea only | The real shared state is the SQLite project store plus Onshape branches/microversions. The useful idea is the namespacing rule: an agent writes only its own namespace and reads everything. |
| Checkpoints with per-tool **commit records** (no re-execution on resume) | **Copy pattern** (high value) | The most important idea for a budget-capped API. Persist "tool call X committed, result R" before reporting it, and on resume replay R instead of calling Onshape again. Implement in the ledger/MCP server, keyed by an idempotency key (agent, branch, sourceMicroversion, change hash) so it also covers ACP agents. |
| Consensus judges (refute/lens, quorum, revise) | Optional; copy the lens prompt for design review | Mechanical claims should be judged by numbers (sim results, mass/bbox; §7.4 "check by numbers"), not LLM votes. Lens judges ("failure modes", "hidden assumptions") could be a cheap pre-approval critic on a proposal card. Run judges one after another so quorum stops spending early. |
| planOnly → PlanArtifact → runFromPlan | **Copy pattern** | Fits human approval: show the plan as a checklist (§7.3 `plan` → checklist), approve, then execute the frozen plan with no re-planning. Add each task's Onshape call estimate to the artifact so the user approves the budget too. |
| Adaptive recovery / plan patches | Defer | Useful later (M6 multi-agent). Bounded revisions (3) and bounded added tasks (20) are good guardrails. |

### Does TypeScript fit Tauri + Python?
Not as designed. OMA needs Node ≥20: `fs`, `child_process` for stdio MCP, provider SDKs and `process.env` keys. It cannot run in the Tauri webview, and the design has no Node runtime. The options are:
1. **Python port of the patterns** inside the core service, next to the ledger and MCP server (recommended). The Claude Agent SDK also has a Python version, so the orchestrator, ledger, cache and MCP server live in one process with one SQLite database. That makes atomic "check budget → call → commit" easy.
2. **Node sidecar** supervised by the Rust shell (Tauri supports sidecar binaries). It works, but adds a bundled Node runtime of tens of MB, conflicting with the small-installer goal (§6, §9). It also adds a second IPC hop to the Python service for every mirror, sim or Onshape tool, and splits budget state across processes.
3. **TS Agent SDK orchestrator in the sidecar** has the same costs as option 2. It is only worth it if the team wants TS end to end, in which case OMA could be used as a library just for `runTasks`/`runFromPlan` orchestration. Even then, metering and commit records must live in the Python MCP server, because ACP agents never pass through OMA.
