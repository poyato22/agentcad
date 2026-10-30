# Home, Agents & Clarity Plan (Plan 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Base:** branch `sim-accuracy` at 23bf082, which is `main` d7a4927 plus the Plan 6 doc. Plan 6 (simulation accuracy, S1–S4) runs in parallel. Its S4 touches the Studies page and approval cards; stay out of those unless your task says otherwise.

## The user's requests (2026-10-30), formalized

Every item has an owner task, and the integration step checks each one.

| # | Requirement | Task |
|---|---|---|
| R1 | On launch, a **Home** screen shows all builds as a thumbnail grid, like Onshape's Documents page. You start working by opening one, which opens it as a tab. | U1 |
| R2 | Mises **accepts technical CAD drawings** (PDF, PNG/JPG, DXF) and creates the part from them. | U5 |
| R3 | **Every agent has its own browser**, with a preset list of suggested sites. Example: for "make a robotic arm", it can look up a servo's dimensions. | U4c, U4a |
| R4 | Agents can **pull up parts for the user to view**, with cost and specs. | U4c, U4a |
| R5 | A **parts list (BOM) UI** per build: made parts and bought parts, quantity, cost, vendor, specs, totals, export. | U4a (+U4c data) |
| R6 | **Remove the Sketch page.** | U2b |
| R7 | **Faster drawing generation.** | U6 |
| R8 | **No agent picker.** The right agent is chosen automatically from the prompt. | U3c, U3a |
| R9 | **Click an area of the build to give feedback.** The pinned location goes to the agent. | U2a (+U3c message) |
| R10 | **Simpler agent management.** One easy view of all agents. **One main agent** per build spawns sub-agents for specific tasks. | U3c, U3a |
| R11 | **Agents control the assembly.** For example, an agent brings a part into the assembly and places it with joints. | U3c |
| R12 | **Part clicking:** keep the label showing what was clicked, but remove the confusing CAD suggestion (ops) callout. | U2a |
| R13 | **Less clutter and a simpler, clearer view.** Move the Part/Assembly buttons. | U2b |
| R14 | **Remove the Variables panel** from the main UI. | U2b |
| R15 | **Onshape-style measure:** click an edge for its length, two points for the distance, a circle for its diameter and radius, a face for its area, two faces for distance and angle. | U2a |
| R16 | **Agents tell you more.** The thread shows a clear card when an agent creates a part, spawns an agent, adds to the assembly, or adds to the BOM. | U3c, U3a |
| R18 | **Study results in the thread are clear, not a wall of text.** Each study is a compact clickable card (type, status, key numbers, pass/fail, thumbnail) that **expands inline** to show its chart, checks and an Immersive button. Raw tool output is hidden by default. The agent's own reply after studies stays short and refers to the cards instead of restating numbers. | U3a (cards), U3c (prompt and result shape) |
| R19 | **Agents can use the assembly fully:** add parts, place them, move them, drive joints and **animate** motion that the user sees in the viewer. | U3c (tools), U2a/U3a (viewer playback) |
| R20 | **Rebuild the parts manager (the part list on the left) to be intuitive and fast, Onshape-style.**<br>- **Color in one click:** a swatch on each row opens the color/appearance popover directly, not three clicks.<br>- **A useful right-click menu**, also on the ⋯ button: Rename, Show/Hide, Isolate, Appearance…, Measure, Add to assembly, Duplicate, Export STEP/STL, Properties (mass, volume, bbox, material), Delete. Every item is useful.<br>- **Simple rows:** eye toggle, swatch, name, small mass/material hint on hover. Multi-select with Shift/⌘-click.<br>- **Speed:** no lag. Profile it, then memoize rows, virtualize long lists, and avoid re-render storms on events. | U8 |
| R21 | **Changeable viewer background:** theme default, solid colors, and an Onshape-style soft gradient, stored in the profile. | U8 |
| R22 | **Shiny materials:** appearance presets per part (Matte plastic, Glossy plastic, Brushed aluminum, Polished metal/chrome, Anodized, Rubber, Clear) with physically based metalness and roughness, plus an environment-light reflection, so metals look metallic. Stored per part, shown in thumbnails and the viewer, and agents can set it. | U8 |
| R17 | **In a build, view all its parts at once** in a zoomed-out grid, like Onshape's overview. | U1 |

