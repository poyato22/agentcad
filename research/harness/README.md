# Harness baseline: what Mises takes from Claude Code, OMA and "Claude Code from Source"

Date: 2026-10-24 · Status: research synthesis, input to `docs/design/2026-10-24-mises-design.md`

## Sources and how far to trust them

| Source | What it is | Trust | Use |
|---|---|---|---|
| Claude Code official (docs, official plugin marketplace, Claude Agent SDK) | Anthropic's published config surface and examples | Authoritative | Adopt directly |
| open-multi-agent (OMA), commit `009bdbe` | MIT TypeScript agent runtime, real code | Real behavior, read from source | Port patterns to Python. If any code is copied verbatim, keep the MIT notice |
| claude-code-from-source.com ("CCFS") | Third-party book written from Claude Code's leaked npm source maps, presented as prose and pseudocode | Unverified, older Claude Code version | **Ideas only.** Design anti-goal: no leaked source. Nothing from it is copied into code, and the reports here paraphrase it |

Detailed reports in this folder:
- `official/agents.md`: agent definitions in the official plugins.
- `official/plugins-hooks-skills.md`: plugins, skills, slash commands, hooks, MCP and LSP configs.
- `official/agent-sdk.md`: the Python Agent SDK internals and the CLI flags it passes.
- `oma/core.md`: OMA's planner, scheduler, agent loop and checkpoints.
- `oma/governance-acp.md`: OMA's durable approvals, journal, governance and ACP backend.
- `third-party-unverified/ccfs-core-loop-agents.md`: book chapters 1–10.
- `third-party-unverified/ccfs-memory-hooks-mcp.md`: book chapters 11–18.

## 1. The one conclusion that shapes everything

**Mises must enforce its rules in the Python core service, not inside any agent's loop.**

- Codex, Gemini CLI and Cursor bring their own loops over ACP. Mises can't rely on their retries, hooks, permission modes or budgets.
- OMA's tool-approval gate never sees ACP agents' tool calls, and its ACP backend auto-approves permission requests.
- Claude hooks and `can_use_tool` only cover Claude.

So scope, the Onshape call budget, stale-write rejection, approval for irreversible actions and evidence for claims all live in the `mises` MCP server and the Onshape adapter. Claude-side hooks are a second layer, never the only one.

```
any agent ──ACP / MCP stdio──▶ mises MCP server ──▶ gate pipeline (§2) ──▶ Onshape adapter / mirror / sim / printer
                                                      │
                                                      └──▶ SQLite: Step, Approval, LedgerEntry (one transaction per call)
```

## 2. Gate pipeline for every MCP tool call

The order combines the permission pipeline the book describes with OMA's approval checks:

1. **Validate** the input against the tool's schema. Reject unknown fields.
2. **Classify the call:** `read`, `local_mutate` (mirror, sim), `onshape_mutate` or `irreversible` (merge, print send, spend, delete). Fail closed: a tool with no class counts as `irreversible`. Publish matching MCP `readOnlyHint` / `destructiveHint` annotations.
3. **Check scope** against the agent's frozen policy (§5): its edit part, the read-only assembly and locked mates. Anything out of scope becomes an approval request, not an error the agent can retry around.
4. **Check the ledger before the call.** Estimate the Onshape calls first. The lowest cap wins (user per year, user per day, agent). Serialize ledger writes per agent. Budget errors are never retried, and a circuit breaker stops an agent after repeated budget or skew failures.
5. **Approve.** Irreversible actions create a content-hashed Approval record (§3) and wait.
6. **Execute idempotently.** Each call carries an idempotency key, and a replay returns the saved result instead of calling Onshape again (OMA-style checkpoints, placed in the server so ACP agents get them too).
7. **Record** a Step event in the same SQLite transaction as the LedgerEntry. Return the typed result `{ok, version, changes, warnings}` plus **handles** for anything big (glTF, renders, feature JSON go to files, and the tool returns path, summary and size).

Typed errors tell the agent what to do next:
- `stale_version`: re-plan from the new microversion. Never blind-retry it. OMA's default retry treats 409 as retryable, which is wrong here.
- `budget_exhausted`: stop.
- `needs_approval`: wait.
- `transient`: back off and retry.

## 3. Approvals (copy from OMA, adapted)

