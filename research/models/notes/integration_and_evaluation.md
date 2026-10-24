# Integrating AI models into the Mises CAD agent harness: competitors, agentic research, evaluation, hosting, licensing (as of 2026-10-24)

Context read first: design doc §6, §7.3, §7.4, §9, §11 (`docs/design/2026-10-24-mises-design.md`) and `research/harness/README.md`. In short, Mises enforces its rules in the core service and the `mises` MCP server. Agents are bring-your-own (BYO) over ACP. The free tier must cost the developer about nothing.

Method note: about 30 tool calls (search and fetch). Several numbers come from secondary aggregators, and each is marked as such. A few arXiv PDFs could not be parsed, so the abstracts were used instead.

## Q1. Existing AI-for-CAD integrations and competitors (2025–2026): what ships, what models, strengths and weaknesses

### Takeaway
By late 2026, every major CAD vendor offers an official MCP surface, but each one is narrow. Onshape's covers FeatureScript code only. Autodesk ships a local Fusion MCP, a cloud data MCP and a cloud "Fusion Compute" MCP, plus Assistant, which is GA. Zoo ships a full in-app agent, Zookeeper, billed by the minute. No vendor names the LLM behind its agent. Mises's gap is the cross-agent harness itself: verify by numbers, simulate, approve, print, all on the user's own Onshape. Model access is not the gap.

