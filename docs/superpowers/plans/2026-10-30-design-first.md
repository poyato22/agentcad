# Plan 9 — Design-first Mises (requirements)

Source: user message of 2026-10-30 (three screenshots: workspace tab strip, agent button bar, assembly
side panel). Every line below is a requirement; each must be met and verified before release.

## Requirements

| ID | Requirement (user's words, formalized) | Area |
|----|----|----|
| D1 | Remove the workspace tab strip (`LOCAL / Default / Robotic Dog / +4 / + / ▾`). Switching builds = go back to Home. | app shell |
| D2 | Remove the agent button bar (`Stop · New · Manage · Browser · Full log`, the agent dropdown, the "Claude Code (your account) · opus → engineer" line). Stop survives only as the composer's send button turning into Stop while a run is busy (Esc also stops). | app console |
| D3 | BUG: with Claude Code as the runtime, the lead agent cannot spawn sub-agents in the harness. Fix and prove with a real run. | core orchestrator |
| D4 | Agent output comes out in three parallel streams that are all visible at once and never block each other: **Simulations**, **View** (renders/immersive views), **Text** (conversation). A running study must not hold up text or views (core + UI). | core + app |
| D5 | Assembly: remove the right-hand panel (Parts / Part select / Name in assembly / Add part / Joints / Check). | app assembly |
| D6 | Assembly: remove the `Move · Turn · Snap · Fit` toolbar. | app assembly |
| D7 | Remove the `Part` toggle next to `Assembly`. The build view is the assembly; to view one part, click it in the left list (opens it; back returns to the assembly). | app viewer |
| D8 | BUG: weight is not shown for every part. Every part shows its mass (material × volume), including groups/assemblies (sum). | core + app |
| D9 | Replace `History` with `BOM` in the bar; remove History from the bar; remove the `Parts` menu. | app shell |
| D10 | When an agent delivers a part, its card shows an image (render) of the part, and the part is placed into the assembly automatically. | core + app |
| D11 | Agents can animate the assembly and return the animation as an immersive view card. | core + app |
| D12 | Assembly has a Play button that opens the immersive view of the parts (animation / exploded tour). | app |
| D13 | Images in agent replies are bigger (cards use the full stream width; click → immersive). | app |
| D14 | Generative design and topology optimization for a part (design space + loads + keep-outs → optimized organic solid; several variants compared with sims). | core + app |
| D15 | Concept sketch before building anything: the agent first produces a sketch of what it will look like; the user can circle areas, click to pin, and leave comments; the agent produces a revised sketch; building starts only after the user approves. | core + app |
| D16 | Aesthetics: no default straight connector bars between servos; cohesive, smooth, covered builds (skin over skeleton, fairings, continuous surfaces). The harness must be able to produce a sleek humanoid (Optimus / NEO-class design language). | harness + recipes |
| D17 | Assembly: a one-click section view with a slider to sweep the cut plane. | app |
| D18 | Agents return an immersive section view (sweepable) when relevant. | core + app |
| D19 | Studies tab is agent-first: a studies agent with full build context (parts, materials, loads, past results) helps set up and run simulations and tells you what to do next. | core + app |
| D20 | Visual redesign: efficient use of space, cohesive layout, nothing looking tacked on. | app |
| D21 | Drawings move under Print. | app |
| D22 | Onshape tab only on the Home screen. | app |
| D23 | Home has no top bar; the top bar exists only inside a build. | app |
| D24 | Home cover images are rendered from the build's assembly and look good (lit, framed, finished). | core + app |
| D25 | Agents can pitch aesthetic design choices (options with sketches) when the build needs it — in the concept sketch stage and later. | harness |
| D26 | BUG: a "robotic dog" request mentioned the drone suggested prompt. Suggested prompts / unrelated context must never leak into agent context. | core + app |
| D27 | Design emphasis depends on the task: design-heavy (car, humanoid, consumer product) vs engineering-heavy (injection mold, jet engine, bracket). The harness classifies and adjusts process (sketch depth, surfacing, critique weights). | harness |
| D28 | Research: how to get good at design, which models to use, multimodal/image input into Claude — and apply the findings in the harness. | research |
| D29 | Delete parts in a build. | core + app |
| D30 | Delete builds from Home. | core + app |

Tasks, research findings and the verification matrix are appended below once research completes.

## Global Constraints

- Base: `main` at 9aaf32f. Branches `p9-<task>`. Plans 1–8 safety invariants hold: one Gate on every tool call, `needs_approval` never auto-approved, untrusted text is data, sandboxed workers, secrets only in Keychain, never `claude auth login/logout` in tests.
- Removing UI means deleting the component, its CSS, its tests and its e2e specs (or rewriting them to the new behavior) — not hiding it with CSS. Keyboard shortcuts that pointed at removed UI are removed from `lib/shortcuts.ts` and `docs/shortcuts.md`.
- Every app task tests against real-core fixtures (see brief). Payload changes are additive (new optional fields) unless the task says otherwise.
- Spec: the requirement table above is the spec. Each task lists the requirement IDs it owns; the task is not done until each owned ID is demonstrably met (test or screenshot).

## Tasks — wave 1 (bugs, core)

### B1 — Sub-agents under Claude Code + no context leaks (D3, D26) · branch `p9-b1`
Root causes are in `core-bugs.md` (D3, D26 sections).
- D3: add `inherited_model_ref(rt, parent_run_id) -> str | None` (claude-code session ref, else native run ref, else stored `provider/model`; skip `ui`/`acp`). `_start_sub` and `orchestrator_run` pass it as `model_ref` unless the child's definition pins a model. Do NOT put it in `child_policy()` (duplicate keyword). Tests per `core-bugs.md`, including a fake-claude-binary wiring test where a `claude-code/*` lead's child runs on `claude-code`.
- D3 proof: re-run the live script `scratchpad/p9-debug/d3_live.py` (temp MISES_HOME, cheap model) after the fix; the child must run on claude-code and not 401. Record output in the report.
- D26: fix `engineering/interview.py:81` regex to `\b(?:drones?|quad(?:copter)?s?|fpv|multirotors?)\b`, add legged-robot guard (quadruped, dog, legged, walker, hexapod, biped, humanoid → not drone; add a `legged_robot` domain with its own interview questions: payload, gait, speed, terrain, battery runtime, DOF per leg, actuator class, aesthetic intent). Parametrized `detect_domain` tests.
- D26: replace drone-centric examples in `agent/builtin/engineer.md` and router/shape_complexity examples with varied neutral ones (no single product family dominates). Filter `context_tools.build_context` and `runtime._claude_context` selection by the run's build (test: selection in build A, lead in build B → none of A's context).
- D26 guard test: for prompts {"robotic dog", "humanoid robot", "car body", "injection mold for a cap", "jet engine compressor"}, the lead's first-turn system+user context (assembled by the real code path, fake model) contains no "drone"/"quadcopter"/"prop" unless the prompt does.