## Global Constraints
- **Earlier plans still bind.** Plans 1–6 constraints remain: the Gate, never auto-approve, untrusted text as data, the sandbox, safe_io, honesty labels, tokens and themes, traffic-light inset, and keyboard reachability.
- **The browser is the existing hardened fetcher, never a real web browser.** Agent browsing reuses C5's fetcher: https only, private IPs blocked, the vetted IP pinned, size and time caps, no cookies. Page text reaches models only as nonce-delimited untrusted data.
  - **Suggested sites** (a curated, user-editable list in the profile) are fetched without a card.
  - **Any other domain** needs an approval card per domain, with "Always allow".
  - **Never:** form submission, logins, purchases or POSTs.
  - **What the user sees:** a readable snapshot (title, URL, extracted text, images proxied and size-capped), not live JS.
- **Simplicity is a requirement.** Every new UI surface must pass the question "can a first-time user tell what to do?" Fewer controls, with clear labels.

## Shared contracts

**Home and overview (U1)**
- `GET /parts/{id}/thumb.png?size=256`
  - Rendered in the sandboxed worker (OCCT plus an offscreen raster) or from the GLB via a headless render path.
  - Cached by part version.
- `GET /projects/{id}/thumb.png`: a composite of up to 4 parts.

**Agent browser (U4c)**
- **Tools**
  - `web_search(query, sites?)`: reads the search pages of the suggested sites first (each site entry carries a search URL template). Results are `[{title, url, snippet, site}]`.
  - `web_open(url)`: returns a page snapshot `{id, url, title, text (untrusted), tables, images:[{proxy_url}], fetched_at}`. The snapshot is stored per run.
  - `part_show(snapshot_id | catalog_id, {name, price, currency, vendor, url, specs[{key,value,unit}], image})`: puts a **part card** in front of the user in the thread.
- **Routes**
  - `GET /runs/{id}/browser` lists the run's snapshots.
  - `GET /browser/snapshots/{sid}` returns one snapshot.
- **Profile:** `suggested_sites: [{name, domain, search_url, categories}]`.
  - Defaults: McMaster-Carr, Misumi, Digi-Key, Mouser, Adafruit, SparkFun, Pololu, ServoCity, GetFPV, RobotShop, AliExpress, Amazon, and manufacturer datasheet hosts. Mark the defaults "suggested", and note in the docs that some sites block bots.

**BOM (U4c)**
- `GET /projects/{id}/bom` returns `[{id, kind: "made"|"bought", part_id|null, catalog_id|null, name, qty, unit_cost, currency, vendor, url, specs, notes, source: "user"|"agent"}]` plus totals.
- `POST/PATCH/DELETE /projects/{id}/bom[/{id}]`.
- Agent tool `bom_add` (local_mutate).
- Made parts come in automatically. Their cost is estimated from mass × material $/kg and labelled "estimate".
- `GET /projects/{id}/bom.csv`.

**Routing and main agent (U3c)**
- `POST /runs` with no `agent` → the router picks one. Response and run carry `routed: {agent, reason, confidence}`.
  - The router is a cheap classifier: rules plus an optional small-model call.
  - It defaults to `engineer` for system builds, `designer` for single parts, `sim` for "test/analyze", `verifier` for "check/review", and drawing-import for an attached drawing.
- **One lead per build.** Each build has one long-lived **lead** run (`role: "lead"`). New prompts in a build go to the lead. The lead decides whether to handle the work itself or spawn sub-agents (existing `agent_spawn`); those children have `role: "sub"` and a `task` title.
  - `GET /projects/{id}/agents` returns the lead plus its sub-tree with status and activity.
