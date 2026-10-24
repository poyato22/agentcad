# Claude Code From Source, ch. 11-18: patterns for the CAD harness

Source: https://claude-code-from-source.com/ (chapters `/ch11-memory/` through `/ch18-epilogue/`), read 2026-10-24.

**Provenance and limits.** This is a third-party book. It says it reconstructs Claude Code from npm source maps that were published by accident. Our design doc forbids using leaked source (anti-goal: "Using leaked Claude Code source"). So this file keeps only **architectural ideas and rationale, in my own words**. It copies no pseudocode, identifiers or passages, apart from a few short attributed phrases. Treat every claim as **unverified and possibly out of date**. Where an official source covers the same point (Claude Code docs, the Agent SDK, or our own notes in `scratchpad/research/02-plugins-hooks-skills.md` and `../official/agent-sdk.md`), I mark it: **[agrees]**, **[differs]** or **[unchecked]**. Some topics I asked about are not in these chapters: MCP output-size limits, tool search, compaction thresholds, and multi-agent UI. Those are marked *absent*. Nothing has been filled in from guesswork.

Naming note: the user's brief calls the server `cad-harness`, but design doc §6/§7.4 calls it `tandem`. Below I use "the harness MCP server" for it.

---

## Ch. 11 Memory: learning across conversations

**Mechanisms**
- Memory is **plain Markdown files with a small frontmatter header** (name, one-line description, type). They live in a per-project directory keyed on the canonical repo root, so all worktrees of one repo share memory. An optional team subfolder holds shared memories.
- There are four memory kinds: about the user, feedback/corrections (each with a rule, *why*, and *how to apply*), project state (relative dates turned into absolute ones), and pointers to external references.
- **Admission filter:** don't save anything that can be re-derived from current project state (code, git history, the instruction file). The book says this filter holds even when the user explicitly asks for something derivable to be saved.
- **Two-tier retrieval.** A short index file is always in context, with hard caps on both lines and bytes; the byte cap exists because a few very long lines can blow the token budget. Each turn, a **small, cheap side-model call** reads only the frontmatter descriptions and picks a handful (≤5) of relevant files. Their bodies load only then. The pick runs asynchronously, so it is usually done before the main model needs it, and returned filenames are checked against the known set.
- **Staleness is a warning, not expiry.** Memories older than a day or so get a human-readable age ("N days ago") and a reminder to check them against current state. Nothing is deleted automatically.
- **Background extractor.** After a turn, a forked helper that shares the main agent's prompt cache scans recent messages. It saves anything the main agent missed, and skips turns where the main agent already wrote memories. It can read anything but can write only to the memory directory.
- Long-lived mode: append-only timestamped daily logs, plus a periodic **consolidation** pass (orient, gather, merge, prune). A lock file serves both as the mutex and, through its mtime, as the "last run" timestamp. Triggers are checked cheapest-first.
- Security: the memory-path override is honored only from trusted (user-level) config, never from repo-committed config. Team-memory paths are validated in layers, and any violation fails closed.

**Rationale:** Files are inspectable, diffable and editable by the user. An LLM selector copes with negation and context without needing an index. The admission filter stops memory from going stale against the code.

**Book's transferable patterns:** files over vector DB; filter saves by derivability; LLM recall over embeddings; descriptions as search keys; small index plus lazy bodies; warn instead of expire; a background safety-net extractor; append-only capture with separate consolidation; reuse existing file tools instead of a memory API; keep overrides out of repo config; fail closed; cheap checks first; tune prompts with evals.

**Cross-check:** The official SDK exposes memory scopes `user|project|local` on agent definitions (03-agent-sdk.md:78), and the official Claude Code docs describe CLAUDE.md hierarchy plus auto-memory **[agrees in broad shape]**. The side-model selector, extractor and consolidation details are **[unchecked]**.

## Ch. 12 Extensibility: skills and hooks

