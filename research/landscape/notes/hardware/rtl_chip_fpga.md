# AI Agents and Agent Harnesses for Digital Chip Design, RTL, FPGA, and Verification

Research date: 2026-10-24. Some arXiv IDs below are 26xx (2026) preprints surfaced by search and not individually fetched. Treat their exact numbers as preprint claims.

## Research harnesses and benchmarks: what exists, how the loop works, best reported results

### Takeaway
Academic RTL agents share one basic loop. The LLM writes Verilog, a compiler or simulator (usually Icarus or Verilator) checks it, and errors, testbench mismatches, or waveforms go back into the prompt. On VerilogEval-Human v2, which is now saturated, the best scores are about 94-97% (VerilogCoder 94.2%, MAGE 95.7%, HDLFORGE 96.8%). Harder agentic benchmarks look much worse: NVIDIA's CVDP topped out at 34% pass@1 on code generation in 2025, and verification and testbench generation are the weakest areas.

### Cited Findings
**Early feedback-loop harnesses (2023-2024)**
- **AutoChip** (NYU: Thakur, Blocklove, Pearce, Tan, Garg, Karri; arXiv Nov 2023, revised Jun 2024). The LLM writes a Verilog module. Icarus Verilog compile errors and simulation mismatch messages (expected vs actual outputs) are fed back for another try. Evaluated on HDLBits problems, the tool feedback yields "24.20% more accurate Verilog". Scripts and data are open source. — [arXiv 2311.04887](https://arxiv.org/abs/2311.04887)
- **RTLFixer** (arXiv Nov 2023, v3 May 2024). It uses RAG plus ReAct prompting so the LLM acts as an autonomous debugging agent. About 55% of errors in LLM-generated Verilog are syntax errors. RTLFixer fixes about 98.5% of compile errors on 212 erroneous VerilogEval implementations and raises pass@1 by 32.3% on VerilogEval-Machine and 10.1% on VerilogEval-Human. — [arXiv 2311.16543](https://arxiv.org/abs/2311.16543)
- **ChipNeMo** (NVIDIA, Oct 2023). This is a domain-adapted LLM, not an agent: 43B parameters, runs on a single A100. Its uses are an engineering Q&A assistant, bug-report summarization, and short EDA-tool scripts. No Verilog benchmark score is reported. — [EE Times Asia](https://www.eetasia.com/nvidia-trains-llm-on-chip-design/); [element14](https://community.element14.com/technologies/businessofengineering/b/blog/posts/nvidia-s-ai---chipnemo-llm-will-assist-in-chip-design)
- **VeriAssist** (arXiv 2406.00115, Jun 2024). This is a self-verification and self-correction RTL assistant: it generates its own testbench, simulates, and fixes. It reports area, slack, and power vs plain GPT-4. In its testbench study, correct testbenches led to 10 of 11 correct RTL designs (90%) and incorrect testbenches to 3 of 6 (50%). — [arXiv 2406.00115](https://arxiv.org/pdf/2406.00115)
- **AIvril** (arXiv 2409.11411, 2024). RTL generation with verification in the loop. — [arXiv 2409.11411](https://arxiv.org/pdf/2409.11411)

**Multi-agent harnesses (2024-2026)**
- **VerilogCoder** (NVIDIA, arXiv 2408.08927, Aug 2024, AAAI 2025). It plans with a task graph and debugs with an AST-based waveform-tracing tool. It scores 94.2% on VerilogEval-Human v2 with GPT-4 Turbo, and 67.3% with Llama3. Caveat: it uses the testbenches, simulation, and waveforms to debug until tests pass, so it is compute-heavy and not directly comparable to single-shot scores. — [arXiv 2408.08927](https://arxiv.org/pdf/2408.08927)
- **MAGE** (arXiv 2412.07822, Dec 2024). A multi-agent engine with RTL-writer, testbench, judge, and debug agents. It uses high-temperature sampling of candidates and checkpoint-based waveform debugging. It scores 95.7% on VerilogEval-Human v2 and 94.8% on the original VerilogEval-Human. — [arXiv 2412.07822](https://arxiv.org/pdf/2412.07822)
- **HDLFORGE** (arXiv 2603.04646, 2026). A two-stage multi-agent system that escalates to a stronger model when needed. With GPT-4o it scores 95.5% Pass@1 on VerilogEval Human and 96.8% on V2. — [arXiv 2603.04646](https://arxiv.org/pdf/2603.04646)
- A GPT-5 comparison found that native GPT-5 and MAGE both pass 146 of 156 VerilogEval cases, and noted that some VerilogEval problems are themselves flawed. This means the benchmark is saturated. — [arXiv 2608.30659](https://arxiv.org/pdf/2608.30659)
- Other 2026 preprints:
  - "Exploring the Agentic Frontier of Verilog Code Generation" (arXiv 2603.19347) says frontier models still score relatively low on CVDP, and that CVDP had not yet been evaluated in its intended agentic setting when the paper was written. — [arXiv 2603.19347](https://arxiv.org/pdf/2603.19347)
  - "Agentic Hardware Design as Repository-Level Code Evolution" — [arXiv 2606.28279](https://arxiv.org/pdf/2606.28279)
  - ChipCraftBrain, a validation-first multi-agent system — [arXiv 2604.19856](https://arxiv.org/pdf/2604.19856)
  - Survey: "The Dawn of Agentic EDA" (Dec 2025) — [arXiv 2512.23189](https://arxiv.org/pdf/2512.23189)

**Verification agents (UVM, cocotb)**
- **UVM²** (arXiv 2504.19959, Apr 2025, open source). Three agents: an analysis agent writes the test plan, a generation agent builds the testbench and regenerates it from errors, and an optimisation agent adds tests based on coverage. — [arXiv 2504.19959](https://arxiv.org/html/2504.19959v3)
- **AgentDV** (arXiv 2608.27148, 2026). Closed loop of Verilator, cocotb, pyUVM, and coverage feedback. With Claude Sonnet 4.6 it reports a 100% pass rate on 4 DUTs, plus 76.9% line coverage and 95.7% branch coverage on its challenge benchmark. — [arXiv 2608.27148](https://arxiv.org/pdf/2608.27148)
- **HAVEN** (arXiv 2604.27643, 2026). Produces UVM testbenches that compile, starting from the spec. — [arXiv 2604.27643](https://arxiv.org/pdf/2604.27643)
- **Lončarević et al.** (UNITECH 2025). A multi-agent system that writes cocotb testbenches and runs them on Verilator. It reports 93.7% (±2.1) functional coverage, 82.5% top-1 pass rate, and 1.4 hours average time to first test. Coverage does not yet saturate on domain-specific tasks. — [UNITECH 2025 PDF](https://unitech.tugab.bg/images/2025/dokladi/4-Computer%20system%20and%20technologies/p214_s4_u213_id378-SP.pdf)
- Inference-time token allocation and coverage limits in agentic verification. — [arXiv 2604.15657](https://arxiv.org/pdf/2604.15657)

**Physical design agents (OpenROAD)**
- **ORAssistant** (arXiv 2410.03845, Oct 2024). A RAG chatbot for OpenROAD covering install, commands, and flow setup. It was originally built on Gemini. It answers questions but does not tune flows. — [arXiv 2410.03845](https://arxiv.org/pdf/2410.03845)
- **ORFS-agent** (UCSD, arXiv 2506.08332, Jun 2025; ISPD '26). An LLM tunes OpenROAD-flow-scripts parameters: it runs parallel flows, reads intermediate metrics, and adjusts the parameters. It beats Bayesian optimization with roughly 40% fewer iterations to reach the same quality of results. A full experiment costs under $10 on a cheap model, and the code is public. — [arXiv 2506.08332](https://arxiv.org/pdf/2506.08332); [UCSD ISPD'26](https://vlsicad.ucsd.edu/Publications/Conferences/426/c426.pdf)
- **AgenticPD** (2026). A Judge Agent steers agents that each handle one stage (floorplan, placement, global routing, CTS) and uses checkpoints so the full flow is not rerun. — [alphaXiv 2607.04758](https://www.alphaxiv.org/abs/2607.04758.md)
- Physical-design agent benchmarks: PDAGENT-Bench ([arXiv 2606.17253](https://arxiv.org/pdf/2606.17253)) and CLOSER-Bench for budgeted design closure across stages ([arXiv 2607.16632](https://arxiv.org/pdf/2607.16632)). ASIC-Agent is a multi-agent RTL-to-GDSII system built on OpenLane ([arXiv 2508.15940](https://arxiv.org/pdf/2508.15940)).

**End-to-end CPU agents**
- **Design Conductor / VerCore** (Verkor, arXiv 2603.08716, Mar 2026).
  - **Run:** The only input was a 219-word spec. The agent ran autonomously for 12 hours and built several RISC-V pipeline variants, each down to GDSII on the ASAP7 research PDK. The paper names no model, only "frontier models".
  - **Loop:** Each module's testbench must pass before integration. An integration testbench is checked against Spike traces, and VCD waveforms are converted to CSV and analyzed with pandas to find mismatches. OpenROAD timing reports then drive RTL and microarchitecture revisions.
  - **Results:** Timing met at 1.48 GHz (spec target 1.6 GHz), CoreMark 3261, about 2809 µm² area excluding cache. Linux-capable.
  - **Not fabricated:** The result is tape-out-ready GDSII on a research PDK. The authors say human architects remain critical. — [arXiv 2603.08716](https://arxiv.org/html/2603.08716v1); [Adafruit](https://blog.adafruit.com/2026/03/22/prompt-to-tape-out-autonomous-ai-agent-builds-1-5-ghz-risc-v-cpu/)
- **QiMeng** (Chinese Academy of Sciences, arXiv 2506.05007, Jun 2025). Three layers: a domain Large Processor Chip Model (LPCM), hardware and software design agents built on it, and applications. **QiMeng-CPU-v2** (arXiv 2505.03195) targets automated design of superscalar processors. It notes that earlier automated designs were tiny (an 8-bit CPU of about 1k gates), and refers to prior industrial-scale work that reached roughly Intel 486-class performance. That is the earlier CAS "QiMeng-CPU-v1 / Enlightenment-1" RISC-V CPU, generated from I/O examples and reportedly taped out in 2023. — [arXiv 2506.05007](https://arxiv.org/html/2506.05007v1); [arXiv 2505.03195](https://arxiv.org/pdf/2505.03195); [SemiEngineering](https://semiengineering.com/fully-automated-hardware-and-software-design-of-processor-chips-chinese-academy-of-sciences/)

**Benchmarks**
- **CVDP** (NVIDIA, arXiv 2506.14074, Jun 2025).
  - **Scope:** 783 problems across 13 categories, covering RTL generation, verification, debug, spec alignment, and Q&A, all written by experienced hardware engineers. Both non-agentic and agentic (Docker plus tools) formats.
  - **Results:** State-of-the-art models reached at most 34% pass@1 on code generation. Agentic RTL-reuse and verification tasks are hardest, and SystemVerilog testbench generation is the weakest category.
  - **Maintenance:** The Si2 LLM Benchmarking Coalition now runs a public leaderboard (LBC Bench). CVDP 1.1.0 was released Jun 8, 2026 and moved to stable open-source tools. — [arXiv 2506.14074](https://arxiv.org/abs/2506.14074); [Si2 CVDP 1.1.0](https://si2.org/summary-and-key-improvements-in-cvdp1-1-0/); [Si2 leaderboard](https://si2.org/introducing-the-lbc-bench-leaderboard-a-new-public-benchmark-for-llms-in-chip-design/)
- Turing's case study describes "1,500+ real-world Verilog tasks" for CVDP, which conflicts with the paper's 783. The extra tasks may include unreleased or held-out problems. — [Turing](https://www.turing.com/case-study/benchmarking-rtl-agents-with-real-world-verilog-tasks-for-nvidia-cvdp)
- **VerilogEval v2.** "Revisiting VerilogEval" (NVIDIA, Aug 2024) adds a spec-to-RTL task and in-context learning. — [arXiv 2408.11053](https://arxiv.org/pdf/2408.11053)
- **RTLLM** (HKUST, 2023). A design-level benchmark of about 50 designs. — [arXiv 2308.05345](https://arxiv.org/abs/2308.05345)

### Inferences
- The field has shifted from "can an LLM write a module" (VerilogEval, now saturated) to repository-scale and agentic tasks: CVDP, multi-stage closure benchmarks, and physical design. Headline numbers on the newer benchmarks are low, and these are now the meaningful ones to quote.
- Verification, meaning testbench and coverage generation, is consistently the weakest capability. It is where most startup and EDA-vendor effort is going.
- Every serious harness uses a ground-truth tool oracle: compiler, simulator, waveform, ISA reference model (Spike), timing report, or formal prover. Waveform tracing (VerilogCoder, MAGE, Design Conductor's VCD-to-pandas step) is a recurring trick that sets the stronger systems apart.

### Gaps
- No current per-model or per-agent numbers from the Si2 CVDP leaderboard were found, and no confirmed ChipAgents CVDP score.
- "ChatChip" as a distinct system was not found. The user probably means NYU's Chip-Chat, covered in the tapeouts section.
- No independent evaluation was found for "ChipAgents" as an academic harness. It is a company, covered below.
- 2026 arXiv preprints (HDLFORGE, AgentDV, AgenticPD, etc.) were mostly seen as abstracts or snippets and were not read in full.

## Startups: what they automate and funding

### Takeaway
ChipAgents is the funding leader among agentic-EDA startups, with a $134M total Series A as of Jul 2026. Cognichip follows with a $60M Series A in Apr 2026 ($93M total), and Lip-Bu Tan joined its board. Most startups target front-end design and verification, because that is where tool oracles exist and labor costs are highest.

### Cited Findings
- **ChipAgents** (formerly Alpha Design AI; Santa Barbara). Agentic RTL design and verification, with debug and testbench agents.
  - **Funding:** $21M Series A in Oct 2025 led by Bessemer. A $50M Series A1 in Feb 2026 led by Matter Venture Partners brought the total to $74M. A $60M Series A2 on Jul 29, 2026 led by B Capital, with Bessemer, Micron, MediaTek, and Ericsson, brought the Series A total to $134M. The A2 was announced with an NVIDIA partnership.
  - **Company claims:** 6x ARR growth in H1 2026 and deployments at more than 120 semiconductor companies.
  - **Discrepancy:** The three tranches add up to $131M, not $134M. One outlet noted that the ARR multiple slowed from "140x" at the A1 to 6x. — [Quartz](https://qz.com/chipagents-series-a2-funding-nvidia-partnership-072926); [Evertiq Feb 2026](https://evertiq.com/design/2026-02-19-chipagents-expands-funding-to-74m-for-ai-driven-ic-design); [Evertiq Aug 2026](https://evertiq.com/news/2026-08-17-us-firm-chipagents-expands-series-a-funding-to-134-million)
- **Cognichip** (Redwood City, founded 2024). Its "Artificial Chip Intelligence" platform is built on a physics-informed foundation model for chip design.
  - **Funding:** $33M seed in May 2025. A $60M Series A in Apr 2026 led by Seligman Ventures brought the total to $93M. Reported participants include SBI Investment, Mayfield, and Lux, and one source names Walden Catalyst; sources differ on the full investor list.
  - **Board:** Intel CEO Lip-Bu Tan and Seligman's Umesh Padval are joining the board.
  - **Claims (projections, unverified):** up to 75% lower design cost and 50% faster timelines, and more than 30 enterprise users. — [BuiltIn SF](https://www.builtinsf.com/articles/cognichip-raises-60m-series-a-20260403); [EPDT](https://www.epdtonthenet.net/article/221338/Cognichip-Gains--60-Million-Worth-of-Series-A-VC-Investment.aspx); [Seedtable](https://seedtable.com/companies/cognichip)
- **Bronco AI** (YC). An "applied AI lab" building AI verification engineers for chips, focused on debug and triage. Backed by YC, 8VC, Elad Gil, SV Angel, and Contrary. Amount not disclosed. — [YC](https://www.ycombinator.com/companies/bronco-ai); [Extruct](https://www.extruct.ai/hub/bronco-ai-funding)
- **Silimate** (YC S23, co-founder Ann Wu). Started as an AI copilot that finds functional bugs and predicts PPA during RTL development, and now offers an AI debugger. Reportedly in production at Fortune 500 chipmakers and chip unicorns (Stanford Daily, Mar 2026). YC was its investor, and Wu says it was YC's first EDA company. No round size was found. — [SEMI/ESD Alliance interview](https://www.semi.org/en/blogs/the-changing-landscape-of-chip-design); [Extruct](https://www.extruct.ai/hub/silimate-com-funding)
- **MooresLabAI / Moores Lab AI** (Austin). An agentic platform covering design, verification, and backend. Its product **VerifAgent** reads specs and generates verified RTL and UVM code. A Caplight page shows a "$16M Series A led by BlackRock and Fin Capital" banner, but it could not be tied to Moores Lab and is unconfirmed. — [SemiEngineering](https://semiengineering.com/moores-law-ai-applying-agentic-ai-across-chip-design/); [techovedas](https://techovedas.com/moores-labai-how-agentic-ai-is-rewriting-the-future-of-chip-design/)
- **Normal Computing** (founded 2022). Its Normal EDA product uses "autoformalization": LLMs plus formal logic turn specs into test plans, SystemVerilog assertions, and stimulus that trace back to requirements. It integrates with VCS and Xcelium. The team says it built an open-source Verilog simulator of about 580K lines with AI agents, including formal and mutation testing. TamRadar, a less-established outlet, reports a $50M strategic round led by Samsung Catalyst, use of Normal EDA by more than half of the top-10 semiconductor companies, and the CN101 thermodynamic chip taped out in Jun 2025. — [AI Engineer org page](https://www.ai.engineer/orgs/normal-computing); [TamRadar](https://www.tamradar.com/funding-rounds/normal-computing-strategic-50m)
- **Rise Design Automation.** Launched IPCreate and IPTransform at DAC 2026. These automate spec to HLS to RTL and claim about 1M gates per week per engineer. No funding found. — [ElectronicsForU](https://www.electronicsforu.com/news/ai-tools-automate-chip-design)
- **Verkor** (verkor.io). Built Design Conductor (see above), the autonomous spec-to-GDSII CPU agent. — [arXiv 2603.08716](https://arxiv.org/html/2603.08716v1)
- **Agentrys.** Agentic design automation for chipmakers. Raised $24.5M including a $19.1M seed led by Etna Labs. — [Silicon Semiconductor](https://siliconsemiconductor.net/article/125240/Agentrys_raises_245_million_to_build_agentic_design_automation_for_chipmakers)
- **ChipStack** was a startup until Cadence acquired it in Nov 2025 (see the big EDA section). — [DCD](https://www.datacenterdynamics.com/en/news/cadence-launches-agentic-ai-design-tool-to-support-chip-design-and-verification/)
- SemiEngineering's DAC 2025 startup roundup lists more agentic-EDA entrants. — [SemiEngineering](https://semiengineering.com/eda-startups-at-dac-2025)

### Inferences
- Strategic investors (Micron, MediaTek, Ericsson, Samsung Catalyst) and Lip-Bu Tan's involvement suggest chipmakers want an alternative to depending only on Synopsys and Cadence agents.
- Big EDA buying startups has started (ChipStack). Other verification-agent startups are plausible acquisition targets. That is speculation.

### Gaps
- Funding is undisclosed or unconfirmed for Bronco, Silimate, MooresLabAI, and Rise.
- ChipAgents revenue figures and customer count are company-reported only.
- No head-to-head public benchmark of startup products was found.

## Big EDA vendors: Synopsys, Cadence, Siemens

### Takeaway
All three big vendors shipped or announced multi-agent "virtual engineer" orchestration layers in 2025-26, and each runs the agents on top of its own simulators, formal tools, and implementation tools. All three cite NVIDIA Nemotron and Agent Toolkit as a supported stack. Performance claims are vendor-reported only.

### Cited Findings
- **Synopsys AgentEngineer / Autopilot Platform.**
  - **Timeline:** First prototype shown at DAC 2025 on Microsoft Discovery. Reuters described the near-term plan as agents that a human engineer instructs. The portfolio of "domain-specific long-horizon agents" was announced in late Sep 2026 on the new Autopilot Platform.
  - **Domains:** verification, system validation, implementation (RTL to GDS), AMS, manufacturing, and simulation and analysis.
  - **Status:** More than 50 customer engagements. GA targeted for end of 2026. Humans keep approval checkpoints.
  - **Claims (LinkedIn, vendor):** up to 50x faster verification closure, 20% higher coverage, 30% productivity gain, 2x token efficiency.
  - **Partners:** An autonomous verification workflow built with NVIDIA Agent Toolkit and Nemotron 3 Ultra. At DAC 2026, autonomous workflows on Microsoft Discovery were shown, with AMD evaluating them. The Synopsys.ai Copilot product line (Sep 2025 expansion) is the earlier assistant layer. — [Tom's Hardware](https://tomshardware.com/tech-industry/semiconductors/synopsys-debuts-autopilot-platform-for-developing-chips-autonomously-using-ai-new-agentengineer-platform-is-poised-for-general-availability-by-the-end-of-2026); [Reuters via StreetInsider](https://www.streetinsider.com/Reuters/Synopsys+lays+out+strategy+for+AI+%27agents%27+to+design+computer+chips/24522377.html); [Synopsys Sep 2025 PR](https://news.synopsys.com/2025-09-03-Synopsys-Announces-Expanding-AI-Capabilities-for-its-Leading-EDA-Solutions); [Electronic Specifier](https://www.electronicspecifier.com/?p=208322); [EE Times Asia](https://www.eetasia.com/?p=75682)
- **Cadence ChipStack AI Super Agent.**
  - **Timeline:** Cadence acquired ChipStack in Nov 2025 and launched the product on Feb 10, 2026.
  - **What it does:** It orchestrates parallel "virtual engineers" for RTL generation, testbenches, test plans, regression, and debug. A "mental model" (a structured representation of design intent, hierarchy, and relationships) underpins the agents. It optimizes RTL for PPA, and writes formal plans and SystemVerilog assertions that Jasper proves automatically.
  - **Integrations:** Verisium, Cerebrus, and JedAI. It supports on-prem or cloud models, including NVIDIA Nemotron and OpenAI GPT.
  - **Early customers:** Altera, NVIDIA, Qualcomm, and Tenstorrent, reporting 4x-10x verification speedup on selected projects. Cadence claims up to 10x productivity.
  - **Roadmap:** "Level-5" autonomous capabilities and the AgentStack orchestration framework reach early-access customers in H2 2026. A later release billed it as the "industry's first fully autonomous virtual engineer," powered by NVIDIA.
  - **Related products:** Cerebrus (RL-based implementation flow optimization) and JedAI (data and AI platform) are the older AI layers that ChipStack plugs into. — [BusinessWire PR](https://secure.businesswire.com/news/home/20260210926552/en/Cadence-Unleashes-ChipStack-AI-Super-Agent-Pioneering-a-New-Frontier-in-Chip-Design-and-Verification); [Cadence product brief](https://www.cadence.com/ko_KR/home/resources/product-briefs/cadence-chipstack-ai-super-agent-pb.html); [DCD](https://www.datacenterdynamics.com/en/news/cadence-launches-agentic-ai-design-tool-to-support-chip-design-and-verification/); [01net](https://www.01net.it/cadence-unveils-industrys-first-fully-autonomous-virtual-engineer-for-chip-design-powered-by-nvidia/)
- **Siemens Fuse EDA AI Agent** (launched Mar 16, 2026 at NVIDIA GTC).
  - **What it does:** It orchestrates multi-agent workflows across the whole Siemens EDA portfolio. It is built on the Fuse EDA AI system: RAG, multimodal EDA data, MCP orchestration, Agent Skills support, and an open framework for third parties.
  - **Tool coverage:** The new Questa One Agentic Toolkit handles testbench generation and debug. Other integrations: Catapult (HLS and RTL), Aprisa (place-and-route and timing), Solido (custom), Veloce (emulation), and Calibre (DRC analysis).
  - **Platform:** Supports NVIDIA Agent Toolkit and Nemotron. — [Siemens PR](https://news.siemens.com/en-us/siemens-fuse-eda-ai-agent/)

### Inferences
- The vendors are converging on three things: MCP or skill-style tool exposure, NVIDIA Nemotron for on-prem deployments (IP-sensitive customers), and human approval gates. Their moat is owning the signoff-grade tool oracles (VCS, Xcelium, Jasper, Questa, PrimeTime, Calibre) that startups have to license.
- Siemens' explicit MCP and Agent Skills support means third-party agents, including general coding agents, can drive Siemens tools.

### Gaps
- No independent benchmarks of any vendor agent were found.
- Pricing and licensing of the agents (per token, per seat) are unknown.

## FPGA-specific agents: synthesis, programming real boards, testing on hardware

### Takeaway
Agents that close the loop on real FPGA hardware exist mainly as 2025-26 research on HLS flows. FPGAgent builds an xclbin and runs it on an Alveo U200. ContractHIL-HLS feeds Vivado, board runtime, and power data back into generation. Most other FPGA agents stop at C-simulation, co-simulation, or synthesis reports.

### Cited Findings
- **FPGAgent** (arXiv 2608.23630, Aug 2026). A multi-agent pipeline that goes HLS code, then verification, then compile to an xclbin bitstream, then deploy and run on a physical Xilinx U200. It auto-generates host code (buffer movement, kernel launch, readback). On build or runtime failure it captures logs and looks them up in an experience database that maps error logs to physical causes. — [alphaXiv 2608.23630](https://www.alphaxiv.org/abs/2608.23630); [arXiv PDF](https://arxiv.org/pdf/2608.23630)
- **ContractHIL-HLS** (Beijing University of Technology, 2026). Multi-agent HLS with hardware in the loop. It feeds HLS, Vivado, board runtime, power, and failure data back into generation. It was validated on a post-quantum crypto accelerator tested on a board, using a dual-bitstream design, cutting average runtime from 207.3 ms to 52.4 ms. — [alphaXiv 2607.25283](https://www.alphaxiv.org/abs/2607.25283.md)
- **LAAFD** (Los Alamos, arXiv 2602.06085, Feb 2026). Agents convert C++ into Vitis HLS kernels, with co-simulation and synthesis feedback. No board programming was seen. — [arXiv 2602.06085](https://arxiv.org/html/2602.06085v1)
- **A2H-MAS** (arXiv 2508.10904, Aug 2025). An algorithm-to-HLS multi-agent system for FPGA implementation. — [arXiv 2508.10904](https://arxiv.org/pdf/2508.10904)
- "From Pragmas to Partners: A Symbiotic Evolution of Agentic HLS" (arXiv 2602.01401, 2026) is a position and survey paper. — [arXiv 2602.01401](https://arxiv.org/html/2602.01401v1)
- On the vendor side, Rise's IPCreate and IPTransform and Siemens Catapult integration address HLS, but they target ASIC and FPGA RTL rather than closing the loop on a board. — [ElectronicsForU](https://www.electronicsforu.com/news/ai-tools-automate-chip-design); [Siemens PR](https://news.siemens.com/en-us/siemens-fuse-eda-ai-agent/)

### Inferences
- An open opportunity is a general-purpose agent harness for RTL (not HLS) on hobby and dev FPGAs. One loop would combine Verilator or cocotb simulation, Yosys/nextpnr (open iCE40 and ECP5 flow) or Vivado batch synthesis, timing and utilization report parsing, openFPGALoader programming, and on-board UART or logic-analyzer readback as the final oracle. No polished open-source project doing exactly this showed up in searches.
- Data-center HLS boards (Alveo) dominate the research because the host-runtime APIs (XRT) make hardware results easy to script.

### Gaps
- No vendor product from AMD/Xilinx or Altera offering an agent that programs and tests boards was found. Altera is a ChipStack customer for verification only.
- No open-source GitHub project for RTL agents with hardware in the loop was verified. Hobbyist Claude Code or Codex plus Vivado/Yosys workflows likely exist on GitHub or blogs but were not found or verified here.
- The 26xx arXiv IDs for FPGAgent and ContractHIL-HLS should be checked directly.

## LLM-designed tapeouts

### Takeaway
The first chip with wholly AI-written HDL was NYU's Chip-Chat (2023): a GPT-4-co-designed 8-bit accumulator CPU on SkyWater 130nm through Tiny Tapeout. Several Tiny Tapeout and Efabless contest chips followed in 2023-24. By 2026 the frontier is autonomous spec-to-GDSII, as with Verkor's VerCore RISC-V at 1.48 GHz on ASAP7. That design is tape-out-ready GDSII on a research PDK and was not fabricated.

### Cited Findings
- **Chip-Chat** (NYU Tandon: Blocklove, Garg, Karri, Pearce; arXiv 2305.13243, May 2023). An engineer used conversational GPT-4 prompting to co-design an 8-bit accumulator-based microprocessor. It was taped out on a SkyWater 130nm shuttle through Tiny Tapeout and described as likely the first wholly AI-written HDL for tapeout. — [arXiv 2305.13243](https://arxiv.org/abs/2305.13243); [NYU Tandon](https://engineering.nyu.edu/news/chip-chat-conversations-ai-models-can-help-create-microprocessing-chips-nyu-tandon-researchers)
- **Efabless AI Generated Open-Source Silicon Design Challenge** (2023). Teams used generative AI to design and tape out an open-source chip in about 3 weeks and had to supply verification testbenches. Winners got free fabrication. 1st place: QTCore-C1 co-processor (Hammond Pearce, NYU). 2nd place: Cyberrio (Tsinghua-Berkeley Shenzhen Institute). Efabless shut down in early 2025, so this pipeline no longer exists (from memory, not re-verified here). — [Semiconductor Digest](https://semiconductor-digest.com/?p=23114); [Wevolver](https://www.wevolver.com/article/ai-generated-open-source-silicon-design-challenge)
- **Tiny Tapeout 3 study** (arXiv 2405.02326). Several conversational LLMs given simple prompts produced functional and verification HDL that was taped out through TT3, with the flow limited to Verilog-2001. — [arXiv 2405.02326](https://arxiv.org/html/2405.02326v2)
- **Johns Hopkins spiking neuron array** (arXiv 2402.10920). ChatGPT-4 wrote the Verilog for a spiking-neuron ASIC submitted to Tiny Tapeout 5. — [arXiv 2402.10920](https://www.arxiv.org/pdf/2402.10920)
- **QiMeng / CAS.** QiMeng-CPU-v2 (May 2025) refers to the earlier industrial-scale automated CPU that reached about Intel 486 performance. That is the CAS automatically designed RISC-V CPU reported as taped out in 2023; this was not re-verified beyond the QiMeng paper's reference. — [arXiv 2505.03195](https://arxiv.org/pdf/2505.03195)
- **Verkor VerCore** (Mar 2026). Autonomous spec-to-GDSII in 12 hours on ASAP7: 1.48 GHz, CoreMark 3261. No silicon. — [arXiv 2603.08716](https://arxiv.org/html/2603.08716v1)
- **Education.** "From RTL to Prompt Coding" (arXiv 2601.13815, Jan 2026) covers teaching chip design with LLMs. — [arXiv 2601.13815](https://arxiv.org/html/2601.13815v1)

### Inferences
- Fabricated LLM-designed silicon is still small (Tiny Tapeout tiles, 8-bit CPUs) or comes from the Chinese QiMeng program. Larger agent-designed cores exist only as GDSII. The first agent-autonomous chip fabricated at real complexity looks like the next milestone, and Verkor or a startup-plus-foundry shuttle are likely candidates (speculative).
- Tiny Tapeout plus the open SKY130/GF180/IHP PDKs plus OpenLane or OpenROAD make up the realistic path for a student or hobbyist to tape out an agent-built design.

### Gaps
- No 2025-26 news was found confirming that an agent-designed chip (beyond Tiny Tapeout-scale) came back from fab and was tested in silicon.
- Not verified: whether any Tiny Tapeout 2025-26 shuttles had notable agent-designed submissions, and the exact silicon status of the QiMeng and CAS CPUs.
- Efabless shutdown details were not re-verified in this session.