- **Bind each approval to its content.** The record stores the exact proposal (variable and feature deltas, mass/volume/bbox changes, sims cited), `payload_hash = sha256(canonical JSON)`, and the Onshape `microversion` it was computed against.
- **Make decisions compare-and-set.** The reviewer's click sends back the hash they saw. A different hash is rejected as stale, and the first decision wins.
- **Re-check at execution.** Recompute the hash and current microversion just before the merge or print. A mismatch means a fresh card, never a silent proceed. The same check makes resume-after-restart safe.
- **Wire ACP and the SDK into the same queue.** ACP `session/request_permission` and SDK `can_use_tool` both create cards. **Nothing auto-approves.** OMA's default does, so don't copy it.
- **Additions over OMA:** expiry, reviewer identity, and letting the human edit a proposal before approving (the SDK's `updatedInput` supports this for Claude).

## 4. Step log and "no fake confidence"

- **Event vocabulary:** start from OMA's journal (message, model request, tool call/result, approval, checkpoint) and add `onshape_call`, `diff`, `plan`, `sim_run`, `measure`, `verify`.
- **Badges cite evidence.** Each step written by the server carries a result hash. A "sim passed", "fits" or "verified" badge links to that event id and hash. Anything an agent asserts in prose shows as an **unverified claim**. This is the enforceable version of the anti-goal. OMA's `verifyRun()` doesn't provide it, because it only proves context wasn't rewritten.
- **Verdicts come from records, not agent text.** This is OMA's governance rule and it applies everywhere: approval cards, the environment matrix, the print gate.
- **Optional:** chain each Step to the previous one's hash if the log ever needs to be tamper-evident.

## 5. Agent host (design §7.3)

**ACP host:** build on the official ACP SDK, not on OMA, whose ACP layer is a thin reference only. Must-haves that OMA lacks:
- `session/new` with `mcpServers: [mises]`
- `request_permission` routed to approval cards
- `plan`, `tool_call`, `diff` and `usage` updates mapped to console lanes
- tool-call failure status shown as failed, not done
- `session/cancel` with a hard timeout, then kill the process
- session resume across app restarts
- `fs/*` and terminal methods confined to the agent's sandbox folder (or not offered)

**Claude orchestrator** (Python `claude-agent-sdk`, API key):
- **Set every default explicitly.**
  - `system_prompt`: unset means empty. Use your own, or the `claude_code` preset plus `append`.
  - `setting_sources`: keep it narrow so the user's `~/.claude` settings, hooks and plugins don't leak into the product.
  - Also set `allowed_tools`, `permission_mode`, `max_turns` and `max_budget_usd`.
- **Subagents:** `AgentDefinition`s for SIM and MAT roles with narrow tools (`mcp__mises__sim_*` only), a small model for mechanical work and `inherit` for judgment. Descriptions should say "dispatched by the orchestrator only" so nothing self-delegates.
- **Hooks:** `PreToolUse` as a second layer, `Stop` for a final evidence check, and in-process `@tool`s only for orchestrator-private helpers. Shared tools stay on the `mises` server.

**For all agents:**
- **Freeze each agent's policy** (scope, caps, allowed tool classes) when its session starts. Widening it is an approval that creates a new policy version.
- **One task at a time per agent branch.** User messages to an agent are delivered between tool rounds, never mid-call.
- **Guaranteed teardown:** on stop, crash or cancel, delete the orphaned `agent/<name>` branch (or flag it), release ledger reservations, and mark the AgentRun.

## 6. Multi-agent orchestration (later milestones)

- **Plans:** require strict JSON, validate it (known agents, no cycles, no missing dependencies), allow one repair attempt, then fail. Never fall back to a guessed layout. (OMA)
- **Scheduling:** dependency-ordered dispatch. A failure cascades to its dependents while independent work continues. (OMA)
- **Plan-only, then approve, then exact replay**, with an estimated Onshape call count on the plan card. This fits the budget anti-goal well. (OMA)
- **Parallel lenses plus a skeptical verifier.** Give reviewers different angles (fit, strength, printability), give verifiers a default "reject" stance, and do vote counting and thresholds in code. (Official plugins: code-review, claude-security)
- **The orchestrator reads the evidence itself** before proposing. Subagents return file lists or structured results, and the parent synthesizes. (The book's "don't delegate comprehension"; feature-dev does the same.)

## 7. MCP tool design (design §7.4)

- ⚠️ **Tool names:** Claude shows tools as `mcp__<server>__<tool>`, capped at about 64 characters. Use `onshape_propose`, not `onshape.propose`. Check what each ACP client accepts.
- **Descriptions:** keep them short and precise (Claude truncates long ones), and say when to use the tool and what it costs in Onshape calls.
- **Return a handle plus a summary**, never big payloads. Long jobs return a job id (MCP Tasks).
- **Treat external text as data:** Onshape names, comments and filament pages come back clearly delimited as data (already in design §10).

## 8. Agent console (design §5.2)

- **Lanes:** tool calls collapsed by default, off-screen lanes frozen, at most one render per frame during bursts.
- **Event flow:** one event stream from the core to the UI; approve, deny and kill go as separate idempotent commands; webhook events are de-duplicated.
- **Keyboard:** approval keys work only inside the focused card and never from a paste, plus a global "stop all agents" key.
- **Status display:** budget meter per agent and per user. Approval cards show the hash-bound proposal and the evidence links from §4.

## 9. Verdicts

| Item | Source | Verdict | Lands in |
|---|---|---|---|
| OMA as a library | OMA | **Skip.** It needs Node (a sidecar plus an extra hop per tool call), and ACP agents bypass its loop anyway | — |
| Durable, content-hashed approvals | OMA | **Copy** to Python and SQLite | §7.3, §8 Approval |
| Journal event vocabulary | OMA | **Copy** and extend | §8 Step |
| Tool-result checkpoints / idempotent replay | OMA | **Copy**, inside the MCP server | §7.1, §7.4 |
| Plan schema, scheduler, plan-replay | OMA | **Copy later** | Orchestrator milestone |
| Consensus judges | OMA | Optional pre-approval critic. Sim numbers still decide mechanical questions | Later |
| `verifyRun`, egress policy, Run Viewer, OTel | OMA | **Skip** for now | — |
| OMA ACP backend | OMA | **Skip**; use as a reference | — |
| Official ACP SDK | ACP | **Adopt** for the host | §7.3 |
| Claude Agent SDK (Python) | Anthropic | **Adopt** for the orchestrator | §7.3 |
| Claude hooks / `can_use_tool` | Anthropic | **Adopt** as a second layer only | §7.3 |
| Agent definition conventions (narrow tools, dispatch-only descriptions, skeptical verifiers) | Official plugins | **Adopt** | Orchestrator agents |
| Fail-closed tool classes, circuit breaker, result-size caps, frozen policy, teardown, stable-first prompt ordering | CCFS (ideas) | **Re-implement independently** | §7.3, §7.4 |
| Terminal renderer, raw key parsing | CCFS | **Skip** (web UI) | — |

## 10. Proposed design-doc changes (not applied; for review)

1. **§8 Approval:** add `payload_hash`, `microversion`, `status` (compare-and-set), `expires_at`, `decided_by`, `edited_payload`.
2. **§8 Step:** add the kinds `onshape_call`, `sim_run`, `measure`, `verify`, plus `evidence_hash` and an optional `prev_hash`.
3. **§8 LedgerEntry:** add `estimate`, `idempotency_key` and `reservation` (estimates reserve budget before the call).
4. **New: AgentPolicy.** A frozen, versioned record (scope, caps, tool classes) per AgentRun.
5. **§7.4:** underscore tool names, a safety class per tool, typed error codes (`stale_version`, `budget_exhausted`, `needs_approval`, `transient`), and handles for large outputs.
6. **§7.3:** "never auto-approve", the ACP must-haves from §5, and explicit SDK `system_prompt` / `setting_sources`.
7. **§4 anti-goals:** spell out "CCFS for ideas only, no code".

## 11. To verify before building

- MCP tool-name length and character rules in each ACP client (Claude, Codex, Gemini CLI, Cursor).
- Claude's MCP output cap (`MAX_MCP_OUTPUT_TOKENS`) and tool-search behavior. These came from the agent's memory, not this run.
- The maturity of the official ACP SDK for Python. If it's weak, put the ACP client in the Rust shell or a thin TypeScript layer and keep the gates in Python.
- Any CCFS claim used for a design decision. It describes an older Claude Code version and is unverified.
