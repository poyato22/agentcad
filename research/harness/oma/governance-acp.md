# OMA baseline: approvals, journal, governance, egress, ACP, budgets, telemetry

Source: `github.com/open-multi-agent/open-multi-agent` (MIT), shallow clone at commit `009bdbe` (2026-10-24), read only. Paths below are relative to the repo root; `core/` means `packages/core/src/`. Nothing was installed or run. The execution modes, planner/scheduler, agent loop, tools, providers and memory are covered in a sibling report.

One framing point first. OMA is a TypeScript library, and Tandem's core service is Python (design §6). So "adopt" below almost always means "copy the pattern into Python". The other option is running OMA as a Node sidecar, which is only worth it for the ACP host, and even there it is too thin (§5).

---

## 1. Durable approvals

**Pause points.** There are four gates, and each returns `true/false` or `{action:'allow'|'deny'|'suspend', reason?}` (`core/types.ts:501`, `:696-699`):

| Gate | Where it fires | Scope / boundary id | Reviewed content |
|---|---|---|---|
| `onPlanReady` (pre-plan; only for coordinator plans from `runTeam`) | `core/orchestrator/orchestrator.ts:2025-2047` | `plan` / `'coordinator-plan'` | `{kind:'plan', continuation:'execute'\|'plan_only', tasks: TaskSnapshot[]}` (`types.ts:410-415`) |
| `onApproval` (legacy round barrier) | `core/orchestrator/task-execution.ts:1712-1748` | `task_round` | `{completedTasks, nextTasks}` (`types.ts:418-422`) |
| `onTaskDispatch` (pre-dispatch) | `task-execution.ts:1019-1062` | `task_dispatch` / task id | one fully assigned `TaskSnapshot` (`types.ts:425-428`). Fires before the backend is resolved, so a rejection means an ACP subprocess is never spawned (`docs/external-agents.md`, "Control boundary") |
| `onToolCall` (pre-tool-call) | `core/tool/executor.ts:250-293` → `core/agent/runner.ts:1285-1303` | `tool_call` / `${taskId}:${toolCallId}` | `{toolName, rawInput, input (Zod-validated), agentName, taskId, toolCallId, consequential}` (`types.ts:431-442`) |

`onApproval` and `onTaskDispatch` cannot be used together (`types.ts:2476`). A `suspend` decision on a tool call works only inside checkpointed, built-in LLM workers. It fails closed for standalone `runAgent`, process backends and ACP backends (`executor.ts:283-293`, `docs/durable-approvals.md:215-220`).

**Record format** (`core/types.ts:452-498`, `core/approval/durable.ts`):
- `ApprovalRequest {version:1, id, runId, scope, boundary, requestHash, requestedAt, reason?, content}`. `requestHash` is the SHA-256 of a canonical JSON of `{scope, boundary, content}`. The canonicaliser sorts keys and rejects non-finite numbers, cycles, `undefined` and non-plain objects (`durable.ts:54-131`). `reason` and `requestedAt` are left out of the hash. `id = 'apr_' + sha256(runId\nscope\nboundary\nrequestHash)[:32]`, so it is deterministic (`durable.ts:184`).
- `ApprovalDecisionRecord {version:1, requestId, runId, scope, requestHash, decision:'approved'|'rejected', reviewer:{id, displayName?}, decidedAt}` (`types.ts:473-482`).
- `ApprovalRecord {version:1, request, decision?}` is stored under the key `__oma_approval__/<requestId>` (`durable.ts:21, 42`).
- Every read re-checks the whole record: version, id format, scope against content kind, recomputed hash, recomputed id, and that the decision binds its request (`durable.ts:305-412`).

**Decision protocol.** `decideApproval(store, {requestId, requestHash, decision, reviewer})` (`durable.ts:483-578`):
- A hash that differs from the inspected one fails with `APPROVAL_STALE_DECISION`.
- A record that already has a decision fails with `APPROVAL_CONFLICT`.
- Otherwise it writes with `compareAndSet(key, oldValue, newValue)`. If the swap fails, the result is `APPROVAL_CONFLICT`, so the first decision wins and decisions are immutable.
- `reviewer.id` must be a non-empty string. It is not authenticated (`durable.ts:207-220`).

