# Mises — AI CAD Harness · Full Design

**Date:** 2026-10-24 · **Status:** draft for approval · **Platforms:** macOS and Windows desktop app
**Working name:** *Mises* (designer's placeholder, from "humans and Claude build in mises"; rename freely)

Companion files:
- Simplified visual overview (PDF): `docs/overview/Mises overview.pdf`
- UI screenshots: `docs/overview/img/01-core-loop.png`, `02-assembly.png`, `03-studies.png`, `04-sketch.png`
- Interactive mockups: `mockups/terminal-core-loop.html` (latest, Bloomberg-inspired), `mockups/core-loop.html`, `mockups/screens-assembly-studies-sketch.html`
- Research index (what each study found, how far to trust it): `research/README.md`
- Proposed changes not yet applied to this doc: `docs/design/pending-changes.md`
- Onshape / agent integration research (endpoints, sources): `research/integration/onshape-and-agents.md`
- Landscape research: `research/landscape/3d-cad-agents.md`

Legend: **[H]** = decided by the human · **[D]** = designer's call, open to change · **[B]** = left to the builder.

---

## 1. Summary

Mises is a desktop app for Mac and Windows where a person and AI agents design physical parts together, on the same live model, and test them before printing. It connects to **Onshape** (which stays the master copy), lets **any AI agent** (Claude, Codex, Gemini, Cursor…) work on parts, runs a **simulator** against real-world environments, and sends parts to **Prusa and Bambu** printers with filament picked from live internet data.

It is a product for other people [H]. The free tier must cost almost nothing to run per user, and a paid tier unlocks the heavy features [H].

## 2. Intent

- **Why:** AI models keep getting better at 3D design. The winning tool will be the one where humans and agents build in mises, so hardware gets faster and cheaper to make [H].
- **Success:** a user describes or sketches a part; agents propose and test changes; the user approves what they like; it lands in Onshape and prints in a filament that survives the use case — with far fewer failed prints and manual iterations than today.
- **Who:** makers, students, hardware startups and engineers who already use Onshape and own a desktop 3D printer.

## 3. Goals, non-goals, anti-goals

### Goals
1. **Live shared model** — 3D viewer plus mirrored Onshape feature tree and variables; human and agent edits visible in place [H].
2. **Part focus inside an assembly** — edit one part with the rest of the build ghosted; mating faces locked; whole-build collision and clearance checks. Modes: Isolate / In context / Full build [H].
3. **Any agent, many agents** — host Claude and other agents, several at once [H].
4. **Agent console built for volume** — roster, filters, step log, collapsible tool calls, feature diffs, approval queue, cost totals [H].
5. **Simulator always attached** — design and live stress side by side; environment bar; environment × material matrix [H]. **The simulator is a guide, not a guarantee** [H].
6. **Studies** — static, thermal, modal, fatigue, CFD / wind tunnel, topology optimization, generative design [H]; heavy ones are paid tier [H].
7. **Sketch to part** — freehand or photo of a paper sketch → constrained part, with only a few clarifying questions [H].
8. **Printer connection** — Prusa (owned by the developer) and Bambu [H].
9. **Filament lookup from the internet** [H].
10. **Onshape is the master copy** for collaboration and history [H].

### Non-goals
- Replacing Onshape or building a general CAD program. A web-based, fully collaborative build123d-native CAD is a possible far-future direction, out of scope.
- Running Onshape locally (impossible: closed cloud service).
- Art/game mesh generation.
- Writing our own physics solvers — we wrap existing ones.

### Anti-goals (failure even if it "works")
- **Burning Onshape API allowances** — ours or the user's.
- **Agents silently changing things** — merges, locked interfaces, scope expansion and printing always need the human.
- **Simulation presented as a guarantee**, or agents reporting checks they didn't run.
- **Using leaked Claude Code source** [H]. Only official routes.
- **Evading Onshape's terms** (account rotation, scraping the web client to dodge metering).
- **A free tier that costs us real money per user.**
- **Toy look** — must feel like an engineering instrument.

## 4. Scope and roadmap

### v1 = the core loop [H]
1. Connect an Onshape document → viewer, feature tree and variables mirror it.
2. Connect an agent (bring-your-own) → it proposes a change on **its own Onshape branch**, iterating on the local copy.
3. Local simulator runs the change against the selected environment and shows stress / deflection / safety factor, labeled as a guide.
4. Human reviews the diff, sim and cost in the agent console → approves.
5. Branch merges into Onshape main.
6. Slice and send to a Prusa or Bambu printer.

### Milestones [D]
| # | Milestone | Proves |
|---|---|---|
| M0 | App shell on Mac + Windows: window, top bar, panes, packaging, signed installers | It ships on both platforms |
| M1 | Onshape connect: OAuth/API keys, document picker, feature tree + variables, glTF viewer, call ledger, cache | Read-only mirror works within budget |
| M2 | Local copy: build123d mirror of a variable-driven part, fidelity check vs Onshape mass/bounding box | Agents can iterate without Onshape calls |
| M3 | Agent host: ACP client, one agent, MCP server, console (roster, log, diffs, approvals), branch + merge | **The core loop minus sim/print** |
| M4 | Simulator: CalculiX static stress, environments, matrix, design \| stress split | Core loop with testing |
| M5 | Printing: Prusa (PrusaLink + PrusaSlicer CLI), then Bambu (LAN + Orca/Bambu Studio CLI) | **Full v1 core loop** |
| M6 | Multi-agent: several agents in parallel, scopes, filters, per-agent budgets | Many agents at once |
| M7 | Assembly focus: assembly tree, ghosts, locked mates, interference checks | Works inside a build |
| M8 | Filament service: live data, material picker, matrix by material | Material choice |
| M9 | Sketch to part | Draw → part |
| M10 | Paid tier + cloud studies: topology opt, generative, CFD; rough/manual rebuild to features first [H] | Power features, revenue |

## 5. User experience

### 5.1 Visual system [H]
- Engineering-drawing aesthetic: drafting grid, dimension lines, title block in viewport corner, corner ticks, FIG. labels.
- **Square corners everywhere.**
- **Greyscale (near-black #0a0a0a, panels #111/#161, lines #242/#333, text #ececec/#9a9a9a/#5e5e5e) with blue (#3d8bff / #8dbaff) only** for selected, being changed by an agent, or needs the human. Green / amber / red only for pass / warn / fail; amber also marks locked interfaces.
- Type: IBM Plex Mono for UI, IBM Plex Sans Condensed for headings.
- **No auto-numbered names** [H]. The feature tree, variables, parts and diffs never show Onshape's numbered default names (`Extrude 1`, `Gusset1`, `Part Studio 1`, `#var1`). Every row reads `Type · purpose` (e.g. `Gusset · upright`, `Extrude · plate`). Agents must give every feature and variable they create a descriptive name. User features still carrying Onshape defaults are *displayed* as `Type · <purpose from parameters>` (e.g. `Hole · M4 ×4`) without renaming them in Onshape. When two rows would read the same, add what differs (`Fillet · 2mm edges` / `Fillet · 0.5mm top`), never a number.
- **Top bar (~44px), three zones** [H: "less cramped"]: left = assembly / part breadcrumb; center = mode tabs (Design · Studies · Sketch · Drawing · Print), active tab underlined blue; right = status lights (Onshape version, printer, agent count). Contextual controls live where they're used, not in the top bar.

### 5.2 Screens
- **Design (core loop)** — `docs/overview/img/01-core-loop.png`. Left: feature tree (with rollback bar), variables (changed values in blue), version history. Center: environment bar over **design | live stress** split. Right: agent console (full height). Bottom dock: printer, filament table (heat deflection temp, tensile, $/kg), environment × material safety-factor matrix.
- **Design in an assembly** — `docs/overview/img/02-assembly.png`. Assembly tree with the focused part's features nested; interfaces panel (locked mates); build checks (collisions, total mass, weakest part). Neighbors drawn as grey ghosts; locked faces outlined amber. Focus switch (Isolate / In context / Full build) bottom-left of viewport. When an agent finds a problem outside its scope, it asks to expand scope or spawn another agent.
- **Studies** — `docs/overview/img/03-studies.png`. Study list with solver per study (local / cloud / Ansys later); study setup (keep-in, keep-out, loads, goal, process, material); result viewports (topology, wind tunnel); generative outcomes grid (mass, safety factor, print time, warnings); "Rebuild as features".
- **Sketch** — `docs/overview/img/04-sketch.png`. Tool rail, freehand canvas (mouse, trackpad, pen; photo upload), the agent's constrained reading (fully constrained / N guessed / confidence), and at most a few questions, then Build / Build + run studies.
- **Drawing, Print** — tabs exist; designs [B] in the same system. Print tab must show slice preview, time, grams, cost, and a final confirm.

### 5.3 Key interactions
- **Approval card** (blue): what will change, mass/print-time delta, environments passed, buttons Merge · Diff · Keep branch · Reject. Required for: merge to main, touching a locked mate, scope expansion, starting a print, spending paid credits above a threshold.
- **Agent console:** roster rows show agent name, provider, task, progress, elapsed; filters: one agent / all / decisions / tool calls / files / errors; tool calls fold into "▸ N more tool calls"; diffs show variable and feature changes; "follow" auto-scroll; command line routes to an agent or `@all` or `/commands`.
- **Pointing:** selecting a face or edge in the viewport attaches it to the next agent message.
- **Sim label:** every result shows solver, mesh size, material model and "guide only".
- Keyboard-first [B], with a command palette.

## 6. Architecture [D]

```
┌──────────────────────────── Mises desktop app (Tauri) ────────────────────────────┐
│ UI (TypeScript + Three.js): viewer · tree · console · sim views · dock · sketch    │
│      │ IPC                                                                          │
│ Rust shell: windows, OS keychain, auto-update, process supervisor, file access     │
│      │ spawns / supervises                                                          │
│ ┌──────────────────── Core service (Python, localhost only) ─────────────────────┐ │
│ │ Onshape adapter · Local mirror (build123d/OCCT) · Sim runner (gmsh+CalculiX)    │ │
│ │ Printer drivers · Filament service · Budget ledger · Project store (SQLite)     │ │
│ │ MCP server "mises" (HTTP + stdio wrapper) ← every agent uses this              │ │
│ │ Agent host: ACP client (any agent) + Agent SDK orchestrator (Claude, API key)   │ │
│ └─────────────────────────────────────────────────────────────────────────────────┘ │
└────────────┬─────────────────────┬────────────────────┬────────────────────────────┘
             │ REST/OAuth          │ LAN                │ HTTPS (paid tier only)
        Onshape cloud        Prusa / Bambu printer   Mises cloud: webhook relay,
                                                     accounts/billing, cloud solvers
```

- **Why Tauri + web UI + Python** [D]: one codebase for Mac and Windows [H: both platforms, an app]; small installers; Three.js is the strongest viewer path for glTF; CAD and solver libraries (build123d, OpenCascade, gmsh, CalculiX bindings) live in Python. Not native Swift because of Windows.
- **Everything local by default.** The Mises cloud is only: (a) a tiny webhook relay, (b) accounts + billing, (c) paid cloud solvers.

### 6.1 Core loop data flow
1. User opens a document → adapter fetches features, variables, glTF (≈3 calls) → cached by microversion.
2. Webhook (via relay) or slow focused polling signals changes → re-fetch only on new microversion.
3. User asks an agent for a change → host starts an ACP session with the `mises` MCP server.
4. Agent calls `mirror.*` and `sim.*` tools on the local copy (no Onshape calls), iterates.
5. Agent calls `onshape.propose(change)` → adapter creates the agent's branch, applies a **batched** feature/variable update with `sourceMicroversion` + `rejectMicroversionSkew`, verifies mass/bbox on real geometry, fetches glTF.
6. Console shows the approval card → human merges → adapter merges branch into main and deletes it.
7. `print.slice` → preview → human confirms → `print.send`.

## 7. Components

### 7.1 Onshape adapter
- **Auth:** per-user. Until App Store status is confirmed, users connect with **their own API keys** (HMAC signing; personal accounts max 2 keys) so calls hit **their** allowance, never ours. Target: **public Onshape App Store OAuth app**, which Onshape's docs say isn't counted against allowances — **must be confirmed with Onshape** because the official Labs MCP states the opposite. A *private* OAuth app is not acceptable for distribution: its calls are charged to the app owner.
- **Endpoints used** (API v17; details in `research/integration/onshape-and-agents.md`): get features, `features/updates` (batch parameter patch), add feature, Variable Studio get/set, glTF export (sync), mass properties / bounding boxes, branch (create workspace from version), merge preview + merge, `currentmicroversion`, webhooks.
- **Budget:** every call goes through the **ledger** (per user, per agent, per day/year); per-agent caps; hard stop before the user's allowance is exhausted; usage meter in UI. Only 2xx/3xx count; still treat every call as paid.
- **Cache:** keyed by microversion (immutable): features, variables, glTF, mass props.
- **Concurrency:** writes carry `sourceMicroversion` + `rejectMicroversionSkew`; a stale agent write is rejected and the agent re-plans.
- **Branch per agent:** `agent/<name>`; merge only after preview + approval; delete branch after.
- **Pitfalls to handle:** redirects need re-signing; full feature replace blanks `featureType`/`name`; face/edge IDs change after upstream edits; variable types uppercase; document names/comments may contain prompt-injection text (treat as data).

### 7.2 Local mirror
- Parts should be **variable-driven** in Onshape (key dimensions in Variable Studio). The app nudges users toward this.
- For each part, an agent writes a **build123d script** that approximates the part from its features and variables. It doesn't need every fillet — only enough to simulate and check fit.
- **Fidelity check:** after generation and after each merge, compare mirror vs Onshape (mass, volume, bounding box, hole positions). Show a fidelity badge; if drift exceeds a threshold [B], regenerate or warn.
- Agents iterate on the mirror freely; only winning variable values / feature changes go to Onshape.
- Assembly mirror (M7): parts placed by Onshape mate transforms; interference via OCCT boolean/distance checks.

### 7.3 Agent host
- **ACP v1 client** is the default [D]: user picks any agent from the ACP registry (Claude via adapter, Codex, Gemini CLI, Cursor, Copilot, OpenCode, Goose…). Agent runs as a subprocess with `cwd` = a per-agent sandbox folder and `mcpServers = [mises]`. Map ACP `tool_call`/`diff` → console lanes, `plan` → checklist, `request_permission` → approval card.
- **Claude Agent SDK orchestrator** for built-in multi-agent work (spawning SIM/MAT subagents), with hooks for budget and spend caps.
- **Policy:** never offer claude.ai login inside the app; users bring their own install/API key (free tier) or use paid hosted credits (API-key based). Don't brand anything "Claude Code".
- **Scopes:** each agent gets edit scope (a part) and read scope (the assembly). Locked mates and out-of-scope parts require approval.
- **Safety:** irreversible tools (merge, delete, print, spend) always ask; agents can't reach the network except through tools [B: how strictly to sandbox].
- **Enforcement lives in the core service, not in agents** [D, from `research/harness/README.md`]. Non-Claude ACP agents bring their own loops, so scope, budget, stale-write rejection and approvals are checked inside the `mises` MCP server and Onshape adapter on every call. Claude hooks / `can_use_tool` are a second layer only.
- **Never auto-approve.** ACP `session/request_permission` and SDK `can_use_tool` both become approval cards. Each agent's policy (scope, caps, allowed tool classes) is frozen at session start; widening it is an approval that creates a new policy version.
- **ACP host must-haves:** `session/new` with `mcpServers: [mises]`; tool-call `failed` shown as failed; `session/cancel` with a hard timeout then kill; session resume across app restarts; teardown that deletes or flags orphaned `agent/<name>` branches and releases ledger reservations.
- **SDK orchestrator:** set `system_prompt` and `setting_sources` explicitly (unset = empty prompt; never load the user's own `~/.claude` config into the product); subagents get narrow `mcp__mises__*` tools and "dispatched by orchestrator only" descriptions.

### 7.4 MCP server `mises` (tool surface) [D, builder may refine]
| Group | Tools |
|---|---|
| onshape | `doc.open`, `features.list`, `variables.get`, `propose` (batched change on own branch), `verify`, `merge.request`, `budget.get` |
| mirror | `part.build`, `part.set_vars`, `measure`, `section`, `interference`, `render_views` (local renders), `fidelity` |
| sim | `env.list`, `run` (static/thermal/modal), `matrix`, `result.get` (with solver/mesh/inputs) |
| studies (paid) | `topology.run`, `generative.run`, `cfd.run`, `job.status` (long-running tasks) |
| print | `printers.list`, `slice`, `preview`, `send` (needs approval), `status` |
| materials | `filament.search`, `filament.get` |
| ui | `ask_user` (structured question), `highlight` (face/edge), `note` |

Tool names on the wire use underscores (`onshape_propose`, not `onshape.propose`) to fit MCP client naming limits [verify per ACP client]. Every tool has a safety class (`read` / `local_mutate` / `onshape_mutate` / `irreversible`; unclassified = irreversible) published as MCP read-only/destructive hints. Each call runs: validate → scope → ledger pre-check (estimate, lowest cap wins) → approval if irreversible → execute with an idempotency key (replays return the saved result, no new Onshape call) → record Step + LedgerEntry in one transaction. Errors are typed: `stale_version` (re-plan, never blind-retry), `budget_exhausted` (stop), `needs_approval` (wait), `transient` (back off). `propose` rejects feature/variable names that end in a number (§5.1). Large outputs (glTF, renders, feature JSON) return a file handle plus summary, never inline.

Every mutation returns `{ok, version, changes:{mass, volume, bbox}, warnings}` so agents check by numbers, not only pictures. Long jobs use MCP Tasks. Local HTTP plus a stdio wrapper (stdio is the transport every ACP agent supports). External agents (Claude Code, Codex, Cursor) can use the same server.

### 7.5 Simulator (guide) [H: guide]
- **Free tier, local:** gmsh meshing + **CalculiX** for static stress, thermal, modal [D].
- **Environments** = presets of temperature, loads, fixtures, vibration, UV/outdoor flags; user-defined environments allowed. Shipped presets: Desk, Outdoor, Bike mount, Car dashboard 80°C [D].
- **Materials:** isotropic models from filament data with conservative knock-down factors for FDM; show the caveat that printed parts are weaker across layers. Orientation-aware models are later work.
- **Outputs:** max von Mises, deflection, safety factor; heatmap; matrix of environment × material. Every result labeled "guide" with solver/mesh/inputs.
- **Live:** re-run on change with coarse mesh first, refine in background [B].

### 7.6 Studies (paid tier) [H]
- Cloud solvers: topology optimization, generative design (many variants, Pareto grid), CFD / wind tunnel (OpenFOAM-class) [D]. Fatigue later.
- Rebuild to editable features starts **rough or manual** [H]: e.g. agent traces the optimized shape as sketch + extrude/loft with the user's help.
- Ansys: not in v1; later "bring your own license" via PyAnsys [D].

### 7.7 Printing [H: Prusa + Bambu]
- **Prusa (first):** PrusaLink local REST API (API key) and optionally Prusa Connect; slicing with bundled **PrusaSlicer CLI** using the user's printer profile; upload G-code / bgcode; status polling.
- **Bambu (second):** LAN mode — MQTT over TLS for status/commands and FTPS for upload, with the printer's access code; recent Bambu firmware restricts third-party control unless LAN/Developer mode is enabled, so the UI walks users through that [verify against current Bambu firmware]. Slicing with **OrcaSlicer or Bambu Studio CLI**. Needs a tester who owns a Bambu printer.
- Later: OctoPrint, Klipper/Moonraker.
- **Always confirm before printing**; show material, time, grams, cost, and a slice preview.

### 7.8 Filament service
- Database of filaments: material, brand, heat deflection temp, glass transition, tensile strength, modulus, print temps, price per kg, store links, color.
- **Sources:** manufacturer technical data sheets and store feeds/APIs; respect site terms and robots rules; prefer official data [D]. Refresh daily into a shared cached dataset (cheap, one fetch serves all users) [D].
- Agents use it to pick materials that pass the environment matrix.

### 7.9 Sketch to part (M9)
- Input: strokes (pen/mouse) or photo. Vision model reads lines, numbers, notes.
- Output: constrained 2D sketch with dimensions; notes linked to assembly entities ("clamps here" → mate face); at most ~3 questions; then build in Onshape on a branch.

## 8. Data model [D]
- **Project**: Onshape document/workspace/element IDs, assembly link, settings.
- **PartMirror**: Onshape part ID, build123d script, variable map, fidelity metrics, last synced microversion.
- **AgentRun**: id, provider, model, scope, status, branch, budget used (Onshape calls, tokens, $), timestamps.
- **AgentPolicy**: run id, version, edit scope, read scope, caps, allowed tool classes (frozen per session; a widening creates a new version).
- **Step**: run id, time, kind (plan / tool call / diff / decision / error / onshape_call / sim_run / measure / verify), payload, evidence hash, optional prev hash, collapsed flag. Badges like "sim passed" or "fits" link to a server-written Step by id + hash; anything an agent only says in prose shows as an unverified claim.
- **Approval**: run id, action, exact proposal payload, payload hash, Onshape microversion it was computed against, deltas, environments passed, status (compare-and-set; first decision wins; a stale hash is rejected), expires at, decided by/at, edited payload. Hash and microversion are re-checked just before execution; a mismatch raises a fresh card.
- **Environment**: name, temperature, loads, fixtures, vibration, flags.
- **Material**: filament record + FDM knock-down.
- **SimResult**: geometry version, environment, material, solver, mesh, outputs, "guide" flag.
- **PrintJob**: printer, slicer profile, file, estimate, status.
- **LedgerEntry**: user, agent, endpoint, estimate, reservation, idempotency key, status code, counted?, time.
Stored locally in SQLite per user; nothing leaves the machine on the free tier except Onshape/printer traffic and the webhook relay.

## 9. Cross-platform packaging [D]
- **Tauri** bundles: `.dmg` (macOS, Apple Silicon + Intel), `.msi`/`.exe` (Windows x64, ARM later).
- **Python core** shipped as a bundled runtime (e.g. `uv`-managed Python or a frozen sidecar) with build123d/OCCT, gmsh, CalculiX binaries, PrusaSlicer/Orca CLIs — per-platform builds in CI.
- **Signing:** Apple Developer ID + notarization; Windows Authenticode certificate (cost noted in §11). Auto-update via Tauri updater.
- Secrets in macOS Keychain / Windows Credential Manager.
- Installer size budget [B] (CAD + solver binaries are large; consider downloading solvers on first use).

## 10. Security and safety
- Localhost-only core service with a per-session token; MCP server bound to localhost.
- Treat all Onshape text (document names, comments, feature names) and web filament data as **untrusted data**, never instructions to agents.
- Agent sandbox folders; no direct file system or network access outside tools [B: strictness].
- Printer: confirm every print; enforce temperature limits from material data; never send while printer reports an error.
- Budget hard stops for Onshape calls and paid credits.

## 11. Business model [H: cheap for all, paid for power]
- **Free:** core loop, local sim, printing, filament lookup, bring-your-own agent, one or two agents at a time [D]. Cost to us ≈ webhook relay bandwidth + filament dataset refresh.
- **Paid (subscription and/or credits)** [D]: cloud studies (topology, generative, CFD), more parallel agents, hosted agent credits, team features, priority relay. Prices [B].
- **Fixed costs to plan for:** Apple Developer ($99/yr), Windows code-signing certificate, small server for relay/accounts, payment processor fees.
- **Billing:** Stripe-class provider; license check in app; paid features gated server-side (cloud jobs) so they can't be unlocked locally.

## 12. Risks and open items
- **Onshape App Store allowance treatment** — must confirm with Onshape; decides how Mises connects at scale. Until then: user's own API keys.
- **Onshape partner/listing requirements and terms** for a commercial app — confirm.
- **Mirror fidelity** — approximations can mislead sims; mitigated by fidelity badge + verification on real geometry.
- **Sim trust** — guide only; FDM anisotropy not modeled in v1.
- **Bambu firmware restrictions** — may change; needs a Bambu tester.
- **Bundle size and solver licensing** — CalculiX (GPL) and OpenFOAM (GPL) must be shipped as separate executables, not linked, and their licenses honored [verify with a license review].
- **ACP v2 / MCP spec churn** — build on ACP v1 and current MCP; gate newer features.

## 13. Decision log
**[H] decided by the human:** product for others; Mac + Windows desktop app; cheap for everyone + paid tier for power features; core loop first; simulator is a guide; no paying for Onshape/Ansys/cloud by the developer; Prusa owned, Bambu supported; Onshape stays master with local iteration; A-style layout with C's sim split; greyscale + blue; blueprint aesthetic, square corners; roomy top bar; agent console for many agents; any agent; assembly focus; studies incl. topology, generative, CFD; sketch to part; filament lookup; no leaked code; no auto-numbered names in the feature tree, variables or diffs; harness baseline from `research/baseline/` accepted.
**[D] designer's calls to check:** name "Mises"; Tauri + TypeScript/Three.js + Python core; ACP v1 as default host + Agent SDK orchestrator; MCP tool surface; CalculiX/gmsh locally; cloud solvers for paid studies; hosted webhook relay; environment presets; filament data via official sources with a shared daily dataset; milestone order; free tier = 1–2 agents; Ansys later as bring-your-own.
**[B] left to the builder:** viewer internals, meshing parameters, live re-sim strategy, drift threshold, sandbox strictness, Drawing/Print tab designs, prices, installer size strategy, tests and internal structure.
