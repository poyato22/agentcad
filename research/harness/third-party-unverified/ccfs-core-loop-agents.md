# Claude Code from Source (chapters 1-10): core loop, tools and agents

**Source:** https://claude-code-from-source.com/ (chapters `/ch01-architecture/` to `/ch10-coordination/`), read 2026-10-24.
**Status of source:** an independent third-party analysis. It says it was reconstructed from Claude Code's npm source maps, which were published by accident, and then rewritten as prose and pseudocode. Treat every claim as **unverified** and probably from an **older Claude Code version**. All numbers below are the site's numbers.
**Clean-room note:** this file records architectural patterns and design reasoning in our own words. It contains no code or pseudocode from the site, and no passages longer than a short phrase. The design doc forbids using leaked source code, so implementers should work from this summary and from official docs, not from the site.
**Cross-check key:** [AGREES] means consistent with official sources (Agent SDK report `../official/agent-sdk.md`, plugin agents report `../official/agents.md`, public Claude Code/SDK docs). [UNVERIFIED] means no official source confirms or contradicts it. [PARTIAL] means the concept is official but the details are not.

---

## Ch 1. The architecture of an AI agent

**Mechanisms.** The site describes six core abstractions:
1. A single **query loop**, an async generator, that every entry point uses (REPL, SDK, headless, sub-agents).
2. **Self-describing tools.** Each tool carries its own schema, permission logic, concurrency flag and rendering.
3. **Tasks** for background work.
4. **Two-tier state.**
5. **Memory** in project, user and team tiers.
6. **Hooks.** The site counts 27 lifecycle events across 4 execution types.

Permissions resolve through a small set of named modes. One of them, "bubble", sends a sub-agent's permission questions up to its parent. A provider factory hides which cloud backend is in use, so the loop never branches on provider.

**Rationale.** A generator gives three things: backpressure, clean cancellation, and a typed "why I stopped" result. Recursion is cheap because a sub-agent is just another instance of the same loop.

**Transferable patterns:** generator loops with typed termination; tools that declare their own safety; state split by rate of change; a few named permission modes in place of ad-hoc checks; sub-agents as recursive instances of one loop.

**Cross-check:**
- [AGREES] The SDK's `permission_mode` values are default / acceptEdits / plan / bypassPermissions / dontAsk / auto.
- [AGREES] Hooks are a first-class, wide surface (PreToolUse, PostToolUse, Stop, SubagentStart/Stop, PreCompact, PermissionRequest, and more).
- [UNVERIFIED] The exact count of 27 events. "Bubble" is not an SDK-facing mode, but `can_use_tool` requests carry an `agent_id`, which implies sub-agent permission questions reach the host.

## Ch 2. The bootstrap pipeline

**Mechanisms.** Startup has a budget of about 300 ms and moves through five stages that narrow the work as they go:
1. A fast path exits early for `--version`, help and similar.
2. Slow I/O (keychain, device policy) starts as early as possible and is awaited only where it is used.
3. Config resolution and the **trust boundary**.
4. Commands, agents, hooks and plugins register concurrently.
5. A launch mode is chosen.

Non-urgent work (git status, cloud credentials) runs after the prompt is on screen. Before the user accepts trust, only low-risk settings are read. After trust, the risky ones are read: environment variables and git commands. Hook config is **frozen into a snapshot** at start, so editing it on disk mid-session cannot change permission decisions. All seven entry modes end in the same loop.

**Rationale.** The working directory itself may be hostile, for example a cloned repo with malicious config. Availability beats strict consistency for local config: a migration failure is logged and startup continues.

**Transferable patterns:** overlap I/O with init; narrow scope early; explicit pre-consent and post-consent trust boundaries; idempotent, memoized init; capture the user's first input before any async work.

**Cross-check:** [PARTIAL] Official Claude Code has a workspace-trust prompt, and the SDK reads settings only from the declared `setting_sources`. Snapshotting hooks at session start is [UNVERIFIED].

## Ch 3. State: the two-tier architecture

**Mechanisms.**
- **Tier 1:** a mutable process singleton of about 80 fields (session id, model override, cost totals, telemetry). Code reaches it only through getters and setters, and it is usable before and after the UI exists.
- **Tier 2:** a tiny reactive store (get / set-with-updater / subscribe / onChange) that drives the UI.
- **Sync point:** the tiers meet at one change hook. It diffs old and new UI state and pushes the relevant changes into the singleton. For example, a model switch updates the singleton, and the API client reads the singleton.
- **Sticky latches:** five feature flags that stay on once they are turned on, because changing request headers would change the server-side prompt-cache key.
- **Memoized context:** git status and project instructions are computed once per session, so the system prompt does not drift.
- **Latency metrics:** reservoir-sampled percentiles (p50/p95/p99) instead of averages.

