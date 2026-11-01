# Plan 10 — Functional robots, cleaner interaction (requirements)

Source: user message of 2026-10-31 (screenshots: SSL error card, exploded view, Spec text block in the
dock, Design-focus chip overlap, empty-assembly state). Every line must be met and verified.

| ID | Requirement | Area |
|----|----|----|
| E1 | Home: clicking **Agents** again closes the agents panel. | app |
| E2 | Home: Parts library, Onshape and Trash move under one **Settings** button (click to open a menu). | app |
| E3 | Error cards: render errors as plain wrapped text (no markdown mangling of `UNABLE_TO_GET_ISSUER_CERT_LOCALLY`), fix the `tool_failed` line that wraps one character per line, and make errors actionable (what happened, likely cause, Retry). Detect Claude Code network/TLS errors and explain them (VPN/proxy/`NODE_EXTRA_CA_CERTS`). | app + core |
| E4 | Exploded view must be coherent: parts move outward along the assembly structure from their real positions, preserving layout and orientation; camera keeps the whole thing framed. | app |
| E5 | **Play** plays the agent's custom animation for this build (saved, survives reload); orbit only when the build has none. Agents create an animation for anything with moving parts. | core + app |
| E6 | Dock: View, Simulations and Conversation sit **side by side horizontally** as columns; any combination (incl. all three) can be open at once; closed ones collapse to a slim tab. | app |
| E7 | Everywhere: clicking the button that opened something closes it again (toggle). Audit every opener. | app |
| E8 | Immersive simulation: clear load and fixture indication — arrows at the load location sized/labelled by magnitude and direction, fixture markers, a legend. | app |
| E9 | Edit and resend a prompt, and Retry a prompt after an error or a stall. | app + core |
| E10 | Resizing the chat/dock never selects page text. | app |
| E11 | Cancel a prompt (queued or running); ⌘Z / ⇧⌘Z undo/redo of user actions (part delete, move, rename, color, assembly edits); back/forward navigation (← → buttons, ⌘← / ⌘→, mouse back/forward) between views/pages/opened items. | app + core |
| E12 | Remove the Spec text block from the dock Conversation (requirements stay on Studies). | app |
| E13 | Opening a spawned agent (or any item) always has a visible way back/close (and Esc). | app |
| E14 | Bought parts (e.g. a servo) appear in the left Parts list under "Bought parts", clickable to show the 3D model and info (specs, price, source), not only in the View stream. | app + core |
| E15 | Parts an agent selects are always inserted into the build and always added to the parts list/BOM — no "insert"/"add to list" buttons. | core + app |
| E16 | Measure: hovering highlights and enlarges the snap target (vertex/edge/face); clicking a second feature adds to the first (two picks → distance/angle), never replaces it. | app |
| E17 | Engineering builds: the agent estimates the electronics needed (battery, controller board, motor drivers/ESCs, radio, antenna, GPS, IMU, wiring, connectors, switches) with sizes/masses, and reserves space for them in the layout. | core |
| E18 | The critique card in View is the same scale as other View cards. | app |
| E19 | Show the origin (axes triad) when a part/instance is off the origin; **R** resets the selected instance to the origin (undoable). | app + core |
| E20 | BUG: after an agent finishes you can't talk to it again — any agent can be messaged again and continues with its context. | core + app |
| E21 | Agents model the whole drivetrain: motors, motor mounts/attachments, shafts, couplers, bearings, gears (spur/helical/bevel/worm, planetary), timing belts and pulleys. | core recipes |
| E22 | Explain why the full agent log is visible; move it out of the everyday UI (Settings → developer). | app |
| E23 | Fix the Design-focus chip layout (overlapping/cramped text). | app |
| E24 | One chat per build: no run selection, no "New run"; the composer always talks to the build's lead on the default model (Claude Code when signed in); the lead delegates on its own. | app + core |
| E25 | Explain what the "recipe doc" is. | answer |
| E26 | Explain how sketches are made; sketches must not ship with clipping parts — the agent checks the sketch (render + interference of the massing) before sending and fixes clashes. | core |
| E27 | Agents design connections and joints properly: joint type, axis, bearings/bushings, shaft + retention, fasteners, clearances, range of motion, no collisions through the motion range. | core |
| E28 | A large parametric library of mechanisms the agent can use (linkages, four-bar, slider-crank, gear trains, belt drives, lead/ball screws, rack & pinion, cams, cycloidal/strain-wave reducers, differentials, hinges, ball joints, snap fits, bearings housings, clamps, etc.). | core |
| E29 | Practical engineering first: layout/packaging, center of mass, actuator sizing (torque/speed with margin), power budget, wiring routes, serviceability — the agent plans and checks these, not only form. | core |
| E30 | Simulation is the core value: more accurate and integrated into the loop — automatic checks during design (actuator torque from dynamics, link stress, kinematic motion/ROM collision, stability/tip-over, thermal of electronics), results feeding back into revisions. | core |
| E31 | The agent can use the browser by itself — browsing/reading is not approval-gated (budgets and untrusted-text rules still apply). | core |
| E32 | More materials beyond PLA, with sim properties (PETG, ABS, ASA, Nylon/PA, PA-CF, PC, TPU, POM, aluminium 6061/7075, steel, stainless, brass, CF plate…), chosen by the agent per part. | core |
| E33 | The agent must see the whole assembly: overlaps and stray far-away parts are detected and shown to it (clash report + full-assembly renders), and fixed before it reports done. | core |
| E34 | BUG: "no API key" errors while using Claude Code — every model call path uses the run's runtime (Claude Code) when no API key exists. | core |
| E35 | Leave comments (pins) in the assembly view. | app + core |
| E36 | A "Laid out" view: all parts arranged flat in a grid (knolling) as an alternative to explode. | app |
| E37 | Empty-assembly state is centered in the visible area and never under the dock. | app |