**Skills.** Skills are Markdown files with frontmatter, loaded from several sources and merged by precedence. Duplicates are found by real path, not inode. Disclosure is progressive: frontmatter is parsed at startup and only the descriptions go into the prompt; the body is expanded (arguments, skill dir, inline shell) only when the skill is invoked. A `paths`-style condition can keep a skill hidden until the agent touches matching files. Skills that come from MCP servers are **content-only**: they never run inline shell. Skills can carry their own hooks, which are registered for the session when the skill is invoked, and can be one-shot.
**[agrees]** with the official 3-level progressive disclosure, `allowed-tools`, `disable-model-invocation` and `user-invocable` (02-plugins-hooks-skills.md:40-68).

**Hooks, lifecycle.** There are two dozen-plus events. The ones that matter most:
- before a tool runs, which can **block, rewrite the input, auto-approve, force a prompt, or add context**;
- after a tool succeeds, which can add context or **replace an MCP tool's output**;
- on stop, where a block forces the agent to keep going, which turns a one-shot reply into a goal loop;
- session start, which can set up env and context but **cannot block**;
- prompt submit, which can block.

There are also failure, permission-denied, subagent start/stop, compaction, notification, file/config-change and session-end events (session-end has a very short timeout).

**Handler types:** a shell command (JSON on stdin, then exit code plus stdout), a single LLM verdict, a multi-turn agent verifier (turn-capped), and an HTTP POST to a policy or audit server.

**Contract:** exit 0 means success and stdout is parsed as JSON. **Exit 2 means block**, and stderr is fed to the model. Any other code is a warning shown to the user only. Exit 2 was chosen because exit 1 is what any crash produces, and a crash shouldn't count as enforcement. When several hooks disagree, **deny beats ask, and ask beats allow**.

**Trust:** the hook config is **snapshotted at the moment the user grants workspace trust**. It is re-read only through an explicit command or a watched rebuild, so a repo can't inject hooks after consent. A single trust gate sits at the top of hook execution; the book says it was added after bugs where hooks ran before or despite declined trust. Managed policy can disable all non-managed hooks.

**Cross-check:**
- Exit-code semantics, `permissionDecision allow|deny|ask`, `updatedInput`, `additionalContext`, matchers and `if` filters: **[agrees]** (02-plugins-hooks-skills.md:98-114; 03-agent-sdk.md:107-110). The official SDK also adds `defer` and an SDK-side `can_use_tool` callback, which fires only when rules say "ask" **[book omits]**.
- Official default timeouts are 60s for command hooks and 30s for prompt hooks; the book gives no general timeout **[gap]**.
- Shipped plugins favour "fail open, exit 0 on internal error". That fits the book's "exit 2 must be deliberate" **[agrees]**.

**Book's transferable lessons:** separate content (skills) from control (hooks); snapshot config at consent; use a distinctive signal for enforcement; block recoverable actions but never irrevocable facts; most-restrictive decision wins; untrusted extensions stay content-only; load metadata eagerly and content lazily.

## Ch. 13 Terminal UI

**Mechanisms:**
- React components on a heavily rewritten Ink-style renderer: a custom node tree with dirty flags that propagate up to the root, plus flexbox layout.
- A staged frame pipeline: layout, paint into a cell buffer, overlay, **cell-level diff against the previous frame**, write merging, then one atomic write wrapped in synchronized-update markers. Each stage is timed.
- Cells are packed into typed arrays, with strings and styles interned into integer pools. The pools are reset periodically to bound growth.
- Two frame buffers are reused rather than reallocated.
- **Blitting:** unchanged subtrees copy their cells from the last frame. A damage rectangle limits the diff to the area that changed.
- Off-screen messages are **frozen**, so a spinner scrolled out of view doesn't cause repaints.
- Scroll bypasses React and mutates layout directly.
- Frames are throttled to about 60fps with leading and trailing edges, and to 30fps when the window is unfocused (ch17).

**Streaming:**
- Parsed Markdown goes into an LRU cache keyed by content hash.
- A fast path skips the parser for plain text.
- A **lenient streaming parser** tolerates unclosed fences mid-stream, then a strict render runs once the message completes.
- Syntax highlighting loads lazily: plain text shows first and is coloured later.

The book says steady-state cost is dominated by re-measuring and re-parsing, not by the paint pipeline. There is no concurrent-agent UI material here **[absent]**.