**Transferable patterns:** choose state tiers by access pattern; once something contributes to a cache key, keep that contribution stable; centralize side effects by diffing transitions; a small store you understand fully beats a library; persist diagnostic counters at process exit.

**Cross-check:** [UNVERIFIED] for internals. [AGREES] in spirit: the SDK offers `exclude_dynamic_sections` and a system-prompt `snapshot`, which shows that prompt stability for caching is an official concern.

## Ch 4. The API layer

**Mechanisms.**
- **Provider client:** one factory, with SDKs loaded lazily. A per-request id lets client timeouts be correlated with server logs.
- **System prompt split at a boundary:** a static prefix that is globally cacheable, then a per-user or per-session suffix. Any conditional placed before the boundary multiplies the number of cache variants. Sections that break the cache must carry a loud name plus a written justification.
- **Cache tiers:** short ephemeral, 1-hour, and global cross-session. Global sharing is disabled when user-specific tools are present.
- **Streaming:** consumes raw server-sent events to avoid re-parsing partial tool JSON on every chunk, which would be quadratic. An **idle watchdog** warns at 45 s with no chunk and aborts at 90 s. If streaming fails, the request falls back to non-streaming, except when tools are already running mid-stream, where a retry could run them twice.
- **Output cap:** 8K tokens by default (production p99 is about 4.9K). A response that hits the cap gets one retry at a higher cap.
- **Retries:** cover overload, model fallback, a smaller thinking budget on context overflow, and one auth refresh. The retry wrapper **yields status events** so the UI can show what is happening.

**Transferable patterns:** treat caching as a design constraint; give costly escape hatches loud names; use activity-based watchdogs, not only initial timeouts; retries emit status events; keep lightweight API paths separate from the full pipeline.

**Cross-check:** [AGREES] Public prompt-caching docs confirm that the cache matches on an exact prefix, with 5-minute and 1-hour TTLs. [UNVERIFIED] The specific timeouts and caps.

## Ch 5. The agent loop

**Mechanisms.** One generator of about 1.7K lines. Each iteration does five things:
1. Compress context if needed.
2. Stream the model's reply.
3. Collect tool calls.
4. Execute the tools.
5. Append the results and repeat.

**Stop conditions** are a typed enum of 10 terminal reasons: completed, aborted while streaming, aborted during tools, max turns, unrecoverable model error, stop hook blocked, token limit, and others. There are also 7 typed **continue reasons**.

When the model asks for no tools, the loop runs the **stop hooks**. A stop hook can report a problem and send the model back to work.

Context management runs in layers, from cheapest to most expensive:
1. Trim oversized tool results.
2. Drop old messages.
3. Clear stale tool outputs.
4. Collapse spans of the conversation.
5. Full auto-compact, which summarizes the history through a forked call.

Auto-compact has a **circuit breaker** that stops after 3 consecutive failures; it was added after sessions looped on failed compactions. Recoverable errors such as context overflow are **withheld** from the consumer until recovery is exhausted.

Output-limit recovery goes from the 8K cap to 64K, then up to 3 multi-turn continuations. A **protocol safety net** writes placeholder results for any tool call left unanswered after a crash or abort, because the API requires a result for every call. Each iteration builds a new, complete state object instead of mutating the old one. Config is snapshotted at entry.

**Numbers:** auto-compact fires about 13K tokens below the window, and a hard block sits about 3K below it. Token budgets stop at 90%, or earlier when recent continuations add little.

**Transferable patterns:** start with a minimal loop and add features only when a real failure calls for them; generators for loops; explicit, immutable state transitions; withhold recoverable errors; compress context from light to heavy; **a circuit breaker on every retry path**, because a runaway session can burn a budget overnight.

**Cross-check:**
- [AGREES] The SDK exposes `max_turns` and `max_budget_usd`. Its `result` message carries a `subtype`, `num_turns`, `total_cost_usd` and `permission_denials`.
- [AGREES] `Stop` and `PreCompact` hooks exist.
- [UNVERIFIED] The exact enum counts and thresholds.

## Ch 6. Tools: from definition to execution

