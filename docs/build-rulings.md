# Build rulings (decisions made on your behalf)

Each line: what was decided — why — what it costs if wrong. Copied from the build ledgers.

## 2026-10-24-harness-core
- Ruling: T4 worker.py is self-contained (stdlib + build123d only) and is run as `python -I /abs/path/cad/worker.py <part_dir> <vars.json>`; no PYTHONPATH — `-I` drops PYTHONPATH, venv site-packages still load — cost if wrong: small runner change.
- Ruling: AgentLoop offers a definition's tools ∩ registry ∩ policy classes; names not in the registry are skipped with a warning — lets planner.md list orchestrator_submit_plan before T9 — cost if wrong: silent missing tool, mitigated by the warning.
- Ruling: T10 launches the stdio proxy as `python -m mises.server.stdio_proxy --run <id>` (T8 adds a `__main__` reading server.json); T11 `mises mcp-stdio` wraps the same function — cost if wrong: one command-line change.
- Ruling: tasks run sequentially on branch harness-core (skill forbids parallel implementers), ignoring the plan's wave grouping — cost: wall-clock only.
- Ruling: sandbox profile must not allow writes to all of /private/var/folders — allow only the resolved write_dir (callers set TMPDIR to write_dir/tmp) — plan text mandated the broad rule; spec §10 asks for sandboxed agent folders — cost if wrong: some library needing system temp fails, fixable by adding a narrow path.
- Ruling: providers never emit Done("error"); exceptions (MisesError) are the error channel and the loop (Task 7) maps them to stop_reason "error" — the brief's base docstring already says a successful stream ends with one Done — cost if wrong: loop handles both, trivial.
- Ruling: residual risk that an in-process part script reads the nonce via frame introspection is accepted — the threat model is a buggy or overconfident agent script, not an adversary with code execution (which could already lie in many ways); the sandbox still blocks writes outside part_dir and network — cost if wrong: a hostile script could fake build metrics for its own part only.
- Ruling: onshape_merge_request stays safety=irreversible; ToolDef gains optional preview(ctx,args)->deltas that the gate awaits before requesting approval (replaces an earlier dispatch ruling that had the handler request its own approval) — keeps MCP destructive hints and the 'irreversible always asks' rule in one place — cost if wrong: one small extra gate stage to remove.
- Ruling: for onshape_mutate and irreversible tools the gate's idempotency key includes call_id (dedupes retries of the same call, never a later identical-looking request whose effect depends on new Onshape state); local_mutate/read keep the input-only key — prevents a second merge silently returning the first merge's cached result — cost if wrong: an agent that re-sends an identical propose with a new call id spends real calls (budget-capped).
- Ruling: upgrade "variable type inference overwrites existing types" from Minor to the fix round — it silently changes a user's LENGTH variable to ANY/ANGLE in their master copy — cost if wrong: one small extra change.
- Ruling: loop retries a whole turn on TRANSIENT (3 attempts total), recording the assistant message Step only after a turn completes; then stop "error" — providers don't replay mid-stream — cost if wrong: duplicated text_delta events on retry (UI cosmetic).
- Ruling: start_run default allowed_classes = all four classes (gate still requires human approval for irreversible); USD costing out of scope (0) — cost if wrong: $ caps unenforced until added.
- Ruling: a max_tokens stop marks the run done with stop_reason max_tokens (not failed) — the user can continue with send() — cost if wrong: status label only.
- Ruling: loop should retry only TRANSIENT failures that occur after the first stream event (providers already retry opens) — avoids 9 requests on a rate-limited provider — deferred to the final fix wave — cost if wrong: one fewer retry layer for pre-first-event errors.
- Ruling: Runtime gains create_external_run/finish_external_run + POST /runs/external so MCP/ACP clients get a run + frozen policy without an AgentLoop — plan had no way to bind external agents to a run — cost if wrong: small API to rename.
- Ruling: add `async serve_async(runtime, *, port=0, token=None)` that runs uvicorn on the caller's event loop (same server.json contract) — T10 (ACP host) and T11 (`mises acp`) need the MCP server in-process with the runtime on one loop; threaded serve() stays for the standalone `mises serve` — cost if wrong: one extra entry point.
- Ruling: child policy = parent edit_scope ∩ request; classes = parent ∩ classes of the child definition's tools (None → parent's); onshape_calls_run = min(parent cap, parent remaining); depth via parent_id, refuse at ≥2; dependency results passed as delimited data — plan said 'narrow' without the formula — cost if wrong: children slightly over/under-privileged, still gated.
- Ruling: AcpSession receives a run_id from runtime.create_external_run(agent=spec.name, provider='acp', model=spec.name); MCP entry = stdio proxy (sys.executable -m mises.server.stdio_proxy --run id, env MISES_HOME); permission options: allow_once/reject_once only, never allow_always, unknown kinds → reject — cost if wrong: an agent with exotic option kinds gets rejected and must retry.
- Ruling: an ACP agent using the user's own existing login (e.g. their Claude Code install) is allowed — design says "users bring their own install/API key"; Mises never calls authenticate or shows login — cost if wrong: policy change to isolate CLAUDE_CONFIG_DIR.
- Ruling: ACP session resume across restarts and orphaned agent/<name> branch teardown are deferred to the backlog (spec §7.3 must-haves not in this plan) — cost: restart loses external sessions; branches need manual cleanup.
- Ruling: on the user's request ("run stuff in parallel"), Task 11 runs in its own git worktree concurrently with Task 10's fix round, and Plan 2 (Mac app) planning starts in parallel — file sets don't overlap (cli/ vs acp_host/) — cost if wrong: a merge conflict to resolve.
- Ruling: C1 → call_id in key for all non-read classes, never cache ok=False — cost if wrong: retried identical local builds re-run (cheap).
- Ruling: C2 → admin vs MCP-only tokens, ACP agents under sandbox-exec denying the Mises home except their workdir, approvals only honoured if decided in-process — cost if wrong: an agent needing to read the Mises home breaks (unlikely; it uses MCP).
- Ruling: part ids stay global (not namespaced per project) for now; locks keyed on resolved path — cost if wrong: two projects with the same part id overwrite each other (deferred).
- Ruling: residual security finding (ACP fs/* handlers run unsandboxed in the host with a TOCTOU symlink swap → could read server.json admin token) gets one small targeted fix: drop the ACP fs capability (advertise readTextFile/writeTextFile false; fs/* requests → method-not-found) — agents' own sandboxed file access covers their workdir — plus unique tool-call ids when an OpenAI-compatible server omits them (C1 dependency). Skill says no second fix wave; overridden because it is security-relevant and two-line-scoped — cost if wrong: agents that rely on client fs (rare) fall back to their own file tools.

## 2026-10-24-mac-app
- Ruling: accept all bracketed defaults in the plan's conflict section; conflict 8 resolved (mises serve exists) — cost if wrong: Keep-branch/Reject indistinguishable server-side until core adds an outcome field.
- Ruling: parallel worktree execution per wave, at the user's request ("run stuff in parallel") — cost if wrong: merge conflicts, mitigated by the ownership rule.
- Ruling: T1's file list widened to cad/worker.py + tests/cad and a minimal public Runtime method for cancel_all — the GLB bug blocks T6 and is in Plan 1 code the final review can't fix in time — cost if wrong: overlap with the final-review fix wave on worker.py (merge conflict).
- Ruling: T3 (Rust shell) dispatched before T2's review completes, branching from mac-t2-foundation — it depends only on connection.ts's invoke contract — cost if wrong: rework if T2's review changes core_info fields. Task 3: dispatched (worktree, base 0c8a3fb)
- Ruling: Wave 2 tasks 4–9 start now from mac-t2-foundation@0c8a3fb in parallel with T2's fix round — fixes touch only reduce/sync/main/session/fake-core cancel, which Wave 2 doesn't own — cost if wrong: rebase conflicts at merge.
- Ruling: T3 may add `mises serve --exit-on-stdin-eof` to core cli/main.py (parent-death via held stdin pipe), plus signal handler + Drop — macOS has no PDEATHSIG; pipe EOF covers SIGKILL/crash — cost if wrong: small merge conflict in cli/main.py with harness-core.
- Ruling: App.test.tsx's stub-marker expectation is expected to fail per Wave 2 task; Task 10 replaces that test; client cancelAll zod schema gains `failed` in Task 10 — cost: one red unit test until T10.
- Ruling: T4 fix round 1 re-review done by the controller reading the 3-file diff (042a01d..050d117) — trivial, fully addresses the finding — cost if wrong: a missed regression in breadcrumb only.
- Ruling: external run ⇔ run.provider === "acp" (core create_external_run uses provider "acp") — cost if wrong: @all may message an external run and get a 400.
- Ruling: T9 fix round 1 re-review done by the controller reading the 56-line diff (9072dd5..3225050) — both Important findings addressed with tests — cost if wrong: missed regression in command line only.
- Ruling: T6's App.test.tsx edit accepted; Task 10 reconciles App.test.tsx across merges. Core already allows If-None-Match (final fix wave); fake core's preflight list updated in T10 — cost: merge conflict in App.test.tsx.
- Ruling: VoiceOver/no-pointerdown clicks no longer decide approvals (keyboard Enter/Space/m/a/k/r still work) — safety over pointer-less activation — cost if wrong: screen-reader users must use the keys.
- Ruling: T11 reviewed by the controller (4 files: build script, tauri bundle config ad-hoc signing, package script, README) — no app logic — cost if wrong: build-script nits only. Task 11: complete (commits 5201963..0b2a2b4, review clean)
- Ruling: roster fallback run highlighted blue is acceptable — it is the run the log shows and the command line targets ("selected" in effect) — cost if wrong: one blue row without a click.
- Ruling: I5 → "agent says:" labelled summary + key payload fields always on the card face — the human must see what they approve — cost if wrong: denser cards.
- Ruling: I7 → @all only targets status "done", live, non-external runs — STOP ALL then @all must not restart work — cost if wrong: user must message stopped runs individually.
- Ruling: I9 → core gains a persisted AgentRun.external flag, replacing the provider=="acp" heuristic — cost if wrong: one schema migration.
- Ruling: I1 → letter keys only after keyboard focus entry + 400 ms; pointer press disables them — cost if wrong: mouse users must click the button.
- Ruling: two safety follow-ups done as one small targeted round (I1 window-refocus re-arm bypass; I8 periodic /status poll + bounded count comparison) — skill says no second fix wave; overridden because both close stated Review Focus gaps and are tightly scoped — cost if wrong: one extra GET every 10 s while busy.

## 2026-10-25-claude-code-and-setup
- Ruling: "login screen" = local first-run setup + profile on this Mac; no Mises cloud account (no server exists; paid tier later) — cost if wrong: an accounts backend is a separate plan.
- Ruling: Claude Code runs headless (claude -p stream-json) with --tools "" + --strict-mcp-config + only mises MCP, allowedTools mcp__mises__* (no permission prompts; the Gate's approval cards handle irreversible actions) — cost if wrong: needs --permission-prompt-tool routing instead.
- Ruling: Tasks 1, 2, 4 run in parallel now; Task 5 after Task 4's client additions; Task 3 after 1+2; Task 6 last — at the user's standing request for parallelism — cost: reconciliation in Task 6.
- Ruling: allow Claude Code's subagent tool only (`--tools "<subagent tool name>"`, verified name) so --agents designer/verifier work; those subagents are defined with tools ["mcp__mises__*"] only — cost if wrong: subagents could inherit more tools; init check must still reject any non-mises tool other than the subagent tool and EndConversation.
- Ruling: pass `--setting-sources ""` (or the documented equivalent that loads no user/project/local settings) so the user's ~/.claude hooks/plugins/permissions don't run inside Mises sessions; NOT --bare (bare mode may restrict auth to API keys and break subscription sign-in) — verify both in docs — cost if wrong: user hooks run sandboxed.
- Ruling: add `POST /local/test {url}` (loopback-only, same validation as profile local_endpoints) in Task 3 so the wizard and Profile can test a local model directly — cost: one small route.
- Ruling: "profile" missing ⇔ display_name empty is accepted for the wizard's step order, but setup completeness stays as Task 1 defines (model + setup_completed_at) — cost: none.
- Ruling: setup "complete" = setup_completed_at set (the user finished or skipped the wizard); has_model stays a separate flag the UI shows as a "connect a model" notice — local-only users must not see the wizard every launch — applied in Task 3 — cost: a user can finish setup without a working model (they get a notice).
- Ruling: Task 3 adds `data_dir` to GET /status (runtime.home) so Profile shows the real data folder; "Copy path" only (no new Tauri capability) — cost: no Show in Finder.
- Ruling: env-sourced API keys: Replace writes a Keychain key (env still wins at lookup — UI must say "environment variable takes precedence"), Remove hidden — cost: none.
- Task 3: implementer DONE (114d66f; core 495). Ruling: stop-all leaves idle (between-turn) Claude Code processes running — they consume nothing; cancelling the run or quitting kills them — cost: an idle process until quit.

## 2026-10-28-pages-models-ux
- Ruling: font = Iosevka (OFL); Bloomberg Prop is proprietary and not bundled — cost: not the exact Bloomberg look.
- Ruling: no Fusion 360 adapter in this plan — Fusion isn't installed here and the user asked a question; offer it separately — cost: none now.
- Ruling: Tasks 6–8 share one brief section; dispatched as three agents (Studies, Sketch, Drawing+Print).
- Ruling: Task 2 branches from p4-core-fixes after it is reviewed.
- Ruling: UI-started tool calls reuse ONE long-lived "ui" external run per core session (agent "you", provider "ui"), not a new run per slice/print — keeps the run list clean; approvals still bound to that run — cost if wrong: one run with many steps.
- Ruling: light-theme `faint` text changes from #7a7a7a to #6a6a6a (≥4.5:1 on #f4f4f2/#ffffff) — Review Focus 5 outranks the plan's exact value — cost: slightly darker hint text.
- Ruling: viewer mesh face/edge colors follow the theme (T1 fix round) — light viewport needs dark edges.
- Ruling: pages Tasks 6–8 start from p4-pages-base = mac-app + p4-t1-shell (unreviewed) in parallel with T1's review — cost: rebase if T1 fixes touch router/tokens.
- Ruling: one-time migration: profiles without a schema_version whose max_parallel_agents == 2 (the old default) become "auto"; add profile schema_version — the user never chose 2 — cost: a deliberate 2 resets once.
- Ruling: local-model concurrency uses a combined memory budget across all running local models (sum of sizes ≤ RAM − 5 GB reserve), not per model — cost: fewer parallel local runs.
- Ruling: light-theme status text colors darkened (same hue) to pass ≥4.5:1 — green #17763f, amber #8f5e0a, blue2 #2560c4 — accessibility outranks the plan's exact values — cost: slightly darker accents.
- Studies: implementer DONE (c51e1ce..b681bd3; vitest 680, e2e 124). Ruling: stress heatmap colours are a data scale (exempt from the blue/status colour rule) but the legend must label units and be readable in both themes. T9 carryover: fixtures re-dump (face_list, printers), sim_progress in reducer, face_list in part schema.
- Ruling: Drawing+Print fix round 1 verified by controller (display-only slice_model line from deltas; fresh status before POST /prints; stale status disables Send) — cost: none.
- Ruling: Studies fix round 1 verified by controller (nonce-delimited, neutralised study_result block; stale banner refresh tested) — cost: none.
- Ruling: Sketch fix round 1 verified by controller (JPEG budget < 3.75 MB base64, 40 MP header cap, 5,000 point cap, failed attach marks run failed) — cost: none.
- Ruling: local/small models (ollama, lmstudio, OpenRouter free) are not offered orchestrator_run/agent_spawn/orchestrator_submit_plan; designer prompt says build directly with mirror_part_create; ui_ask_user accepts `prompt` alias; investigate Ollama num_ctx for 27b — cost if wrong: small models can't delegate (they couldn't anyway).
- Copy pass: DONE (af77117; 110 strings; vitest 1013, e2e 174). Ruling: keep the 44 px top bar (user's decision) and move the traffic lights to its centre line via trafficLightPosition instead of shrinking the bar to 32 px — cost: lights sit lower than stock macOS.
- Ruling: install the plain grey cube (concept-8) as the app icon — user said "just make the logo a cube"; blue-edge variant (8b) is a one-file swap — cost: none.

## Known residuals
- Task 4: minor (deferred): slicer not sandboxed; no concurrent-slice limit / slice dir cleanup; dxf private API; cleartext PrusaLink hint in UI
- Task 4: minor (deferred): cancel_ui_call matches card by tool+payload (bind approvals to call_id later; fails safe)
- Task 3: minor (deferred): semaphore acquire outside try could leak on cancel-after-acquire