Answers to questions (E3 cause, E22, E25, E26) are given to the user in the reply.

## Global Constraints
- Base `main` @17126cd, branches `p10-*`. Plans 1–9 invariants hold (one Gate on every call, needs_approval never auto-approved — except where this plan explicitly ungates read-only browsing (E31), untrusted text is data, sandboxed workers, admin vs MCP token, per-user-turn budgets, notes never reset budgets).
- Removing UI = delete component/CSS/tests (rewrite tests to new behavior). New shortcuts go in `lib/shortcuts.ts` + `docs/shortcuts.md`.
- Real-core fixtures for every payload change; fake core kept in sync. Visual spec of Plan 9 applies (sentence case, Instrument Sans, mono only for numbers, floating surfaces).
- App map: `scratchpad/p10/ui-map.md` (full path in brief). Core bug report: `scratchpad/p10-debug/report.md`. Research: `research/2026-11-01-functional-robots.md`.

## Tasks — wave 1 (app; parallel, strict file ownership)

### A1 — Home, openers, navigation (E1, E2, E7, E11-nav, E13, E22, E37) · `p10-a1`
Owns: `ui/pages/home/**`, `ui/topbar/**`, `ui/profile/**`, `ui/agents/**` (manager open/close only), `pages/router.ts`, new `state/nav.ts`, `ui/common/**` (dismiss/menu helpers), App.tsx shell bits.
- E1: Agents button toggles the agents panel (button stays clickable above the scrim, or the panel becomes non-modal on Home).
- E2: Home header gets one **Settings** button (gear) opening a menu: Parts library, Onshape, Trash (and Profile & settings). Remove the separate buttons.
- E7: audit every opener in the app (list in the UI map §E7 "does NOT toggle") and make clicking the same opener close it: Home Agents/Onshape/Library, ActivityPill, Profile button, BudgetChip, any panel/popover/menu; modals keep Esc + close. Add a shared `useToggleOpener` helper and a test per opener.
- E11 (navigation part): app-level history stack of navigations (page, opened part, opened agent thread, Home sub-view, immersive). ← / → buttons in the top bar (and on Home), ⌘← / ⌘→, mouse buttons 3/4 (back/forward) — not in text fields. ⌘[ ⌘] stay for panels.
- E13: opening a spawned agent's thread shows "← Back to lead" in the Conversation header, Esc returns; every "open X" in the app has a visible way back (verify list).
- E22: "Full log" and "Agent browser" move out of the dock ⋯ menu into Profile & settings → Developer (a "Show full agent log" opens it; same view as today).
- E37: empty-assembly/part states centered in the visible area between floating panels, never under the dock (fix insets re-measure on dock mount/resize).