**Transferable lessons:** intern repeated values; diff at the right granularity; keep high-frequency paths out of the reactive framework; clean up periodically; track damage bounds; remove work rather than speed it up.

## Ch. 14 Input and Interaction

**Mechanisms:**
- A tokenizer for raw terminal bytes. It handles the ambiguity of a lone Escape (waits about 50ms) and detects pastes.
- All keys from one read are batched into a single UI update, so a 100-character paste causes one render.
- Pasted text is flagged so **keybindings never fire on pasted content**.
- Keybindings are **declarative data, separate from handlers**, and activated by **contexts** (global, chat, confirmation, task, …). Context priority gives modal precedence for free.
- User overrides live in JSON; binding a key to null unbinds it. A reserved-key tier is checked at definition time, not when a key is pressed.
- Chords are an explicit state machine with a timeout. Vim mode is a typed union of states with pure transitions that return side effects.
- Virtualized message list with cached row heights.
- There is a "kill all subagents" action.

Approval-dialog flow, prompt queueing, the command palette and the background-task display are **[absent]**.

**Transferable patterns:** bindings as data; context as a first-class idea; chords as state machines; validate at definition time; null-unbind as an escape hatch; type-checked state machines; push parsing complexity to the boundary.

## Ch. 15 MCP: the universal tool protocol

**Client design:**
- Many transports. stdio is the default when `type` is omitted. Streamable HTTP is preferred for remote servers. SSE is legacy. There is also an in-process transport for servers the client controls itself (a tiny linked pair that delivers messages on a microtask, and where closing one end closes the other).
- Config comes from several scopes. Servers are **deduplicated by content signature** (command or URL), not by name.
- MCP tools are wrapped into the **same internal tool interface** as built-in tools, so the model sees no difference.
- **Names** are normalized to a safe character set and namespaced as server plus tool, within a ~64-character limit.
- **Descriptions are truncated at about 2KB.** The book cites generated servers shipping 15-60KB descriptions, roughly 15k tokens on every turn.
- Schemas pass straight through and are only validated at call time.
- **Annotations:** read-only tools may run concurrently, and destructive ones get extra permission checks. Annotations come from the server, so they are treated as hints, not trusted facts.

**Connection management:**
- Explicit states: connected, failed, needs-auth, pending, disabled.
- needs-auth results are cached for a while, so dozens of servers don't all rediscover the same expired token.
- Local servers connect in small batches, to avoid running out of file descriptors; remote servers connect in larger batches.
- When a session expires (HTTP 404 plus a specific JSON-RPC code), the client clears its cache and retries once.
- Timeouts are **per request, not per connection**. This fixed a bug where one idle-created signal killed later calls. Tool calls get a very long ceiling, since long jobs are legitimate.

**Auth:**
- OAuth with PKCE and the standard discovery RFCs, plus a metadata URL override.
- An optional federated token exchange, so one IdP login unlocks several servers.
- Normalization of spec-breaking providers, for example a 200 response carrying an error in the body.
- **Lazy auth:** only attempt OAuth after a 401.

**Security:** sanitize tool output for misleading Unicode (bidi overrides, zero-width characters) before it reaches the model.

**Absent from the chapter:** output-size limits, persisting large results, images, resources, prompts, elicitation, tool search/deferred loading, server instructions.
- ch17 does give general tool-result caps (below).
- From memory of the official Claude Code docs, which I did not re-fetch: MCP output has a warning threshold and a configurable cap (`MAX_MCP_OUTPUT_TOKENS`), and **tool search defers MCP tool schemas** when there are many. The `mcp__<server>__<tool>` naming is **[agrees]** (03-agent-sdk.md:123; 02:136). readOnlyHint is an official SDK annotation **[agrees]** (03:117). The other details are **[unchecked]**.

**Book's transferable lessons:** start with stdio; namespace names and cap descriptions; defer auth; use in-process transport for your own servers; trust annotations only partly; sanitize output; give each request its own timeout. The chapter gives no explicit advice to server authors. The implied advice: keep descriptions short, make annotations honest, and return standard 401 and session-expiry signals.

## Ch. 16 Remote Control and Cloud Execution