### Cited Findings
**Onshape / PTC (shipped as early access)**
- PTC launched **Onshape Labs** in July 2026 as an early-access program. The **FeatureScript MCP Server** became available on 2026-08-13. It connects coding LLMs (Claude, ChatGPT, Gemini) to FeatureScript. — [PTC press release via Nasdaq](https://www.nasdaq.com/press-release/ptc-advances-leadership-ai-driven-design-launch-onshape-featurescript-mcp-server-2026); [Embedded Computing](https://embeddedcomputing.com/technology/software-and-os/ptcs-onshape-featurescript-mcp-server-delivers-ai-text-to-code-to-cad)
- How it works: the AI generates FeatureScript, inserts it into a document, runs it, evaluates the result and revises it. Setup: subscribe through the Onshape App Store, then add the remote server `https://fs-mcp.labs.onshape.app/mcp` over HTTP. The blog is dated 2026-08-11, calls the server "early-access… experimental", and doesn't mention API allowance counting. — [Onshape blog](https://www.onshape.com/de/blog/featurescript-mcp-server-enables-text-code-cad)
- Limitation: it generates code that defines geometry. It doesn't read assemblies or report changes, so those tasks still go through the REST API and its limits. — [Leo AI, "MCP for CAD in 2026"](https://www.getleo.ai/blog/cad-mcp-server-comparison-2026) (vendor blog by a competitor, Sep 2026)
- **AI Advisor** is a help panel inside Onshape that answers from documentation. It doesn't build geometry and isn't an MCP server. This rests only on secondary and search-snippet sources; I couldn't fetch Onshape's AI Advisor page. — [ARC Advisory](https://www.arcweb.com/blog/ptc-launches-onshape-featurescript-mcp-server-ai-assisted-cad-automation); [techdrawai blog](https://techdrawai.com/blog/connect-claude-to-onshape)
- Community Onshape REST MCP servers exist, for example `altendky/onshape-mcp` (Rust, early stage, API keys or OAuth). — [GitHub altendky/onshape-mcp](https://github.com/altendky/onshape-mcp)

**Autodesk (shipped)**
- **Fusion MCP** (local, runs alongside Fusion; enable under Preferences > General > API; usual port 27182). It covers live design sessions, modeling steps and multi-step changes through Fusion's API and scripting. — [Autodesk Fusion blog, "Introducing the Fusion MCP"](https://www.autodesk.com/products/fusion-360/blog/introducing-the-fusion-mcp-opening-fusion-to-ai-powered-workflows/) (page returned 403 to fetch; details from search snippets); [Leo AI](https://www.getleo.ai/blog/cad-mcp-server-comparison-2026)
- **Fusion Data MCP**, introduced 2026-07-02: cloud-hosted, covering hubs, projects, folders and permissions. It doesn't touch geometry. Clients discover its capabilities at connection time. — [Autodesk blog](https://www.autodesk.com/products/fusion-360/blog/introducing-the-autodesk-fusion-data-mcp-server/)
- Autodesk describes a third cloud server, **Fusion Compute**, that runs Fusion sessions for AI agents. It also says **Autodesk Assistant became GA for all Fusion users at AU 2026** and is live in Inventor, Moldflow and Vault. Both claims come from search snippets of Autodesk blog pages that I couldn't open. — [Autodesk Fusion blog](https://www.autodesk.com/products/fusion-360/blog/?p=93064)
- Monoist reported in June 2026 that Autodesk announced a Fusion connection to Claude alongside its MCP offerings. — [Monoist 2026-06](https://monoist.itmedia.co.jp/mn/spv/2606/04/news037.html)
- **Project Bernini** (text, sketch or point cloud to 3D) is still an **Autodesk Research proof of concept, not a product**. In 2025 Autodesk said it isn't available for commercial use. I found no 2026 status update. — [Monoist 2025-06](https://monoist.itmedia.co.jp/mn/spv/2506/05/news014_3.html); [Digital Engineering 24/7, AU 2024](https://www.digitalengineering247.com/article/au-2024-project-bernini-offers-glimpses-of-the-future)
- Earlier Fusion AI features announced at AU 2024: AutoConstrain, Automated Drawings and Autodesk Assistant. — [engineering.com](https://www.engineering.com/3-new-ai-features-in-autodesk-fusion/)

**Zoo / KittyCAD (shipped)**
- **Zookeeper**, the conversational CAD agent in Zoo Design Studio, was announced 2026-02-05. It creates and edits KCL models, lets users select faces or edges to steer edits, **takes four-view "vision snapshots" by default to review its own changes**, imports STEP and STL, and runs web searches for datasheets and standards. KCL is called open source, while the geometry engine is proprietary. The model behind it isn't disclosed. — [Zoo blog](https://zoo.dev/blog/announcing-zookeeper)
- Zookeeper pricing at launch: Free plan gets $10/month of credit, then **$0.50/min**. Plus is $20/month (about 400 min). Pro is $99/month (unlimited). Modeling is billed separately from Zookeeper reasoning time. Enterprise adds unlimited credits, SSO and fine-tuning on team CAD data. — [Zoo blog](https://zoo.dev/blog/announcing-zookeeper); [Zoo Zookeeper page](https://zoo.dev/zookeeper)
- Zookeeper has three reasoning modes: Auto, Standard and Thoughtful. — [Zoo docs](https://zoo.dev/docs/zoo-design-studio/features/ml-ai/modes)

**Adam / AdamCAD (shipped consumer product)**
- Adam was a YC W25 company and raised a $4.1M seed led by TQ Ventures. It started consumer-first, then moved to enterprise. — [TechCrunch](https://techcrunch.com/?p=3063885)
- From a single review site, unverified: a parametric text-to-CAD web app with an Onshape plugin, STL, OBJ and SCAD export (STEP on the roadmap), $5.99 and $17.99 monthly plans, more than 1M models generated, and an open-source "CADAM" repo with about 3.5k stars (May 2026). — [pasqualepillitteri.it review](https://pasqualepillitteri.it/en/news/3372/adamcad-text-to-cad-ai-review-2026)

**Open-source local MCP servers (shipped)**
- **build123d-mcp** (Apache-2.0, `uv tool run build123d-mcp`). It runs build123d in a persistent session, renders PNG, SVG and DXF previews, measures volume, area, bbox, topology and CoM, finds holes, bosses, countersinks and patterns, checks printability, fit and export validity, compares against imported STEP/STL, and keeps session snapshots. Its HTTP mode has no auth and one shared session. Its README claims that on the public **CADGenBench** leaderboard (June 2026) it raised the same model's score from 0.360 to 0.457 and validity from 88% to 100%. — [GitHub pzfreo/build123d-mcp](https://github.com/pzfreo/build123d-mcp)
- **mcp-build123d** (Casys-AI, Deno and Python). It pins build123d==0.11.1 with cadquery-ocp-novtk==7.9.3.1.1 and serves Streamable HTTP on loopback port 3014. — [JSR @casys/mcp-build123d](https://jsr.io/@casys/mcp-build123d)
- SolidWorks has only community MCP servers. Creo has only the experimental community CREOSON MCP. AutoCAD has only community servers. — [Leo AI](https://www.getleo.ai/blog/cad-mcp-server-comparison-2026)

### Inferences
- **Mises overlaps build123d-mcp heavily.** It is Apache-2.0, so Mises can vendor or fork it with attribution, or at least copy its tool vocabulary (`compare`, `validate`, hole finding, printability). Mises's own value sits above it: the Onshape mirror, fidelity checks, ledger and approvals.
- Render-and-self-review is now table stakes. Zookeeper does four-view snapshots by default, and CADGenBench entries use render tools. `mirror_render_views` should return **fixed, labeled multi-view composites** (iso plus three orthographic views, with dimensions overlaid), and the tool description should push agents to call it after each geometry change.
- Onshape's MCP covers only FeatureScript generation. A user can add it as a second MCP server next to `mises`, and Mises could tell users about it. Mises should **not** route its own geometry edits through it, because those writes would bypass the ledger and approval gates.
- Vendors hide their models, but every MCP-first offering (Onshape, Fusion, build123d-mcp) is **model-agnostic, BYO-client**. That validates Mises's BYO-agent free tier.
- Zoo's $0.50/min price is a public anchor for what users pay for hosted CAD agent time. Mises's paid "hosted agent credits" can be priced against it.

### Gaps
- The models behind Zookeeper, Autodesk Assistant and Adam weren't disclosed in any source I found.
- Whether Onshape Labs MCP calls count against the user's API allowance isn't stated in the Onshape blog. Design §7.1 already flags this.
- I found no FreeCAD-specific MCP server in my searches; one may exist. I didn't research CADAgent, other text-to-CAD startups (Backflip, Leo AI's own generator, and so on) or Godela/Archie, which appear only as leaderboard names, because of the call budget.
- No independent quality comparison of these products exists. The only shared yardstick is CADGenBench, and vendors there self-submit.

## Q2. Agentic CAD research: frameworks, render-and-critique loops, and measured gains from feedback

### Takeaway
2025–2026 papers agree on two points. Closed-loop feedback helps a lot: execution errors, then exact geometric checks (OCCT bbox, volume, validity), then a vision judge. But even frontier coding agents (GPT-5.5 Codex, Opus 4.7 Claude Code) **fail strict engineering requirements**. Mises's existing design (numeric checks on every mutation, multi-view renders, FEA via CalculiX) matches what the research shows works best.

### Cited Findings
- **CADSmith** (arXiv 2603.26512, 2026) is multi-agent. Its inner loop fixes execution errors. Its outer loop uses exact OpenCASCADE measurements (bbox, volume, solid validity) plus a separate VLM judge, and it uses RAG over API docs instead of fine-tuning. On a 100-prompt, three-tier benchmark against a zero-shot baseline: execution rate 95% to 100%, median IoU 0.8085 to 0.9629, median F1 0.9707 to 0.9846, mean Chamfer 28.37 to 0.74. — [arXiv 2603.26512](https://arxiv.org/abs/2603.26512) (the abstract gives no ablation split between the vision judge and the geometry checks, and doesn't name the LLMs)
- **"Self-Improving CAD Generation Agents with FEA as Feedback"** (arXiv 2605.17448v2, work in progress). The task is a fully assembled multi-part STEP from a free-form engineering brief, validated by FEA (CalculiX). **Codex (GPT-5.5) and Claude Code (Opus 4.7) produced zero strict-passing artifacts first time.** The best configuration met about 20% of typed requirements. Adding a text-only blueprint schema and a **21-view renderer** raised Box-IoU on S2O from 0.444 to 0.592 and on Fusion360 from 0.397 to 0.505 (GPT-5.5/xhigh). — [arXiv 2605.17448](https://arxiv.org/abs/2605.17448)
- **CAD-Assistant** (ICCV 2025) uses a VLLM planner with CAD-specific tools executed step by step in a Python interpreter with FreeCAD. Tools: a sketch-image parameterizer, rendering modules, a 2D cross-section generator. It checks each command's effect on geometry and adapts. It beat VLLM baselines and supervised task-specific methods on several benchmarks, with the biggest gains on autoconstraining and hand-drawn sketch parameterization (secondary summary). Code: github.com/dimitrismallis/CAD-Assistant. — [ICCV 2025 open access](https://openaccess.thecvf.com/content/ICCV2025/html/Mallis_CAD-Assistant_Tool-Augmented_VLLMs_as_Generic_CAD_Task_Solvers_ICCV_2025_paper.html); [arXiv 2412.13810](https://arxiv.org/abs/2412.13810v3)
- **CADDesigner** (arXiv 2508.01031, Aug 2025) is a general-purpose LLM agent that uses visual feedback throughout generation to improve fidelity step by step, with an ablation against a plain CadQuery baseline. — [arXiv 2508.01031](https://arxiv.org/html/2508.01031v1) (numbers not retrieved)
- **BenchCAD** findings (2026): frontier models do much better reading **code than images**. Best Code-QA total is about 0.84 against about 0.59 for Vision-QA, and the blank-image baseline is 0.375. Models recognize the part family from images but miss fine detail, operations and design intent. Simple code edits are nearly solved, compositional edits are not. Vision2Code: the best frontier MLLM (Gemini 3.1 Pro thinking) scored 0.397, specialist CADEvolve v3 scored 0.601, and the authors' RL-trained **Qwen3-VL-2B scored 0.768 in-distribution**. — [arXiv 2605.10865](https://arxiv.org/html/2605.10865v1)
- BenchCAD's Code Edit scores: GPT-5.3 thinking 0.865, Opus 4.7 thinking 0.853, Gemini 3.1 Pro thinking 0.837, GPT-4o 0.615, gpt-oss-120B 0.561. — [arXiv 2605.10865](https://arxiv.org/html/2605.10865v1)
- **CADEngBench** (arXiv 2608.09296): editing supplied CAD is much easier than generating it from scratch. Complex edits and matched FEA stay hard. Assembly predictions find the right region but miss the exact joint or mating entities. — [arXiv 2608.09296](https://arxiv.org/abs/2608.09296)
- Tool access measurably helps on a public leaderboard: build123d-mcp raised the same model's score from 0.360 to 0.457 and validity from 88% to 100% on CADGenBench. This is self-reported by the tool author. — [GitHub build123d-mcp](https://github.com/pzfreo/build123d-mcp)
- Related work cited in these papers (not read individually): Text2CAD, CAD-Llama, Text-to-CadQuery, cadrille (online RL), CAD-Coder (GRPO with a Chamfer reward), CAD-Recode (about 1M scripts, point cloud to code), CADCodeVerify, EvoCAD, SeekCAD, CADmium, GIFT (geometric-feedback bootstrapping, arXiv 2603.27448), CADENA (stepwise reverse engineering, arXiv 2608.00799), LLM4CAD-Editor (arXiv 2606.20607), Ortho2CAD (arXiv 2607.08891), CADialogue. — [arXiv 2605.10865](https://arxiv.org/html/2605.10865v1); [arXiv 2605.17448](https://arxiv.org/abs/2605.17448); search results

### Inferences
- **Numbers before pictures.** BenchCAD shows models reason better over code and text than over images. CADSmith gets its gains from exact OCCT measurements plus a judge. Mises's `{ok, version, changes:{mass, volume, bbox}, warnings}` return contract is the right core signal. Renders are a secondary check for overall shape and gross errors.
- Two cheap additions are supported by this research. (a) A **requirements or blueprint schema** the agent fills in before modeling: typed dimensions, interfaces, loads, keep-outs. `mirror_verify` then checks these as pass/fail, as the FEA paper's blueprint does. (b) **Many-view renders on demand.** The default should be 4–6 views, with optional section views, because the 21-view renderer gave a gain of about 0.1 Box-IoU.
- Editing beats generating. That favors Mises's flow: agents tweak Variable Studio values and patch existing features, not regenerate parts. The UI nudge toward variable-driven parts (§7.2) is supported by the evidence.
- A small RL-tuned VLM (Qwen3-VL-2B) beat frontier models at image-to-CadQuery in-distribution. A local `vision_read_sketch` (M9 sketch to part) is plausible on an M5 Pro, but it would need fine-tuning on Mises-like data. Off-the-shelf frontier VLMs are weak at it.
- "Zero strict passes" is direct support for the anti-goal "no fake confidence". Badges must come from tool records, never from agent prose.

### Gaps
- I found no paper measuring **retrieval of part libraries** (McMaster, standard-part catalogs) as a gain for agents. CADSmith uses RAG over **API docs**, and BenchCAD's families are anchored to ISO/DIN tables. Treat part-library retrieval as unmeasured.
- The CADSmith ablation split (vision judge vs geometry checks) and CADDesigner's numbers weren't retrieved, because the full texts weren't parsed.
- Query2CAD, LLM4CAD and "mechanical design agents" weren't individually checked.

## Q3. Evaluation: benchmarks, metrics, and building Mises's own regression set

### Takeaway
Use the field's metric stack in this order. First a **validity gate** (executes, valid solid). Then **geometric similarity** (volume IoU, Chamfer, surface-distance F1). Then **intent and interface** checks (keep-in/keep-out volumes, topology or Betti numbers, hole and feature recall). Then **engineering** checks (DFM or printability, FEA pass, parameter-perturbation robustness). Mises can build its regression set from user sessions cheaply, because every approved proposal already stores a prompt, a starting microversion, the accepted geometry and its numbers.

### Cited Findings
- **CADGenBench** (Hugging Face, 2026) has two tasks: generation from an engineering drawing, and editing an existing STEP. Metrics: a **validity gate** (failure zeroes everything), shape similarity (surface-distance F1, volume IoU), **interface match** (keep-in/keep-out sub-volumes), **topology match** (Betti numbers b0, b1, b2). Submissions are one STEP per sample, scored against private ground truth on the HF Space, and validated by a maintainer. Repo license is Apache-2.0; the dataset license isn't stated. — [GitHub huggingface/cadgenbench](https://github.com/huggingface/cadgenbench)
- CADGenBench leaderboard snapshots conflict. BenchLM's August 2026 text says build123d-mcp with Claude Opus 5 (xhigh) leads at 59.6%, then a GPT-5.6 entry at 45.1% and "Archie in Forge" at 42.3%, while its own table shows Godela at 84.5%. — [BenchLM](https://benchlm.ai/benchmarks/cadgenbenchgeneration); conflicting with [AI Release Tracker](https://aireleasetracker.com/md/benchmark/cadgenbench) (Grok 4.7 44.4%). Use the official [HF Space](https://huggingface.co/spaces/HuggingAI4Engineering/CADGenBench).
- **BenchCAD** has 17,900 execution-verified CadQuery parts in 106 industrial families (52 anchored to ISO/DIN/EN/ASME/IEC tables), 2,400 QA pairs and 748 edit pairs. Metrics: execution rate, **voxel IoU (64³)**, Chamfer, Hausdorff, Feature-F1, essential-operation recall, and a headroom-normalized edit accuracy. **Data CC-BY-4.0, eval code MIT.** — [arXiv 2605.10865](https://arxiv.org/html/2605.10865v1)
- **CADEngBench** has 300 parametric parts × (zero-to-CAD and functional edit) = 600 tasks. It measures B-Rep validity, engineering and DFM checks, **parameter-family perturbations** (does the model survive variable changes, i.e. editability), functional editing, and matched linear-static FEA in CalculiX. Its assembly track has 150 body pairs: joint retrieval, face and edge grounding, joint frames, kinematic checks. Eight models were evaluated. — [arXiv 2608.09296](https://arxiv.org/abs/2608.09296)
- IoU definitions differ between papers (GIFT takes the best IoU over axis permutations; BenchCAD uses a coarse 64³ voxel grid), so numbers aren't comparable across papers. — [search summary of arXiv 2603.27448 / 2608.00799](https://arxiv.org/pdf/2603.27448)
- Classic datasets: DeepCAD (178K Onshape sketch-and-extrude models), Fusion 360 Gallery (about 8K human design sequences), Text2CAD (about 660K text annotations over DeepCAD), CADPrompt (about 200 prompts). — [arXiv 2605.10865](https://arxiv.org/html/2605.10865v1)

### Inferences (proposed Mises eval design)
- **Metric stack per task.** All metrics can be computed locally with OCCT, gmsh and CalculiX, which Mises already bundles.
  1. *Gate:* the script executes, gives one valid closed solid, and the proposal applies cleanly in Onshape (no feature errors).
  2. *Geometry:* volume IoU (state the method, e.g. B-Rep boolean, not voxels), Chamfer and F1, bbox and mass within tolerance, compared against the human-accepted result.
  3. *Intent:* hole and feature recall (positions and diameters), interface keep-in/keep-out volumes, Betti numbers.
  4. *Editability:* perturb each Variable Studio variable by ±10–20%, rebuild, and count rebuild failures. This is CADEngBench's parameter-family idea and fits variable-driven parts directly.
  5. *Engineering:* sim_run pass or fail against a stated load case, printability (overhangs, minimum wall thickness, slicer success via PrusaSlicer/Orca CLI).
  6. *Process:* Onshape calls used, tool calls, wall time, tokens or dollars, approvals requested, stale-version replans. Mises already logs these in its Step and Ledger records.
- **Building the regression set from user tasks (opt-in only).** Each approved merge is a candidate case: {prompt, start microversion snapshot as STEP plus the build123d mirror script, accepted end geometry, numeric deltas, and the sim the agent cited}. Store only the mirror STEP/script, not the Onshape document (see Q5 on licensing). Rejected proposals become negative cases ("the agent did X, the human rejected it for reason Y"). Run the cases nightly against each supported agent and model, offline on the local mirror (no Onshape calls), and gate releases of prompts and tool descriptions on there being no regressions. Seed the set with Apache, MIT or CC-BY public sets (BenchCAD CC-BY-4.0, CADGenBench repo Apache-2.0) and hand-made parts in the Mises dogfood docs.
- Use a VLM judge only as a tie-breaker. Judges are weak at fine detail (BenchCAD), so numeric metrics should decide.
- Mises could submit "Mises + agent X" results to CADGenBench for public credibility. Both tasks (drawing to STEP, edit STEP) map to mirror tools.

### Gaps
- I found no standard metric for "constraint satisfaction" in Onshape-native sketches (fully constrained or under-constrained). CAD-Assistant targets autoconstraining, but I didn't retrieve its metric.
- The CADGenBench dataset size and license aren't published in the repo README.

## Q4. Hosting options for extra models, costs, and exposing them as MCP tools

### Takeaway
For the free tier, run any extra model **locally inside the Python core** and expose it as a `mises` MCP tool. Every ACP agent then benefits, at zero developer cost. On a 24 GB M5 Pro, a 4B-class VLM runs comfortably, and a 30B-A3B MoE VLM at 4-bit is borderline. For the paid tier, use per-second serverless GPUs (Modal from about $0.80/h on L4) behind the Mises cloud, gated server-side.

### Cited Findings
- MLX community benchmarks: **Qwen3-VL-4B-Instruct** on an M4 32 GB gives about 396 tok/s prefill and about 32.8 tok/s generation at a 1k context, with 3.6 GB peak memory. On an M1 16 GB it gives about 20.7 tok/s. **Qwen3-VL-30B-A3B-Instruct** on an M4 32 GB gives about 424 tok/s prefill, 41.1 tok/s generation, 2.4 s time to first token and **17.5 GB peak memory**. — [oMLX community benchmarks](https://omlx.ai/benchmarks/kkmh7ej8) (and sibling benchmark pages in the same search; community-submitted, unaudited)
- A small RL-trained **Qwen3-VL-2B** reached 0.768 on BenchCAD Vision2Code in-distribution, far above frontier models (about 0.40). — [arXiv 2605.10865](https://arxiv.org/html/2605.10865v1)
- **Modal**: billed per second with no idle charge. L4 about $0.000222/s (about $0.80/h), A10 about $0.000306/s (about $1.10/h). — [Spheron blog on Modal pricing 2026](https://www.spheron.network/blog/modal-gpu-pricing-2026-per-second-billing/); [computeprices.com Modal L4](https://computeprices.com/providers/modal/gpus/l4) (aggregators; verify on modal.com/pricing)
- **Replicate**: billed per second; T4 is about $0.000225/s ($0.81/h). It publishes rates for T4, L40S, A100 and H100 (no L4 or A10). Public-model pricing differs from dedicated hardware pricing. — [Spheron blog on Replicate pricing 2026](https://www.spheron.network/blog/replicate-pricing-2026-per-second-cost/) (aggregator)
- Exposing a model or tool as an MCP tool works in practice. build123d-mcp exposes render, measure and validate tools to Claude Code, Claude Desktop, Cursor and VS Code through a single `uv tool run` command, and warns that its HTTP mode has no auth and one shared session. — [GitHub build123d-mcp](https://github.com/pzfreo/build123d-mcp)

### Inferences
- **Pattern for `vision_read_sketch`, `predict_stress` and similar tools.** Implement each as an ordinary `mises` tool with safety class `read` and a backend interface `{local_mlx | local_onnx | cloud}`.
  - The tool returns structured output (sketch entities with dimensions and confidence; stress field summary plus a "surrogate, not FEA" flag) and a file handle, following §7.4.
  - Any ACP agent gets it for free, with no per-agent integration.
  - The backend is chosen by the core: local by default (free tier); cloud only when a paid licence check passes on the server, and charged against a per-user ledger budget.
  - Load models lazily, unload them after idle, and cap concurrent heavy calls. On 24 GB, a resident 17.5 GB VLM competes with OCCT, gmsh and CalculiX for memory.
- **Local runtimes by platform.** On macOS use MLX / mlx-vlm (fastest on Apple Silicon per the benchmarks above). On Windows use ONNX Runtime (DirectML) or llama.cpp (GGUF) as the cross-platform fallback. Core ML suits small, fixed-shape models such as a surrogate stress net. These runtime choices are standard practice, not sourced here.
- `predict_stress` should be a **surrogate that triages** which designs get a full `sim_run`. It must never be a verdict, because the badge rule says verdicts come from records. Label its outputs as estimates in the Step log.
- Cost sense-check: at about $0.80/h on L4 with per-second billing, a 10-second VLM call costs about $0.002 of GPU time, before cold starts. Hosted extra models are cheap. The paid tier's real cost is hosted *agent* LLM tokens, not the extra models.
- Download model weights on first use (as §9 already plans for solvers) to keep the installer small. A 4-bit 4B VLM is a few GB.

### Gaps
- I found no measured MLX numbers on an **M5 Pro 24 GB** specifically. The figures above are from an M4 32 GB. The 30B-A3B at 4-bit on 24 GB is plausible but unverified; run mlx-vlm locally to check.
- I didn't retrieve **Fal, Together or Hugging Face Inference Endpoints** pricing, Modal's official pricing page, or ONNX/DirectML performance on Windows.
- I found no off-the-shelf open "stress surrogate" model suitable for arbitrary parts. It would need to be trained, for example on CalculiX runs from Mises's own sim pipeline.

## Q5. Licensing pitfalls for commercial use of open models and datasets

### Takeaway
Model licences are mostly fine, though Llama's multimodal EU exclusion is a real trap. The CAD **datasets** are the bigger problem. DeepCAD, ABC and Text2CAD derive from Onshape public documents whose copyright stays with their creators under Onshape's terms. Fusion 360 Gallery is non-commercial research only. Prefer CC-BY or Apache data (BenchCAD) and Mises-generated synthetic data.

### Cited Findings
- **Llama 4**: services with more than 700M MAU need a separate licence. For **multimodal Llama models, the licence grant does not extend to individuals or companies based in the EU**, although end users of a product that merely incorporates the model are exempt. — [The Decoder](https://the-decoder.com/meta-releases-first-multimodal-llama-4-models-leaves-eu-out-in-the-cold/); [WZ-IT](https://wz-it.com/en/ai/llm-hosting/llama/) (secondary; verify against Meta's licence text)
- **Gemma 4** moved to **Apache 2.0** (April 2026), replacing the custom Gemma Terms of Use that had use restrictions. — [urandom.io 2026-04-05](https://urandom.io/blog/2026-04-05-gemma-4-apache-license-matters-more-than-benchmarks); [MindStudio](https://www.mindstudio.ai/blog/gemma-4-apache-2-license-commercial-use)
- **Qwen**: licences vary by model. One source says Qwen and Mistral open models are on Apache 2.0, while another reports that newer releases (Qwen3.5 Omni, Qwen 3.6 Plus) moved to more restricted terms. — [noze.it](https://noze.it/en/insights/gemma-nuova-release) vs [MindStudio](https://www.mindstudio.ai/blog/what-is-gemma-4-apache-2-license-commercial-ai-deployment) (conflicting or secondary; check each model card)
- **ABC dataset**: copyright in the models belongs to their creators, and the licence refers to Onshape's Terms of Use §1.g.ii. One NYU archive record lists MIT for one release chunk, which conflicts with the creator-copyright note. — [NYU archive record](https://archive.nyu.edu/handle/2451/44395?mode=full)
- **DeepCAD**: the code repo is MIT, but the README says the data was "parsed from Onshape public documents with links from ABC dataset" and states no data licence. — [GitHub rundiwu/DeepCAD](https://github.com/rundiwu/DeepCAD)
- **Fusion 360 Gallery** (assembly/JoinABLe): the licence permits **non-commercial research**. — [JoinABLe CVPR 2022 supplemental](https://openaccess.thecvf.com/content/CVPR2022/supplemental/Willis_JoinABLe_Learning_Bottom-Up_CVPR_2022_supplemental.pdf)
- **BenchCAD**: data CC-BY-4.0, code MIT. **CADGenBench** repo Apache-2.0 (data licence unstated). **build123d-mcp** Apache-2.0. **Hephaestus/FEA paper** text CC BY-SA 4.0. — [arXiv 2605.10865](https://arxiv.org/html/2605.10865v1); [GitHub cadgenbench](https://github.com/huggingface/cadgenbench); [GitHub build123d-mcp](https://github.com/pzfreo/build123d-mcp); [arXiv 2605.17448](https://arxiv.org/abs/2605.17448)
- Project Bernini isn't available for commercial use, according to Autodesk (2025), even though it was trained on "publicly licensed data". This shows a major vendor treating training-data provenance as a commercial blocker. — [Monoist 2025-06](https://monoist.itmedia.co.jp/mn/spv/2506/05/news014_3.html)

### Inferences
- **Don't fine-tune a shipped model on DeepCAD, ABC, Text2CAD or Fusion 360 Gallery** without legal review. Text2CAD and CAD-Recode-style corpora inherit DeepCAD's Onshape provenance. Safer sources are BenchCAD (CC-BY-4.0, with attribution), procedurally generated build123d parts, and opt-in user data with explicit consent.
- A model trained on restricted data may carry that restriction. Check the training-data licence, not just the weights licence. For example, verify cadrille, CAD-Recode and CADEvolve before shipping any of their checkpoints.
- For any shipped local VLM, prefer Apache-2.0 weights (Gemma 4; check each Qwen3-VL card) and avoid multimodal Llama if Mises (or its company) is EU-based or sells to EU businesses. The end-user exemption doesn't cover the distributor.
- User CAD is the user's data. The regression set from Q3 must be opt-in, anonymized, and must store mirror geometry rather than Onshape documents. Onshape's Terms also apply to public documents.
- Onshape Labs MCP and Fusion MCP are vendor services with their own terms. Mises should *point users to* them, not proxy them.

### Gaps
- I didn't retrieve Onshape Terms of Use §1.g.ii or the exact licence text for Llama 4, Qwen3-VL, cadrille or CAD-Recode. Conclusions above rest on secondary sources and need legal confirmation.
- The Text2CAD and CADPrompt data licences weren't checked.