**Mechanisms.** The tool interface is large (about 45 members), but five members matter most: `call`, the input schema, `isConcurrencySafe(input)`, `checkPermissions(input)`, and `validateInput(input)`. A builder applies **fail-closed defaults**: a new tool is assumed to write, runs serially, and is not classifier-approved.

The pipeline, in order:
1. Look up the tool (aliases cover renamed tools).
2. Check for abort.
3. Validate against the schema.
4. Run semantic validation, for example rejecting a no-op edit.
5. Start the shell-safety classifier early, to save latency.
6. Add derived fields such as expanded paths. Hooks and permission checks see these fields; the transcript keeps the original input.
7. Run **PreToolUse hooks**. A hook can allow, deny, rewrite the input, or stop; its decision is final.
8. Apply allow/deny/ask **rules**, matched on the tool name plus a content pattern.
9. Run the tool-specific check.
10. Apply the mode default.
11. Prompt the user, or call the auto classifier.
12. Execute.
13. Budget the result.
14. Run PostToolUse hooks and classify errors for telemetry, logging no raw text.

Concurrency safety is a function of the **input**, not the tool. A read-only shell command is parallel-safe; `rm` is not. If the shell command cannot be parsed, the check fails safe and the hooks always run.

**Result budgets:** about 30K characters for shell output and about 100K for search and edits. File reads bound themselves instead, since spilling a file read to disk would invite read loops. An oversized result is written to disk and replaced by a preview plus a path, and an aggregate budget applies across the whole conversation. Search is paginated with a default of 250 results.

**Deferred tools** are sent to the model as names only. The model fetches the full schema through a search tool, which keeps the prompt small and the cache stable.

Tools are sorted by name within their groups, so adding one tool does not shift the cache breakpoint. An edit is rejected if the file changed after the model last read it, and edits are previewed exactly as they will be written.

**Transferable patterns:** safe defaults when a developer forgets a flag; safety that depends on the input; layered permission mechanisms, since no single one is enough; budget tool outputs, not just inputs; classify errors without logging raw text; preview exactly what will execute.

**Cross-check:**
- [AGREES] The SDK documents three gating layers: tools/disallowed_tools decide what exists, allowed_tools/permission_mode decide what is auto-approved, and `can_use_tool` plus PreToolUse hooks handle dynamic decisions.
- [AGREES] `can_use_tool` fires only when the rules say "ask", and it can return `updatedInput`.
- [AGREES] Hooks can block or rewrite. Deferred tool loading through ToolSearch is visible in current Claude Code.
- [PARTIAL] MCP tools can declare `readOnlyHint`. Whether Claude Code uses that hint for concurrency is [UNVERIFIED].

## Ch 7. Concurrent tool execution

**Mechanisms.** The tool calls the model requested are split, in order, into batches. A run of consecutive concurrency-safe calls forms one parallel batch, and any unsafe call starts a serial batch. Reads, search, glob and fetch are always safe; edit and write never are. A shell command is safe only if every subcommand is read-only, and a parse failure means serial. Parallel batches are capped at **10** by default. Context changes from a parallel batch are applied afterwards, in request order.

**Streaming execution:** a tool starts as soon as its block has finished parsing, while the model is still writing the rest of the reply. An unsafe tool needs the executor to itself. The site cites about 16% less wall-clock time.

**Error cascade:** only a shell error cancels sibling calls, because shell steps usually depend on each other. Read errors stay isolated. Aborts form a 3-level hierarchy (query, sibling group, single tool). Results are **released in request order**, even though they finish in any order; progress updates show immediately. Each tool declares whether it can be interrupted, and the UI offers "interrupt" only when every running tool can be.

**Transferable patterns:** partition by the actual inputs, not by operation type; start independent work while slow output is still being produced; keep submission order even if that means buffering.

**Cross-check:** [AGREES] Parallel tool calls are part of the public API. The SDK's `PostToolBatch` hook event implies batching. [UNVERIFIED] The cap of 10, the shell-only cascade, and the streaming start.

## Ch 8. Spawning sub-agents

**Mechanisms.** The model calls an `Agent` tool with a description, a prompt, an optional agent type, a model and a background flag. Fuller schemas add a name, team, permission mode, isolation (worktree or remote) and cwd. **Fields the model cannot use are removed from the schema**, not warned about in the prompt. The tool's own prompt stays static, and volatile data such as the list of available agents goes into an attachment to protect the cache.

A routing step decides several things before anything starts:
- teammate, sub-agent or fork;
- which definition to use;
- isolation;
- foreground or background.

