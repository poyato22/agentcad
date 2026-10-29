# Builds, Assembly & Harness Implementation Plan (Plan 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn Mises into a real working tool for multi-part builds. Every point of the user's 2026-10-29 request has a task (map at the end):

- an agent manager where you can delete and clean up agents;
- several builds open at once, each with a part tree of individual parts;
- assemblies with joints that move;
- per-part color;
- live parameter edits with regenerate;
- the user editing the model alongside the agent;
- drag-reorder of the parts list;
- an Onshape document browser with a live budget meter;
- an online parts directory that turns a part page into sizing data the agents use;
- clear "working / thinking" indicators;
- short, richly formatted agent replies;
- a much better sketch tool;
- orchestrators that call worker agents;
- a system check that finds and installs missing tools (PrusaSlicer, CalculiX, Ollama);
- a loading screen and a cube-assembling welcome intro;
- working select / copy / paste;
- a requirements-driven engineering loop, so "build me a drone" means questions, specs, variants, simulation and iteration instead of a flat X;
- the whole harness working with Claude Code underneath.

**Spec:** `docs/design/2026-10-24-mises-design.md`. Earlier plans: `docs/superpowers/plans/2026-10-2{4,5,8}-*.md`. Rulings so far: `docs/build-rulings.md`.

## Global Constraints

- Every constraint from Plans 1–4 still binds:
  - the Gate on every tool call;
  - never auto-approve; approvals only by a trusted action on the card;
  - secrets only in the Keychain and never echoed;
  - untrusted text (web pages, model output, file contents) is data, never instructions — wrap it with the nonce delimiters already used for sketch context;
  - no auto-numbered names;
  - 127.0.0.1 plus the admin token; the MCP-only token for MCP;
  - the Claude Code compliance rules: Mises never reads or stores Claude credentials, sign-in only via `claude auth login`, and no feature is named "Claude Code".
- **Deleting is safe:**
  - Deleting a run refuses while it is running or has a pending approval. Cancel first; the UI offers "Cancel and delete".
  - Deleting a run removes its steps, approvals and workdir from the Mises home only. It never touches Onshape and never deletes parts other runs reference.
  - Deleting a build (project) moves it to Trash (`trashed_at`). It is purged only by an explicit "Empty trash".
- **Installs need a click:** the core never installs software on its own or at an agent's request. `POST /system/install/{dep}` is admin-only, called only from a button the user pressed. It runs a fixed command from an allowlist (no arguments from the request body), streams output, and is not a tool (no agent can reach it).
- **Concise agents:** every built-in agent prompt and the Claude Code appended system prompt carry the same response rules (Task C3 owns the text):
  - at most ~6 short lines or one small table per reply unless the user asks for detail;
  - lead with the result;
  - use Mises markup (below) for numbers, status and part names;
  - no restating the request; no filler.
- **Mises markup** (rendered by Task A2; emitted by the agents) is standard Markdown (GFM tables, bold, code, lists) plus:
  - `> [!pass]` / `> [!warn]` / `> [!fail]` / `> [!note]` callouts, colored with the status tokens;
  - `[[part:<part_id>|Label]]`, which renders as a clickable chip that selects the part;
  - numbers with units (`3.2 mm`, `48 g`, `SF 2.4`, `120 MPa`), auto-highlighted in a value tint;
  - `==text==` for highlight.

  Raw HTML is never rendered; it is escaped. Links open in the system browser only after showing the domain.
- **Physics honesty:** impact and crash loads are approximations (equivalent static load = m·g·k, with k stated), labeled "estimate" next to the existing "guide only" label.
- Branch base: `mac-app` at e283338. Each task runs in its own worktree on its own branch: `p5-c1` … `p5-a7`.

## Shared contracts (core ⇄ app; app tasks build against the fake core using exactly these)

**Builds (projects)**
- `GET /projects` returns
  `[{id, name, created_at, updated_at, trashed_at|null, part_count, run_count, onshape: {did, wid}|null}]`.
- `POST /projects {name}` creates a build.
- `PATCH /projects/{id} {name?}` renames it.
- `DELETE /projects/{id}` moves it to Trash; `POST /projects/{id}/restore` brings it back; `POST /projects/trash/empty` purges.
- A "Default" build is created once by migration, and every existing part and run is assigned to it.
- Runs and parts carry `project_id`. `POST /runs` accepts `project_id`; when it's missing, the active build is used.
- The profile gains `active_project_id` and `open_project_ids` (tabs).

**Part tree**
- `GET /parts?project_id` returns
  `[{id, label, project_id, parent_id|null, order, color: "#rrggbb"|null, kind: "part"|"group"|"catalog", vars, version, ...existing fields}]`.
- `PATCH /parts/{id} {label?, color?, parent_id?}` (rejects cycles).
- `POST /parts/reorder {project_id, parent_id|null, ids: [..]}` sets the order.
- `POST /parts/{id}/vars {vars: {name: value}, base_version}`:
  - This is the user's edit. It runs through the Gate as a UI call on the project's ui run, regenerates and bumps `version`.
  - A `base_version` mismatch returns 409 `{current_version}`.
- `GET /parts/{id}/source` returns the build123d script plus `vars` schema `[{name, value, min?, max?, unit?, doc?}]`.
- `PUT /parts/{id}/source {source, base_version}` is the user's code edit (UI call, sandboxed regenerate, 409 on a stale version).
- `POST /parts/{id}/ops {op: "hole"|"fillet"|"chamfer"|"shell"|"extrude", target: {face_id|edge_id}, params}`:
  - Quick feature ops from face or edge picks.
  - Implemented as a script transform: each op appends a recorded `# mises-op` block to the source, then regenerates.
  - The user's ops and the agent's edits share one source of truth.
