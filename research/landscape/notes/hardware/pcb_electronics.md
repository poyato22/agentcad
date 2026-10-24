# AI Agents and Agent Harnesses for PCB, Schematic, and Board-Level Electronics Design

Research date: 2026-10-24. GitHub star counts were pulled live via `gh api` on 2026-10-24; for those, the repo URL is the source.

## Which open-source projects exist (KiCad MCP servers, atopile + LLM, SKiDL, circuit-synth, Claude Code/Cursor workflows)?

### Takeaway
The open-source agent ecosystem is built around (a) KiCad MCP servers that give a general agent (Claude Code, Cursor, Codex) tools for schematic and PCB editing, ERC/DRC, and Gerber/BOM export, and (b) code-first hardware languages (atopile, SKiDL, circuit-synth, Diode's Zener) that LLMs can write as text. mixelpixx/KiCAD-MCP-Server (about 2.9k stars) is the dominant MCP server. atopile (about 4.0k stars) is the most agent-focused code-first tool, and its team also runs the EEBench agent benchmark.

### Cited Findings
**KiCad MCP servers**
- mixelpixx/KiCAD-MCP-Server: 2,928 stars, last push 2026-10-01. It is an MCP implementation that "enables LLMs like Claude to directly interact with KiCAD for PCB design" — [GitHub](https://github.com/mixelpixx/kicad-mcp-server). Its v2.1.0 release added creating a project with both PCB and schematic files, plus template schematics — [mcpservers.org](https://mcpservers.org/servers/mixelpixx/kicad-mcp-server)
- lamaalrajih/kicad-mcp: 529 stars, last push 2025-10-17 (stale for about a year). It is a cross-platform MCP server for KiCad — [GitHub](https://github.com/lamaalrajih/kicad-mcp)
- Seeed-Studio/kicad-mcp-server: 167 stars, last push 2026-10-22. It targets KiCad 9.0+ and is mostly analysis: it lists components and nets, traces pin-level connections, and runs ERC/DRC headlessly via kicad-cli. It also offers some design editing — [GitHub](https://github.com/Seeed-Studio/kicad-mcp-server)
- oaslananka/kicad-mcp-pro: 149 stars, pushed on 2026-10-24, so it is very active. It covers schematic and PCB workflows, ERC/DRC, DFM checks, BOM analysis, and manufacturing prep with "controlled edits and human-gated release workflows." It ships agent-plugin configs for Claude Code, Codex, VS Code, and OpenCode — [GitHub](https://github.com/oaslananka/kicad-mcp-pro)
- ril3y/kicad-mcp-pro: an unrelated project with the same name, 0 stars. It targets KiCad 10/9 and claims schematic capture, PCB layout, DRC/ERC/DFM, signal and power integrity, EMC checks, SPICE simulation, FreeRouting, and gated exports (Gerber, BOM, IPC-2581, STEP) — [GitHub](https://github.com/ril3y/kicad-mcp-pro)
- danidask/mcp-server-kicad (0 stars) and ProductOfAmerica/mcp-server-kicad (15 stars): both describe "100+ MCP tools": place parts, route traces, run ERC/DRC, and export Gerbers, BOM, and 3D models. The ProductOfAmerica repo says it is in maintenance mode (security and compatibility fixes only) — [danidask](https://github.com/danidask/mcp-server-kicad); [ProductOfAmerica](https://github.com/ProductOfAmerica/mcp-server-kicad)
- Pablomonte/MCP-KiCad: 5 stars. It is a small Claude + MCP project focused on placement and reading netlists — [GitHub](https://github.com/Pablomonte/MCP-KiCad)
- A KiCad forum thread announces another open-source MCP server for AI-assisted KiCad workflows, which shows community interest — [KiCad forum](https://forum.kicad.info/t/i-built-an-open-source-mcp-server-for-ai-assisted-kicad-workflows/68678)

**Code-first HDLs used by agents**
- atopile/atopile: 4,011 stars, last push 2026-10-14. It is a declarative language for circuits that compiles to KiCad — [GitHub](https://github.com/atopile/atopile). Hackaday describes it as "a code-based system for creating PCBs in KiCad with a strong focus on use by such AI agents" — [Hackaday, 2026-09-05](https://hackaday.com/2026/09/05/can-ai-now-design-pcbs-that-just-work/)
- A community Claude Code skill/plugin for atopile exists (mawildoer/atopile-dev) — [claudepluginhub](https://www.claudepluginhub.com/plugins/mawildoer-atopile-dev); [mcpmarket](https://mcpmarket.com/tools/skills/atopile-ato-hardware-design)
- A July 2025 write-up (atomic14) used atopile with Claude to design a working ESP32-S3 dev board, and the author found Claude better than Cursor for the task — [atomic14, 2025-07-12](https://www.atomic14.com/2025/07/12/vibing-hardware)
- devbisme/skidl: 1,702 stars, last push 2026-10-17. It is a Python library for describing circuits — [GitHub](https://github.com/devbisme/skidl)
- circuit-synth/circuit-synth: 288 stars, last push 2026-03-08. It uses Python-defined circuits "with Claude Code as your intelligent design partner" — [GitHub](https://github.com/circuit-synth/circuit-synth)
- diodeinc/pcb: 466 stars, pushed 2026-10-24. It is open-source PCB tooling from Diode Computers (the Zener code-first language) — [GitHub](https://github.com/diodeinc/pcb); [Diode](https://www.diode.computer/)
- JITX: a code-based EDA tool with AI-assisted routing — [219design](https://219design.com/6-next-gen-tools-for-electronics-design/). (I couldn't find its public GitHub org under the guessed name.)

**Anecdotes about generic agents**
- A Hackaday commenter says they designed three boards with Fable that worked on the first spin. They also wrote a Rust fork of Freerouting because the routing looked uneven. A separate commenter says ChatGPT recommended discontinued parts — [Hackaday comments](https://hackaday.com/2026/09/05/can-ai-now-design-pcbs-that-just-work/). These are anecdotes, not controlled results.
- A 2026 StationX article describes an experiment in AI-driven PCB design and calls atopile a "great LLM pairing" — [StationX](https://app.stationx.net/articles/ai-pcb-design)

### Inferences
- The typical open-source "harness" is not a bespoke agent. It is a general coding agent (Claude Code/Cursor/Codex) plus an MCP server or a text HDL, with kicad-cli ERC/DRC and Freerouting serving as the verifiers and the router.
- Text-first representations (atopile, SKiDL, Zener) seem to suit LLMs better than direct GUI or s-expression editing. That is also the thesis behind Diode and atopile.
- MCP server activity is fragmented: many forks share near-identical names, and most have few stars. mixelpixx is the de facto standard.

### Gaps
- No independent measurement compares how well these MCP servers succeed at end-to-end tasks.
- I didn't verify JITX's current open-source status or whether it has an AI agent product. JITX's own site wasn't fetched.

## Which startups offer agentic PCB design, and what is autonomous vs assisted?

### Takeaway
The startups split by stage. Flux (browser EDA plus an agent that plans, picks parts, builds schematics, does layout, and checks), Diode (requirements to code-first schematic and layout, agent plus in-house EEs), and Circuit Mind (block diagram to schematic and BOM) cover the front end. Quilter covers layout only: an RL/physics engine that starts from an existing schematic. Its most-cited proof point is an 843-part Linux SBC that booted on the first power-up. Cadstrom focuses on validation. Every vendor keeps humans in review loops.

### Cited Findings
**Flux (flux.ai)**
- Flux previewed AI Auto-Layout in December 2024 as one-click routing powered by Flux Copilot — [Flux blog](https://www.flux.ai/p/blog/introducing-ai-auto-layout)
- Spring 2026 update (March 2026): a faster, self-correcting agent with "multi-step execution." A user describes a goal, and Flux plans parts research, schematic creation, layout, checks, and review milestones — [Flux blog](https://www.flux.ai/p/blog/spring-2026-updates-faster-ai-better-layouts-smarter-sourcing)
- August 2026 (per search summary of Flux news): a public MCP server lets external agents (Claude, Cursor, Codex) drive Flux — [Flux company news](https://www.flux.ai/p/company-news). This came from a search summary, not a page I fetched directly.
- According to competitor Quilter, Flux's published Auto-Layout examples are 2- and 4-layer boards with about 40 to 100 components, and Flux's docs say it is not meant to fully autoroute every class of board without user input. Credits range from about 375 (4-layer, 40 parts) to 1,250 (2-layer, 100 parts) — [Quilter guide](https://www.quilter.ai/blog/the-2026-guide-to-autonomous-pcb-design-quilter-vs-deeppcb-vs-flux-ai). This is a competitor source and may be biased.
- Earlier coverage called Flux's upgrade a move from "AI assistant to AI circuit co-designer" — [All About Circuits](https://www.allaboutcircuits.com/news/flux-upgrade-graduates-ai-assistant-ai-circuit-co-designer/)

**Quilter**
- Raised a $25M Series B led by Index Ventures in October 2025, for $40M total (Benchmark, Coatue). It uses physics-guided RL to place, route, and verify. It integrates with Altium, Cadence, and Siemens Xpedition, and it claims to be "the first and only company to publicly demonstrate fully autonomous PCB layout" — [BusinessWire, 2025-10-07](https://www.businesswire.com/news/home/20251007165399/en/Quilter-Secures-$25M-Series-B-to-Eliminate-Manual-PCB-Design-with-Physics-Driven-AI)
- Raised a $10M Series A in February 2024 while still in open beta — [The Register](https://www.theregister.com/2024/02/15/quilters_ai_platform/)
- Project Speedrun (December 2025): a dual-PCB, 843-component Linux computer on NXP i.MX 8M Mini that booted Debian on first power-up. Human labor was about 38.5 hours, versus a quoted 428 hours (about 11 weeks) for professionals — [VentureBeat](https://venturebeat.com/ai/quilters-ai-just-designed-an-843-part-linux-computer-that-booted-on-the); [TechRadar](https://www.techradar.com/pro/ai-designed-this-linux-computer-with-843-parts-and-dual-pcbs-in-just-a-week-and-it-even-booted-the-first-time); [Slashdot](https://hardware.slashdot.org/story/25/12/16/229209/dual-pcb-linux-computer-with-843-components-designed-by-ai-boots-on-first-attempt). The human time went to supervising and reviewing. This is a single-company demo with limited independent validation — [letsdatascience](https://letsdatascience.com/news/quilter-designs-linux-sbc-with-ai-901b83c8)
- Quilter starts from an existing schematic, generates multiple physics-validated layout candidates, and supports air-gapped/ITAR deployment — [Quilter guide](https://www.quilter.ai/blog/the-2026-guide-to-autonomous-pcb-design-quilter-vs-deeppcb-vs-flux-ai)

**Diode Computers (YC S24, NYC)**
- Raised an $11.4M Series A led by a16z — [Dealroom](https://app.dealroom.co/news/feed/diode-computers-raises-11-4m-for-ai-pcb-design). It is a roughly 10-person team (founders Davide Asnaghi and Lenny Khazan) whose stated mission is to "automate circuit board design using AI" — [YC](https://www.ycombinator.com/companies/diode-computers-inc)
- Self-serve: "You, with our AI agent," starting from a description, block diagram, or KiCad project. An enterprise tier uses Diode's EEs. Outputs are schematics and layouts written as code in Zener, with integrated DFM and CAM — [diode.computer](https://www.diode.computer/)
- In December 2025 Diode said it worked with Anthropic so that "Claude 4.5 is the first frontier model trained with our data" for EE/reference designs — [Diode on X](https://x.com/diodeinc); [gend.co](https://www.gend.co/blog/enhancing-claude-pcb-design-skills)

**Circuit Mind**
- ACE turns a hardware block diagram into a schematic, BOM, and supporting analysis "in minutes." A press release reports $25M in new financing (an $18.5M Series A led by Norwest plus a seed round led by Third Prime), for $33.4M total — [AI Journal](https://aijourn.com/circuit-mind-announces-25m-in-funding-to-accelerate-the-rise-of-electronic-design-intelligence/). The release date shown in search (2026-10-28) is after today, so the date is unverified.
- An older source says it did not yet handle power or analog circuitry — [219design](https://219design.com/6-next-gen-tools-for-electronics-design/)

**Cadstrom**
- Raised a $6.8M seed round led by Bison Ventures (Innovation Endeavors, AI2 Incubator), announced around December 2024 / January 2025. It is based in Montreal, and its Sigma Engine focuses on AI design validation rather than generation — [citybiz](https://www.citybiz.co/article/642854/cadstrom-raises-6-8m-seed-funding/); [justainews](https://justainews.com/companies/funding-news/cadstrom-secures-6-8m-seed-funding/). PitchBook lists $4.96M total, which conflicts — [PitchBook](https://pitchbook.com/profiles/company/556373-35)

**Others**
- Celus: a Munich platform for automated component search and schematic generation. I found no 2026 detail — [219design](https://219design.com/6-next-gen-tools-for-electronics-design/)
- DeepPCB (InstaDeep): RL-based routing within defined complexity limits — [Quilter guide](https://www.quilter.ai/blog/the-2026-guide-to-autonomous-pcb-design-quilter-vs-deeppcb-vs-flux-ai)
- siliXon (UK, text-to-PCB): one source reports a $1.5M raise in May 2026, unconfirmed elsewhere — [AtlasPCB](https://www.atlaspcb.com/blog/text-to-pcb-generative-ai-design-disruption-2026/)
- ProtoFlow publishes comparisons of AI PCB tools and is itself a vendor — [ProtoFlow](https://www.protoflow.ai/compare/best-ai-pcb-design-software-2026)
- A JLCPCB roundup lists 10 AI PCB tools for 2026 — [JLCPCB](https://jlcpcb.com/blog/ai-tools-for-pcb-design)

### Inferences
- Autonomy is greatest in layout (Quilter, and Flux on small boards) and in schematic generation from a constrained block diagram (Circuit Mind, Diode). End-to-end, requirements-to-fab autonomy is still marketed with heavy human review. Even Quilter's best case used about 38 hours of human supervision.
- Diode's tie-up with Anthropic suggests frontier labs are now training directly on EE data, which may lift the open-source MCP harnesses as well.

### Gaps
- I didn't fetch primary pages for Celus, Circuit Mind ACE, or JITX, so details on autonomous vs. assisted features are thin.
- I found no independent customer success rates for any startup.

## What do Altium, Cadence, Siemens, and Zuken offer as agentic features?

### Takeaway
Cadence (AuraStack on Allegro X AI) and Siemens (Fuse EDA AI Agent in Xpedition/HyperLynx) shipped true orchestrating agents in 2026. In both, the LLM plans the work and calls deterministic physics and sign-off engines, with human checkpoints along the way. Zuken's 2026 offering is AI placement and routing plus design-reuse learning, not an LLM agent. I found no dedicated agent announcement from Altium.

### Cited Findings
- **Cadence AuraStack** is an "AI super agent" for PCB and advanced packaging, built on Allegro X AI and powered by NVIDIA Blackwell/CUDA-X. It went into early access, with production availability around late August or early September 2026. It interprets an objective, plans, and calls placement, routing, PSpice simulation, DRC, IR-drop, and SI tools. In the demo it built a power tree, ran PSpice, proposed about 28% BOM savings, and flagged thermal hotspots. Cadence calls it about Level 4 autonomy (Allegro X AI alone is Level 2-3). Engineers approve at checkpoints. The LLM doesn't compute physics; it orchestrates Sigrity X, Clarity, Celsius, and MSC Nastran/Marc. Claims: up to 15x productivity. Customer: Forvia Hella, which says a 300-component placement went from up to four days to four minutes — [Embedded.com](https://www.embedded.com/cadence-develops-agentic-ai-platform-for-pcb-design/)
- AuraStack was presented at DAC 2026 as part of four "AI Super Agents," which Cadence calls the industry's first complete silicon-to-system agentic stack — [Futurum](https://futurumgroup.com/insights/synopsys-cadence-and-siemens-take-agentic-chip-design-autonomous-at-dac/); [Cadence 8-K, July 2026](https://www.sec.gov/Archives/edgar/data/0000813672/000081367226000089/cdns07272026ex9901.htm)
- **Siemens Fuse EDA AI Agent** was announced on 2026-03-16. It is a "domain-scoped autonomous agent" that plans and orchestrates workflows across IC, 3D IC, and PCB, through to manufacturing sign-off. The PCB work happens in Xpedition and HyperLynx. It is built on the Fuse EDA AI system (RAG, MCP support, open third-party framework) and uses NVIDIA Agent Toolkit and Nemotron. It offers RBAC, audit trails, human checkpoints, and air-gapped deployment — [Siemens press release](https://news.siemens.com/en-us/siemens-fuse-eda-ai-agent/); [IT Brief](https://itbrief.co.uk/story/siemens-unveils-fuse-eda-ai-agent-for-chip-workflows); [Electronics Online](https://www.electronicsonline.net.au/content/design/news/siemens-launches-ai-agent-to-automate-pcd-design-workflows-675622707)
- At DAC 2026, Siemens stressed self-verification: it cross-checks LLM outputs against deterministic, physics-based sign-off engines — [Futurum](https://futurumgroup.com/insights/synopsys-cadence-and-siemens-take-agentic-chip-design-autonomous-at-dac/)
- **Zuken CR-8000 2026** (announced 2026-10-21/22) adds automated schematic routing in Design Gateway, a Main Path View in Design Force, and Smart Design Reuse. Its "Dynamic Brain" shares learned placement knowledge through a locally hosted AIPR server — [EEJournal](https://www.eejournal.com/industry_news/cr-8000-2026-streamlines-pcb-development-with-new-automation-and-reuse-capabilities/); [ENGtechnica](https://engtechnica.com/zuken-cr-8000-2026-automates-schematic-routing-layout-reuse/). AIPR (Autonomous Intelligent Place and Route) dates from 2023 — [Zuken](https://www.zuken.com/en/resource/zuken-introduces-ai-powered-place-and-route-technology/); [Zuken AI PCB](https://www.zuken.com/en/product/cr-8000/ai-pcb-design/)
- **Altium**: secondary sources only say Altium Designer offers "AI-assisted routing and part creation" — [ProtoFlow, August 2026](https://www.protoflow.ai/compare/enterprise-ai-pcb-design-software). I found no Altium agent announcement.

### Inferences
- Incumbents converge on the same architecture: an LLM planner, MCP or tool calls into existing deterministic solvers, and human approval gates. This is the same pattern the open-source KiCad MCP harnesses use, just with far stronger verifiers (full-wave EM, thermal, SI/PI).
- Incumbents start from layout and analysis of existing schematics, not from requirements-to-schematic generation.

### Gaps
- Altium's 2025-2026 AI roadmap (now under Renesas) isn't covered by any primary source I found.
- I found no independent benchmarks of AuraStack or Fuse.

## How do they verify designs (DRC/ERC, SPICE, physics sims, ordering boards)? Does anyone close the loop by fabricating and testing?

### Takeaway
Verification is layered. Open-source harnesses use kicad-cli ERC/DRC (some add SPICE/ngspice and Freerouting). Startups add DFM/CAM and physics checks (Quilter). Incumbents call full multiphysics sign-off. Physical closed-loop verification happens only as one-off demos, chiefly Quilter's 843-part SBC that booted first try. I found no system that autonomously orders boards, then tests them and iterates.

### Cited Findings
- Seeed's MCP server runs ERC/DRC headlessly through kicad-cli — [GitHub](https://github.com/Seeed-Studio/kicad-mcp-server). ril3y/kicad-mcp-pro advertises SPICE, SI/PI, EMC, and FreeRouting — [GitHub](https://github.com/ril3y/kicad-mcp-pro). oaslananka gates manufacturing release behind a human — [GitHub](https://github.com/oaslananka/kicad-mcp-pro)
- Flux runs checks and review milestones in its multi-step agent plan — [Flux](https://www.flux.ai/p/blog/spring-2026-updates-faster-ai-better-layouts-smarter-sourcing). Diode integrates DFM and CAM in Zener — [Diode](https://www.diode.computer/)
- Cadence orchestrates PSpice, Sigrity X, Clarity 3D EM, Celsius thermal, and Nastran — [Embedded.com](https://www.embedded.com/cadence-develops-agentic-ai-platform-for-pcb-design/). Siemens uses HyperLynx SI and self-verifying physics sign-off — [Siemens](https://news.siemens.com/en-us/siemens-fuse-eda-ai-agent/); [Futurum](https://futurumgroup.com/insights/synopsys-cadence-and-siemens-take-agentic-chip-design-autonomous-at-dac/)
- Physical validation: Quilter's Speedrun boards were fabricated and booted Debian on first power-up — [VentureBeat](https://venturebeat.com/ai/quilters-ai-just-designed-an-843-part-linux-computer-that-booted-on-the). Hobbyists report fabricated agent-designed boards working (an ESP32-S3 board) — [atomic14](https://www.atomic14.com/2025/07/12/vibing-hardware); [Hackaday comments](https://hackaday.com/2026/09/05/can-ai-now-design-pcbs-that-just-work/)
- Hackaday cautions that production boards must handle edge cases, be validated by several engineers, and pass testing, so there is "a long way to go" — [Hackaday](https://hackaday.com/2026/09/05/can-ai-now-design-pcbs-that-just-work/)

### Inferences
- The main open frontier for a hacker project is the autonomous fab-and-test loop: the agent orders from JLCPCB, then runs a bring-up and test rig and feeds the results back. No one appears to have productized it.

### Gaps
- I didn't confirm whether any MCP server calls a fab ordering API (JLCPCB/PCBWay) directly.
- I found no data on first-spin success rates for agent-designed boards beyond anecdotes and Quilter's demo.

## Benchmarks and papers on LLM circuit/PCB design

### Takeaway
2026 brought the first real board-level benchmarks. On HWE-Bench (300 realistic tasks, verified with static rules plus simulation), the best model scored only 8.15%. Narrower code-synthesis setups report 80-88% pass rates under ERC-style checks. EEBench (run by atopile) puts frontier agents (GPT-6 Astra, Claude Opus 5) around 69%. The gap between these numbers mostly reflects task difficulty and how strict the verifier is.

### Cited Findings
- **HWE-Bench** (arXiv 2603.18102, 2026-03-18; Qiu, Xiao, Pan): 300 board-level schematic tasks from GitHub and OSHWLab across 8 domains, with a 2,914-datasheet knowledge base. Verification is a static electrical-rule check plus circuit simulation. The best model's overall pass rate was 8.15%, and the authors say models "lack physical intuition" — [arXiv](https://arxiv.org/abs/2603.18102)
- **PCBSchemaGen** (arXiv 2602.00510): introduces PCBBench (62 hand-authored tasks) and Open-Schematics-Eval (165 tasks) with deterministic pin-level verification and a Thompson-sampling refinement loop. Gemma-4-31B averages an 81.3% pass rate on PCBBench under the framework — [arXiv](https://arxiv.org/abs/2602.00510); [HTML](https://arxiv.org/html/2602.00510v2)
- **CircuitLM** (arXiv 2601.04505): a multi-agent natural-language-to-schematic framework tested on 100 prompts with deterministic ERC and Pass@1. Gemini scored 88% and Qwen 87% — [arXiv](https://arxiv.org/html/2601.04505v2)
- **SchGen** (arXiv 2605.30345): PCB schematic generation via semantic-grounded code representations — [arXiv](https://arxiv.org/pdf/2605.30345). I didn't read it in detail.
- **PCB-QA** (arXiv 2606.23704): a question-answering dataset for PCB design. It notes there was no standardized text-based benchmark for netlist and schematic evaluation — [arXiv](https://arxiv.org/html/2606.23704v1)
- **"Agentic Electronic Design Automation: A Handoff Perspective"** (arXiv 2606.19795): a position/survey paper on agentic EDA — [arXiv](https://arxiv.org/pdf/2606.19795). I didn't read it in detail.
- **EEBench** (atopile team): GPT-6 Astra scored 69.3% (±10%), roughly on par with Claude Opus 5. Routing a populated PCB from a schematic is "just one step of many." The article doesn't detail the tasks or how boards were verified, and the benchmark has a conflict of interest — [Hackaday, 2026-09-05](https://hackaday.com/2026/09/05/can-ai-now-design-pcbs-that-just-work/)
- The same article says OpenAI claimed GPT-6 Astra can take a schematic and produce a fully routed, manufacturable KiCad PCB — [Hackaday](https://hackaday.com/2026/09/05/can-ai-now-design-pcbs-that-just-work/)
- Adjacent analog/circuit benchmarks: AMSbench — [arXiv](https://arxiv.org/pdf/2505.24138); MMCircuitEval — [arXiv](https://arxiv.org/pdf/2507.19525)

### Inferences
- Results swing from 8% to 88% depending on verifier strictness (static plus simulation vs. ERC-only) and task realism (real datasheet-driven boards vs. synthetic prompts). The report should present them as non-comparable.
- Frontier labs now cite PCB routing as a marketed capability (GPT-6 Astra) and train on EE data (Diode with Claude 4.5), which suggests fast improvement through 2026.

### Gaps
- I couldn't get the specific models tested in HWE-Bench, EEBench's task list, or its verification method.
- I found no benchmark that scores layout (placement and routing) quality with physical fab-and-test.
