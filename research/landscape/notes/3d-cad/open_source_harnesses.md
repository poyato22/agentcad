# Open-source AI agent harnesses, MCP servers, and Claude Code/Cursor workflows for 3D / mechanical CAD

Research date: 2026-10-24. Star counts and "last push" dates come from the GitHub API (`gh api repos/...` / `gh search repos`) pulled on 2026-10-24 unless noted, so they are point-in-time. "Pushed" means the date of the last push to any branch, which approximates last activity. README claims are the projects' own claims, not independent verification. A GitHub search rate limit cut off a few late queries (Prusa/Orca/Moonraker/Shapr3D/"cad-mcp"), and those gaps are noted below.

## Q1. MCP servers / plugins per CAD application

### Takeaway
Every major CAD and 3D tool now has at least one community MCP server, and most of them follow the "addon in the app + socket + MCP bridge + `execute_code` escape hatch" pattern that blender-mcp started. The dominant ones by stars are ahujasid's Blender MCP (about 32k, now renamed `mcp-for-blender`), neka-nat/freecad-mcp (about 3.0k), and mhyrr/sketchup-mcp (about 509). Official vendor servers exist for Fusion (Autodesk), Onshape (PTC, FeatureScript only) and Rhino (McNeel RhinoAI). I found no official server for SolidWorks, Shapr3D or Inventor.

### Cited Findings