- Event `part_changed {part_id, version, by: "user"|"agent", run_id|null, summary}`.
- The agent's context (`mises_context_get`) includes the last 10 user edits as data.
- An agent tool call on a stale version gets a structured conflict result telling it to re-read; it is never silently overwritten.

**Assembly**
- `GET /projects/{id}/assembly` returns:
  - `instances: [{id, part_id, name, transform: 4x4 row-major, fixed: bool}]`
  - `joints: [{id, kind: "fixed"|"revolute"|"slider"|"cylindrical"|"ball", a: {instance_id, frame}, b: {instance_id, frame}, value, limits: [lo, hi]|null}]`
  - `frame` is `{origin: [x,y,z], axis: [x,y,z]}` in part coordinates, or `{face_id}`, resolved to a face center and normal.
- Mutations:
  - `POST/PATCH/DELETE /projects/{id}/assembly/instances[/{iid}]`
  - `POST/PATCH/DELETE /projects/{id}/assembly/joints[/{jid}]`
  - `POST /projects/{id}/assembly/drive {joint_id, value}` returns the solved transforms.
  - `GET /projects/{id}/assembly/interference` returns `[{a, b, volume_mm3}]`.
  - `GET /projects/{id}/assembly.glb`
- Event `assembly_changed {project_id}`.

**Runs and agents management**
- `GET /runs` gains these fields:
  - `project_id`, `parent_run_id`, `title` (user-renamable), `pinned`, `archived`
  - `last_activity_at`, `cost {usd|null, tokens}`
  - filters `?project_id&archived=0|1`
- `PATCH /runs/{id} {title?, pinned?, archived?}`
- `DELETE /runs/{id}` returns 409 `{reason: "running"|"pending_approval"}` per the delete rule.
- `POST /runs/cleanup {project_id?, older_than_days?, status_in: ["done","failed","cancelled"], dry_run}` returns `{ids, count}`.
- `GET /runs/tree?project_id` returns a nested orchestrator → worker tree.
- Custom agent definitions:
  - `GET /agents` adds `{builtin: bool, hidden: bool}`.
  - `POST /agents {name, description, model, tools, body}` writes `~/…/Mises/agents/<name>.md`.
  - `PUT /agents/{name}` and `DELETE /agents/{name}` work on custom agents only. Built-in agents can only be hidden, via the profile `hidden_agents`.

**Activity**
- Event `agent_activity {run_id, state, detail, since, tokens_out?}`, where `state` is one of:
  - `"queued"`, `"starting"`, `"thinking"`, `"writing"`, `"tool"`
  - `"waiting_approval"`, `"waiting_user"`, `"idle"`, `"done"`
- It is emitted on every transition and throttled to ≥ 4/s while writing.
- `detail` is a short human label: `"measuring arm"`, `"FEM study · 2/6"`.
- Thinking text, when the provider streams it, arrives as `agent_thinking {run_id, text_delta}`. It is display-only and stored truncated.
- `GET /runs/{id}` includes `activity`.

**Specs and studies (engineering loop)**
- `GET/PUT /projects/{id}/spec` holds:
  - `goal`
  - `requirements: [{id, kind: "max_mass_g"|"min_safety_factor"|"max_deflection_mm"|"envelope_mm"|"mount_pattern"|"printable"|"custom", target, load_case_id?, priority: 1-5, source: "user"|"agent"}]`
  - `load_cases: [{id, name, fix, load, newtons, direction, note, estimate: bool}]`
  - `answers` (the interview Q&A)
- `GET /projects/{id}/studies` returns
  `[{id, created_at, variables: [{name, lo, hi}], objective, status, iterations: [{n, vars, metrics: {mass_g, sf_by_case, max_disp_mm, bbox}, feasible, pareto}], best_id}]`.
- Events `study_progress {study_id, n, total}` and `spec_changed`.

**Onshape**
- `GET /onshape/documents?q&owner=me&offset&limit` returns
  `[{did, name, modified_at, owner, thumbnail_url|null, default_wid}]`.
- `GET /onshape/documents/{did}/elements?wid` returns Part Studios and Assemblies.
- `GET /onshape/thumbnail?href=` is a proxied, cached, size-capped image.
- `POST /onshape/import {did, wid, eid, project_id}` mirrors the element into a build.
- `GET /onshape/budget` returns `{used_year, limit_year, used_run, warn_at, reset_at}`.
- Event `onshape_budget` follows every real Onshape request.

**Parts directory (catalog)**
- `GET /catalog/search?q&category` and `GET /catalog/items/{id}`. An item is
  `{id, name, category, source: "builtin"|"url", url|null, vendor|null, specs: [{key, value, unit}], dims: {…}, mount: {pattern, hole_d, spacing_mm}|null, geometry: "builtin"|"envelope"|null}`.
- `POST /catalog/import {url}` returns an item:
  - The core fetches the page: https only, ≤ 2 MB, 10 s, no cookies, and the redirect target is re-checked.
  - It extracts specs from tables, JSON-LD and "key: value" lines, normalizes units to mm/g/N, and keeps the raw text as untrusted data.
- `POST /catalog/items/{id}/insert {project_id}` adds a `kind: "catalog"` part whose geometry is a parametric envelope (or bd_warehouse geometry for fasteners and bearings).
- `DELETE /catalog/items/{id}` (url items only).