### A2 — Dock, chat, errors (E3-app, E6, E9-app, E10, E12, E18, E23, E24, E15-card) · `p10-a2`
Owns: `ui/console/**`, `ui/spec/index.tsx` (strip removal only), `dialogs/NewRunDialog*`, `dialogs/ModelPicker*` usage in console, related CSS.
- E6: dock streams become **horizontal columns** (View | Simulations | Conversation), each independently open/closed; closed columns collapse to a slim vertical tab with an unread badge; open columns share the dock width with draggable vertical dividers; the dock width grows with open columns (sensible defaults: 1 col 440, 2 cols 760, 3 cols 1080, capped to window − 360 for the viewport) and `shell/insets.ts` follows. Persist layout.
- E10: no text selection while dragging any edge/divider (user-select none + preventDefault + body.resizing).
- E12: remove the Spec strip from the dock (keep Studies page usage).
- E18: critique card in View fills like view cards (large before/after images).
- E23: Design-focus chip fits: compact segmented control (icons/short labels with tooltips) that shrinks; no overlap at any dock width ≥ 360.
- E24: one chat per build: remove run selection, "New run", Composer form, NewRunDialog, ⌘N, `/new`, "no run selected"; the composer always targets the build's lead; if none exists the first message creates it with the default model (Claude Code when signed in, else profile default) — core support from C1 (`POST /projects/{id}/chat` or createRun without model_ref); viewing a sub-agent thread (from its card) still possible read-only + "message this agent" when it's not the lead.
- E9 (app): each user message gets Edit (puts text back in the composer; sending creates a new turn and marks the old as edited) and Retry on error/stall (stall = no activity 90 s); queued messages get Cancel (core C1 provides cancel of a queued message).
- E3 (app): error rows render as plain wrapped text (never markdown), the grid no longer squeezes the message into one char per line; show code, cause, hint and Retry; Claude-Code network/TLS errors get a specific hint (from core `hint`).
- E15 (card): PartCard becomes display-only (no "Add to parts list"/"Insert" buttons) — core C1 inserts + adds to BOM automatically.

### A3 — Viewer & assembly (E4, E5-app, E8, E11-undo, E14-app, E16, E19, E35, E36) · `p10-a3`
Owns: `ui/viewer/**`, `ui/assembly/**`, `ui/left/**`, `pages/studies/immersive/**`, `pages/studies/StudyView*` (markers only), new `state/undo.ts`.
- E4: rewrite explode: direction from the assembly structure (joints/parent tree: children move away from their parent along the joint axis or the vector from parent center; roots radially from the assembly center), magnitude proportional to the unit's own size (not a global constant), stacked parts along their shared axis keep order, orientation preserved; recompute on pose change; camera re-frames (center + zoom) to the exploded bbox. Golden tests on a 3-level assembly.
- E36: **Laid out** mode in the pill (and agent view kinds): all parts laid flat in a grid sorted by size (largest first), each on its largest flat face if cheap (else as-is), labels under each, camera top-down-ish; glide in/out.
- E5 (app): Play loads saved build animations on open; plays the newest agent animation; orbit tour only when none; a small menu lists saved animations when >1.
- E8: immersive study view draws load arrows (direction, magnitude label, at the load face), fixture markers (hatched/anchor glyphs) and a legend over the result; toggle "Show loads".
- E11 (undo): `state/undo.ts` command stack for user actions (part/instance delete, move/reset pose, rename, color/finish, hide/show, material change, assembly remove/restore): ⌘Z / ⇧⌘Z when focus is not in a text field (text fields keep native undo); toast "Undid: …"; use existing restore/patch endpoints.
- E14: left tree gets a **Bought parts** group from the BOM (servo etc.), each row clickable → info panel (image, specs, price, vendor link) and 3D model when the catalog has geometry (catalog-backed parts are already real parts — show them there too).
- E16: measure hover: pointermove snap preview with enlarged vertex dots / thickened edge / tinted face; clicks accumulate (2 picks → distance/angle, a third starts a new pair), Esc clears; also available in the assembly view.
- E19: origin triad at world origin (subtle) and a dashed offset line + "Δ x,y,z" for the selected instance when off-origin; **R** resets the selected instance to origin (undoable; not for joint-driven instances — explain).
- E35: comment pins in the assembly (instance-aware) → core stores `instance_id` + world point; pins follow poses/explode; sent to the lead like part pins.

