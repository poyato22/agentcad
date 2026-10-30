# Geometry Quality Plan (Plan 8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Make the first draft of complex, curved and layered models good. Today the user's B-2 bomber came out as flat planes with an oval cockpit.

**Base:** `sim-accuracy`. Plans 6 and 7 run in parallel; keep edits additive.

**Why this happens today:**
- Agents build blind; they never see their own result.
- They have no recipes for curved geometry, so they fall back to boxes, cylinders and ellipsoids.
- They have no top-down layout. Internals inside a case are improvised.

## Requirements (from the user's 2026-10-30 request: "go for it all of these")

| # | Requirement | Task |
|---|---|---|
| G-R1 | **Render-and-critique loop:** after each build, render standard views and sections, have a vision-capable model compare them with the request or reference, and fix the issues before presenting. Score the silhouette against a reference when one is given. | G1 |
| G-R2 | **Curved-geometry recipe library** that agents use: NACA and custom airfoils, planform-to-wing lofts, blended-wing-bodies, sweeps along splines, multi-section lofts, smooth spline surfaces, thickened surfaces and shells, blends and fillets, enclosure recipes (shell, bosses, ribs, lids, snap fits). | G2 |
| G-R3 | **Layout skeleton first.** A skeleton part (key dimensions, datums, envelope, reference planes and curves) that the other parts are built against. For internals: define the inner volume, place components with clearances, then check with sections and interference. | G3 |
| G-R4 | **Reference input:** the user can attach reference images or a three-view, and the agent traces outlines and sections from them. Reuses Plan 7 U5's drawing import where possible. | G1 (+U5) |
| G-R5 | **X-ray, transparency and section views, plus an exploded view,** in the Design viewer, so the user and agents can see internals. | G4 |
| G-R7 | **Agents present views in their replies:** orbiting shots, internal (X-ray) shots, cutaways/sections, exploded views, and subsets (a few parts out of the whole). Each shows as a live view card in the thread that you can click to open in an **immersive** full-window viewer. | G1 (core `present_view` tool + event), G4 (cards + immersive) |
| G-R6 | **Model choice for shape work:** routing prefers the strongest vision-capable model (the user's Claude Code / Opus) for geometry-heavy prompts, and the UI warns when a small or non-vision model is used for complex shape work. | G1 (core routing hint) + G4 (UI warning) |

## Global Constraints
- **Earlier plans still bind:** Plans 1–7 (Gate, sandbox with deny-fork and no Mises-home reads, safe_io, untrusted data, honesty labels, tokens).
- **Rendering runs in the sandboxed worker.** Use offscreen rendering such as OCCT's offscreen view, or pyrender/vtk if they install cleanly. Rendering in the app via WebGL and posting the images back is an option only if worker rendering is infeasible, and it must be justified.
  - Plan 7 U1 builds part thumbnails in parallel. Put shared rendering in `core/src/mises/cad/render.py` with a small API: `render_views(part_or_assembly, views, size, appearance, section?)`. Note the merge with U1.
- **Images to models:** images sent to models count toward the context budget. Use ≤ 4 views at 512 px by default, and downscale.
- **Critique text is model output.** Treat it as data for the next step. It never runs as code.
- **The critique loop is bounded:** at most 3 rounds by default, configurable, with a time and cost budget, and each round visible in the thread as a compact card (before/after thumbnails, issues fixed).

## Tasks

### G1: Render-and-critique loop, reference matching, model routing hint (core)
- **`render_views` tool (read):** front, top, side and iso views, plus optional section planes and transparency. Returns image handles. The sandboxed worker produces PNGs.
- **`critique_model(part_id|project_id, request, reference_image_ids?)` (read):**
  - Calls the run's model if it is vision-capable; otherwise a configured vision model. If none is available, it returns "unavailable" with a clear reason.
  - Returns structured issues `[{view, issue, severity, suggested_fix}]` and a score.
- **Silhouette matching:** when a reference image is given, align it by bbox and orientation per view, then compute IoU (intersection over union) between the reference silhouette and the render. Report per view.
- **Loop:** add the loop to the designer, engineer and lead prompts: build → render → critique → fix, at most 3 rounds, stopping early when the score passes a threshold or no high-severity issues remain.
- **Thread cards:** emit `agent_announce` (Plan 7 U3c) cards of kind `"critique_round"`. If that event doesn't exist on your base, add the event kind additively.
- **Reference attach:** `POST /references` (image upload; reuse the Plan 4 photo validation) → `reference_id`. Agents get the id via context.
- **Routing hint:** add a `shape_complexity` estimate to the run (keywords: aircraft, organic, curved, loft, enclosure with internals, etc.). Plan 7's router reads it to prefer vision-capable strong models. If U3c isn't merged, expose it in the run record.
- **Tests:**
  - render produces 4 PNGs of correct size, with a section showing internals
  - IoU on synthetic shapes (identical → 1.0, offset → lower)
  - a fake vision model gives a deterministic critique and the loop stops correctly
  - budgets are respected

**G-R7 core (added 2026-10-30):** the read tool `present_view(target: part_id|project_id, kind: "orbit"|"internal"|"section"|"exploded"|"subset", parts?: [part_ids], section?: {origin, normal}, xray_parts?: [...], explode?: 0..1, camera?: {azimuth, elevation, distance}|"auto", orbit?: {duration_s, turns}, caption)`.
- It validates ids against the run's own build and emits `agent_announce` kind `"view_presented"` with a `view_spec` (no rendering needed; the app renders it live).
- It returns a small static thumbnail via render_views for non-visual clients such as the Claude Code transcript.
- Prompts: when presenting results, show 1–3 views chosen to explain the design: an orbit for the overall shape, a section or internal view for enclosures, a subset for specific parts. Keep the text short (R18).

### G2: Curved-geometry recipe library (core)
- **`mises_shapes`:** a module pre-imported into the sandboxed worker namespace, alongside build123d, bd and math. Recipes:
  - airfoils: `naca4/naca5`, `airfoil_from_points`
  - `wing(planform, sections, twist?, dihedral?)`: a loft through section placements
  - `blended_wing_body(...)`: a B-2/flying-wing style body with a smooth center-body blend and a sawtooth trailing edge option
  - `fuselage(stations)`: a multi-section loft
  - `sweep_profile(path_spline, profile, twist?)`
  - `smooth_loft(sections, ruled=False)`
  - `spline_surface(points_grid)` → `thicken(t)`
  - `blend_edges` / `variable_fillet` helpers
  - `enclosure(outer, wall, split_plane, bosses, ribs, lid, snap_fits)`
  - `standoffs`, `vent_slots`, `cable_path`
- **Documented recipe index:** a short one-line-per-recipe index injected into the designer and engineer prompts, plus a `recipes_list` / `recipe_doc(name)` read tool so the full docs load on demand and don't bloat prompts.
- **Prompt rules:** for organic or aero shapes, never approximate with ellipsoids or boxes; use the lofts and recipes.
- **Tests:**
  - every recipe builds a valid solid (`is_valid`, closed, positive volume)
  - NACA 2412 coordinates match the published formula
  - the blended-wing-body builds and is smooth (no self-intersection)
  - an enclosure with internals passes interference checks
  - a gallery script renders every recipe (uses G1 render if merged; otherwise skip)

### G3: Layout skeleton and internals packing (core)
- **Skeleton:** a part kind `skeleton` per build, with named parameters, datums (planes, axes, points), an envelope and reference curves.
  - Parts can reference skeleton values in their scripts via `skeleton("wing_span")` / `datum("spar_plane")`. These are injected read-only into the worker namespace.
  - Changing the skeleton regenerates the dependent parts (dependency tracking via the references used during a build).
- **Internals:**
  - `inner_volume(case_part)` returns the cavity solid.
  - `pack(components, cavity, clearance_mm, strategy="layered"|"grid")` proposes placements. It is greedy and simple, with clearances and keep-outs.
  - `section_check(project_id, planes)` gives interference and clearance reports per section.
- **Prompts:** for multi-part systems and enclosures, the lead and engineer create the skeleton first, then parts against it, then check internals with section_check and assembly_check.
- **Tests:**
  - a skeleton change regenerates dependent parts
  - pack fits 3 boxes into a case with 2 mm clearance, and reports a misfit when they're too big
  - section_check finds a deliberate collision

### G4: Viewer: X-ray, transparency, sections, exploded view, reference overlay, model warning (app)
- **Viewer modes:**
  - X-ray / transparency slider, per part or for all
  - section plane, draggable, with cap faces
  - exploded view, with a slider and automatic explode directions from the assembly or part centers
  - reference image overlay, pinned to a view with adjustable opacity, for comparing against a three-view
- **Critique round cards:** in the thread, show before/after thumbnails and the list of fixed issues. They are compact and expand on click, following R18.
- **Model warning:** a small warning chip when the selected model is not vision-capable or is a small local model and the prompt looks shape-heavy (uses the run's `shape_complexity`).
- **Coordination:** Plan 7 U2a/U2b/U8 are changing the viewer in parallel. Keep the new modes in `app/src/ui/viewer/modes/**`, with minimal hooks.

### G5: Integration and QA
- **B-2 benchmark:** prompt "model a B-2 bomber, ~1:100 scale" with the user's Claude Code (or a scripted fake model in CI). Record round-by-round renders and the critique scores. Also a "Raspberry Pi in a vented case with a fan and battery" internals benchmark.
- **Before/after:** compare against the old behavior and record it.
- **Merge:** merge with Plans 6 and 7, run the full suites, rebuild, then the final review and fix wave.