**Model:** the agent loop stays the same and "remote" is just a transport layer.

**Bridge v1.** A local machine registers with a cloud environments service, long-polls for work, and runs one child process per session, talking newline-delimited JSON over stdio. Permission prompts are relayed to the web user, who answers them remotely.

**Bridge v2.** No registration or polling. The client creates a session, then "connects", which returns a short-lived worker token and an **epoch** number that increments on each connect. After that it opens an asymmetric transport:
- **reads over a persistent stream** (SSE or WebSocket);
- **writes over HTTP POST with idempotency keys**.

On an auth drop, the stream reconnects with fresh credentials and keeps its sequence cursor. An epoch conflict (409) shuts down the stale instance, which prevents split-brain. A **flush gate** queues live writes while history uploads, so ordering is preserved.

**Routing and dedup:**
- One inbound router handles everything.
- Two **fixed-size ring-buffer dedup sets**: one for echoes of the client's own writes, one for redeliveries. No TTLs are needed.
- Unknown control requests get an explicit error reply, so the other side never hangs waiting.
- The message type guard is deliberately loose, so new message types pass through rather than being silently dropped.

**Retry policy by failure type:**
- unauthorized: stop;
- "session not found": a few linear retries, because it can be transient;
- other errors: exponential backoff with a cap.

**Local direct-connect mode:** a local WebSocket server with no cloud and no OAuth. Session states are starting, running, detached, stopping, stopped. Metadata is persisted so sessions can resume after a restart.

**Cloud containers:**
- An upstream proxy injects org credentials into outbound HTTPS.
- The token is read from a file, the process is made non-dumpable, and the file is deleted, so the **secret lives in memory only**.
- The proxy **fails open**: it is an auxiliary feature, and if it fails, inference still works.

**Transferable patterns:** split read and write channels; bound dedup memory; match retries to the failure class; keep secrets in memory only in hostile environments; fail open only for optional features; keep the core loop transport-agnostic.

**Cross-check:** The SDK's stdio control protocol (`control_request`/`control_response`, `can_use_tool` relayed to the host, `interrupt`, `stop_task`, `mcp_reconnect`; 03-agent-sdk.md:152-155) matches the "permission prompts relay over the transport" idea **[agrees in shape]**. The cloud-side details are **[unchecked]**.

## Ch. 17 Performance: every millisecond and token

**Startup:**
- Slow I/O (keychain, MDM probes) is fired off before the heavy imports, so the waits overlap.
- A HEAD request pre-warms TLS to the API.
- Fast paths for subcommands; heavy modules and schemas are loaded only when first needed.
- About 50 startup checkpoints are instrumented.

**Tokens:**
- **Output reservation sized to real p99.** The book says a small default reservation, escalated only when output gets truncated (rare), recovers a large share of usable context compared with a generous fixed reservation.
- **Tool-result budgets:** a per-tool character cap, with oversized results **persisted to disk and replaced by a reference**, plus a per-message aggregate cap so many parallel tools can't flood a single turn.
- Token counts come from the API's `usage` field, not client-side estimates.
- A multi-layer compaction scheme summarizes older content progressively. Thresholds are **[absent]**.

**Prompt cache:**
- Caching is exact-prefix, so the prompt is **ordered stable to volatile**.
- A "global" shareable prefix is used across users, and disabled when per-user MCP schemas are present.
- **Sticky latches:** once a cache-affecting flag or header turns on, it stays on for the session, because flipping it would discard tens of thousands of cached tokens.
- **The session date is frozen at start**, so midnight doesn't bust the cache.
- Prompt sections are memoized. Any intentionally uncached section has to be named and justified.

**Execution:**
- **Streaming tool execution:** tools start while the model is still streaming.
- Consecutive read-only calls are batched to run concurrently; writes run serially; results come back in their original order.
- When one sibling errors, the others are aborted.
- Tool-input JSON is accumulated raw rather than re-parsed on every delta, which would be quadratic.
- An idle watchdog falls back to a non-streaming call.

**Search:** a per-path letter bitmap rejects most candidates before fuzzy scoring. Indexing is incremental and yields to the UI.