- **Announcement events:** `agent_announce {run_id, kind: "part_created"|"agent_spawned"|"assembly_added"|"bom_added"|"part_shown"|"drawing_imported", title, detail, ref}` is emitted by the core whenever those happen, whatever the model says. The lead's prompt also tells it to say so briefly.
- **Feedback pins:** `POST /projects/{id}/feedback {part_id, point:[x,y,z], face_id?, text}` → a message to the lead with a location block (part, face, point, nearby feature) inside the untrusted user-data delimiters. It returns the run and step.

**Drawing import (U5)**
- `POST /imports/drawing` (multipart: file ≤ 20 MB; pdf/png/jpg/dxf, sniffed) → `{import_id}`. It starts a run routed to the `drawing-import` agent, or a tool flow.
- **DXF:** parse the geometry directly (ezdxf, read-only) into profiles plus dimensions.
- **PDF and images:** rasterize the PDF pages (pdftoppm or pypdfium2), then a vision-capable model reads the views, dimensions, tolerances and notes into a structured `DrawingSpec`. The part script is built from that.
- **Verify:** render our own drawing (the existing drawing.py) and compare the dimensions programmatically against the `DrawingSpec`. Iterate up to 3 times, then report the matches and mismatches as a table.
- No vision model available → a clear error saying which models support images.

## Tasks

### U1: Home screen and build overview (app + small core)
- **Home on launch**, after boot and the system check:
  - A grid of build tiles: thumbnail, name, part count, last edited, lead-agent status dot.
  - Search, sort, and a New build tile.
  - Trashed builds behind a "Trash" filter.
- **Opening a tile** opens or focuses a build tab. A Home button (⌘0) returns to the grid.
- **Build overview:** inside a build, an "All parts" grid view showing each part's thumbnail, name, material and mass. Click one to focus it in the viewer.
- **Core:** the thumbnail routes and their cache.

### U2a: Viewer interaction: selection labels, measure, feedback pins (app + tiny core)
- **Click selection** keeps the label chip ("Face · planar · 1,200 mm²", "Edge · circle Ø4.2"). Remove the ops suggestion callout. Ops move to an explicit "Modify…" action in the part's menu.
- **Measure (M key, or Measure button), Onshape-style:**
  - edge → length
  - circle or arc → Ø and R
  - face → area
  - two points or vertices → distance with ΔX/ΔY/ΔZ
  - two faces → distance and angle
  - Shift-click to add to the selection
  - results in a small panel
- **Precision:** compute from exact B-rep data. The core gets `POST /parts/{id}/measure {refs:[{face_id|edge_id|vertex_id|point}]}` in the sandboxed worker. Mesh-picked IDs map to topology IDs (existing face/edge maps). Do not rely only on mesh math.
- **Feedback pins:** a "Comment" mode. Click a spot, type, send → `/projects/{id}/feedback`. Pins stay visible on the model and in the thread.

### U2b: Declutter, remove Sketch and Variables, move the toggle (app)
- **Remove:**
  - the Sketch page, route, shortcut, tests and fake-core routes (app only; the core sketch API can stay)
  - the Variables panel from the left pane. Parameters stay reachable only in "Modify…" → Code/parameters for power users.
- **Move the Part/Assembly switch** into the viewer's top-left corner, as a segmented control.
- **Audit every page for clutter.** Merge duplicate status displays, hide rarely used controls behind menus, and give more space to the model. Record before/after screenshots and a list of what was removed or moved.
- **Renumber the tab shortcuts** after Sketch's removal and document them.

### U3c: Routing, lead agent and sub-agents, announcements, assembly control (core)
- Implement the routing, lead, announcements and feedback contracts.
- **Prompts:**
  - The lead decomposes work into named sub-agent tasks. Each sub-agent is spawned with a clear task title.
  - The lead reports back in short messages.
- **Tools:**
  - Assembly tools are in the lead's and engineer's toolsets.
  - New `assembly_add_part(part_id, place: {on_face?, mate?, offset?})` is a convenience wrapper that adds an instance and a default joint.
  - Prompts tell agents to bring created parts into the assembly when building systems.
- **Claude Code mapping:** the lead runs as the Claude Code main session, and sub-agents run as Mises spawns, so the hierarchy is the same on both runtimes.