## Tasks — wave 2 (core)

### C1 — Core bugs, one chat, browsing, assembly health (E3, E5, E9, E15, E20, E24, E31, E33, E34) · `p10-c1`
Root causes: `scratchpad/p10-debug/report.md`.
- E34: `default_model_ref(rt, project_id)` = build lead's model → profile default → `claude-code/<default>` when Claude Code is signed in and no API key exists → anthropic default. Used by `model_ref_for`, `vision_model_for`, design classifier, drawing import, orchestrator fallbacks. `start_run` with an anthropic ref and no key falls back to Claude Code with a visible note. Test every listed path with no API key + fake claude binary.
- E20: never retire a Claude Code lead in `_spent`; a forced retire inherits model + a summary of the old thread. A direct user message to a sub-agent opens its own cap window (a user turn), the child cap has a floor, and `prompt()` failures write an error step + event (never swallowed). Messaging any finished run resumes it with context.
- E24: `POST /projects/{id}/chat {text, images?}` — routes to the build lead, creating it with `default_model_ref` if none (agent `engineer`/router as today). App A2 uses it.
- E9: `DELETE /runs/{id}/queue/{msg_id}` cancels a queued message; `POST /runs/{id}/retry` re-sends the last user turn after an error; message edit = new turn referencing the edited message id (`edits` field); a `stalled` event after 90 s without activity.
- E3: classify Claude Code errors (TLS/cert, network, auth, rate limit, overloaded) into `{code, cause, hint}` on error steps (TLS hint: VPN/proxy filter, `NODE_EXTRA_CA_CERTS`, link to network config); Profile gets "Extra environment for Claude Code" (NODE_EXTRA_CA_CERTS, HTTPS_PROXY…) forwarded by `claude_environment` (values are paths/URLs, not secrets; never logged beyond names). Wire the `retry` event.
- E5: `assembly_animate` always saves (auto-named when no name), ordering by recency; engineer/designer/lead prompts: any build with moving joints must end with a saved animation demonstrating the motion.
- E15: `part_show` of a catalog-backed item and `catalog_insert` both insert into the build + add to BOM (idempotent) + auto-place; non-catalog shop items are added to the BOM as bought rows with their card; prompts updated. Remove the need for user buttons.
- E31: `web_open` and `catalog_import_url` no longer need approval for public https URLs passing the shape/SSRF checks; keep budgets, untrusted wrapping, new-domain cap per turn, query stripping for non-suggested hosts; blob-shaped/exfil-looking URLs are refused (not carded).
- E33: `assembly_health(project)` → overlaps (pairwise interference volume > tolerance), stray parts (distance from the cluster > k·median spacing or outside the layout envelope), floating parts (no joint/contact), extents. Returned (summarized) by every assembly-changing tool and auto-place; a whole-assembly render (framed to all instances, outliers flagged) is attached to the lead after assembly changes in a turn; the lead may not report done while health has errors (prompt rule + a `finish` check that appends a warning). Auto-place uses the layout skeleton slot or near related parts (joints/parent), never far +X stacking.