**Cross-check:** Stable-first prompt ordering and the SDK's `exclude_dynamic_sections`, which exists so the prompt can be cached across users (03-agent-sdk.md:28), **[agree]**. Official prompt-caching docs (prefix match, breakpoints, 5-minute and 1-hour TTL) **[agree in principle]**. The specific numbers are **[unchecked]**.

**Book's transferable lessons:** audit the context budget; design for cache stability; overlap I/O with CPU work; use cheap pre-filters; measure first (profiler, the usage field, cache-hit rate).

## Ch. 18 Epilogue

**The five bets:**
- one explicit agent loop with typed exit reasons;
- files over databases for memory;
- self-describing tools, where MCP and built-in tools share one pipeline;
- forked helpers that share the parent's cache, so background work is cheap;
- out-of-process hooks over in-process plugins, for isolation.

**Forward-looking points:**
- standard protocols such as MCP will win;
- multi-agent coordination has real token and reconciliation costs;
- file memory works at hundreds of entries but may strain at thousands;
- autonomy grows only as trust is earned through transparency and visible controls.

**Closing principle:** keep complexity at the boundaries (input parsing, external protocols, untrusted config) and keep the interior typed and clean.

---

## Mapping to cad-harness

Patterns most worth adopting. All are re-expressed from the design ideas, not from any code.

1. **Expose MCP tools with names, descriptions and honest annotations that stay small.** *Where:* the harness MCP server (§7.4).
   - Names must survive client prefixing (`mcp__<server>__<tool>`) within about 64 characters. Clients may also reject dots, so prefer `onshape_propose` over `onshape.propose`, or check what every ACP client accepts.
   - Keep each description well under 2KB and front-load *when* to use the tool. Clients truncate, and every byte is charged on every turn for every agent.
   - Mark `mirror.*`, `sim.result.get`, `features.list` and `budget.get` as `readOnlyHint` so clients can run them in parallel. Mark `merge.request`, `print.send` and paid `studies.*` as `destructiveHint`.
   - *Why:* many agents times many turns makes tool-schema tokens a recurring cost, and annotations drive client concurrency and prompting.

2. **Never return big payloads in a tool result. Return a handle.** *Where:* every tool that can produce a large result: `features.list`, `render_views`, `sim.result.get`, `section`.
   - Cap results; put meshes, renders, full feature JSON and FEA fields in the project store under a microversion- or job-keyed ID, and return a summary plus the ID with paged or filtered getters.
   - This builds on the existing `{ok, version, changes:{mass, volume, bbox}, warnings}` rule.
   - *Why:* ch17's persist-and-reference budget; clients also cap MCP output.

3. **Make a mandatory budget gate that fails closed, with the most restrictive decision winning.** *Where:* the Budget ledger, sitting in front of the Onshape adapter, plus Agent SDK PreToolUse hooks for hosted Claude agents.
   - Every Onshape-touching tool asks the ledger first. Deny beats ask, and ask beats allow, across per-agent, per-day and the user's allowance.
   - A denial returns a structured, recoverable error ("budget: 3 calls left today; use mirror.*"), not a crash, so the agent re-plans.
   - *Why:* ch12's precedence rules plus our #1 anti-goal. Enforcement belongs in the server, so external agents can't bypass it; client-side hooks are only a second layer.

4. **Only recoverable actions can be gated; treat everything else as a fact.** *Where:* the approval queue.
   - Merge, locked-mate edits, scope expansion, print and paid spend are gated *before* they happen. The UI never offers "undo" for things that can't be undone (a print sent, API calls spent).
   - Map ACP `request_permission` and SDK `can_use_tool` to the same approval card. The SDK's `updatedInput` lets the human edit a proposal, for example adjusting a variable value, before approving it.

5. **Snapshot agent policy and scope at spawn.** *Where:* the Agent host.
   - Freeze each agent's edit/read scope, budget caps and allowed tools when it starts. Changing them mid-run is an explicit, logged event (a scope-expansion approval), never a silent re-read.
   - Treat Onshape document names and comments as data, which is already in §7.1, and strip bidi and zero-width Unicode from any text the server returns.
   - *Why:* ch12's time-of-check/time-of-use defence and ch15's output sanitizing.