### U3a: Agent UI: no picker, one clear view, announcement cards (app)
- **Remove agent pickers** from New run, Composer and the dialog. Show the routing result as a small chip ("→ engineer · system build"). A hidden "Use a specific agent…" override stays in an overflow menu.
- **Agents panel redesign:** one simple panel per build, showing the lead at the top and sub-agents indented beneath it. Each row shows task title, status, live activity, and a click target that opens the conversation. All builds are reachable from Home.
  - Remove the separate dense manager view, or fold it into an "All agents" list on Home.
  - Keep delete and clean-up, but simpler.
- **Announcement cards** in the thread for each `agent_announce` kind, with a jump to the referenced item.

### U4c: Agent browser and BOM (core)
- Implement the browser and BOM contracts on top of the C5 fetcher.
- **Site search adapters:** URL templates with an HTML result extractor, made robust and tested on fixture pages.
- **Part spec extraction:** reuse the catalog importer (dims, mass, mount pattern, price).
- **Prompts:** for system builds, the engineer and lead look up real components (servos, motors, bearings) through `web_search`/`web_open`. They `part_show` the chosen ones, `bom_add` them, and use their dimensions in the design.

### U4a: Browser panel, part cards and parts list UI (app)
- **Per-agent "Browser" tab** in that agent's conversation. It lists the pages the agent visited, in order, as readable snapshots: title, URL with domain, text, tables, and proxied images. "Open in browser" uses openExternal.
- **Part cards** in the thread: image, name, price, vendor, key specs, "Add to parts list", "Insert into build" for catalog-backed parts, and "Open".
- **Parts list page or panel per build:** a table with made and bought parts; editable qty, cost and notes; totals by vendor; CSV export; estimate labels.
- **Suggested sites editor** in Profile.

### U5: Technical drawing → part (core + small app)
- Implement the drawing-import contract, including the vision step and the verify loop.
- **DXF:** add ezdxf (exact version) and parse its entities, plus DIMENSION entities and their measurements.
- **PDF and raster:** pypdfium2 (or pdftoppm, if present) at 200 dpi, with a cap on page count.
- **Untrusted input:** drawing text is untrusted data.
- **App:** a drop zone on Home and in a build ("Drop a drawing to make a part"), a progress card, and a result card with the dimension match table and a side-by-side view of the original drawing and the generated drawing.
- **Tests:** a fixture DXF (plate with holes, known dims) gives an exact part. A fixture PNG drawing uses a scripted fake vision model to produce a `DrawingSpec`, then the part, then the verify table.

### U6: Faster drawing generation (core)
- **Profile first.** Measure the current drawing time on 3 parts (simple plate, bracket with holes, 50-face part) and record the baseline.
- **Then speed it up:**
  - parallel view projection
  - cache HLR results by part version
  - simplify tessellation and HLR tolerances
  - lazy DXF/PDF generation
  - background pre-generation on `part_changed`
  - stream views as they finish
- **Target:** ≥3× faster on the 50-face part, or document why not. Output stays identical, checked against fixture SVG structure.

### U8: Parts manager, appearance, background (app + small core)
Implements R20, R21 and R22.
- **Core:**
  - `PATCH /parts/{id}` gains `appearance: {preset, color, metalness, roughness, opacity}`.
  - The profile gains `viewer_background: {mode: "theme"|"solid"|"gradient", colors}`.
  - Agent tool `part_set_appearance` (local_mutate).
  - Thumbnails (U1) use the appearance when it is present.
- **App:** a rebuilt part list (it owns app/src/ui/left/PartList*, together with its menus), an appearance popover, a background setting in the viewer corner menu and in Profile, and PBR materials with a small built-in environment map (RoomEnvironment/PMREM) in the viewer.
- **Performance:** measured before and after (React/Preact profiler, interaction latency), with a target under 50 ms per click on a 100-part build.

### U7: Integration, real-core QA, final review
- Merge with Plan 6.
- Verify every R1–R17 against the real core in WebKit, with screenshots.
- Run the full suites, rebuild the app, run the final review, and do one fix wave.