A recursion guard stops a fork from forking again.

A single parameterized lifecycle of about 15 steps runs every agent type:
- **Model** is chosen by precedence: caller override, then the definition, then the parent, then the default.
- **Context:** a fork clones the history and strips unfinished tool calls. Read-only agents do not get project instructions or git status, which saves tokens.
- **Permissions** are layered so a child can never be more permissive than its parent allows.
- **Tools** are filtered through allow and deny lists.
- **Background agents** get their own abort controller, so Esc does not kill them.
- Scoped hooks, scoped MCP servers and preloaded skills are set up.
- Each agent writes its own resumable transcript.
- A guaranteed `finally` step tears down MCP connections, hooks, caches, todos and orphaned shell processes.

The **parent sees only the child's final output.** Agent definitions come from four sources (built-in, user, plugin, policy). The source matters because policy can block hooks and MCP in agents that users wrote. Built-in agents include a general worker, a fast read-only search agent on a small model, a planner, a background adversarial verifier, a docs guide and a statusline helper.

**Transferable patterns:** remove unusable fields from the schema instead of describing them; keep tool definitions static for caching; use one precedence chain for settings; size each agent's model and context to its job, because cost compounds; share state on purpose; guarantee cleanup; one parameterized lifecycle, not one per agent type.

**Cross-check:**
- [AGREES] The SDK's `AgentDefinition` has description, prompt, tools, disallowedTools, model (including `inherit`), skills, mcpServers, maxTurns, background, effort and permissionMode.
- [AGREES] Sub-agent messages carry `parent_tool_use_id`. The `Agent` tool was formerly called "Task".
- [AGREES] `../official/agents.md` shows official plugins scoping tools tightly (read-only explorers, narrow writers) and using `inherit` for high-judgment roles.
- [UNVERIFIED] The child-cannot-be-more-permissive rule and the 15-step lifecycle.

## Ch 9. Fork agents and the prompt cache

**Mechanisms.** A fork child inherits the parent's **already-rendered** system prompt (passed by value, not regenerated), the exact tool array (unfiltered and in the same order), the history and the model. For each pending tool call in the parent's last turn, the child gets an identical placeholder result. Only the final directive text differs between children, so the shared prefix is byte-identical and cached.

A boilerplate wrapper tells the child it is a worker: stay in scope, stay quiet, return a fixed report format. Two guards stop recursive forking: a tag set at spawn, and a fallback scan of the history for the wrapper marker, which still works if compaction dropped the tag. A fork can move from foreground to background without losing work, because the transcript is its state. Forking is disabled in coordinator mode, in non-interactive runs, and whenever an explicit agent type is requested.

**Cost:** with a 48.5K-token prefix and 5 children, the first child pays full price and the others pay about 10% for the shared part. With a 100K-token warm session and 8 forks, the site claims savings above 90% of uncached input.

**Trade-offs, acknowledged:** children carry irrelevant history, the placeholder results are fictional, and tool restrictions are enforced at runtime instead of by removing the tools.

**Transferable patterns:** capture rendered prompts and pass them by value; keep the tool array stable and enforce limits with runtime guards; shared content first, per-worker content last; identical placeholders where the API requires a response; do the arithmetic before adopting any of this.

**Cross-check:** [PARTIAL] The SDK has `fork_session` and `resume_session_at`, which fork at the session level; that is related but not the same as a fork sub-agent. Prefix caching and its pricing are public. The fork sub-agent mechanism itself is [UNVERIFIED] and may have changed.

## Ch 10. Tasks, coordination and swarms

**Mechanisms.**
- **Unified task model.** Every background unit (shell, sub-agent, teammate, workflow, MCP monitor, remote session, speculative task) is one record type. Ids are a type prefix plus 8 random characters. Statuses are pending, running, completed, failed and killed. Each record has an output file, an incremental read cursor, and a "notified" flag so completion is reported once. The shared interface was cut down to just `kill`.
- **Channels.** Foreground agents stream upward. Background agents use output on disk, completion notices **injected into the parent's context**, and a message queue that is drained only at **tool-round boundaries**, so an agent is never interrupted mid-thought.
- **Coordinator mode.** The coordinator has only spawn, message and stop, and it cannot touch files. Workers get nearly every tool. Its prompt runs four phases: research, synthesis, implementation, verification. The core rule is **do not delegate comprehension**. The coordinator decides whether to continue a worker or spawn a fresh one based on how much context overlaps. A **shared scratchpad** lets workers hand each other file references instead of routing content through the coordinator.
- **Swarms.** Named, colored teammates whose membership persists on disk. A name registry lets messages go to roles instead of ids. File-based **mailboxes** were chosen for durability, inspectability and zero dependencies. Broadcast goes to everyone except the sender. Structured messages handle shutdown requests and plan approval. **Dead recipients are resumed automatically from their transcripts.**
- **Worker permissions.** A restricted worker tries the classifier first. If that does not settle it, the request goes to the leader's mailbox and the worker waits.
- **Limits.** UI history is capped at 50 messages, after about 20 MB per long-running agent was measured. One session reached 292 agents in 2 minutes and 36.8 GB of RSS. Killed tasks linger for a grace period before garbage collection.
- **Warning.** Todo tools and task tools have overlapping names, which confuses the model.