6. **Long jobs should return a job ID and status, retry only one way, and use per-request timeouts.** *Where:* `sim.run`, `studies.*` and `print.status` through MCP Tasks.
   - Set timeouts per call, not per session.
   - Classify errors as permanent (auth, out of budget: stop), transient (backoff, capped) or ambiguous (a single retry). For Onshape writes, a microversion-skew rejection is a re-plan, not a retry.
   - *Why:* ch15 and ch16 retry-by-failure-class; it also protects the call budget from retry storms.

7. **Use one transport-agnostic core, asymmetric channels and bounded dedup.** *Where:* Python core service to Tauri UI, and core service to the paid cloud and webhook relay.
   - Stream agent events to the UI over one persistent channel (SSE or WebSocket). Send commands (approve, kill, route a message) as idempotent request/response calls with request IDs.
   - Webhook relay deliveries are at-least-once, so dedupe them with a fixed-size ring of recent event IDs, plus a microversion check.
   - The agent loop must behave the same for in-app, ACP and external-client agents.
   - *Why:* ch16.

8. **Key the prompt cache on stable-first prompts, shared across agents.** *Where:* the Agent SDK orchestrator for hosted Claude agents.
   - Put a byte-identical system prompt and tool list first, then the per-part context, then volatile items (budget left, time) last or in messages.
   - Freeze the date and feature flags per session, and use `exclude_dynamic_sections` (official SDK).
   - Fork SIM and MAT helper subagents from a shared prefix, so many parallel agents reuse one cached prefix.
   - Track the cache-hit rate and cost per agent from the API `usage` field, and show those real numbers in the console totals.

9. **Memory as small inspectable files, scoped by project and part, with lazy recall.** *Where:* the Project store, as "part notes" an agent can read.
   - Admission rule: don't store what can be re-derived from Onshape or the mirror (dimensions, features).
   - Do store non-obvious lessons: "this bracket failed at the fillet in PETG", "user prefers 3mm walls", printer quirks, fidelity-drift notes.
   - An always-loaded index with a short line per note; bodies fetched on demand. Show each note's age with a "verify against current geometry" caveat.
   - Optionally, a cheap background pass after each approved or rejected merge extracts lessons.
   - Expose the notes as a read-only MCP tool (`notes.search`) for external agents.

10. **Agent console: render the stream as data, freeze what's off-screen, and fold by default.** *Where:* the Tauri console UI (§5.3).
   - A virtualized step log with cached row heights.
   - Freeze rows that are scrolled away or belong to collapsed or unfocused agents, so their spinners and timers don't re-render.
   - Use a lenient streaming Markdown render, then a strict final pass, with a content-hash render cache.
   - Fold tool calls by default ("▸ N more tool calls").
   - Batch event bursts into one render per frame, and throttle unfocused agent lanes harder than the followed one.
   - *Why:* ch13 "eliminate work". The Tauri DOM already gives diffing, but many concurrent agents flood the UI with streamed tokens.

11. **Keyboard model: contexts, bindings as data, and pastes can't trigger actions.** *Where:* the console command line and the command palette.
   - Approval cards form a modal context whose keys (Merge/Diff/Keep/Reject) take priority over chat keys.
   - Pasted text can never trigger approve or merge shortcuts.
   - Provide a "kill all agents" chord, and let users rebind keys in JSON (including null to unbind).
   - *Why:* ch14. An accidental approve is exactly the "silent change" anti-goal.

12. **Fail open for auxiliary services, fail closed for core and safety.** *Where:* the whole service.
   - Filament lookup, webhook relay or cloud studies going down degrades features. Fall back to slow focused polling for change detection.
   - Ledger, scope and approval failures **block**.
   - Keep the Onshape keys and OAuth tokens in the OS keychain and in process memory; never hand them to agent subprocesses (the agent's `cwd` sandbox gets no secrets).
   - *Why:* ch16.

**Book patterns deliberately not adopted:** the custom cell renderer and packed-cell buffers (the web DOM covers this), terminal key tokenizing, and eight transport types (stdio plus local HTTP is enough, per ch15's own "start with stdio").