**Storage backends.** Any `MemoryStore` that implements `compareAndSet` (`types.ts:3065-3095`). Without CAS, suspension fails with `APPROVAL_ATOMIC_STORE_REQUIRED` (`durable.ts:419-425`). Shipped stores:
- `InMemoryStore`: process-local CAS.
- `FileStore` (`core/memory/file-store.ts:135`): CAS only within one instance, with no cross-process lock.
- `RedactingStore`: deliberately has no CAS, because it is lossy and would break the hash (`memory/redacting-store.ts:43`).
- A separate `RunStore`/`RunLedger` adds leases and fencing so that two workers cannot both resume one approved run. Its `atomicity` field is `'process'|'cross-process'` (`core/run/store.ts`, `run/ledger.ts`).

**Suspend → restart → resume.**
1. `persistPendingApproval` (`task-execution.ts:388-418`) writes the checkpoint strictly; if the save fails, suspension is not reported. It then calls `ledger.ensureRequest` (create-if-absent CAS, `durable.ts:438-459`), emits a journal `approval/request` event and an `onProgress {type:'approval_pending'}` event.
2. The run returns `status.code === 'suspended'` with `pendingApprovals` (`types.ts:525-527`).
3. A fresh process calls `restore()` (`orchestrator.ts:2344+`). It takes a run lease, then:
   - checks every checkpointed decision against the primary ledger and fails on a mismatch (`:2545-2554`);
   - re-checks every pending request against the checkpoint (`:2567`, `assertApprovalMatchesCheckpoint` at `:3552`);
   - returns `suspended` again for any request that is still undecided;
   - for a decided request, sets `approvedBoundaries` (dispatch), drops the gate (plan/round), or attaches the decision to the pending tool call (`:2569-2610`).
4. A consumed boundary is matched by `id` plus `requestHash`, so the gate is not called again (`task-execution.ts:362-372`).
5. For a tool call, the executor parses the raw input again with the current Zod schema, rebuilds the content, rehashes it and compares it with the reviewed hash. "Stale or tampered" is reported as an error result, and the tool does not run (`executor.ts:213-248`). A rejected tool call becomes a denied `ToolResult` and the model adapts on its next turn. A rejected plan, round or dispatch is terminal (`docs/durable-approvals.md:125-142`).

**Explicitly not provided** (`docs/durable-approvals.md:171-236`):
- no approval UI, CLI or transport/notification;
- no expiry, revocation, quorum or reassignment;
- the hash binds the serialised data, not the code or prompts that will execute it;
- tasks that carry `verify` config cannot be suspended.

## 2. Run journal and `verifyRun()`

**What is recorded.** The journal is opt-in. It is an append-only stream of `RunEvent`s with base fields `{seq (1-based, monotonic across attempts), timestampUnixMs, runId, attempt, taskId?, agentName?, traceId?, spanId?, sourceEventSeqs?}` (`core/journal/events.ts:46-60`). There are 16 event types (`events.ts:67-245`):
- `run/start|end`, `plan/set`, `task/status`;
- `turn/start|end`, `user/message`, `assistant/message`, `llm/request`, `tool/call`, `tool/result`, `context/replace`;
- `memory/set`;
- `approval/request|decision` (`events.ts:229-237`);
- `checkpoint/saved {watermarkSeq}`.

`llm/request` stores one `{messageIndex, blockIndex, role, blockType, sourceEventSeqs, contentHash}` descriptor per model-visible block, plus `systemPromptHash` and `toolsHash`, rather than the whole conversation (`docs/run-journal.md:150-162`).

`approval/decision` is emitted only when a resumed attempt consumes the decision (`orchestrator.ts:3137-3141`). `decideApproval` itself never writes to the journal.

**Backends.**
- `InMemoryRunJournal`: a ring buffer with `maxEvents` 10k.
- `JsonlRunJournal` (`core/journal/jsonl-journal.ts`): flushes in batches on a fixed 50 ms deadline, with one `write` plus `fsync` per batch, tolerance for one trailing partial line, and a single writer. It can redact at write time.
- Appends are best-effort. A failure shows up as `onProgress {kind:'journal_append_failed'}` (`orchestrator.ts:952`) and never fails the run.

**Hashing.** `canonicalContentHash` is SHA-256 over JSON with sorted keys, applied per content block (`core/journal/hash.ts`). The approval hash (§1) is a separate implementation with stricter validation.

**There is no chaining.** No event carries its predecessor's digest, and nothing is signed or stored WORM (`docs/run-journal.md:514-520`).