### Shared contract (C2 ↔ C3 ↔ C1)
- Joint model gains `coupling?: {kind: "gear"|"belt"|"rack"|"screw", ratio: number, partner_joint: str}` and assemblies may contain **loop-closure** constraints `{kind:"loop", joint_a, joint_b, frame_a, frame_b}` (C2 implements solving + storage; C3's MJCF exporter maps couplings to MuJoCo equality/tendon constraints and loops to `equality connect/weld`).
- Part metrics gain `inertia: {mass_kg, com_mm[3], inertia_kgm2[3x3]}` from the B-rep (C3 implements in the worker; precise GProp like B2's mass).
- Clash/health = C1's `assembly_health` (E33); C3's checks call it, never re-implement.
- Materials table (C2) is the single source for density/E/yield used by mass (B2's `mass.py`), FEM, dynamics and BOM.

### C2 — Mechanisms, drivetrain, materials (E21, E27, E28, E32) · `p10-c2`
Spec: research `research/2026-11-01-functional-robots.md` §1, §2, §5, §6 (mech parts).
- `mises.mech` package as in §6.2 (drive, linkage, joints, grippers, legs, sizing, library, card, tools). Deps: bd_warehouse (present) + `py-gearworks==0.0.24`. Gear pairs auto-phase and run an interference check (planetary ring half-tooth fix). GT2/HTD pulley + belt recipe. Actuator catalog data (servos, steppers, BLDC + planetary/cycloid actuators, Dynamixel) with envelopes, torque curves, mass, price.
- ≥40 mechanisms in the library with cards (inputs/outputs, joints incl. couplings/loops, ROM, ratio, sizing formulas, checks). Tools `mech_list`, `mech_insert`, `mech_check`, `drivetrain_plan`, `material_pick`.
- Assembly solver: closed-loop solving (scipy least squares) + coupled joints (contract above); `assembly_animate` respects couplings/loops.
- E27 joint design rules in the engineer/designer prompts + `mech_check` checks: bearings/bushings at every revolute, shaft retention, fastener sizing, clearances (FDM 0.2–0.4 mm), ROM limits, no collisions through ROM (via C3 `dyn_rom_sweep` when present, else kinematic sweep with `assembly_health`).
- E21: prompts require the full drivetrain to be modelled (motor, mount, coupler/shaft, bearings, gears/belts) — no "floating servo".
- E32: materials table extended per §5 (process, UTS, failure mode, max temp, knockdowns by process) with ≥15 materials incl. metals; used everywhere (contract). Agent picks per part.
- Recipe doc ("recipe index") updated with every new recipe/mechanism; tests per §6.4 (gears, GT2 center, four-bar vs Freudenstein, Grashof).

### C3 — Engineering intelligence + integrated dynamics (E17, E26, E29, E30, E33-use) · `p10-c3`
Spec: research §3, §4, §6 (dyn + engineering parts).
- `mises.dyn` (export to MjSpec incl. couplings/loops per contract, B-rep inertia in the worker, trajectories/IK + named motions, child-process runner in the sandbox, checks, fem_bridge) — deps `mujoco`, `coacd`, `trimesh`. Tools `dyn_build`, `dyn_torque`, `dyn_stability`, `dyn_reactions`, `dyn_rom_sweep`, `dyn_to_fem` (creates load cases + runs CalculiX per link; results into the spec and the Simulations stream as background jobs per B3).
- `engineering/checks.py`: `actuator_size`, `power_budget` (battery pick, wire gauges), `thermal_estimate`, `cable_route`; `engineering/electronics.py` + catalog data: `electronics_estimate` (battery, controller, drivers/ESCs, radio, antenna, GPS, IMU, BMS, connectors, switches) with envelopes/mass/power and `reserve=True` packing into the layout (existing internals packing).
- Lead/engineer pipeline (prompts + a `finish` check): at skeleton stage run actuator_size + electronics_estimate + power_budget before skinning; before done run dyn_torque on a representative motion, dyn_stability, dyn_rom_sweep, dyn_to_fem on the most-loaded links and C1's assembly_health; state margins in the final message. Studies agent can launch these too.
- E26: `concept_sketch` runs an interference check on the massing and refuses to emit (returns the clashes) while solids overlap beyond tolerance; the agent must view its own sketch render (image returned) before presenting.
- Accuracy statements (§4.5) shown on result cards (guide-only bands).
- Tests per §6.4 (2-link arm inverse dynamics exact, reactions, inertia of a box, stability margin, power budget, electronics picks) + a quadruped squat integration test (small, < 30 s).