**Blender**
- ahujasid/blender-mcp has been renamed to **ahujasid/mcp-for-blender**: 32,216 stars, pushed 2026-10-24, described as a "Community plugin to control Blender 3D with any LLM of your choice. Not affiliated with the official Blender Foundation." It installs with `claude mcp add blender uvx mcp-for-blender`. — [GitHub](https://github.com/ahujasid/mcp-for-blender)
- Its features include two-way socket communication. Its "Visual verification" feature offers "Multi-angle, camera, wireframe, X-ray and animation-strip views, plus renders, so the AI checks its own work." It can pull assets from Poly Haven, Sketchfab and Poly Pizza, and it can generate AI 3D models via Meshy, Tripo, Hyper3D Rodin and Hunyuan3D. There is now a paid "Premium" tier for generation without bringing your own keys, and telemetry is on (a section covers turning it off). — [README](https://github.com/ahujasid/mcp-for-blender)
- A third-party comparison calls Blender MCP "not a precision CAD tool (no exact solids or STEP)." It produces meshes only. — [BuildCAD AI guide, updated 2026-10-16](https://buildcad.ai/guides/cad-mcp-servers)
- Other Blender MCPs:
  - seehiong/blender-mcp-bridge: 79 stars, pushed 2026-09-13, "93 tools for modeling, sculpting, architecture/MEP".
  - bpy-dev/blender-mcp: 98 stars, pushed 2026-09-12, an "Independent enhanced distribution of Blender Lab MCP".
  - ProfRino/Blender-MCP-Assembly-Skill: 78 stars, pushed 2026-05-25, a geometry-building skill for "reliable 3D models in Blender via MCP".
  — [GitHub search](https://github.com/search?q=blender+mcp&type=repositories)
- Whether there is an "official" Blender Lab MCP is unconfirmed. Only one blog source and the bpy-dev repo description refer to a "Blender Lab MCP", and a `blender/blender-mcp` repo does not exist on GitHub (404). — [bpy-dev/blender-mcp](https://github.com/bpy-dev/blender-mcp)

**FreeCAD**
- **neka-nat/freecad-mcp**: 3,036 stars, pushed 2026-10-13. It is an RPC-server addon in FreeCAD with tools for creating and editing models, and its docs cover "Available tools, screenshots, FEM analysis." — [GitHub](https://github.com/neka-nat/freecad-mcp)
- BuildCAD says it supports FreeCAD 1.0/1.1, returns "screenshots after most tools" plus CalculiX FEM, includes a code-execution tool, and needs FreeCAD open. It also says "Older forks not updated since 2025." — [BuildCAD AI](https://buildcad.ai/guides/cad-mcp-servers)
- Forks and alternatives:
  - bonninr/freecad_mcp: 227 stars, last push 2025-03-20, so stale.
  - contextform/freecad-mcp: 139 stars, pushed 2025-08-15, with an npx setup and a "model a house" demo.
  - **blwfish/freecad-mcp**: 61 stars, pushed 2026-10-23, with "32 tools". It reads the Report View, inspects sketch constraints, checks geometry errors, wall thickness and collisions, and covers CNC toolpaths/G-code and mesh import.
  - sandraschi/freecad-mcp: 32 stars, pushed 2026-10-24, a headless build plus FluidX3D/OpenFOAM CFD extensions.
  - CREATeNG/freecad-mcp-bridge: 19 stars, pushed 2026-10-24.
  — [GitHub search](https://github.com/search?q=freecad+mcp&type=repositories); [blwfish README](https://github.com/blwfish/freecad-mcp)

**OpenSCAD**
- **RobertCoop/openscad-mcp**: 163 stars, pushed 2026-09-10, installs with `uvx openscad-mcp`. The `render` tool returns "Images with a text digest before each one (camera, view direction, scale, bbox)" and supports multi-view, custom camera, and exact cross-section with scale bar. It also has measurement and validation (volume, cavities, wall thickness via section), assembly interference `check` (CLI exit codes 0/1/2), before/after comparison, and a bundled Claude Code skill (`skills/openscad-design/SKILL.md`) plus plugin. — [GitHub](https://github.com/RobertCoop/openscad-mcp)
- **jhacksman/OpenSCAD-MCP-Server**: 208 stars, pushed 2026-09-07, described as "Devin's attempt at creating an OpenSCAD MCP Server that takes a user prompt and generates a preview image". It now needs no API keys or CUDA, has a headless Docker image, and its old image-to-3D reconstruction prototype is archived. — [GitHub](https://github.com/jhacksman/OpenSCAD-MCP-Server)
- Smaller servers include format37 (13 stars), rahulgarg123 (9 stars), sergiudanstan (7 stars, for Claude Code) and fboldo (7 stars, STL+PNG rendering). — [GitHub search](https://github.com/search?q=openscad+mcp&type=repositories)
- BuildCAD's verdict is that OpenSCAD MCP is "Good for simple printable parts" and produces meshes only, with no STEP. — [BuildCAD AI](https://buildcad.ai/guides/cad-mcp-servers)

**CadQuery / build123d**
- **pzfreo/build123d-mcp**: 129 stars, pushed 2026-10-15, Apache-2.0, on PyPI and in the MCP Registry, tagline "Give your AI CAD eyes." It renders previews, measures geometry, inspects `last_error`, validates before export, and exports STEP/STL/SVG/DXF. For imported-STEP editing it has feature recognition (`find_candidates`, `interface_features`, `edit_feature` with protected references and predicted-versus-measured volume checks). — [GitHub](https://github.com/pzfreo/build123d-mcp)
- build123d-mcp self-reports that on the public CADGenBench leaderboard (June 2026) using it "raised the same model's score from 0.360 to 0.457 and CAD validity from 88% to 100%." — [README](https://github.com/pzfreo/build123d-mcp). There is a reproducible pipeline in pzfreo/cadgenbench-build123d (6 stars, "Claude Code + build123d-mcp"). — [GitHub](https://github.com/pzfreo/cadgenbench-build123d)
- rishigundakaram/cadquery-mcp-server: 20 stars, last push 2025-06-29, "CAD generation and verification tools using CAD-Query". — [GitHub](https://github.com/rishigundakaram/cadquery-mcp-server)
- **Svetlana-DAO-LLC/cad-agent**: 53 stars, pushed 2026-02-17, a containerized build123d rendering server that "lets AI agents see what they're building." It frames the problem as "AI agents doing CAD work are essentially blind." — [GitHub](https://github.com/Svetlana-DAO-LLC/cad-agent)
- BuildCAD calls build123d-mcp "the most complete local build123d server." BuildCAD (Reshot Technologies) sells its own hosted build123d MCP with 5 tools and 300 runs/day free, so its rankings are self-interested. — [BuildCAD AI](https://buildcad.ai/guides/cad-mcp-servers)

**Fusion 360**
- Autodesk's official Fusion MCP was announced in April 2026 with a local Fusion MCP server (needs Fusion running) and a remote Fusion Data MCP. A cloud **Fusion Compute MCP** went to public beta on 2026-09-15. — [Autodesk blog](https://www.autodesk.com/products/fusion-360/blog/fusion-compute-mcp/); [Engineering.com](https://www.engineering.com/autodesk-announces-fusion-mcp-servers-and-more-ai-updates/)
- BuildCAD says the Fusion MCP has been built into Fusion since May 2026, that its tools are discovered from Fusion rather than documented, and that Autodesk calls it "not yet reliable for complex models or full assemblies." It is not open source. — [BuildCAD AI](https://buildcad.ai/guides/cad-mcp-servers)
- Community Fusion servers:
  - **faust-machines/fusion360-mcp-server**: 131 stars, pushed 2026-10-06, marked Beta, on PyPI. It uses an add-in TCP server on :9876 and a CustomEvent to the main thread, and is tested with Claude Code.
  - **AuraFriday/Fusion-360-MCP-Server**: 127 stars, pushed 2026-01-28, listed on the Autodesk App Store. It runs arbitrary Python inside Fusion (`adsk.core/fusion/cam`), added a thread-safe work queue in November 2025, and pulls API docs and samples from Autodesk cloudhelp.
  - ArchimedesCrypto/fusion360-mcp-server: 82 stars.
  - JustusBraitinger/Autodesk-Fusion-360-MCP-Server: 56 stars.
  - ndoo/fusion360-mcp-bridge: 27 stars, for Claude Code CRUD.
  - rahayesj/ClaudeFusion360MCP: 13 stars.
  — [GitHub search](https://github.com/search?q=fusion+360+mcp&type=repositories); [faust README](https://github.com/faust-machines/fusion360-mcp-server); [AuraFriday README](https://github.com/AuraFriday/Fusion-360-MCP-Server)

**Onshape**
- PTC's official Onshape FeatureScript MCP Server was announced 2026-08-13 and is early access via Onshape Labs. It "writes FeatureScript custom features, not general modelling." — [PTC press release](https://www.ptc.com/en/news/2026/onshape-launches-featurescript-mcp-server); [BuildCAD AI](https://buildcad.ai/guides/cad-mcp-servers)
- **ReshefElisha/jarvis-onshape-mcp**: 175 stars, pushed 2026-04-22, a Claude Code plugin with about 60 tools.
  - Every mutation returns `{ok, status, feature_id, error_message, changes, hints}` with bbox, part-count and mass deltas.
  - `render_part_studio_views` / `render_assembly_views` return shaded PNGs (front/top/right/iso). It also has `crop_image` and a `compare_to_reference` side-by-side composite.
  - Drawing OCR (`extract_drawing_dimensions` via Tesseract) works around "Claude's vision downsampling".
  - Other tools: Variable Studios, a FeatureScript escape hatch, mates, and bbox interference checks.
  - It ships a `vision-decompose` skill and a RESEARCH.md that recommends human review of a feature tree extracted from the image before building.
  — [GitHub](https://github.com/ReshefElisha/jarvis-onshape-mcp)
- Bennyco86/ultron-onshape-mcp (1 star, pushed 2026-10-02) is a fork that claims a "3.2x faster verify loop". — [GitHub](https://github.com/Bennyco86/ultron-onshape-mcp)
- **hedless/onshape-mcp**: 156 stars, pushed 2026-09-10, 45 tools. It covers sketches on standard planes, extrude/revolve/fillet/chamfer/boolean/pattern, assemblies with mates, interference checking, variable tables, FeatureScript eval/bbox, and export to STL/STEP/Parasolid/glTF/OBJ. It has no image rendering. — [GitHub](https://github.com/hedless/onshape-mcp)
- Others: altendky/onshape-mcp (20 stars, pushed 2026-10-24) and gpambrozio/onshape-mcp (3 stars, "87 tools", browser sign-in). — [GitHub search](https://github.com/search?q=onshape+mcp&type=repositories)

**SolidWorks** (Windows/COM only)
- **vespo92/SolidworksMCP-TS**: 243 stars, pushed 2026-09-20. Its README says "Alpha / Experimental … most tools have not been validated against a live SolidWorks instance." It routes COM calls with 13 or more parameters through auto-generated VBA macros. — [GitHub](https://github.com/vespo92/SolidworksMCP-TS)
- Others:
  - alisamsam/Solidworks-MCP: 181 stars, pushed 2026-03-23, "22 tools for parts, sketches & features".
  - andrewbartels1/SolidworksMCP-python: 84 stars, pushed 2026-10-24.
  - arthurle3210/swapi-pilot-solidworks-mcp: 32 stars, API-doc search.
  - HarrierPigeon/Solidworks-MCP-Server: 27 stars.
  - Cai-aa/CAD-Agent-Hub: 114 stars, pushed 2026-09-07, MCP bridges for CATIA, SolidWorks, Siemens NX and Fusion.
  — [GitHub search](https://github.com/search?q=solidworks+mcp&type=repositories)

**Rhino / Grasshopper**
- **mcneel/RhinoAI**: 377 stars, pushed 2026-10-23, McNeel's official "AI features for Rhino" including a Rhino MCP server. The old repo name was mcneel/RhinoMCP. Docs are at mcneel.github.io/RhinoAI. — [GitHub](https://github.com/mcneel/RhinoAI)
- Community Rhino servers: always-tinkering/rhinoMcpServer (57 stars, last push 2025-03-23, stale) and tanishqbhattad/rhino-mcp (32 stars, pushed 2026-10-12, architectural modeling in Rhino 8). — [GitHub search](https://github.com/search?q=rhino+mcp&type=repositories)
- **brookstalley/cordyceps**: 107 stars, pushed 2026-09-07, a Grasshopper MCP (Rhino 8.21+). It adds components, wires connections, sets values, bakes geometry, applies PBR materials and renders scenes. Its author reports on HN that Claude Code iterating on screenshots handled "fairly complicated geometry." — [GitHub](https://github.com/brookstalley/cordyceps); [HN](https://news.ycombinator.com/item?id=47970497)
- alfredatnycu/grasshopper-mcp has 101 stars but its last push was 2025-03-22. — [GitHub](https://github.com/alfredatnycu/grasshopper-mcp)

**SketchUp**
- **mhyrr/sketchup-mcp**: 509 stars, pushed 2026-04-25, modeled on blender-mcp. It supports component create/modify/transform, materials, scene inspection, selection, and arbitrary Ruby eval. — [GitHub](https://github.com/mhyrr/sketchup-mcp)
- Forks and alternatives: zinin/sketchup-mcp2 (52 stars, pushed 2026-10-20) and andyluu98/vn-sketchup-mcp (11 stars, a "Fixed fork … pin mcp<2"). — [GitHub search](https://github.com/search?q=sketchup+mcp&type=repositories)

**Autodesk Inventor** (Windows/COM)
- **NeonGlay/inventor-mcp**: 47 stars, pushed 2026-06-10, 34 tools. It covers sketch, extrude/revolve, native hole features, fillets, patterns and sheet metal, plus an `execute_python` escape hatch. "Every feature operation returns a volume/topology delta" for self-verification, and it supports transactions and rollback. — [GitHub](https://github.com/NeonGlay/inventor-mcp)
- hsavas/inventor-mcp: 5 stars, pushed 2026-10-20, covering sketches, features, assemblies and drawings. — [GitHub search](https://github.com/search?q=inventor+mcp&type=repositories)

**Other kernels**
- BuildCAD lists smlibpy MCP (AWV, MIT, SMLib kernel, also a Claude Code plugin, with "Strong snapshots: drawings, sections, x-ray, pixel diffs") and ClassCAD MCP (AWV, free MCP, but STEP export and commercial use are paid). — [BuildCAD AI](https://buildcad.ai/guides/cad-mcp-servers)
- Zoo (KittyCAD) Design Studio is open source: KittyCAD/modeling-app has 1,321 stars, pushed 2026-10-24. KittyCAD/text-to-cad-ui has 295 stars, last push 2026-01-16, and is a UI for Zoo's proprietary Text-to-CAD API. — [GitHub](https://github.com/KittyCAD/modeling-app)

### Inferences
- Blender MCP's star count (about 10 times the next-largest) reflects general 3D/art popularity, not mechanical CAD fitness. For dimensioned parts, the B-rep servers (FreeCAD, build123d, Onshape, Fusion, Inventor) are the relevant ones.
- Fusion, SolidWorks and Inventor community servers are Windows/desktop-bound and mostly lack built-in renders. They rely on the agent running arbitrary API Python/VBA.
- Repos last pushed in March 2025 (bonninr, always-tinkering, alfredatnycu) are the early wave and look abandoned.

### Gaps
- **Shapr3D**: no MCP server or official announcement found. The GitHub search hit a rate limit before the "shapr3d" query ran, so this is unconfirmed rather than proven absent.
- No SolidWorks-official (Dassault) MCP was found in searches.
- Exact tool lists for Autodesk's official Fusion MCP are not documented publicly (the Autodesk intro blog returned 403).

## Q2. CAD-specific agent harnesses, skills, and print pipelines

### Takeaway
The biggest 2026 development is "skills plus local server" plugins for coding agents. **earthtojake/text-to-cad** (about 22.9k stars) is a build123d/OCCT plugin for Claude Code, Codex and Cursor that covers STEP, DFM checks, drawings, OrcaSlicer G-code and Bambu printing. Alongside it are standalone text-to-CAD apps: CADAM (OpenSCAD-WASM plus Claude), Amagine3D (build123d enclosures) and MAC (multi-agent build123d). Narrower Claude Code workspaces for OpenSCAD and CadQuery also exist.

### Cited Findings

**Plugins and skills for Claude Code, Codex and Cursor**
- **earthtojake/text-to-cad**: 22,875 stars, pushed 2026-10-23, MIT. It is "supported by all popular agents that support plugins or the skills framework, including Claude Code, Codex, Cursor, Gemini and Grok." It installs with `claude plugin marketplace add earthtojake/text-to-cad#latest`, and its runtime is the `cadgen` PyPI package (build123d 0.11, OCCT 7.9) with a local `cadgen mcp` viewer server. Telemetry is on by default (`uvx cadgen telemetry off`). — [GitHub](https://github.com/earthtojake/text-to-cad)
- Its skills:
  - CAD (STEP-first; also STL/3MF/GLB)
  - step.parts (off-the-shelf STEP screws, bearings and motors)
  - Engineering Drawing (dimensioned PDF)
  - DXF
  - URDF, SRDF and SDF (robotics)
  - SendCutSend checks
  - DfAM Check ("wall thickness, overhangs, support volume, and build orientation")
  - DFM (sheet metal/CNC/injection molding "with measured evidence and the cited rule behind every finding")
  - G-code (OrcaSlicer with user presets)
  - Bambu Labs (via Bambu Connect/Bambu Studio)

  In Claude Desktop, models appear as orbitable viewer cards and "Claude can read what you selected and see what you see." — [README](https://github.com/earthtojake/text-to-cad)
- Third-party listings describe the `cad` skill as enforcing "snapshot validation after every visible change" and as not doing render art or FEA. — [claudemarketplaces.com](https://claudemarketplaces.com/skills/earthtojake/text-to-cad/cad)
- It was a Show HN titled "Text-to-CAD", with 186 points and 48 comments at capture. — [HN](https://news.ycombinator.com/item?id=47970497)
- **jmwright/cadquery-llm-skill**: 11 stars, pushed 2026-05-19, from CadQuery maintainer Jeremy Wright. "LLMs regularly get CadQuery wrong – defaulting to Boolean/CSG thinking instead of CadQuery's BRep-native approach, misusing selectors, building in global coordinates." It bundles SKILL.md, concepts, patterns/anti-patterns, a local docs snapshot and an Ollama Modelfile. — [GitHub](https://github.com/jmwright/cadquery-llm-skill)
- **iancanderson/openscad-agent**: 130 stars, pushed 2026-02-03, a Claude Code workspace. It versions iterations as `model_001.scad`…, uses "Automatic PNG rendering lets the agent see and self-correct", and runs STL non-manifold checks. — [GitHub](https://github.com/iancanderson/openscad-agent)
- Other Claude-focused repos:
  - andreahaku/openscad_claude_skill: 28 stars, pushed 2026-10-17.
  - levkropp/ClawSCAD: 17 stars, "OpenSCAD + Claude Code with checkpoint branching".
  - ankurhanda/claude-cad: 34 stars, build123d with `/render`.
  - rohittp0/ClaudeCAD: 6 stars, Claude Code plugin.
  - 0oKevino0/claude-cad: 5 stars, "verification layer… Make Claude Code prove a CadQuery part is valid".
  - lightsong-agents/cad-design-skill: 0 stars, a build123d MCP "build/measure/render/export loop".
  - KennethWussmann/openscad-agentic-workspace: 1 star.
  — [GitHub search](https://github.com/search?q=openscad+claude&type=repositories)

**Standalone agent apps**
- **Adam-CAD/CADAM**: 5,322 stars, pushed 2026-10-15, "the open source text-to-CAD web application". It takes natural language and image references, runs OpenSCAD WebAssembly with BOSL/BOSL2/MCAD, gives a Three.js live preview and parametric sliders, and exports STL/SCAD/DXF. The AI is the Anthropic Claude API, on a React/TanStack/Supabase stack. — [GitHub](https://github.com/Adam-CAD/CADAM)
- **amagine-ai/Amagine3D**: 6,259 stars, pushed 2026-10-22, aimed at printable hardware enclosures.
  - It builds from internal components outward and produces editable build123d source, real STEP, STL and 3MF.
  - Its "3D-native Agent" runs an inner loop of read model, plan, check and analyze, and checks "wall thickness, print orientation, plate fit, connectivity, interference, and exported-file readback." It also checks collisions along motion paths for hinges and sliders.
  - Visual review uses a five-view preview, and `npm run doctor -- --vision` verifies that the model can actually perceive images.
  — [GitHub](https://github.com/amagine-ai/Amagine3D)
- **Pan-Chera/Multi-Agent-CAD (MAC)**: 1,048 stars, pushed 2026-10-17, a multi-agent build123d framework for parts and assemblies that exports STEP/STL/GLB plus URDF. On a 141-feature single-part benchmark that uses text-to-cad's CAD Skill as the baseline, it self-reports:

  | Workflow | Feature pass rate | Tokens | Cost |
  |---|---|---|---|
  | MAC v2 | 140/141 (99.3%) | 1.40M | $4.85 |
  | CAD Skill (visual loop) | 132/141 | 87.46M | $29.45 |

  The README says these are "not a measured prompt-success rate." — [GitHub](https://github.com/Pan-Chera/Multi-Agent-CAD)
- **Kevoyuan/AgentSCAD**: 17 stars, pushed 2026-10-12, a local-first OpenSCAD agent workspace with geometry and manufacturing checks and a live demo. Other small harnesses: neuronaline/local-ai-cad-agent (16 stars, Ollama), SmartAI/Chamfer (10 stars, "text/image to CAD Agent harness that supports different MCPs and LLMs"), SSSSSia/CadAgent (8 stars, a FreeCAD plugin). — [GitHub search](https://github.com/search?q=cad+agent&type=repositories)
- OpenOrion/CQAsk (186 stars) is an older CadQuery generator, last push 2024-06-17. — [GitHub](https://github.com/OpenOrion/CQAsk)

**Research and benchmarks**
- Text-to-CadQuery/Text-to-CadQuery (121 stars) is a fine-tuning and eval pipeline (IEEE T-ASE submission) that annotates Text2CAD with CadQuery via Gemini 2.0 Flash and evaluates with Blender renders, Gemini judging and Chamfer distance. — [GitHub](https://github.com/Text-to-CadQuery/Text-to-CadQuery)
- SadilKhan/Text2CAD (491 stars, NeurIPS'24) is another research codebase. — [GitHub](https://github.com/SadilKhan/Text2CAD)
- The CADGenBench leaderboard is a Hugging Face Space under HuggingAI4Engineering. The table did not load when fetched. — [HF](https://huggingface.co/spaces/HuggingAI4Engineering/CADGenBench)

**Agent to slicer to printer**
- **DMontgomery40/bambu-printer-mcp**: 233 stars, pushed 2026-10-23. It covers STL manipulation, slicing with BambuStudio or FULU OrcaSlicer-bambulab, a direct LAN print path, AMS handling and Blender MCP integration. Its README advises: "Ask for the result you want, not the steps. Current models plan across tools: they search the web, read photos, look up exact dimensions, edit models, slice, and print." — [GitHub](https://github.com/DMontgomery40/bambu-printer-mcp)
- Other Bambu servers: griches/bambu-mcp (57 stars, fleet management), schwarztim/bambu-mcp (23 stars, MQTT/FTP/camera), subhashdasyam/bambustudio-mcp (2 stars, inspect and compare slices) and vladthecto/hermes-3d-print (CadQuery MCP, Blender previews and Bambu Studio slicing for an A1 mini). — [GitHub search](https://github.com/search?q=bambu+mcp&type=repositories)
- Klipper and OctoPrint servers: Charleslotto/klipper-mcp (28 stars), mikehatch/KlipperMCP (7 stars, Moonraker config) and OctoEverywhere/mcp (38 stars, last push 2025-07-03, live state, webcam snapshots and control). — [GitHub search](https://github.com/search?q=klipper+mcp&type=repositories); [OctoEverywhere/mcp](https://github.com/OctoEverywhere/mcp)
- text-to-cad's own G-code (OrcaSlicer) and Bambu skills already chain prompt to STEP/3MF to G-code to printer inside one plugin. — [text-to-cad](https://github.com/earthtojake/text-to-cad)

### Inferences
- text-to-cad's roughly 23k stars make it the de facto "Claude Code CAD plugin" of 2026. Comparative benchmarks against it so far come from competitors (MAC) or self-reports (build123d-mcp), so it has no independent benchmark yet.
- The convergence is clear: B-rep kernel (build123d/OCCT) plus code-as-model plus a local viewer/MCP plus manufacturability checkers plus slicer/printer hooks.

### Gaps
- The searches for "gptcad" and "cadgpt" turned up no notable open-source repos. A dedicated OctoPrint MCP search returned nothing beyond OctoEverywhere. The Prusa/Orca/Moonraker searches were rate-limited.
- No independent, third-party head-to-head benchmark of text-to-cad, build123d-mcp, CADAM, Amagine3D and MAC was found.

## Q3. Which tools give render feedback, measurement/geometry queries, or constraint checks?

### Takeaway
Render-and-look loops are now standard in the leading tools. The strongest verification stacks pair renders with numeric geometry: per-feature bbox/volume/mass deltas, sections, interference, wall thickness, DFM, and validity gates. Practitioners and benchmarks suggest that the numeric checks matter at least as much as images.

### Cited Findings

| Tool | Renders / vision | Measurement / geometry queries | Constraint / manufacturability checks | Source |
|---|---|---|---|---|
| mcp-for-blender | multi-angle, camera, wireframe, X-ray, render | scene info | none (meshes) | [README](https://github.com/ahujasid/mcp-for-blender) |
| neka-nat/freecad-mcp | screenshots after most tools | via code execution | CalculiX FEM | [BuildCAD](https://buildcad.ai/guides/cad-mcp-servers) |
| blwfish/freecad-mcp | — | reads Report View, sketch constraints | geometry errors, wall thickness, collisions | [README](https://github.com/blwfish/freecad-mcp) |
| RobertCoop/openscad-mcp | multi-view with text digest, sections with scale bar | volume, cavities, bbox | interference, watertightness, printability | [README](https://github.com/RobertCoop/openscad-mcp) |
| pzfreo/build123d-mcp | PNG/SVG previews | measure, topology, volume | validity gate before export, printability, protected-feature edits | [README](https://github.com/pzfreo/build123d-mcp) |
| jarvis-onshape-mcp | 4-view shaded PNG, crop, compare-to-reference, drawing OCR | per-feature bbox/mass/part deltas, entity normals | regen-warning hints, bbox interference | [README](https://github.com/ReshefElisha/jarvis-onshape-mcp) |
| hedless/onshape-mcp | none | bbox, body details | interference checking | [README](https://github.com/hedless/onshape-mcp) |
| NeonGlay/inventor-mcp | none | volume/topology delta per feature | transactions and rollback | [README](https://github.com/NeonGlay/inventor-mcp) |
| text-to-cad | snapshots plus viewer cards | — | DfAM, DFM (cited rules), SendCutSend | [README](https://github.com/earthtojake/text-to-cad) |
| Amagine3D | 5-view preview plus vision doctor | measured dimensions | wall thickness, interference, motion-path collision, export readback | [README](https://github.com/amagine-ai/Amagine3D) |
| MAC | visual plus geometric in-loop | — | execution and repair QA | [README](https://github.com/Pan-Chera/Multi-Agent-CAD) |

- In MAC's benchmark, the geometric-only loop (MAC v1) used 0.90M tokens and $1.43 for 99.3%, while the visual-loop CAD Skill run used 87.46M tokens and $29.45 for 93.6%. That suggests image loops are token-expensive. — [MAC README](https://github.com/Pan-Chera/Multi-Agent-CAD)
- jarvis-onshape-mcp says the bottleneck in its experience "is not CAD execution, it's image interpretation." — [README](https://github.com/ReshefElisha/jarvis-onshape-mcp)
- HN users report "text debug output often helps more than screenshots," and that screenshot loops can multiply cost, to the point that one product disabled its loop. — [HN: Claude Tips for 3D Work](https://news.ycombinator.com/item?id=47365299)

### Inferences
- Returning structured numeric deltas after every operation (Inventor, Onshape-jarvis, build123d-mcp) is a cheap, high-signal alternative or complement to renders. Renders are most valuable for gross shape and orientation errors.

### Gaps
- No controlled ablation separating render feedback from numeric feedback was found, other than MAC's self-reported table, which is confounded by different workflows and models.

## Q4. Community reports: how well Claude/GPT do CAD, failure modes, best practices

### Takeaway
2026 reports converge on a consistent picture. Frontier models, led by Claude Opus 4.6/4.7, can now produce working simple-to-moderate printable parts through code-CAD with feedback loops, but results are typically "95% there" and need manual checking. The core weaknesses are spatial reasoning, image-to-geometry translation, missing implicit constraints, and context bloat. OpenSCAD and build123d/CadQuery each have advocates. CadQuery/build123d "fails loudly" and exports STEP, while OpenSCAD is better represented in training data but "fails silently."

### Cited Findings

**Benchmarks**
- **Kerrick Staley, "Can frontier LLMs solve CAD tasks?" (2026-02-22)** tested seven models writing OpenSCAD wall mounts (bike pump, mug, pan) with a MuJoCo physics check, up to 10 sim calls, and 10 trials each. Pass counts by pump / mug / pan:

  | Model | Pump | Mug | Pan | Cost |
  |---|---|---|---|---|
  | Claude Opus 4.6 | 10/10 | 10/10 | 10/10 | $41.11 |
  | GPT-5.2 | 8 | 9 | 9 | — |
  | Gemini 3.1 Pro | 5 | 6 | 4 | — |
  | Gemini 3 Flash | 6 | 4 | 5 | — |
  | Kimi K2.5 | 4 | 2 | 0 | — |
  | Qwen 3.5 397B | 2 | 1 | 0 | — |
  | GLM-4.6V | 1 | 0 | 1 | — |

  Claude Opus 4.1 failed 100% in a smaller run. Opus designs were "not directly usable" but "almost all are close." Failure modes included designs that were too large, too small or too weak, "random shapes that coincidentally work", and floating parts (GPT-5.2, open-weight models). Gemini models looped or produced garbled designs, and models made frequent tool-call errors. In an April 2026 update, a Gemini 3.1 Pro pump mount printed unmodified worked. The code is at kerrickstaley/llm-cad-mount. — [Blog](https://kerrickstaley.com/2026/02/22/can-frontier-llms-solve-cad-tasks)
- **Modelrift, "Benchmark: CadQuery vs. OpenSCAD for agentic CAD work" (2026; HN about 41 days before capture).** As quoted by commenters, six parts were all printable. "CadQuery fails loudly and early… OpenSCAD fails silently and late", verifiability favors CadQuery, yet the author stays with OpenSCAD. Commenters criticized it as LLM-written marketing. — [HN thread](https://news.ycombinator.com/item?id=49676577) (article: modelrift.com/blog/cadquery-vs-openscad/, not fetched directly)

**Practitioner reports (HN 2026)**
- In the text-to-cad Show HN thread:
  - One user uses Opus 4.7 with OpenSCAD for nebulizer connectors and calls it "incredibly powerful but still needs heavy manual checking." He hits context compaction on 10–40 KB .scad files.
  - Others say it works for simple parts but struggles with complex ones, that Codex was weak at spatial placement, and that LLMs are "very weak at spatial reasoning."
  - A detailed prompt still left no room for nuts between a flange and fins (missing implicit constraints), and benchmark prompts that omitted hole positions led to gussets overlapping holes.
  - The model's view was rotated relative to the STL preview, which the author says is now fixed.
  - The demo did not understand servo motors (domain knowledge).
  — [HN](https://news.ycombinator.com/item?id=47970497)
- In the same thread, the author says homepage prompts were "shortened and expanded with GPT 5.5 Pro." Asked for non-cherry-picked benchmarks, he said they were in progress. — [HN](https://news.ycombinator.com/item?id=47970497)
- In an HN OpenSCAD thread, one user says "working with an LLM and OpenSCAD has been totally painless," and another one-shotted a threaded light-saber hilt with BOSL2. A third found "cadquery and build123 with Claude code… incredibly painful," while others report build123d skills give very good results and STEP output. The common caveat is a "disconnect between 'LLM views image' and 'LLM emits scad that looks like that image'." — [HN 48174405](https://news.ycombinator.com/item?id=48174405); [HN 48174434](https://news.ycombinator.com/item?id=48174434)
- In the Modelrift thread:
  - Several commenters note AI parts are "95% there" when hardware needs exact tolerances.
  - Another says OpenSCAD lacks constraints and edge/face ops.
  - Replicad (TypeScript) was suggested because LLMs write TS well.
  - One user has agents write Blender Python, render previews and annotate screenshots.
  - Another treats "constraints as tests run after geometry."
  — [HN](https://news.ycombinator.com/item?id=49676577)
- **"Claude Tips for 3D Work" (Dave Snider, davesnider.com/posts/claude-3d; HN 200 points).** The tips: build tooling first "to give us a shared language" and give the model a render, screenshot and log feedback loop. Commenters report a first-try FreeCAD Python church tower and that Claude can "go deep in a wrong direction" and insist a curve is fine after viewing a screenshot. Enclosures and mechanical models often get details wrong. Fixes include matching loft vertex counts, per-feature functions and orthographic views. — [HN](https://news.ycombinator.com/item?id=47365299)

**Best practices collected**
- Render PNGs from multiple, preferably orthographic, angles and feed them back. — [HN](https://news.ycombinator.com/item?id=47970497); [HN](https://news.ycombinator.com/item?id=47365299)
- Encode domain shorthand in AGENTS.md or a skill file. — [HN](https://news.ycombinator.com/item?id=47970497)
- Provide reference photos and caliper measurements, and use part libraries. — [HN](https://news.ycombinator.com/item?id=47970497)
- Build one feature at a time, render or measure after major steps, and validate before export. — [build123d-mcp README](https://github.com/pzfreo/build123d-mcp)
- For image inputs, decompose the image into a feature tree that a human reviews before building. — [jarvis-onshape-mcp](https://github.com/ReshefElisha/jarvis-onshape-mcp)
- Teach BRep idioms (selectors, workplanes) rather than CSG habits. — [cadquery-llm-skill](https://github.com/jmwright/cadquery-llm-skill)
- Maintain a single repo of models plus accumulated reusable skills. — [HN](https://news.ycombinator.com/item?id=48174405)

### Inferences
- Model quality matters a lot. The gap between Opus 4.1 (0%) and Opus 4.6 (100%) on the same task suggests early-2025 negative experiences with blender-mcp and FreeCAD-mcp may not reflect late-2026 capability.
- The OpenSCAD-versus-build123d debate is really meshes and CSG with strong training-data coverage versus B-rep, STEP and loud errors. Serious mechanical work, meaning STEP and downstream CAD, tilts toward build123d/CadQuery or native-CAD MCPs.

### Gaps
- Searches surfaced no first-hand Reddit threads (r/3Dprinting, r/cad, r/ClaudeAI, r/FreeCAD) on these tools; the results were aggregator listings. Reddit sentiment is therefore uncovered.
- No YouTube reviews were assessed.
- The Dave Snider and Modelrift article bodies were not fetched directly, so their tips are taken from HN commenters' quotes.
- The CADGenBench leaderboard contents (top model and harness combinations) could not be retrieved.