**`verifyRun(journal | {events})`** (`core/journal/verify.ts:143-310`) runs three checks in order:
1. `SEQ_NOT_MONOTONIC`: a repeated or reversed seq. Forward gaps are allowed.
2. `BROKEN_LINK`: a `sourceEventSeqs` entry at or above its citing seq.
3. Per block of every `llm/request`: the block passes only if a cited message event, or a `context/replace` replacement, hashes to the recorded `contentHash`. Otherwise it fails with `MISSING_CONTEXT_REPLACE`, reason `no-lineage` or `not-reproducible`.

Events that cannot be checked because they fell outside the readable window go into `inconclusive`, not `failures`. `stats` reports how much was examined.

**What it proves:** every block the model saw is explained by a recorded event or a recorded rewrite, so a silent context rewrite is structurally detectable.

**What it does NOT prove:**
- **Not tamper-evidence.** A writer can edit an event and recompute the citing hash.
- **Not completeness.** Turns are not paired, and `run/end` is not required.
- **Not truth of tool results.** `tool/result` is whatever the tool returned.
- **Not anything about external agents.** Process and ACP backends never touch the journal recorder, so only `task/status`, `memory/set` and the other orchestrator-level events appear for them (`docs/external-agents.md`, "Does not apply").
- **Not human identity.** The only human identity is `reviewer.id` on decisions.

Redaction also breaks reproducibility for any block a redaction pattern touched.

## 3. Governance (required roles, ordering, conclusions)

**Declaration.** A run declares governance through `RunTeamOptions.governanceIntent: 'required'|'preferred'|'none'`, plus `requiredRoles` and an optional `requiredOrder`. `buildGovernanceTaskSpecs` (`core/orchestrator/governance.ts:108-180`) validates the declaration:
- roles must be unique and present in the roster;
- `requiredOrder` must be a permutation of the roles.

It then builds one task per role with the goal as its description. When an order is given, each task depends on the previous one, with `memoryScope:'dependencies'`.

**Evaluation.** `evaluateGovernance(declaration, receipt)` (`governance.ts:37-62`) runs after execution and looks only at the `ExecutionReceipt`. It never reads model text (`observability/execution-receipt.ts:25-28`). The conclusion is `satisfied` only when all of these hold:
- every required role executed;
- every adjacent ordered pair started in order and has a dependency path between them (`:198-249`);
- with two or more roles, `independentReviewOccurred` is true, which means at least two distinct worker instances and at least one cross-assignee dependency edge (`execution-receipt.ts:379`).

**Surfacing.** `finalizeGovernanceRun` (`governance.ts:69-101`, called at `orchestrator.ts:1506` and `:3300`) attaches the following to the result:
- `governanceConclusion: 'satisfied'|'unsatisfied'|'not-applicable'`;
- `governanceReason: 'overridden'|'budget'`;
- the flags `governance-overridden` and `review-skipped-due-to-budget`.

`success` is deliberately independent of these fields, so a caller must gate on `governanceConclusion` itself (`docs/observability.md`, "Post-execution governance conclusion").

**Limit.** "Review occurred" is a statement about topology only. A reviewer that ran after the coder, with a dependency edge between them, satisfies the gate even if it did nothing useful.

## 4. Egress policy

`egressPolicy: {mode:'offline'} | {mode:'allowlist', allowedOrigins}`. It is normalised and intersected across the orchestrator, run and agent scopes, so a narrower scope can only narrow (`core/llm/egress.ts:49-139`). In `offline` mode only `localhost`, `*.localhost`, `127/8` and `::1` are allowed (`:84-95`).

**Enforcement** happens in `createEgressFetch` (`egress.ts:171-186`). It is a fetch wrapper that calls `assertEgressAllowed` on every request and forces `redirect:'error'`. It is injected into these adapters:
- Anthropic (`llm/anthropic.ts:411`);
- OpenAI and OpenAI-compatible (`llm/openai.ts:110`);
- Azure (`llm/azure-openai.ts:126`);
- Copilot (`llm/copilot.ts:269`);
- the image adapters (`image/http.ts:22`).

Adapters it cannot guard are rejected before they are imported or connected (`rejectUnsupportedEgress`, `llm/adapter.ts:100-108`, `agent/agent.ts:187`): Gemini, Bedrock, the AI SDK bridge and custom adapters.

**Not covered:** MCP stdio children, process and ACP backends, `bash`, custom tools, and OTel exporters (`docs/egress-policy.md`, enforcement matrix). It is a URL check on framework-owned LLM calls. It is not a sandbox, DNS pin or proxy.

## 5. External agents (process and ACP backends)