**System check**
- `GET /system/check` returns `[{id, name, status: "ok"|"missing"|"outdated"|"error"|"optional_missing", version|null, path|null, detail, fix: {kind: "install"|"open_url"|"setting", label, dep?}|null}]`. It covers:
  - core Python and venv
  - build123d / OCCT
  - gmsh
  - CalculiX
  - PrusaSlicer (paths `/Applications`, `~/Applications`, the bundle id via `mdfind kMDItemCFBundleIdentifier == com.prusa3d.slic3r`)
  - Homebrew
  - conda
  - Ollama (installed / running)
  - the Claude Code CLI (installed / signed in, via `claude auth status` only)
  - Onshape keys (present / valid, reusing `/onshape/test` with no budget hit beyond 1 call)
  - each printer (reachable)
  - free disk space
  - Keychain access
- `POST /system/install/{dep}` with `dep` in the allowlist:
  - `prusaslicer` runs `brew install --cask prusaslicer`
  - `calculix` runs `conda install -y -c conda-forge calculix`
  - `ollama` runs `brew install ollama`
  - It streams `system_install {dep, line, done, ok}`.
  - If brew is missing, it returns `fix: open_url https://brew.sh`.

## Review Focus

1. Deleting, cleaning up or trashing can never remove something running, awaiting approval, or referenced by another build. Onshape is never touched.
2. User edits and agent edits can't silently clobber each other (versions, 409s, conflict results). The agent always sees the user's edits as data.
3. Install endpoints can't be reached by an agent or with request-controlled arguments.
4. Catalog import treats the page as untrusted (no instructions followed, delimiters intact, size and time caps, https only, redirects re-checked).
5. Worker agents spawned by an orchestrator, Claude Code included, stay inside the parent's tools, parallel limit and budget. The Gate still sees every call.
6. Every new interaction works by keyboard. Drag-reorder has an accessible alternative (Move up / Move down / Alt+↑↓).

---

### Task C1: Builds, part tree, runs and agent management (core)

**Files:**
- `core/src/mises/store.py` (migrations: `projects`; columns `project_id`, `parent_id`, `ord`, `color`, `kind`, `version`; run columns `title`, `pinned`, `archived`, `parent_run_id`, `last_activity_at`)
- new `core/src/mises/projects.py`
- `core/src/mises/server/app.py` (runs routes), new `core/src/mises/server/projects_api.py`
- `core/src/mises/agent/definitions.py` (custom CRUD, hidden)
- `core/src/mises/profile.py` (`active_project_id`, `open_project_ids`, `hidden_agents`)
- tests

**Scope:** implement the Builds, Part tree (except `/source`, `/ops` and `/vars`, which are owned by C6) and Runs and agents management contracts exactly as written.

**Tests:**
- Migration of an existing DB keeps all rows and puts them in Default.
- Delete is refused while running or with a pending approval.
- Cleanup with `dry_run`.
- The reorder is stable, and cycles are rejected.
- Trash, restore, and empty trash.
- A custom agent's create / update / delete never writes outside the agents dir (name validation).
- Built-ins can be hidden but not deleted.

### Task C2: Assembly (core)

**Files:**
- new `core/src/mises/assembly/{__init__,model,solve,tools}.py`
- `core/src/mises/server/assembly_api.py`
- `core/src/mises/cad/worker.py` (interference and assembly GLB, in the sandboxed worker)
- tests

**Scope:**
- The Assembly contract.
- Kinematics: a tree solve from fixed instances through joints; loops are rejected with a clear error.
- Revolute rotates about the axis by value (degrees). Slider translates by value (mm). Cylindrical takes both. Ball takes a 3-vector.
- Limits are clamped.
- Interference uses the OCCT common volume, over a 0.01 mm³ threshold.
- Agent tools:
  - `assembly_get` (read)
  - `assembly_add_instance`, `assembly_add_joint`, `assembly_drive`, `assembly_remove` (local_mutate)
  - `assembly_check` (read, interference plus the degrees of freedom left)

**Tests:**
- A hinge (two plates, revolute) at 0°/90° gives the expected transforms.
- The slider respects its limits.
- An interference fixture is detected.
- Loop detection.
- The GLB has one node per instance.

### Task C3: Engineering loop — requirements interview, specs, design studies, concise replies (core)

**Files:**
- new `core/src/mises/engineering/{spec,interview,study,loads,tools}.py`
- `core/src/mises/server/spec_api.py`
- `core/src/mises/agent/builtin/{engineer.md,designer.md,planner.md,verifier.md,sim.md}`
- `core/src/mises/agent/prompts.py` (shared response rules and Mises markup)
- tests

