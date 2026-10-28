# Pages, Models & UX Implementation Plan (Plan 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make every top-bar page real (Design, Studies, Sketch, Drawing, Print), fix the window chrome (macOS traffic lights covering UI, unclickable tabs), switch to a Bloomberg-terminal-style open font with a light mode, simplify the agent interface into a conversation, make model choice smart (downloaded Ollama models, OpenRouter free models, Kimi, recommendations, one-click local model download), and make parallel agents automatic from the Mac's specs.

**Spec:** `docs/design/2026-10-24-mises-design.md` (§5 UX, §7.5 simulator, §7.7 printing, §7.9 sketch); research: `research/models/model-choice-2026-10.md`, `research/integration/fusion-360.md`, `research/models/README.md`.

## Global Constraints

- Plans 1–3 constraints still bind (Gate on every call, never auto-approve, approvals only by trusted action on the card, secrets never echoed, untrusted text as text, no auto-numbered names, 127.0.0.1 + admin token, compliance rules for the user's Claude Code).
- **Font:** Iosevka (SIL OFL) bundled locally via `@fontsource/iosevka` (or the official release files), used for ALL UI text; headings use Iosevka at a heavier weight (no second family). Bloomberg's own typeface (Bloomberg Prop) is proprietary — never bundle or name it. Remove IBM Plex packages.
- **Themes:** `dark` (current palette, unchanged values) and `light`: background `#f4f4f2`, panels `#ffffff`/`#ececea`, lines `#d6d6d2`/`#bdbdb8`, text `#111111`/`#4a4a4a`/`#7a7a7a`, blue `#0a5bd8`/`#2f6fe0` (tint `rgba(10,91,216,.10)`), green `#1f8a4c`, amber `#b7790f`, red `#c8321e`. Same rule: blue only for selected/agent-changing/needs-human; green/amber/red only pass/warn/fail. Setting `theme: "system"|"light"|"dark"` (default system) stored in the profile; applied via `data-theme` on `<html>`; viewer background/grid follow the theme.
- **Window chrome:** the window uses Tauri's overlay title bar; reserve a 78 px left inset and 28 px top drag strip on EVERY full-height surface that touches the top-left (top bar, setup wizard, profile pane, dialogs, page headers) so the traffic lights never cover content. `data-tauri-drag-region` on empty top-bar space.
- **Simulator is a guide, not a guarantee:** every sim result shows solver, mesh size, material model, error band and "guide only"; FDM knock-down applied; `fidelity_class: "fem"`.
- **Print safety:** sending a job to a printer is `irreversible` (approval card); never send while the printer reports an error; show material, time, grams, cost and preview before the card.
- External binaries are optional and detected: CalculiX (`ccx`, conda-forge `calculix`), gmsh (pip wheel in the core venv), PrusaSlicer CLI (`/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer`), Ollama (`/opt/homebrew/bin/ollama`). Missing → the page shows exactly what to install and the command; never crash.
- GPL solvers/slicers run only as separate processes (never linked).

## Review Focus

1. Traffic lights never overlap any control or text in any page, dialog, wizard or pane, at any window size ≥ 1280×800, in both themes.
2. A study or slice on a large/complex part can't hang the app: timeouts, cancel, progress, and a clear error when a binary is missing.
3. The simplified conversation view still shows every approval card, failed tool call and error — nothing safety-relevant hidden behind "details".
4. Auto parallel agents never oversubscribe a local model (one concurrent run per local model unless memory allows more) and respect provider rate limits (OpenRouter free: 20 req/min).
5. Light mode contrast: all text ≥ 4.5:1 against its background; status colors still distinguishable.

---

### Task 1: App shell — chrome, clickable tabs + page router, Iosevka, light mode (app)

**Files:** `app/src/App.tsx`, `app/src/styles/**`, `app/src/ui/topbar/**`, new `app/src/ui/pages/{router.ts,PageShell.tsx}` + `app/src/ui/pages/{studies,sketch,drawing,print}/index.tsx` stubs (each renders a PageShell titled with the page and a "coming next" body — Tasks 6–8 replace them), `app/src-tauri/tauri.conf.json` (titleBarStyle overlay + hiddenTitle if not already), `app/package.json` (fonts), theme toggle in `app/src/ui/profile/sections/ProfileSection.tsx` + `profile` type field, e2e `app/test/e2e/shell.spec.ts`.
- Router: `activePage` signal (`"design"|"studies"|"sketch"|"drawing"|"print"`), ⌘1–⌘5 shortcuts, tabs clickable, active tab underlined blue; Design keeps today's layout; other pages render full-width under the top bar; the agent console stays available on every page as a right-side panel that can collapse to a 36 px rail.
- Tests: computed font-family is Iosevka everywhere; no IBM Plex in bundle; theme switch flips tokens (unit) and persists via PUT /profile; e2e at 1280×800 and 1600×1000 in both themes: the bounding boxes of all visible interactive elements and text don't intersect the traffic-light zone (x < 78, y < 28); tabs navigate; ⌘1–5 navigate; contrast check for light tokens (compute WCAG ratios in a unit test).

### Task 2: Core — models, Kimi, local models, auto parallelism (core)

Branch from `p4-core-fixes` (which adds Ollama-downloaded and OpenRouter-free listings). **Files:** `core/src/mises/providers/registry.py`, `core/src/mises/server/ui_api.py`, `core/src/mises/profile.py`, `core/src/mises/runtime.py`, new `core/src/mises/hardware.py`, new `core/src/mises/local_models.py`, tests.
- Vendor `moonshot`: kind openai, base `https://api.moonshot.ai/v1`, env `MOONSHOT_API_KEY`; models `kimi-k3.1-pro`, `kimi-k3.1-pro-highspeed`, `kimi-k3` (verify ids in Moonshot docs); carry `reasoning_content` back on multi-turn tool calls (verify field per Moonshot docs; implement in OpenAICompatProvider behind a vendor flag).
- `/models` entries gain `recommended: "best"|"local"|"free"|null` and `note` (one line, from research): best = claude-code/opus & anthropic/claude-opus-5-5; local = qwen3-coder:30b, gpt-oss:20b, qwen3.6:27b when they fit memory; free = OpenRouter free tool-capable.
- `hardware.py`: `machine() -> {cpu_cores, perf_cores, ram_gb, chip}` (sysctl on macOS behind platform.py). `auto_parallel(model_ref) -> int`: cloud/claude-code → min(4, max(1, perf_cores // 2)); OpenRouter free → 1; local model → 1 unless free RAM ≥ 2× model size (then 2).
- Profile `max_parallel_agents: int | "auto"` (default "auto"); Runtime ENFORCES the limit: runs beyond it queue (status `queued`, event `run_queued`) and start when a slot frees; orchestrator respects it.
- `local_models.py`: `ollama_status()` {installed, running, version, models[{name,size_gb}]}; `recommended_local(machine)`; `POST /local/pull {model}` streams Ollama `/api/pull` progress as events `local_pull` {model, completed, total, status}; `POST /local/start` launches `ollama serve` if installed but not running (detached, user-visible note). Routes admin-only.
- Tests with mocks for sysctl/ollama; queueing tests; Kimi request mapping test.

### Task 3: Core — static stress studies (gmsh + CalculiX) (core)

**Files:** new `core/src/mises/sim/{__init__,mesh,ccx,results,environments,tools}.py`, `core/src/mises/server/sim_api.py`, tests `core/tests/sim/**`. Add `gmsh` to core deps.
- Detect `ccx` (PATH, conda base `~/miniconda3/bin/ccx`, `/opt/homebrew/bin/ccx`); report install command `conda install -c conda-forge calculix` when missing (do NOT install it from code; the implementer may install it on this Mac for tests).
- Pipeline: mirror part STEP → gmsh tetra mesh (coarse then fine, size from bbox) → CalculiX static linear-elastic input (fixed face set + load face set + force vector, material E/ν from filament data × FDM knock-down 0.7 on strength) → run in a temp dir under the run's workdir with timeout → parse `.frd`/`.dat` → max von Mises (MPa), max displacement (mm), safety factor = yield×knockdown / max σ, error band = |fine−coarse|/fine on peak stress, and a GLB with per-vertex von Mises colors.
- Face selection: environments presets as structured choices — `fix: "bottom"|"top"|"x-min"|...|face_id`, `load: {face: same, newtons, direction: "-z"|...}`; face_id from `mirror_measure` faces list (add face ids/centers/normals to metrics if absent).
- Tools: `sim_static_run(part_id, fix, load, material)` (local_mutate, step_kind SIM_RUN, `fidelity_class:"fem"`), `sim_result_get(sim_id)` (read), `sim_env_list()` (read). Routes: GET /sims?part_id, GET /sims/{id}, GET /sims/{id}/result.glb, POST /sims (UI-started run → goes through Gate under a UI run).
- Tests: cantilever beam analytic check (σ = 6FL/(bh²) within 15% on fine mesh), missing ccx → clear error, timeout kill, error band computed, GLB has colors.

### Task 4: Core — drawings and printing (core)

**Files:** new `core/src/mises/drawing.py`, `core/src/mises/print/{__init__,slicer,prusalink,tools}.py`, `core/src/mises/server/{drawing_api,print_api}.py`, tests.
- Drawing: `mirror_drawing(part_id)` → SVG with front/top/right orthographic + iso views (build123d `project_to_viewport` + `ExportSVG`), overall dimensions from bbox, hole callouts from metrics, title block (part label, material, date, "Mises"), no numbered names; also DXF export; worker runs in the existing sandboxed CAD worker. Route GET /parts/{id}/drawing.svg (+ .dxf).
- Printing: printers in profile (`printers: [{id, kind:"prusalink", host, name}]`, API key in Keychain `mises/printer:<id>`); PrusaLink v1 API (verify endpoints: status, upload via PUT /api/v1/files/usb/<name> with `Print-After-Upload`); PrusaSlicer CLI detect + slice (`--export-gcode` or bgcode, printer/filament profile from bundled default ini for MK4/MINI selectable), parse estimates (time, filament g/mm, cost from material $/kg); tools `print_printers_list` (read), `print_slice(part_id, printer_id, material)` (local_mutate → handle + estimate), `print_send(slice_id, printer_id)` (irreversible; refuses when printer state is error/busy). Bambu: listed as "coming later" in the UI only.
- Routes: GET/POST/DELETE /printers, GET /printers/{id}/status, POST /slices, GET /slices/{id}, GET /slices/{id}/preview.png (if PrusaSlicer can render a thumbnail; else none), POST /prints (creates the irreversible tool call under a UI run so the approval card appears).
- Tests: SVG has 4 views + dims; fake PrusaLink server (status, upload, error state refusal); slicer missing → clear install hint (`brew install --cask prusaslicer`); estimate parsing from a fixture gcode.

### Task 5: Simplified agent interface + setup order + smart model picker (app)

**Files:** `app/src/ui/console/**` (not approvals internals), `app/src/ui/dialogs/NewRunDialog.tsx`, `app/src/ui/setup/**` (step order), `app/src/ui/profile/sections/AgentsSection.tsx`, e2e.
- Conversation view (default): one thread per run — user messages, agent replies, and compact activity lines ("measured mount plate · 8.0 cm³", "proposed change on Onshape branch") with a ▸ to expand raw tool IO; approval and question cards inline in the thread; failed calls and errors always visible inline (red); run switcher as a single dropdown at the top ("designer · stiffen upright ▾" with status dot); "+ New" opens an inline composer (agent + model chips, defaults from profile) instead of the dialog; the old dense log stays available as "Details" toggle. Keep every Plan 2 safety behavior.
- Setup wizard order: Welcome → **Profile** (name, units, material, theme) → Connect a model → Onshape (optional) → Done. Profile is what "login" sets up.
- Model picker everywhere: groups "Recommended", "Your Claude Code", "On this Mac (Ollama)", "Free (OpenRouter)", "API keys"; badges from `recommended`/`free`/`note`; Ollama not running → "Start Ollama" button (POST /local/start); recommended local model not downloaded → "Download (19 GB)" with progress from `local_pull` events.
- Parallel agents: "Auto (N on this Mac)" default with the reason ("M5 Pro · 24 GB · cloud model → 3"), manual override optional.
- Tests: thread rendering from real step fixtures; approval card + failed call visible in conversation view; inline composer creates a run; setup order; picker groups and badges; download progress rendering.

### Task 6: Studies page (app)  ·  Task 7: Sketch page (app + small core)  ·  Task 8: Drawing and Print pages (app)

- **Studies:** part picker; setup (fixed face, load face + N + direction, material) with presets; Run → progress → results: design | stress split viewer using `result.glb` colors with legend in MPa, numbers (max σ, max displacement, safety factor colored pass/warn/fail with thresholds 2.0/1.2), error band, solver/mesh/material label, "guide only"; history of studies per part; "Ask agent to fix" sends the result as context to a run. Missing ccx → install card.
- **Sketch:** canvas (pen/mouse/trackpad strokes, line/rect/circle tools, snap, typed dimension labels on strokes, undo/redo, clear), photo upload; "Build part" creates a run with the sketch as structured data (strokes as polylines in mm with dimensions) in the context block plus the photo as an image for vision-capable models; core: `POST /sketches` stores sketch JSON/PNG in the run workdir and `mises_context_get` includes it; at most 3 clarifying questions come back via ui_ask_user (already exists).
- **Drawing:** part picker → SVG drawing view (pan/zoom), export SVG/DXF/PDF (PDF via the webview print-to-PDF or rendering the SVG), regenerate on part change.
- **Print:** printer setup (add PrusaLink printer: host, API key), status light; part + material → Slice → preview, time, grams, cost; "Send to printer" creates the irreversible call → approval card (in the console thread) → status polling; slicer missing → install card; Bambu "coming later".
- Each page: unit tests + e2e against the fake core (extend fake core with the Task 3/4 routes and realistic fixtures dumped from the real core once those land).

### Task 9: Integration, real-app QA, final review

- Merge all; contract test covers new routes with real fixtures; full suites; rebuild app; **real-app visual QA**: launch the built `.app`, take screenshots of every page in both themes at 1280×800 (cmux-cua window screenshots or `screencapture -l <window id>`), check traffic-light clearance and layout; run one real study on a mirror box part with installed CalculiX; one real slice if PrusaSlicer is installed; one real local-model run with the user's Ollama (`huihui_ai/qwen3.5-abliterated:9b`) building a 40×20×5 plate to confirm the build123d fix; record results.