**Wiring.** Setting `AgentConfig.backend: {kind:'process'|'acp', command, args?, env?, cwd?, input?, permission?}` (`core/types.ts:895-979`) bypasses `AgentRunner` entirely (`agent/agent.ts:173-181`, `:270+`). The agent still takes part in the DAG, shared memory, cascading failure, the token budget and the plan/round/dispatch gates. It does not get the `onToolCall` gate, the filesystem sandbox, `egressPolicy`, tool-level journal events or mid-task checkpoints (`docs/external-agents.md`, "Control boundary").

**Process backend** (`agent/process-backend.ts`):
- one subprocess per run, with the prompt passed by stdin or argument;
- stdout plus exit 0 is success;
- a non-zero exit or a signal is a failure, with stderr redacted;
- abort kills the process tree (`:76`, `:153-156`);
- usage is always `{0,0}`.

**Launch commands** (`docs/external-agents.md`):
- Claude Code: `npx -y @agentclientprotocol/claude-agent-acp`
- Gemini CLI: `gemini --acp`
- Codex: `codex-acp`

All three use the same ACP code path, with no per-agent special cases.

**ACP backend in depth** (`core/agent/acp-backend.ts`, 501 lines; optional peer dependency `@agentclientprotocol/sdk` ^1.2.0, loaded lazily at `:402-412`):

| Aspect | What OMA does | Gaps relevant to Tandem |
|---|---|---|
| Transport | Spawns the child with `stdio:['pipe','pipe','inherit']` and frames it with the SDK's `ndJsonStream` (`:415-454`). A spawn error closes the connection so pending requests reject. | stderr is inherited, not captured. There is no restart or crash supervision. |
| Methods sent | `initialize {protocolVersion, clientCapabilities: {}}` (`:356-359`), then `buildSession(cwd).start()`, i.e. `session/new` (`:360`), then `session/prompt` once per OMA run (`:204`) and `session/cancel` as a notification on abort (`:176-180`). | Never sent: `authenticate`, `session/load`, `session/resume`, `session/set_mode`, `session/close`. **OMA's code passes no `mcpServers`**: only `cwd` reaches `buildSession`, and there is no config field for it. Tandem needs `mcpServers:[tandem]`. |
| Client methods served | Exactly one handler: `session/request_permission` (`:350`). | Not served: `fs/read_text_file`, `fs/write_text_file`, `terminal/*`, `elicitation/create`. Empty capabilities mean the agent does its own I/O in `cwd`. |
| Session lifecycle | One session is created lazily on the first run (`ensureSession`, `:328-344`), cached, and reused for every later run of that agent so context carries over. A failed start clears the cached promise so the next call can retry. `dispose()` closes the session and connection, kills the child, and resets the usage counter and system prompt (`:305-322`). | Nothing in `runTeam` or `runTasks` calls `dispose()`; the child lives until the process exits. A session cannot be recovered after a host restart. The system prompt is prepended to the first prompt only (`:197-200`). Input is text only (`lastUserText`, `:461-472`). |
| Permission requests | `resolvePermission` (`:363-395`). Policy is `'auto-approve'` (the **default**), `'reject'`, or `fn({title, kind, optionKinds}) → boolean`. It prefers `allow_once` over `allow_always` and `reject_once` over `reject_always`. If no matching option is offered it returns `{outcome:'cancelled'}`. | The callback sees no `toolCallId`, `rawInput`, `locations` or diff content, so it cannot drive a content-bound approval card. It is synchronous within the turn and cannot be suspended durably. |
| Streaming updates | It drains `session.nextUpdate()` until a `stop` message (`:208-254`). It handles `agent_message_chunk` (text only, also yielded as `text` stream events), `tool_call`, `tool_call_update` and `usage_update`. | Everything else falls through `default` and is dropped: `agent_thought_chunk`, `user_message_chunk`, `plan`, `available_commands_update`, `current_mode_update`, and non-text chunks. |
| Tool-call reporting | On `tool_call` it records start time, title and `rawInput`. On a `tool_call_update` with status `completed` or `failed` it pushes `ToolCallRecord {toolName: title, input, output: rawOutput or JSON of content, duration}` into `result.toolCalls` (`:223-242`). | It is reporting only, with no gate. `kind`, `locations`, `pending`/`in_progress` states and `failed` status are lost: a failed call looks the same as a completed one. Diff content is stringified, not structured. No journal events are emitted. |
| Stop reasons | `end_turn` is success. `max_tokens` or `max_turn_requests` give `budgetExceeded:true`. `refusal` is an error that fails the task and cascades. `cancelled` returns partial output (`:262-296`). | — |
| Cancellation | Cooperative: `session/cancel` and wait for the agent's `cancelled` stop. An abort before the turn starts returns empty output without prompting (`:184-190`). The subprocess stays alive afterwards. | No timeout (`callTimeoutMs` is "reserved, unused", `:81-82`), so a hung agent hangs the task. |
| Usage | `usage_update.used` is cumulative context tokens. It is converted to a per-turn delta and stored as `input_tokens`, with output 0 (`:243-283`). | `usage_update.cost` is ignored. An agent that never sends `usage_update` is not budget-gated at all. |