### B2 — Mass for every part (D8) · branch `p9-b2` (core + app)
- Per-part `material` (optional; default = build default material = profile default). Densities from the existing materials table used by sims/BOM (single source). Settable by `PATCH /parts/{id}` `{material}` and by an agent tool `part_set_material` (Gate-classed like other part edits).
- `PartSummary` gains `mass_g: number|null`, `mass_source: "built"|"catalog"|"sum"|"unknown"`, `mass_note: string|null`, `material: string|null`. Built parts: volume × density of *its* material (worker's PLA default no longer decides). Groups/parents: recursive sum of children (`sum`). Catalog: stated/estimated. Unknown: null with a human reason ("build failed", "not built yet").
- Assembly response gains `total_mass_g` (instances × part mass, unknown parts listed).
- BOM mass == tree mass (test).
- App: every place mass shows (`PartList` row hint, `PartProperties`, `Overview`, `TitleBlock`, BOM) reads `p.mass_g` / reason; groups show the sum; the assembly shows total mass. Fix `partSummarySchema.metrics .catch(null)` so one bad field does not null all metrics (per-field catch). Re-dump fixtures.

### B3 — Non-blocking streams in the core (D4 core half) · branch `p9-b3`
Root causes in `core-bugs.md` (D4).
- Study/sim tools gain `wait: bool` (default **false** for agent calls): returns `{sim_id, status:"running", stream:"sim"}` immediately; the study runs as a background job (reuse the Studies-page 202 pattern in `server/sim_api.py`). New tools `sim_wait(sim_id, timeout_s≤600)` and `sim_result_get(sim_id)`. Gate still checks and budgets at start; `needs_approval` studies still wait for approval before starting.
- On finish/fail the core emits `sim_finished {sim_id, run_id, build_id, status, summary}` and enqueues a short system note into the owning lead's queue ("Study <name> finished: <summary>") so the lead reacts without blocking. Notes count toward the queue cap but never 429 (drop oldest note instead).
- Renders never starve: reserve ≥1 heavy slot for render jobs (`heavy.py`), test with all solve slots held.
- Concurrency: allow up to 2 concurrent studies when the heavy pool has ≥3 slots (configurable), otherwise 1.
- Every event/step the UI consumes carries `stream: "text"|"sim"|"view"` (sim tools, sim events → sim; render/present_view/critique/view events → view; everything else → text). Additive field; re-dump fixtures.
- Tests per `core-bugs.md` D4 list, including: lead replies (text step) before a fake 5 s study finishes; a render submitted after `sim_start` finishes first.

## Visual spec (D20) — "the model is the hero"

Problem today: every surface is a boxed panel in one monospace face with ALL-CAPS labels and hairlines, so the
app reads as a pile of tools bolted around a small viewport. Direction: the build is a full-bleed studio
viewport; chrome is a few quiet floating surfaces that hold only what is needed now.

- **Color** (dark default; light keeps parity): Graphite `#14161a` (bg/viewport edge), Panel `#1b1e23`
  (floating surfaces, 92% opacity + 18px backdrop blur), Raised `#252930` (hover/selected rows), Hairline
  `#2f343b`, Ink `#e9ebee`, Dim `#9aa1aa`. Semantic colors unchanged (blue = selected/agent/needs you;
  green/amber/red = pass/warn/fail). No new accent colors.
- **Type**: UI text in **Instrument Sans** (bundled via `@fontsource/instrument-sans`, exact version, offline),
  sentence case everywhere — no ALL-CAPS labels, no letter-spaced eyebrows. **Iosevka stays only for numbers
  and dimensions** (mass, lengths, costs, stresses; tabular figures). Scale 11 / 12.5 / 14 / 18 px.
- **Shape**: floating surfaces radius 10px, rows radius 6px, buttons radius 7px; one soft shadow for floating
  surfaces only. Section headers are plain 12.5px semibold text, not boxed bars.
- **Layout (build screen)**:
```
┌ ⌂  Robotic Dog ▾      Design   Studies   Print   BOM               ● 2 agents   ◐ ┐  44px top bar (build only)
│ ┌ Parts ──────────┐                                         ┌ View ─────────────┐ │
│ │ ▸ Body    1.24 kg│                                         │ [latest view, big]│ │
│ │   Leg FL   180 g │          full-bleed assembly            │ ◂ filmstrip ▸     │ │
│ │   …              │                                         ├ Simulations  2 ● ─┤ │
│ │ Total     3.10 kg│                                         │ ▪ Stress   running│ │
│ ├ BOM ─────────────┤                                         ├ Conversation ─────┤ │
│ │ 14 items  $212   │     ▶ Play   ◧ Section ═══○═══  X-ray   │ …                 │ │
│ └──────────────────┘          (floating viewer pill)         │ [ Message…   ⏎ ■ ]│ │
└──────────────────────────────────────────────────────────────┴───────────────────┘ ┘
```
  Left surface: Parts tree (with mass) and BOM summary (replaces History). Right "agent dock" (≈440px,
  resizable 360–640) with three stacked, independently scrolling, collapsible streams. Bottom-center floating
  viewer pill. Home is a full-window grid with no top bar.
- Motion: only in response to actions (open/expand/collapse ≤160ms); respect reduced motion.

## Tasks — wave 2 (app restructure; parallel, strict file ownership)

### U-A — Shell, Home, top bar (D1, D9 tabs, D20 shell, D21, D22, D23, D24, D30) · branch `p9-ua`
Owns: `ui/topbar/**`, `ui/pages/router.ts`, `ui/pages/home/**`, `pages/print/**`, `pages/drawing/**`, `pages/onshape/**` mount point, `App.tsx` (shell grid/top bar/home only), `styles/tokens.css`, `styles/base.css`, `styles/fonts.ts`, core `cad/thumbs.py` + `server/projects_api.py` thumb route.
- D1: delete the build tab strip (`BuildTabs`, `BuildsMenu`, `+N`, `+`, `▾`, `openBuildIds` persistence UI). Top bar left = ⌂ Home + build name (click → rename inline). Switching builds only via Home. ⌘0 = Home.
- D23: Home renders with no top bar (full window; keep traffic-light inset). Top bar only inside a build.
- D22: Onshape is reachable only from Home (a Home toolbar entry opening the Onshape page as a Home sub-view); removed from build tabs.
- D9 tabs: build tabs become exactly `Design · Studies · Print · BOM`. Remove the `directory` ("Parts") tab/page entry from the strip (keep the route only if something else needs it; otherwise delete). D21: Drawings become a section/mode inside Print (Print page has `3D print | Drawing` segmented switch); the drawing tab is removed; ⌘3 = Print, ⌘4 = BOM.
- D30: Home tile has a delete action (context menu + hover ⋯): moves to Trash with a 5 s Undo toast; Trash view has "Delete forever" per tile (confirm) and Empty trash.
- D24: core renders the cover from the **assembly** (all instances with poses and finishes, iso 3/4 view, studio lighting, transparent → themed background, framed to bbox), cached by assembly+parts version; falls back to the largest part render; never a 2×2 grid. App tile shows it full-bleed (16:10), name + mass + updated below.
- D20 shell: apply the Visual spec tokens/fonts globally (tokens, base, fonts) — other tasks consume them. Sentence case in everything you own.
- Tests: rewrite/delete topbar/builds/home specs; e2e: Home has no top bar; open build → top bar with 4 tabs; ⌂ returns Home; delete → undo; cover image endpoint returns an assembly render (core test with a 2-instance assembly checks the PNG is not a grid).

### U-B — Agent dock: three streams, no button bar (D2, D4 UI, D10 card image, D13) · branch `p9-ub`
Owns: `ui/console/**`, console CSS, `state/activity.ts`, the console region in `App.tsx` (width var only).
- D2: delete `RunSwitcher` bar (dropdown, Stop/New/Manage/Browser/Full log, `cv-who` line, RoutingChip display, "N needs you" moves into the Conversation header as a small blue count). The composer (CommandLine) gains a round send button that becomes a **Stop** square while the build's lead (or any of its sub-agents) is busy; Esc in the composer stops; `/stop` stays. Agent browser and full log stay reachable from the Conversation header ⋯ menu (one menu, not buttons). Agents panel stays on Home toolbar.
- D4: the dock shows three stacked streams, all visible simultaneously, each independently scrolling and collapsible, with heights resizable by dragging dividers (persisted): **View** (view/critique/render/part images; newest shown large, older in a filmstrip), **Simulations** (study cards, running ones on top with progress), **Conversation** (text, questions, approvals, announcements, composer). Classification uses the event/step `stream` field (from B3) with fallback by type (sim tools/events → sim; present_view/critique/render/part_shown/view events → view). Items for a collapsed stream show an unread badge. Nothing in one stream waits on another.
- D10: `part_created` announcement card shows the part's render (thumb endpoint, large) and a "Show in assembly" jump.
- D13: View stream images use the full dock width (≥400px wide), click → immersive (existing `ViewImmersive`). Conversation inline images ≥ 320px.
- Tests: unit for classification + layout persistence; e2e: a fake study running for 5 s while text streams in the Conversation and a view lands in View — all three visible; composer Stop appears while busy and stops the run.

### U-C — Assembly is the build view (D5, D6, D7, D9 left, D10 core, D12, D17, D29) · branch `p9-uc`
Owns: `ui/assembly/**`, `ui/left/**`, `ui/viewer/Viewer.tsx` (mode switch only), `ui/viewer/modes/**` (to reuse in assembly), core assembly auto-place + delete cascade.
- D5: delete `AssemblyPanel` (Parts/Add part/Joints/Check) and its CSS; move `fmtVolume`/`KIND_LABEL` first. Joints and checks remain agent-driven (core tools unchanged); the check result shows as a small status chip in the viewer pill when issues exist.
- D6: delete the Move/Turn/Snap/Fit toolbar and gizmo code. Fit = double-click empty space / `F` key.
- D7: delete `ModeSwitch`/`designMode` (⌥⌘A gone). Design page always shows the assembly. Clicking a part in the left tree **opens that part alone** (part viewer, with a "← Assembly" back chip and Esc); clicking it in the 3D assembly selects/highlights it.
- D9 left: left surface = Parts tree (each row shows mass from B2's `mass_g`, group sums, total at the bottom) + BOM summary section (item count, total cost, link to BOM page) replacing History. Remove the "Parts list" button and History pane.
- D12/D17: floating viewer pill on the assembly: **▶ Play** (opens immersive full-window view of the assembly: plays the agent's latest animation if any, else an exploded orbit tour), **Section** (one click → cut plane on the assembly, slider sweeps through bbox, axis X/Y/Z, flip), **X-ray**, **Explode**. Reuse `ui/viewer/modes/**` with assembly instance poses (extend LiveView/modes to accept poses).
- D10 core: when an agent creates (or first builds) a part in a build, the core auto-adds an instance to the build's assembly (placement: layout skeleton slot if one matches, else beside the current assembly bbox, non-overlapping) and emits `assembly_added` + `part_created` with `thumb_url`. Tool option `place: false` opts out.
- D29: part delete from the tree context menu and ⌫ key, with confirm "Also removes N instances from the assembly"; core `DELETE /parts/{id}?cascade=true` removes instances then trashes the part (Undo toast restores both). Agents get `part_delete` (Gate-classed; needs_approval when the part was user-edited).
- Tests: e2e: no right panel, no Move/Turn toolbar, no Part toggle; tree click opens part and back returns; section slider changes the clipped geometry; Play opens immersive; delete with cascade + undo. Core: auto-place, cascade delete.

### U-D — Studies are agent-first (D19) · branch `p9-ud`
Owns: `ui/pages/studies/**`, `ui/spec/**`, core studies-agent definition (`agent/builtin/`), studies context tool.
- The Studies page opens on a **studies agent** conversation pane (left ~40%) beside the results/3D (right). The studies agent is a builtin definition `simulation` with full build context via a `studies_context` tool: parts + materials + masses, assembly, loads/fixtures known, requirements/spec status, every past study with results, calibration data, available solvers (FEM, nonlinear, CFD built-in/OpenFOAM) and their validated accuracy bands. On open it proposes the next 1–3 studies with why ("The hip bracket has no stress study; at 2× body weight…"), each with a Run button (Gate/approval unchanged).
- The manual form (type tabs, SchemaForm) moves behind "Set up manually" (collapsed), not deleted.
- Results land in the Simulations stream too (B3 events). The agent explains results in plain words and recommends design changes, which it can hand to the build lead (`agent_message` to the lead).
- Visual spec applies; use space: results 3D large, charts below.
- Tests: core studies_context content test; e2e: open Studies → agent proposals visible; Run → study card appears; manual setup still works.

## Tasks — wave 3 (design capability)

Research: `research/2026-10-30-design-quality.md` (read §0, §1, §2, §4, §5, §6, §7, §8 — it is the spec for D15/D16/D25/D27/D28)
and `research/2026-10-30-topopt.md` (D14).

### Shared contracts (wave 3)
- **Render styles** (G-R provides, others consume): `render_views(..., style="shaded"|"zebra"|"sketch"|"silhouette", perspective: bool, size ≤ 2048)`; `shaded` gets smooth (crease-split) normals, per-part color/finish, 3-point light, ground shadow. `sketch` = NPR line art on paper-white with wobble + over-sketch (research §4.4). `silhouette` = black fill. Default image size for design critique 1024; PNG < 1 MB.
- **Design class** (G-B provides): `design_intensity(prompt, parts?) -> {score: 0..1, cls: "engineering"|"mixed"|"design", per_part: {name: score}, reasons[]}`, stored on the lead's run as `design_class` and exposed in run payloads (additive). Knobs table = research §6.
- **Concept gate** (G-A provides): build-level `concept` record `{status: "none"|"pitching"|"sketching"|"approved"|"skipped", sketch_ids[], chosen_option}`; for `design` class, `part_create` of non-massing parts is refused (`needs concept approval`) until `approved` or the user chose "skip". Mixed class: sketch recommended, not enforced. Engineering: off.

### G-R — Renderer for design (D24 quality, D15 sketch style, critique quality) · branch `p9-gr` (core)
Owns `cad/render*.py`, `cad/views.py` sizing, `sketch.MAX_IMAGE_EDGE`.
- Implement research §4.3 items 1–5 exactly (vertex welding, crease-split smooth normals, zebra, NPR sketch with silhouette/crease/contour lines + toon fill + wobble + over-sketch, perspective camera, CMF color/gloss per part from appearance, soft ground shadow, ≤2048 px).
- Still runs inside the sandboxed render subprocess with the existing STL caps. Perf budget: 1024 px shaded render of a 200k-triangle assembly < 3 s on this Mac (measure; report).
- Golden-image tests (tolerant, e.g. SSIM or pixel stats): a smooth lofted ellipse shows no faceting (normal-variance metric), zebra stripes continuous on a G2 blend and broken on a G0 step, sketch output has line pixels on silhouette and paper background, silhouette mask matches.
- Save sample PNGs (humanoid-ish massing in each style) next to the report.

### G-B — Design harness: intensity, skin-over-skeleton, recipes, critique (D16, D25 rules, D27, D28) · branch `p9-gb` (core)
Owns `design_intensity.py` (new), `cad/mises_shapes.py` + its recipe index, `agent/builtin/*.md` design rules, critique rubric/weights (`cad/critique*` / orchestrator knobs), model-per-stage defaults, `evals/design/` (new).
- D27: classifier = keyword/archetype prior (research §6 table) + optional cheap-model rubric (Haiku via the run's runtime when available, never required; tests use the prior only). Per-part scores. Knobs table wired into: concept stage requirement (contract above), skeleton requirements, surfacing rules injected in the lead/designer prompt, critique weights/views/rounds, DFM strictness, model/effort suggestion.
- D16: recipes from research §2.3/§2.5 into `mises_shapes` (rounded-box section, station loft, inset-loft shell (never `offset_3d` on lofts), tapered capsule link, joint cowl, panel/clamshell split with real gaps, `trust()` check), each with a test. Archetype starter recipes: **humanoid** (8-head proportions, skeleton hardpoints, torso/pelvis/limb/head shells, joint cowls — Optimus/NEO-class language), **quadruped** (robot dog), **car body** (wheelbase/greenhouse proportions, beltline). Prompt rules: visible geometry = covers; links are capsule/loft shells; never a straight bar between actuators; hide or celebrate mechanism; ≤3 CMF; feature lines from panel gaps.
- Critique (Plan 8 G1 loop) upgrades per research §8 G1 row: separate judge context, checkable-question rubric (research §6 aesthetic rubric), class weights, strategy switch at round 3, zebra/hero views for design class (use G-R `style` param; if G-R not merged, call with style and fall back to shaded on unknown style), bar heuristic auto-fail.
- D25 rules: when to pitch (design class, or user asks), pitch format (research §7) — the tool itself is G-A.
- D28: `evals/design/` with the 20-prompt set (humanoid, robot dog, car, kettle, drone, mold, jet-engine nacelle, …), a runner that builds + renders each with a given model and saves a contact sheet, and a pairwise-preference sheet for the user. Not run in CI. Also a real proof: run the humanoid archetype recipe end to end (script only, no model) and save hero/zebra renders next to the report.
- Tests: classifier cases (robotic dog/humanoid/car → design; injection mold/bracket → engineering; jet engine → mixed with nacelle part design), each recipe valid + volume within 1% of reference, prompt contains surfacing rules only for design/mixed.

### G-A — Concept sketch, markup and pitch (D15, D25) · branch `p9-ga` (core + app)
Owns core `concept/` (new package: store, tools, API), app `ui/sketch/**` (new), and one card registration in the dock's View stream.
- Core tools (Gate-classed; read/write as appropriate):
  - `concept_sketch(massing_script, views?, notes?)` → builds the massing in the sandboxed worker, renders `style="sketch"` 3/4 + side (perspective, human-scale figure for scale, ground line), stores a sketch version `{sketch_id, build_id, version, images[], camera per image, overlay_svg (agent callouts), notes}`, emits `concept_sketch` event (stream `view`). Revisions link `parent_sketch_id`.
  - `pitch_options(options[2..3])` each `{name, mood[3], massing_script, proportion_note, cmf[2..3], tradeoffs{mass_delta, part_count, molding, cost_tier, risk}, recommended?}` → silhouettes (front+side) + sketch per option, one must be flagged `baseline`; emits `concept_pitch` event. User picks / mixes via the card.
  - `concept_status()` returns record + all user annotations as **data** (untrusted text rules apply).
- User annotations stored per sketch image in normalized image coords with the camera: `circle {cx,cy,r}`, `lasso {points[]}` (freehand circle-around), `pin {x,y}`, each with comment text; plus a general comment. Submitting annotations posts a user message to the build's lead ("Sketch feedback v2: 3 marks…") with the annotated images (marks drawn on top) attached as images so Claude sees exactly what was circled; the lead must answer with a revised `concept_sketch`.
- Approval: "Approve sketch" (and "Skip sketch") on the card set the concept gate (wave-3 contract). Lead prompt: for design/mixed class, the first deliverable is a pitch (design) or a sketch (mixed) — never parts.
- App `ui/sketch/`: `SketchCard` in the View stream (large image, version chips v1 v2 …, side-by-side compare with previous version) and `SketchReview` immersive: tools Circle (drag), Lasso (freehand), Pin (click), each opening an inline comment box; marks list; Send feedback; Approve; Skip. `PitchCards`: 2–3 option cards (silhouettes, sketch, mood, CMF swatches, trade-off table, recommended badge), Pick / Mix (select regions per option + comment).
- Tests: core — sketch versioning, annotation round-trip, gate refuses part_create for design class until approval, feedback message contains annotated images; app — drawing a circle/lasso/pin produces normalized marks, feedback posts, approve flips gate (fake core + real fixtures). e2e flow: sketch v1 → circle + comment → v2 appears → approve.
- If U-B (dock) is not merged when you start, build `SketchCard` standalone and register it via a tiny exported hook; the merge wires it into the View stream.

### G-D — Agents present animations and section sweeps (D11, D18) · branch `p9-gd` (core + app) — starts after U-B and U-C merge
- Core: `present_view` gains kinds `animation {animation_id | keyframes}` and `section_sweep {axis, from, to}`; `assembly_animate` can save a named animation and return a card; the lead prompt says to present an animation when motion matters and a section sweep when internals matter (design or packing).
- App: View-stream cards for both; immersive: animation plays with scrub/loop/speed; section sweep auto-sweeps then hands the slider to the user; both use assembly poses (U-C's pose-aware modes).
- Tests: core payloads; e2e: card → immersive plays; slider sweeps.

### G-C — Generative design and topology optimization (D14) · branch `p9-gc` (core + app)
Spec: `research/2026-10-30-topopt.md` (module + tool design) and the working spike at
`/private/tmp/claude-501/-Users-hartigan-Documents-Claude-Projects/8b6e121f-d432-4043-912f-dff97e8094a0/scratchpad/topopt-spike/` (port it; keep its fixes: symmetric MG V-cycle + 2-level fallback, density shaping so keep-booleans cut cleanly, degenerate-triangle removal, STL weld + gmsh mesher fallback).
- Core package `topopt/` running in the sandboxed worker (memory limit, cancellable), progress events every few iterations with a coarse density preview mesh (stream `sim`). Tools per research doc: `topopt_run`, `topopt_generate` (variants/sweep, Pareto rank on mass vs deflection filtered by safety factor), `topopt_status`, `topopt_result`, `topopt_cancel`, `topopt_accept` (creates a new part version or a new part from the chosen variant; `needs_approval` when replacing a user-edited part). Long runs are background jobs like B3 studies (`wait` default false).
- Defaults from the spike: filter radius 2–2.5 voxels, auto-refine resolution when volume fraction < 0.15 (members ≥ 3 voxels), symmetry option, overhang (3D-print) filter per pyMOTO approach (MIT, cite), min member size. Every variant verified with gmsh + CalculiX; unverified or failing variants are marked and never ranked first.
- Size control: simplify/decimate the organic surface before B-rep (target < 15 MB STEP for a 60×40×20 bracket; report the actual), keep the exact keep-in features as true B-rep.
- HTTP for the app: start/list/get/cancel/accept.
- App (new `ui/topopt/**`, registration hooks only elsewhere): a "Generative design" panel reachable from a part's context menu and from the Studies page's manual setup — choose design space (current part), keep regions (pick faces → keep), loads/fixtures (reuse the studies face pickers), goal (volume fraction or max deflection), variants count, manufacturing (3D print / machined / none). Live preview of density as it converges; result card in the Simulations stream with variant grid (render, mass, deflection, peak stress, verified ✓/✗) and Accept.
- Tests: core — small-grid (e.g. 12×8×4) optimization converges and is deterministic, verification compliance within 5% of optimizer on the small case, keep features survive, cancel works, Pareto ranking; app — panel/model unit tests + e2e with fake core. Real proof: run the 60×40×20 bracket end to end through the tool path and save renders + numbers next to the report.

## Final fix wave (from the whole-branch review)

Findings: `scratchpad/p9-final-core.md` and `scratchpad/p9-final-app.md` (full paths in briefs), plus ledger follow-ups.

### FW-core · branch `p9-fwc` (from p9-int)
All core Important (I-1..I-5) and Minors (M-1..M-8) in p9-final-core.md, plus: covers use G-R `view="hero"`, shaded CMF finishes, transparent background with NO opaque ground slab (shadow either off or alpha-composited); VIEW_ANNOUNCE includes concept_sketch/concept_pitch (and Event.stream honors data.stream); B3 follow-ups that are core-side; studies agent tool list gets design_review_get/job_wait/sim_wait; `restore_part` refuses when the dir exists and deletes are blocked/cancel jobs first (user confirm text mentions running studies).

### FW-layout · branch `p9-fwl` (from p9-int) — D20
- Build screen per Visual spec: the viewport is full-bleed behind everything; the left Parts surface and the right agent dock are **floating** surfaces (Panel color 92% + blur, radius 10, 12px inset from window edges, soft shadow), each collapsible to a slim tab (⌘[ / ⌘] or a chevron) so the model can take the whole window. Viewer pill stays bottom-center over the viewport. Fit/framing accounts for the floating panels (model centered in the visible area).
- Studies page: the dock is hidden (the studies agent pane is the agent there); the page uses the full width; at 1280 the proposals and "Set up manually" are fully visible without clipping. Print and BOM: dock collapsed by default (user can open it).
- Home: responsive tile grid that fills the width (min tile 280, max 420; 3–5 columns), larger covers; empty-state for 0–2 builds looks intentional.
- Top bar: remove the red "Stop all" button (⇧⌘. stays; the composer Stop is the visible control); keep activity/status quiet.
- Sweep remaining ALL-CAPS / monospace labels outside numbers to sentence case Instrument Sans ("Guide only", "Needs you", "Pass", "Pause", remove "FIG. 1 — DESIGN" captions).
- Screenshots at 1280 and 1440, dark + light, of Home, build, Studies, Print, BOM; review against the spec.

### FW-app · branch `p9-fwa` (after G-B merges) 
App Important I1–I3, I6 (app side) and Minors in p9-final-app.md not covered by FW-layout: tree Hide/Show/Isolate drive the assembly; remove the part viewer's "Whole build"/"All parts" scope (D7); dock Simulations stream includes every study/topopt job of the build (Studies page runs, studies agent runs, user-started generative runs) via build-scoped job/sim events; concept cards in View; filmstrip thumbnails load, captions not clipped; run labels not cut mid-phrase (use the task title or first sentence); total mass shown once; B3 follow-ups (job list refresh on topopt start/finish; composer Stop does not cancel user-started generative runs).