**Transferable patterns:** many patterns on one shared state machine; delete abstractions that no longer earn their keep; delegate precise actions, not understanding; simple durable channels when message volume is low; make dead recipients recoverable; cap memory in UI snapshots; start with the simplest pattern that works.

**Cross-check:**
- [AGREES] The SDK emits `system/task_*` messages (TaskStarted/Progress/Notification/Updated) and has `stop_task(task_id)`.
- [AGREES] `../official/agents.md` lists official orchestration patterns: the parent synthesizes while narrow agents report, deterministic scripts gate progress, and the user approves between phases. That matches "don't delegate comprehension".
- [UNVERIFIED] Mailboxes, coordinator mode and swarm internals; these are probably experimental or gated.

---

## Mapping to cad-harness

**Key point.** The harness hosts *heterogeneous* agents over ACP: Claude, Codex, Gemini and others. Most loop-level patterns (ch 4, 5, 7, 9) live **inside each agent**, and the harness cannot rely on them. Any guarantee the harness *needs* must therefore be enforced at the layers it owns:
- the **`tandem` MCP server** (design doc §7.4; the brief calls it `cad-harness`);
- the **budget ledger and Onshape adapter** (§7.1);
- the **agent host** (§7.3).

| # | Pattern (chapter) | Where it lives in cad-harness | Why |
|---|---|---|---|
| 1 | **Fail-closed, input-dependent tool safety** (6, 7) | Every `tandem` MCP tool declares `readOnly` / `mutating` / `irreversible`, and the default is mutating. `onshape.propose`, `merge.request` and `print.send` are classified from their inputs, for example by how many features a change touches. Set the MCP `readOnlyHint` / `destructiveHint` annotations to match. | When a builder forgets a flag, the tool must still be safe. The hints let Claude and other clients run `mirror.measure` and `sim.result.get` in parallel while serializing writes. |
| 2 | **Layered permission pipeline, decided at the server** (6) | A server-side gate in the MCP server, in this order: schema validation, then semantic validation (no-op change, out-of-scope part, stale microversion), then **scope check** (edit scope = one part, assembly read-only), then **budget check**, then approval card for irreversible tools. | ACP agents differ in their own permission UX. The server is the one choke point every agent passes through. It also covers external agents such as Claude Code or Cursor that use the same server. |
| 3 | **Budget as a circuit breaker on every retry path** (5) | Budget ledger (§7.1). Each agent has a hard cap. N consecutive failed `propose` or `verify` calls trip a breaker that pauses the agent and raises a console card. Return a structured `budget_exhausted` error, not a crash. | The site's warning that a runaway session can burn a budget overnight maps directly onto Onshape call allowances, which are hard and annual. |
| 4 | **Structured, budgeted tool results** (6) | The MCP result contract `{ok, version, changes:{mass, volume, bbox}, warnings}` already exists. Add a per-tool size cap. Large outputs (meshes, FEA fields, glTF) go to the project store and come back as a handle plus a short summary. | This keeps agent context small and makes results checkable "by numbers". It applies to any ACP agent. |
| 5 | **Optimistic concurrency: reject a write against stale state** (6) | This is already the adapter's `sourceMicroversion` + `rejectMicroversionSkew`. Mirror it locally: `mirror.part.set_vars` rejects a change if the part script changed since the agent's last `measure`. | Claude Code's rule ("reject an edit if the file changed after the model read it") is the same idea. Apply it to both tiers. |
| 6 | **Explicit trust boundary and frozen policy snapshot** (2) | Agent host. Snapshot each agent's scope, budget and approval policy when its session starts, and make them immutable for the run; changing them requires restarting the session. Treat Onshape document names and comments as untrusted data (§7.1 already notes this). | This stops a session from widening its own scope mid-run, and it makes audit straightforward. |
| 7 | **One unified task state machine** (10) | Project store (SQLite): one `tasks` table covering agent sessions, sim runs, slices, prints and cloud studies. Statuses are pending, running, completed, failed and killed. Each task has an output pointer, a read cursor and a `notified` flag. The table backs both the console roster and MCP Tasks for long jobs. | One model drives the console lanes, the budget meter and resume. The `notified` flag avoids duplicate approval cards. |
| 8 | **Deliver messages at turn boundaries; inject completions as context** (10) | Agent host. Human feedback and sim-complete events are queued and delivered at the next ACP prompt turn, or the agent pulls them from `sim.result.get` / `job.status`. Never cancel an agent mid-`propose`. | This avoids half-applied batched updates and matches how ACP turns work. |
| 9 | **Don't delegate comprehension; the orchestrator synthesizes** (10, plus official plugins) | Agent SDK orchestrator (§7.3). SIM and MAT sub-agents return structured reports (numbers plus file handles). The orchestrator writes the proposal and the human approves it. Deterministic code, not a model, decides pass/fail gates (safety-factor thresholds, fidelity drift). | This matches official plugin practice in `../official/agents.md` and keeps the decision about whether a change is safe outside any model. |
| 10 | **Size sub-agents to the job: narrow tools, smaller models, trimmed context** (8) | `AgentDefinition`s for the orchestrator: a read-only explorer (feature tree, variables) on a small model with no `onshape.propose`; a SIM agent with only `sim.*` and `mirror.measure`; an editor with `mirror.*` plus `onshape.propose` for one part. Set `maxTurns` on each. | Cost compounds when many agents run at once (M6). Removing a tool beats telling the agent not to use it. |
| 11 | **Static prefix, volatile data last (cache discipline)** (4, 8, 9) | Orchestrator system prompts and the `tandem` tool list: keep tool descriptions static and sorted. Put per-part context (feature tree, variables, budget left) in the user turn or a resource, not the system prompt. Consider the SDK's `exclude_dynamic_sections`. | Many agents on the same assembly share one long prefix, which is a large cache win for the API-key and hosted-credit tiers. |
| 12 | **Guaranteed cleanup and resumable transcripts** (8, 10) | Agent host and process supervisor. Each agent session runs in a lifecycle with a guaranteed teardown: kill its sim processes, release mirror locks, and **delete the Onshape branch if the agent dies unmerged** (after asking the user). Persist transcripts so a dead agent can be resumed, not restarted. | Orphaned Onshape branches and CalculiX processes are the CAD equivalent of orphaned shells. Resuming avoids paying to rebuild context. |