The full hook surface amounts to "prompt in, text plus a tool list out". It is a clean minimal reference, but Tandem's host needs about five times more: mcpServers, fs/terminal mediation, plan and diff lanes, content-bound permission cards, and session resume.

## 6. Budgets, Run Viewer, OpenTelemetry

**Budgets** (`core/orchestrator/budget.ts`, `docs/budgets-and-limits.md`):
- `maxTokenBudget` can be set on the agent, orchestrator or run. `maxCostBudget` can be set on the orchestrator or run and requires a caller-supplied `estimateCost(usage, {agentName, model, provider, phase, taskId})`; otherwise it throws. No price table ships.
- When two scopes set a ceiling, the lower wins (`resolveBudgetCeiling`, `budget.ts:80`).
- Budgets are checked at turn and task boundaries, so a run can overshoot by one turn. A trip triggers when usage strictly exceeds the cap.
- On a trip, nothing is thrown. The run gets an `onProgress budget_exceeded` event and `status.code:'budget_exhausted'`, remaining tasks are skipped, synthesis is skipped, and the trip is not retried.
- There is no per-tool or per-API-call ledger, and nothing for external side-effect quotas.

**Run Viewer** (`core/dashboard/render-run-viewer.ts`, `run-viewer-model.ts`, `docs/run-viewer.md`):
- A post-run, self-contained static HTML page with a task DAG and a span waterfall, built from a `TeamRunResult` and/or a `TraceStore` run. It does no I/O and loads nothing remote.
- Its payload comes from an allowlist and contains no prompts or tool arguments.
- It is read-only with respect to approvals. It is not live and not multi-run.

**OTel** (`packages/otel/src/mapping.ts`, `exporter.ts`):
- A separate package that maps TraceRecord v2 to spans of kind run, agent, task, llm, tool, plan, consensus, checkpoint and callback, using GenAI semconv `1.43.0-development` attributes (`mapping.ts:139-169`).
- `llm` spans map to `CLIENT`; everything else maps to `INTERNAL` (`:171`).
- Content and secret attributes are filtered by regex (`:35-36`).
- The provider is application-owned and shutdown is opt-in.
- Approval facts appear as `oma.approval.*` attributes and `approval_decision` span events. Cost appears as `oma.cost.amount|currency|source|price_version`.
- Telemetry is explicitly not execution state: losing a trace never loses a decision.

---

## Mapping to cad-harness