**Scope:**
- New default agent **engineer**, used for any "build/design me a <system>" request:
  1. **Interview** via `ui_ask_user` with structured choices. Ask at most 6 questions, batched 2–3 at a time, each with options and a free-text fallback.
     - Example for a drone: use, size class (3"/5"/7" props), motor and prop choice from the catalog, battery, flight controller mount (20×20 or 30.5×30.5 M3), printer and material, and ranking of priorities (weight / impact strength / stiffness / print time).
  2. **Write the spec** (`spec_set`) with requirements and load cases.
     - Crash and impact cases use `loads.impact_equivalent(mass_kg, drop_m, k)`, which returns newtons with `estimate: true` and the formula noted.
     - Motor thrust cases come from the catalog specs.
  3. **Propose 2–4 concept topologies** (for a drone: X, H, stretched-X, deadcat; arm sections: solid, ribbed, I-beam, hollow), each a parametric part script with named variables.
  4. **Run a design study** with `study_run(part_id, variables[{name, lo, hi}], objective: "min_mass", constraints from spec, budget: {evals ≤ 24, minutes ≤ 20})`.
     - Coarse Latin-hypercube sampling, then coordinate refinement around the best feasible point.
     - Each evaluation regenerates the part, measures mass, and runs `sim_static_run` per load case (coarse mesh in the loop, fine mesh to confirm the winner).
     - Evaluations run in parallel under the ParallelLimiter and the local memory budget.
  5. **Report** a compact Pareto table and the chosen variant, with every requirement shown pass / warn / fail.
  6. **Iterate** on user feedback.
- Tools:
  - `spec_get`, `spec_set` (local_mutate)
  - `spec_check(part_id)` (read; evaluates the requirements)
  - `study_run` (local_mutate, long, cancellable, emits `study_progress`)
  - `study_get` (read)
  - `loads_impact` (read)
- Response rules: one shared `RESPONSE_RULES` string (see Global Constraints) appended to every built-in agent and exported for C4's Claude Code prompt.

**Tests:**
- The interview returns ≤ 6 questions with options.
- The spec validates.
- The impact formula is used and flagged as an estimate.
- On a fake evaluator with a known optimum, the study finds a feasible minimum within 10% and respects the budget and cancel.
- The Pareto flags are correct.
- Every built-in prompt contains `RESPONSE_RULES`.
- A recorded drone transcript fixture (scripted fake model) produces a spec with ≥ 3 requirements and ≥ 2 load cases.

### Task C4: Claude Code underneath everything, orchestrators calling workers, activity events (core)

**Files:**
- `core/src/mises/claude_code/{session,prompts}.py`
- `core/src/mises/server/mcp_server.py` (INTERNAL_ONLY review)
- `core/src/mises/orchestrator/**`
- `core/src/mises/agent/loop.py`, `core/src/mises/runtime.py`, `core/src/mises/providers/*` (thinking streams)
- `core/src/mises/events.py`
- tests

**Scope:**
- **Every Mises tool reachable from Claude Code:**
  - Expose `agent_spawn` and `orchestrator_run` over MCP, so a Claude Code run can delegate to Mises workers on any model (e.g. a local qwen designer).
  - Keep `orchestrator_submit_plan` internal.
  - Spawned children inherit the parent's project, tools ∩ requested, depth limit, parallel limit and budget (Review Focus 5).
  - Children appear as runs with `parent_run_id` (C1 column).
  - `agents_json` gives Claude Code one subagent per Mises agent definition, including custom and engineer, with that agent's prompt plus `RESPONSE_RULES`.
  - The new C1/C2/C3/C5/C6 tools are picked up automatically from the registry. Add a test that diffs the registry against the MCP list, so nothing new is ever forgotten.
- **Native orchestrator:**
  - The orchestrator can be the parent of mixed workers (Claude Code workers plus API or local workers).
  - `agent_spawn` gains `wait: false` with `agent_wait(run_ids)` / `agent_result(run_id)`, so it can fan out in parallel.
- **Activity:**
  - Emit `agent_activity` and `agent_thinking` (contract) from the native loop (provider stream callbacks: Anthropic thinking deltas, OpenAI `reasoning_content`, Ollama `thinking`) and from Claude Code stream-json (`thinking` / `text` / `tool_use` blocks).
  - Map tool names to human `detail` labels.
- **Concise:** the Claude Code appended system prompt includes `RESPONSE_RULES` (import from C3's module; if C3 isn't merged yet, define the constant in `agent/prompts.py` exactly as C3's brief says and let the merge reconcile).

**Tests:**
- The MCP tool list equals the registry minus the documented internal set.
- A Claude Code fake stream yields activity transitions thinking → tool → writing → done.
- A child spawned from a Claude Code run is limited to the parent's tools and the parallel limit.
- `wait: false` fan-out of 3 workers returns all results.
- Thinking deltas are throttled and truncated in storage.

### Task C5: Onshape browser, live budget, parts directory (core)

**Files:**
- `core/src/mises/onshape/{client,tools,browse}.py`
- `core/src/mises/server/onshape_api.py`
- new `core/src/mises/catalog/{__init__,builtin,importer,geometry,tools}.py` plus `core/src/mises/catalog/data/*.json`
- `core/src/mises/server/catalog_api.py`
- tests

**Scope:** implement the Onshape and Parts directory contracts.

- **Onshape:**
  - Documents list and search, elements, thumbnails (proxied, cached under the Mises home, ≤ 512 KB), and import into a build.
  - Every real request emits `onshape_budget`.
  - Agent tools `onshape_docs_search` and `onshape_doc_elements` (read; they count against the budget estimate).
- **Built-in catalog (JSON data, cited sources in a `sources` field):**
  - ISO metric screws, nuts and washers M2–M8
  - bearings 6xx and 6xxx
  - brushless motors (1404, 2207, 2306, 2806.5 classes, with mount patterns 9×9 / 12×12 / 16×16 / 19×19 M2/M3, mass, max thrust ranges labeled "typical")
  - props 3"/5"/7"
  - LiPo 4S/6S packs (dims and mass ranges)
  - flight controller and ESC stacks (20×20 / 25.5×25.5 / 30.5×30.5)
  - servos SG90 and MG996R
  - NEMA17
  - 2020 / 2040 extrusion
  - Raspberry Pi and Arduino board outlines
- **Geometry:**
  - bd_warehouse for fasteners and bearings, if the package installs. Verify that; fall back to simple envelopes.
  - Parametric envelopes for everything else.
- **URL import:** follows the contract. Unit normalization is tested. The page text is wrapped as untrusted data.
- Agent tools: `catalog_search`, `catalog_get` (read), `catalog_import_url` (network_read; the Gate shows the domain), `catalog_insert` (local_mutate).

**Tests:**
- Fake Onshape server: docs list paging, thumbnail cap, a budget event per request.
- Import fixture HTML pages (a table spec sheet, JSON-LD, a prompt-injection page whose text stays data).
- Redirect to http refused.
- Size cap.
- Builtin search.
- Insert creates a catalog part with the right bbox.

### Task C6: Live editing alongside the agent, colors, system check and installers (core)

**Files:**
- `core/src/mises/cad/{tools,runner,worker}.py` (source get/put, ops transforms, versioned regenerate)
- new `core/src/mises/edits.py`
- `core/src/mises/server/edit_api.py`
- new `core/src/mises/system_check.py`, `core/src/mises/server/system_api.py`
- `core/src/mises/print/slicer.py` (detection paths)
- `core/src/mises/context_tools.py` (recent user edits)
- tests

**Scope:**
- The Part tree `/vars`, `/source` and `/ops` contracts, plus versions, 409s, stale-version conflict results for agent tools, and the `part_changed` event.
- The ops library:
  - hole: through or depth, at a face point, diameter, optional counterbore
  - fillet and chamfer: on edge ids
  - shell: open faces, thickness
  - extrude: from a sketch id onto a face
- The system check and install contracts.
- PrusaSlicer detection everywhere it can live, including the bundle-id `mdfind`.

**Tests:**
- Simultaneous user and agent edits produce exactly one 409 or conflict.
- An op appends a `# mises-op` block, and the regenerated volume changes as expected (a hole removes π r² h ± 2%).
- Source edits run sandboxed (a network or file escape fails).
- The check lists every dependency with a fake environment.
- The install allowlist rejects an unknown dep, and the body can't inject arguments.
- Install output streams.
- Brew missing gives the open_url fix.

### Task A1: Shell — copy/paste, loading screen, welcome intro, system check screen, global activity (app)

**Files:**
- `app/src-tauri/src/lib.rs` (a macOS app menu with an Edit submenu built from PredefinedMenuItems: undo, redo, cut, copy, paste, select all. **This is why copy and paste don't work today:** macOS webviews need the Edit menu to route ⌘C/⌘V/⌘A.)
- `app/src/ui/boot/{Loading.tsx,Intro.tsx,intro.ts}`
- `app/src/ui/system/SystemCheck.tsx`
- `app/src/ui/topbar/**` (global activity indicator and Onshape budget slot)
- `app/src/App.tsx`
- tests

**Scope:**
- **Loading screen:**
  - Shown while the core starts: cube mark, a status line (Starting core / Loading models / Checking tools), no spinner text filler.
  - Times out into a clear error with Retry and Show log.
- **Welcome intro** (first launch, plus "Replay intro" in Profile):
  - A 2.5–3.5 s Three.js sequence. A few hundred small cubes fly in from scattered positions and assemble into the plain grey Mises cube with eased, staggered timing and a soft light sweep. Then "Welcome to Mises" types in under it in Iosevka. Then fade to setup.
  - Skippable by click or any key.
  - Respects `prefers-reduced-motion` (it becomes a 400 ms fade).
  - Both themes.
- **System check screen:** shown after the intro on first run and from Profile → System. It renders `/system/check` rows with status lights and fix buttons. Install buttons stream output in an expandable log and re-check when done.
- **Text inputs:** everywhere text is selectable, and context-menu copy/paste works. The composer, conversation and logs allow `user-select: text`.
- **Top bar:** an activity pill shows the count of working agents with an animated state ("2 working · 1 thinking"); clicking it opens the agent manager. There is a slot for A6's budget meter.

**Tests:**
- Unit tests: the menu builder includes the Edit items (cargo test).
- The intro is skippable, and reduced motion is respected.
- The loading timeout shows the error.
- System check rows and the fix flow against the fake core.
- e2e: select all and copy in the composer via keyboard, and select text in the thread.

### Task A2: Agent manager, activity indicators, Mises markup rendering (app)

**Files:**
- `app/src/ui/agents/**` (new manager view, reached from the activity pill, the run switcher, and ⌘6, or a slide-over on every page)
- `app/src/ui/console/**`
- `app/src/ui/markup/{render.tsx,parse.ts,markup.css}`
- tests

**Scope:**
- **Agent manager:**
  - Runs grouped by build, shown as an orchestrator → worker tree from `/runs/tree`.
  - Each row shows status, activity, model, cost, last activity and title.
  - Rename, pin and archive.
  - Delete, plus Cancel and delete. The 409 reasons are shown in plain words.
  - Multi-select bulk delete.
  - "Clean up" dialog (dry-run count first).
  - An Agents tab: custom agent definitions with create, edit and delete, built-ins with hide, and model and tools pickers.
- **Activity indicators:**
  - The thread shows a live status line per run: an animated dot or shimmer for thinking, the tool name with elapsed seconds for tool, a streaming caret for writing, and a highlighted card state for waiting.
  - An optional "Show thinking" disclosure renders `agent_thinking` text in faint type.
  - Run switcher dots animate by state.
- **Markup:** render Mises markup (contract) in every agent message.
  - The part chip selects the part.
  - Values are tinted.
  - Callouts are colored.
  - Tables are styled.
  - HTML is escaped.
  - Long replies over ~12 lines collapse with "Show more".
  - Copy button per message.

**Tests:**
- Parser unit tests for every markup feature, plus an XSS corpus.
- Tree rendering.
- Delete 409 handling.
- Cleanup dry-run.
- Activity state transitions from fixtures.
- Approval and failed-call visibility unchanged (Plan 4 Review Focus 3).

### Task A3: Builds, part tree, colors, parameters, editing with the agent (app)

**Files:**
- `app/src/ui/left/**`
- `app/src/ui/topbar/Breadcrumb.tsx` (build tabs)
- `app/src/ui/viewer/**` (color, pick, ops)
- `app/src/ui/editor/{SourceEditor.tsx,ops.tsx}`
- tests

**Scope:**
- **Build tabs:** next to the breadcrumb. Open, switch and close several builds, each with its own parts, runs and assembly. New build, rename, trash and restore.
- **Part tree** (top left):
  - Nested parts and groups.
  - Drag to reorder and to re-parent, with drop indicators.
  - Alt+↑/↓ and a Move menu as the keyboard alternative.
  - Color swatch per part: a palette of 12 plus a custom hex picker, applied in the viewer.
  - Visibility toggle.
- **Variables panel:** sliders and number fields per var with min, max and unit, live-debounced regenerate (`/vars` with `base_version`). A 409 shows "Agent changed this part — reload / keep mine".
- **Edit alongside:**
  - Click a face or edge in the viewer to get an inline ops menu (hole, fillet, chamfer, shell) with a numeric field.
  - A "Code" tab with a source editor (CodeMirror 6, Python mode), Save, and regenerate.
  - User edits appear in the thread as "You · changed wall 3 → 4 mm" lines, and agent edits flash the part in the tree.

**Tests:**
- Drag reorder (mouse and keyboard) calls `/parts/reorder` with the right ids.
- Re-parent with cycle prevention in the UI.
- The color applies to the mesh material.
- The vars debounce, and the 409 UI.
- The ops menu calls `/ops` with the face id.
- Source save and conflict handling.

### Task A4: Assembly mode (app)

**Files:**
- `app/src/ui/assembly/**`
- a viewer integration (Assembly toggle on the Design page, or a sixth tab "Assembly" with ⌘6 shifting the manager shortcut to ⌘7 — choose the tab and record the ruling)
- tests

**Scope:**
- Instance list and add-part.
- Drag parts in 3D with a transform gizmo (three TransformControls; snapping 1 mm / 15°).
- Joint creation by picking two faces, then choosing a kind and limits.
- A joint slider drives `/assembly/drive`; "Animate" sweeps a joint between its limits.
- Interference highlighting in red with the volume.
- Colors from the parts.
- Agent changes arrive via `assembly_changed` and animate in.

**Tests:** the drive call on slider input, joint creation flow, the interference overlay, gizmo snapping math (unit).

### Task A5: Sketch v2 — guides, snapping, constraints, dimensions (app + small core)

**Files:**
- `app/src/ui/pages/sketch/**`
- `core/src/mises/sketch.py` (closed-profile extraction, constraints in the JSON)
- tests

**Scope:**
- **Grid and scale:** a mm grid with adaptive spacing, rulers, and an origin with axes.
- **Snapping and inference lines:** grid, endpoints, midpoints, centers, intersections, and horizontal / vertical / perpendicular / parallel / tangent guides drawn as dashed blue inference lines with a small label, SketchUp-style.
- **Tools:** line, polyline, rectangle (2-point and center), circle (center-radius), arc (3-point), slot, and fillet corner.
- **Precise input:** type a length or angle while drawing (`40` Enter, `40,20`, `<30`).
- **Editing:**
  - Select, move and drag vertices.
  - Delete.
  - Mirror about a construction line.
  - Offset.
  - Construction-line toggle.
  - Undo and redo.
- **Constraints:** horizontal, vertical, equal, coincident, and dimension (driving) badges shown on the geometry, with a lightweight iterative solver for these.
- **Closed profiles:** detected and shaded.
- **Build:** an "Extrude N mm" preview in 3D, and "Build part" sends profiles, dimensions and constraints as data.
- **Templates:** plate with holes, bracket (L), and flange.
- A hint bar at the bottom shows the current tool's next step in ≤ 8 words.
- Core: `sketch.py` validates and exports closed profiles (mm polylines and arcs) for the agent context. Keep the caps.

**Tests:**
- Snapping and inference unit tests (each guide).
- The typed-length parser.
- Constraint solve for a rectangle with equal and dimension constraints.
- Closed-profile detection.
- e2e: draw a 40×20 rectangle by typed dims, add a Ø4 hole, build.

### Task A6: Onshape browser, budget meter, parts directory pages (app)

**Files:**
- `app/src/ui/pages/onshape/**`, `app/src/ui/pages/directory/**`
- router entries
- `app/src/ui/topbar/BudgetMeter.tsx`
- tests

**Scope:**
- **Onshape page:**
  - Not connected: a connect card.
  - Connected: a grid of documents with thumbnails, search, and "Open in Onshape" (system browser). Opening a document shows its elements; "Import to build" works from there.
- **Budget meter** (top bar):
  - Shows used / limit for the year, a run delta, and reset date on hover.
  - Turns amber at warn and red at the limit.
  - Updates live from `onshape_budget`. ★ This answers "does it update as I work": yes.
- **Directory page:**
  - Category sidebar, search, item cards with key specs, and a mount-pattern diagram.
  - "Insert into build".
  - "Use with agent" adds the item to the composer as a context chip that the agent sees via `catalog_get`.
  - "Import from link": paste a URL, see the extracted specs for review and edit, then save.
  - "Open online" opens the source URL in the system browser.

**Tests:**
- Grid and paging, import flow, budget meter thresholds and live update.
- Import review: edits are saved; injected page text is shown as plain text.

### Task A7: Specs and studies UI (app)

**Files:** `app/src/ui/spec/**` (a panel in the console and a section on the Studies page), tests.

**Scope:**
- **Interview cards:** batched questions with option chips and free text.
- **Spec card:** a requirements table with target and current value, pass / warn / fail, priority dots, editable by the user (PUT spec), and load cases with an "estimate" tag.
- **Study view:**
  - Live progress.
  - An iterations table, sortable.
  - A mass-vs-safety-factor scatter with the Pareto front highlighted (follow the dataviz skill).
  - Click a point to preview that variant.
  - "Use this variant".
- **Studies page:** a design-study history per build next to stress studies.

**Tests:** interview answers posted, spec edit round-trip, scatter Pareto highlighting, variant select.

### Task C7: More study types and full study control (core)

Added 2026-10-29 at the user's request: "more control over studies and add more studies like a wind aerodynamic study".

**Files:**
- `core/src/mises/sim/**`: new `studies.py` (study-type registry), `modal.py`, `buckling.py`, `thermal.py`, and `cfd/{__init__,solver,setup,results}.py`
- `core/src/mises/server/sim_api.py`
- `agent/builtin/sim.md`
- tests

**Study types**, every one reporting solver, mesh, settings, error band or convergence, "guide only", and `fidelity_class`:

| Type | Solver | Result |
|---|---|---|
| static (existing) | CalculiX | stress, displacement, safety factor |
| modal | CalculiX `*FREQUENCY` | first N natural frequencies and mode shapes; flags modes inside a user-given excitation band, e.g. motor RPM/60 × blade count for drones |
| buckling | CalculiX `*BUCKLE` | load multipliers |
| thermal (steady-state) | CalculiX `*HEAT TRANSFER` | temperature field from fixed temperatures, heat flux or convection film coefficients |
| drop/impact | static equivalent from `loads.impact_equivalent` (C3), plus an optional explicit-dynamics ccx `*DYNAMIC` run if feasible within timeout | labeled "estimate" |
| wind/aero (CFD) | see below | drag force and coefficient Cd, lift force and coefficient Cl, moments, pressure on the surface (GLB colored by Cp), velocity slices (PNG or GLB plane), and a convergence history |

**Wind/aero (CFD) inputs:**
- wind speed (m/s), direction vector, air density and viscosity (defaults: sea level at 15 °C)
- reference area (auto-computed from the frontal projection, or set by the user)
- domain size multiplier
- mesh refinement level
- turbulence model where the solver supports it
- iteration cap and timeout
- yaw sweep option: a list of angles, giving a Cd-vs-angle table

**Wind/aero solver choice:**
1. Research and verify what actually runs on macOS arm64 (WebFetch the docs and cite them in the report). Candidates:
   - SU2 (LGPL; check conda-forge `su2` for osx-arm64)
   - OpenFOAM (only via a container; detect Docker or OrbStack, and don't require it)
   - a built-in fallback
2. Run the chosen external solver as a separate process (same GPL/LGPL rule as CalculiX).
3. Detect it through C6's system check (add a row and an install allowlist entry if installable via conda).
4. **The built-in fallback must always work:**
   - A numpy D3Q19 or D3Q27 lattice-Boltzmann solver on a voxelized part (a coarse grid sized from the bbox, capped by a memory and time budget). It computes drag and lift by the momentum-exchange method and is labeled "low fidelity".
   - If that's infeasible in the time budget, an empirical estimate from the frontal area and a shape-class Cd table, labeled "estimate".
5. Validate against a sphere (Cd ≈ 0.47 at Re ~1e4–1e5, within 25% for the built-in solver) and a flat plate normal to the flow (Cd ≈ 1.17–1.28). Record the measured error in the report.

**Control over every study (contract additions):**
- `POST /sims` takes `{type, part_id | project_id (assembly), settings}`.
- `GET /sims/types` returns each type with a JSON schema of its settings (defaults, ranges, units, docs), so the UI renders forms generically.
- Settings include:
  - **mesh:** element size or auto, local refinement on picked faces, second-order elements on/off
  - **materials:** a custom material editor (E, ν, density, yield, thermal conductivity, expansion) saved to the profile; FDM settings (infill %, wall count, layer orientation → knock-down factors, documented as approximate)
  - **multiple load cases per study:** force, pressure, moment, gravity or acceleration, bolt-hole bearing, remote force at a point
  - **boundary conditions:** fixed, pinned (cylindrical), roller or slider, symmetry plane
  - **face picks by face_id**
  - **convergence study:** auto-refine until the peak changes by < X% or the max level is reached
  - **solver timeout and threads**
- `POST /sims/{id}/cancel`, with live `sim_progress {sim_id, phase: "mesh"|"solve"|"post", pct, detail}` events.
- `POST /sims/compare {ids}` returns a side-by-side metrics table.
- Saved study templates: `GET/POST/DELETE /sims/templates`.
- Result channels for the viewer:
  - von Mises, displacement magnitude and per-axis, principal stresses, safety-factor field, temperature, Cp, velocity
  - a deformed-shape scale factor
  - a probe value at a point (`GET /sims/{id}/probe?x&y&z`)
  - section plane data
- Agent tools:
  - `sim_types_list` (read)
  - `sim_run(type, target, settings)` (local_mutate). `sim_static_run` stays as a thin alias.
  - `sim_compare` (read)
  - `sim_cancel` (local_mutate)
- C3's design studies may call any study type as an evaluator, e.g. "minimize drag subject to SF ≥ 2".

**Tests:**
- modal frequencies of a cantilever against the analytic value (within 10%)
- the buckling multiplier of an Euler column (within 15%)
- thermal 1-D conduction against the analytic result
- sphere and plate drag on the built-in CFD (marked slow; run a coarse version in the default suite)
- the settings schema drives validation (bad ranges are rejected)
- cancel kills the process group
- convergence loop stops
- compare table
- templates CRUD
- probe interpolation

**Agents run studies and use them for review** (added 2026-10-29 at the user's request):
- Every study type is callable by agents through `sim_run`.
- A study started by an agent:
  - is tagged `run_id` and `step_id`;
  - emits `sim_progress`;
  - attaches its result summary (key metrics, pass/fail versus the spec, and the result GLB and charts as URLs) to the tool_result step, so the thread can render it.
- New read tool `design_review(part_id | project_id, spec?)`:
  - chooses the studies the spec requires (static per load case, modal if an excitation band is given, wind if a drag or wind requirement is present, drop if impact is present);
  - runs them under the parallel limit;
  - returns a requirement-by-requirement pass / warn / fail table with links to each study.
  - It runs the studies as child `sim_run` calls through the Gate (so their safety class is local_mutate, approved like any sim run).
- The **verifier** agent prompt (C3 owns the text) calls `design_review` before approving a design, and cites the study ids in its verdict.
- The engineer agent runs `design_review` on the chosen variant.

### Task A8: Studies page — full control and all study types (app)

**Files:** `app/src/ui/pages/studies/**`, viewer result overlays, tests. Runs in wave 2, after C7.

**Scope:**
- Study type picker: Static, Modal, Buckling, Thermal, Drop, Wind.
- A settings form generated from `/sims/types` schemas, with an advanced section.
- Pick faces in the viewer for loads and boundary conditions, with colored markers and arrows showing load direction and magnitude.
- A load case list.
- A material editor.
- Mesh preview and refinement.
- Run, progress by phase, and cancel.
- **Results:**
  - a channel picker
  - a legend with an adjustable range
  - a deformed-scale slider and mode-shape animation for modal results
  - a section plane and a probe on click
  - for wind: animated streamlines or particles on the velocity slices, a surface Cp map, a Cd/Cl readout, and a yaw sweep chart
- Compare two or more studies side by side.
- Save and load templates.
- "Ask agent to fix" includes the full settings and results as data.
- Keep "guide only" and fidelity labels always visible.

**Tests:** schema form rendering and validation, face-pick to load assignment, channel switch, cancel, compare view, wind results rendering from a real fixture.

**Inline study cards and immersive view** (added 2026-10-29 at the user's request):
- When an agent runs a study, the conversation thread shows a **study card** inline in the step:
  - type and status;
  - a live phase progress bar;
  - key metrics with pass / warn / fail;
  - a small chart (e.g. the convergence curve, the stress histogram, the Cd-vs-yaw sweep, or a modal frequency bar chart against the excitation band; follow the dataviz skill);
  - a thumbnail of the colored result.
- Cards on the Studies page and in the A7 study scatter get the same treatment.
- **Immersive view**: an "Immersive" button on every study card and chart point (and double-click on the chart) opens a full-window 3D view of that study:
  - While it runs, you can watch the mesh appear, then the solve progress, then the results animate in.
  - Once done, you can orbit, change the channel, scrub the deformed scale, play mode shapes or the airflow particle animation, move the section plane and probe values.
  - A side strip shows the metrics, the requirement checks, and which agent ran it and why (the agent's one-line reason from the tool call).
  - Esc exits.
  - ⌘[ / ⌘] step through the other studies in the same review.
  - It works in both themes, and with reduced motion it shows static frames.
- `design_review` results render as a review card: a requirement table with each row linking to its study's immersive view.

**Tests:** the card renders from a real agent step fixture with each study type; the immersive view opens from a card and from a chart point, shows live progress from `sim_progress`, and Esc and keyboard navigation work; the review card links resolve.

### Task I: Integration, real-app QA, final review

- Merge order: C1 → C6 → C2 → C5 → C7 → C3 → C4 → A1 … A8.
- Update the fake core with real fixtures dumped from the merged core.
- Contract tests cover every new route.
- Run all suites.
- Rebuild `Mises.app`.
- **Real QA:**
  - Launch the app. Watch the intro.
  - The system check lists PrusaSlicer; install it via the button if the user approves at that moment, otherwise verify detection.
  - Copy and paste in the composer.
  - Create two builds; reorder parts by drag; set a color; change a var; regenerate.
  - Build a hinge assembly and drive it.
  - Import a catalog motor.
  - Run the engineer agent on "design a 5-inch drone frame" with the user's Claude Code and a scripted interview, through to a study with ≥ 6 evaluations.
  - A Claude Code orchestrator spawns a local-model worker.
  - Delete old runs.
  - Take screenshots.
- A final whole-branch review, then one fix wave.

## Request → task map (nothing forgotten)

| User point | Task |
|---|---|
| Why can't I delete agents; better agent manager; delete old agents | C1, A2 |
| Work on different builds at once; individual parts in a bigger build | C1, A3 |
| View your Onshape builds | C5, A6 |
| Directory to view parts online and get sizing info for Claude | C5, A6 |
| More indicators when Claude is working or thinking | C4, A1, A2 |
| Agent should not respond with so much text | C3, C4 |
| Formatted replies with colors and highlights | C3 (emit), A2 (render) |
| Sketch easier, guidelines, usable output | A5 |
| Orchestrator agents call worker agents | C4, A2 (tree) |
| Why install PrusaSlicer manually; app checks everything | C6, A1 |
| Loading screen and cube-assembling welcome intro | A1 |
| Select, copy and paste text in the box | A1 (Edit menu, root cause) |
| Improve harness; drone → questions, specs, iterate | C3, A7 |
| Does Onshape show my remaining budget live | C5, A6 |
| Assembly that Claude can connect to, parts move | C2, A4 |
| Choose shape color | C1, A3 |
| Alter variable thickness and regenerate | C6, A3 |
| Work alongside Claude making my own edits | C6, A3 |
| Harness integrated with Claude Code underneath | C4 |
| Drag parts around in the top-left list | C1, A3 |
| More control over studies | C7, A8 |
| More studies, e.g. wind/aerodynamic | C7, A8 |
| Agents run studies and use them for review | C7, C3 (verifier prompt) |
| Immersive view of an agent's study from the chart | A8 (with A2 thread cards) |