**Also worth noting (lower priority):**
- **Activity-based watchdogs** (ch 4) for long sim and slicer jobs: kill a job on *no progress*, not only on wall-clock time.
- Release results in request order (ch 7) in the console lanes.
- Cap UI history per agent (ch 10), since M6 runs many agents at once.

### What the harness gets for free by hosting Claude via the Agent SDK or ACP

When the agent is Claude through the Agent SDK, or Claude Code through its ACP adapter, the following come built in. The harness should not rebuild them:
- **The loop:** generator loop, typed stop reasons (`result.subtype`), `max_turns`, `max_budget_usd`, `task_budget`, and stop hooks.
- **API resilience:** retries, model fallback, streaming watchdogs, output-limit recovery.
- **Context compression:** auto-compact and its circuit breakers.
- **Prompt caching:** the cache breakpoints themselves. The harness still controls what goes in its own prompts (pattern 11).
- **Tool execution:** parallel read-only tool calls and streaming tool execution.
- **Permission pipeline:** rules, modes, PreToolUse/PostToolUse hooks, and `can_use_tool` with input rewriting. Use these for the budget/scope hooks named in §7.3.
- **Sub-agents:** spawning through `AgentDefinition` (tools, model, maxTurns, permissionMode, background), with `parent_tool_use_id` linking and `task_*` lifecycle messages plus `stop_task`.
- **Sessions:** resume and `fork_session`.

**Not free under ACP with non-Claude agents** (Codex, Gemini, and others): none of the above can be assumed. This is why patterns 1-5 and 7-8 belong in the MCP server and agent host, which are agent-agnostic, and not in Claude-specific hooks. A good rule: a Claude hook may **add** a check, but the MCP server must **enforce** every invariant (scope, budget, staleness, approval for irreversible tools) on its own.