| OMA mechanism | Verdict | Why / how |
|---|---|---|
| Durable approval record (content hash, deterministic id, first-wins CAS decision, `requestHash` echoed by the reviewer) | **Copy the pattern (Python + SQLite)** | This fits the approval card (design §5.3) exactly. Persist `ApprovalRequest{scope, boundary, content, requestHash}` before showing the card. Use scopes Tandem actually needs: `merge`, `locked_interface`, `scope_expand`, `print_start`, `spend_over_threshold`, `tool_call`. Content should be the exact Onshape change set: the batched feature/variable patch with `sourceMicroversion`, the merge-preview id, and the slice file hash. The card POSTs `{requestId, requestHash, decision, reviewer}`, and the decision is a SQLite `UPDATE … WHERE decision IS NULL AND request_hash=?` (a true cross-process CAS, which OMA's FileStore lacks). Before execution, the adapter recomputes the hash from live state, for example the branch's current microversion. A mismatch is "stale" and forces a new card, which is OMA's `executor.ts:213-248` check carried over to CAD. |
| Gate placement (plan / dispatch / tool-call) | **Copy, but put the gate in the MCP server** | OMA's `onToolCall` gate never sees an ACP agent's tool calls. In Tandem, irreversible actions are `tandem` MCP tools (`onshape.merge.request`, `print.send`), so the gate and the durable approval must live in the MCP server and Onshape adapter, not in the agent host. ACP `request_permission` stays a secondary, advisory layer. `onTaskDispatch` maps to "start or spawn an agent" and to scope expansion. |
| Restore / run leases | **Copy lightly** | Tandem is a single desktop process, so leases matter less. Still, pending approvals must survive an app restart, and resuming one should re-validate it against the current microversion rather than replay a stale patch. |
| Run journal (event vocabulary, `sourceEventSeqs`, JSONL + fsync batches) | **Copy the vocabulary for the step log** | `tool/call` → `tool/result` → `approval/request` → `approval/decision` → `task/status` is the right spine for the console step log and its filters (decisions / tool calls / files / errors). Add Tandem events: `onshape/call` (one per ledger debit), `diff`, `plan`, `sim/run {solver, mesh, inputsHash, resultHash}`. Emit them from the ACP host for every `session/update` and from the MCP server for every tool execution. OMA emits nothing for ACP agents, so this is new work. |
| `verifyRun()` lineage check | **Skip the check as written; copy the idea** | It proves "the model-visible context is explained by recorded events". That is useful for OMA's own LLM loop, but ACP agents' internal context is opaque to Tandem. **It does not enforce "no fake confidence" on its own**: it has no chaining and does not check claims against tool results. The pattern that does carry over is OMA's governance and receipt rule that verdicts come from execution facts, never answer text (`execution-receipt.ts:25-28`). Concretely, every "passed sim / fits / within budget" badge on an approval card must cite an MCP-server-written `sim/run` or `measure` event id whose stored result hash matches. Agent prose saying "I ran FEA" renders as an unverified claim. Because the MCP server, not the agent, writes these events, the agent cannot forge them. Add a per-event `prevHash` chain (cheap) if the log should be tamper-evident for paid or engineering users. |
| Governance (required roles, order, independent review) | **Copy the pattern later (M6)** | This is a good model for "a SIM or reviewer agent must have run after the designer, with a dependency edge, before a merge card is enabled". Evaluate it from receipts, not text. Keep in mind OMA's own limit that topology is not quality. |
| Egress policy | **Skip** | It covers only OMA-owned LLM fetches and explicitly not ACP children. Tandem's requirement that agents reach the network only through tools (design §7.3) needs OS-level containment: a per-agent sandbox, network namespace or proxy. Keep the "intersect, never widen" scoping rule for per-agent caps. |
| ACP backend | **Do not adopt; use it as a minimal reference** | It shows correct handling of the cumulative `usage_update` delta, least-privilege option selection, lazy spawn with a retryable start, and cooperative cancel. It lacks everything Tandem's host needs: `mcpServers` in `session/new`, `fs/*` and `terminal/*` mediation to the sandbox folder, `plan`, `diff` and `thought` handling, `tool_call` kind, locations and status lanes, permission requests carrying `toolCallId` and `rawInput` that become durable cards, `session/load`/`resume`, `authenticate`, timeouts, stderr capture, and lifecycle supervision. Its default is auto-approve; Tandem's must be "ask". Build Tandem's host on the official ACP Python or TypeScript SDK directly. |
| Budgets | **Copy the semantics, not the code** | Useful rules: lower ceiling wins, no-throw trip with a status code, budget trips are not retryable, and `maxCostBudget` without an estimator is an error. OMA has no per-API-call ledger, and Tandem's Onshape ledger must be a hard pre-call check in the adapter, not a boundary check that can overshoot by one turn. ACP tokens are not gated when an agent sends no `usage_update`, so Tandem's console cost totals should label ACP token figures "agent-reported". |
| Run Viewer | **Skip** | It is a post-run static page and the console must be live. Its allowlist privacy boundary is a nice touch for exportable run reports later. |
| OTel package | **Skip for v1, optional later** | It is application-owned telemetry with no core dependency. Tandem is local-first, so a developer-only OTel export behind a flag would be fine. Never use telemetry as the step log, because OMA is right that telemetry is not execution state. |

**Bottom line.** OMA's durable-approval design (content-hashed request, reviewer echoes the hash, first-wins CAS, re-validation before execution, fail closed) is the best part to lift. It should back Tandem's approval queue almost one-to-one, with the gate moved into the `tandem` MCP server and Onshape adapter so that it covers every ACP agent. The journal vocabulary is a good start for the step log. Its verification proves context lineage, not honesty: "no fake confidence" has to come from server-written evidence events that cards cite, which is OMA's "facts, not answer text" receipt rule applied to sim and measure results.
