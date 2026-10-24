# LLM Agents for 3D/Mechanical CAD and Physical Fabrication Loops (research frontier as of 2026-10-24)

## Q1: Key papers, agent architectures, benchmarks and best scores (2024–2026)

### Takeaway
The field has split into two lines. One trains specialist models that turn point clouds or images into CadQuery code (CAD-Recode, then cadrille with online RL), and these now reach about 85–92% IoU on DeepCAD and Fusion360. The other runs frontier models (Claude, GPT, Gemini) in agent loops that generate, execute, render, simulate and refine. On realistic engineering briefs these loops still do badly: only about 20–30% of requirements are met, and about 58% of executable outputs violate at least one engineering requirement. The 2026 benchmarks (Text2CAD-Bench, BenchCAD, CADTests, CADEngBench) all move away from Chamfer/IoU towards executable tests, requirements and FEA. Many surveyed systems work in CadQuery or build123d on the OCCT kernel; CAD-Assistant and Query2CAD use FreeCAD and SceneCraft uses Blender.

### Cited Findings
**Early agent loops (2024–2025)**
- CADCodeVerify (Alrashedy, Tambwekar, Zaidi, Langwasser, Xu, Gombolay; arXiv 2410.05340, ICLR 2025). A VLM writes validation questions about the generated 3D object, answers them, and then corrects the code. The paper also introduced CADPrompt, 200 natural-language prompts paired with expert CAD code and built on DeepCAD samples. With GPT-4 it reduced point-cloud distance by 7.30% and raised compile/success rate by 5.0% over prior work. The arXiv abstract says 5.0%, while a secondary summary says 5.5%. — [arXiv](https://arxiv.org/abs/2410.05340); [ICLR proceedings](https://proceedings.iclr.cc/paper_files/paper/2025/hash/81a934cd364e18ea6fdeaf57a93c17d4-Abstract-Conference.html)
- CAD-Assistant (arXiv 2412.13810, ICCV 2025). A VLM planner calls CAD tools that run on a FreeCAD Python interpreter and inspects how each command changes the geometry. The authors report beating VLLM baselines and supervised task-specific methods on several CAD benchmarks. — [arXiv](https://arxiv.org/abs/2412.13810v3); [orbilu poster](https://orbilu.uni.lu/handle/10993/66150)
- Query2CAD generates FreeCAD code with GPT-4 Turbo. It refines that code using self-refinement loops, human-in-the-loop feedback and a VQA score. — [review](https://themoonlight.io/review/query2cad-generating-cad-models-using-natural-language-queries)
- SceneCraft (Hu et al., ICML 2024) is a Blender-code agent. It plans a scene graph, converts it to numeric constraints, and refines the result by having a VLM inspect renders, while keeping a reusable function library. It scored 88.9 on constraint passing versus 5.6 for BlenderGPT, with CLIP-score gains of 45.1% and 40.9%. — [PMLR](https://proceedings.mlr.press/v235/hu24g.html); [arXiv](https://arxiv.org/html/2403.01248v1)
- BlenderLLM (arXiv 2412.14203) trains an LLM for Blender-based CAD with self-improvement. — [arXiv](https://arxiv.org/html/2412.14203v1)
- MechAgents (Ni & Buehler, arXiv 2311.08166; Extreme Mechanics Letters 2024) uses multi-agent teams for elasticity FEA. A two-agent team writes, runs and self-corrects FEM code, and larger teams split the work into plan, formulate, code, execute and critique roles. It is analysis-only, not CAD generation. — [arXiv](https://arxiv.org/abs/2311.08166v1); [summary](https://awesomepapers.io/ai-agents/papers/2311.08166)

**Specialist models trained to write CAD code**
- CAD-Recode (arXiv 2412.14042) reverse-engineers CadQuery code from point clouds. It was trained on about 1M procedurally generated samples and improved IoU by more than 10% over prior best methods on DeepCAD and Fusion360. Out of domain (CC3D) it drops to about 60% IoU with about 10% invalid outputs, recovering to 74% IoU with best-of-10 sampling. — [arXiv PDF](https://arxiv.org/pdf/2412.14042); numbers via [search summary of follow-up papers](https://arxiv.org/html/2505.22914v1)
- cadrille (arXiv 2505.22914) takes point-cloud, image or text input and adds online RL (Dr.CPPO) to CAD-Recode-style training. Reported results are DeepCAD IoU 92.2, Fusion360 IoU 84.6 and 0.0% invalid outputs. The 92.2 figure appears to be the multi-view-image setting. I did not verify these numbers in the original tables. — [arXiv](https://arxiv.org/pdf/2505.22914)
- CAD-Coder (NeurIPS 2025, arXiv 2505.19713) generates CadQuery from text using supervised fine-tuning, chain-of-thought and GRPO with a Chamfer-distance geometric reward. — [NeurIPS PDF](https://proceedings.neurips.cc/paper_files/paper/2025/file/564a224e88f2490f3c1deaae877d37e3-Paper-Conference.pdf); [arXiv](https://arxiv.org/html/2505.19713v3)
- A 2026 benchmark notes that the CAD-Recode / cadrille / CADEvolve line still cannot build helical sweeps or parametric involute gears. — via [BenchCAD](https://arxiv.org/pdf/2605.10865)

**2026 benchmarks**
- Text2CAD-Bench (arXiv 2605.18430, May 2026; HF dataset AICAD/Text2CAD-Bench) confirms that code beats command sequences. Every model degrades when asked for DeepCAD-style command sequences instead of CadQuery; for example, Gemini-3-Flash scores L2 CD 193.45 with sequences versus 88.57 with CadQuery. One caveat: Claude-4.5 had a lower invalid rate with command sequences on L2-Geo. The leaderboard is led by GPT-5.2 (CD 63.97), followed by Claude-4.5-Sonnet (CD 66.90). The authors warn that CD and IoU are computed only over samples that execute, which inflates scores for models that fail often. — [arXiv](https://arxiv.org/html/2605.18430v1); [HF](https://huggingface.co/datasets/AICAD/Text2CAD-Bench)
- BenchCAD (arXiv 2605.10865) is an industry-standard benchmark for programmatic CAD. On BenchCAD-Edit, GPT-5.3-thinking scored 0.865 and Claude Opus 4.7-thinking scored 0.853. The paper finds that "textual instruction does the heavy lifting; the visual signal is at best a clarifier." — [arXiv](https://arxiv.org/html/2605.10865v1)
- CADTests (arXiv 2605.07807; github dimitrismallis/CADTestBench) replaces Chamfer distance with executable tests. Using Claude as the planner gave significant gains over GPT-based variants across all methods. — [arXiv](https://arxiv.org/html/2605.07807v1); [GitHub](https://github.com/dimitrismallis/CADTestBench)
- CADEngBench (MBZUAI, arXiv 2608.09296, Aug 2026) has two parts. The parametric part covers 300 parts and 600 tasks with layered checks: validity, engineering and DFM requirements, parameter perturbation, functional edits, and CalculiX FEA. The assembly part covers 150 Fusion 360 joint pairs checked in PyBullet. Eight models were tested.
  - Best validity was Claude Sonnet 4.5 at 58.0%.
  - Best requirement score was Gemini 3 Flash at only 30.0%.
  - Best end-to-end assembly kinematics score was Gemini at only 15.8%.
  - No model led everywhere. — [arXiv](https://arxiv.org/html/2608.09296v1)

**Agent-loop papers with FEA (2026)**
- Son et al., "Self-Improving CAD Generation Agents with FEA as Feedback" (arXiv 2605.17448, May 2026, work in progress). The task is a free-form engineering brief that must become a fully assembled multi-part STEP file, checked by FEA against typed requirements. Agents were Codex (GPT-5.5) and Claude Code (Opus-4.7). Results:
  - Neither agent produced a single strict pass on the first attempt.
  - The best configuration met about 20% of typed requirements.
  - Adding a blueprint schema and a 21-view renderer raised GPT-5.5/xhigh Box-IoU from 0.444 to 0.592 on S2O and from 0.397 to 0.505 on Fusion360. — [arXiv](https://arxiv.org/abs/2605.17448)
- Physics-in-the-Loop (Berger et al., TU Dresden / MAN / DFKI; arXiv 2605.19717, May 2026) is a LangGraph Generate-Simulate-Refine loop with four roles: Planner, CAD Engineer writing CadQuery, a VLM Geometry Reviewer, and a Structural Reviewer running torch-fem FEA with gmsh meshing.
  - Benchmark: 20 load cases × 25 scalings = 500 configurations, with a 10-iteration cap.
  - Hitting the target safety factor succeeded 59.0% of the time with FEA feedback versus 22.2% without (p = 0.0008).
  - Execution succeeded 96–97.5% of the time, meshing 76–88% and FEA 81–87%.
  - The agent loop converged faster than a single LLM (e.g., Sonnet 4.5 needed 4.5 iterations versus 13.0).
  - Cost ranged from $0.02 (Gemini Flash) to $0.20 (Opus 4.5) per iteration.
  - The paper reports a 3.4–3.5% compile-rate gain over CADCodeVerify; the abstract and body disagree slightly on this number. — [arXiv](https://arxiv.org/html/2605.19717)

### Inferences
- The best agent architecture is converging on the same pattern: a planner produces a structured blueprint, the model writes code in an OCCT kernel (CadQuery/build123d), deterministic checks run (does it execute, is it a valid solid, does it mesh), then a VLM reviews multi-view renders, then FEA runs. Agents may read the deterministic results but not edit them.
- Benchmark scores are not comparable across papers. Older papers report CD/IoU (where specialist models reach about 90% IoU), while the newer ones report requirement and test pass rates (where frontier agents reach only about 20–30%). The newer metrics are the ones that matter for physical parts.
- Frontier-model rankings are close and shift with the task (GPT-5.x vs Claude vs Gemini), so the harness design matters more than which model is chosen.

### Gaps
- I did not verify FlexCAD, CAD-Llama, CADialogue, Text2CAD (NeurIPS 2024), Text-to-CadQuery (Badagabettu et al.), BlenderAlchemy or 3D-GPT in this session; searches returned nothing usable within budget. Their numbers should not be cited from these notes.
- I did not retrieve cadrille's single-modality numbers or the full CADPrompt leaderboard (e.g., CADmium) from primary tables.
- I did not search beyond ArtiCAD (arXiv 2604.10992, articulated assembly via multi-agent code generation), which appeared only in search results.

## Q2: Projects that close the loop with physical fabrication

### Takeaway
Very few systems actually fabricate an agent's CAD design, test it physically, and then let the agent revise the CAD from the measurements. I found none that do this fully autonomously. The physical loops that do exist fall into three groups:
- **Process-level loops.** LLM-3D Print adjusts printer parameters, not geometry.
- **Parametric optimization loops without an LLM.** MAMA BEAR uses Bayesian optimization.
- **Agent-designed parts that are fabricated and tested once.** RocketSmith and Text2Robot fall here; in RocketSmith the flight data was compared with simulation but not clearly fed back into redesign.

The closest open-source example of a measurement-to-CAD loop is print-in-the-loop, and it is human-gated.

### Cited Findings
- **RocketSmith** (Pak, Barkley, Loghmani, Baich, Pamal, Barati Farimani; CMU; arXiv 2606.00097, May–June 2026). An LLM orchestrator with subagents and skills generates parametric rocket parts and checks flight stability. Four high-power rockets were FDM-printed, manually checked for flight readiness, and flown. All four launched stably and two were recovered in reflyable condition. Altimeter apogee reached about 80% of the predicted value. The paper does not detail whether flight data drove a redesign. — [arXiv](https://arxiv.org/abs/2606.00097)
- **LLM-3D Print** (Jadhav, Pak, Barati Farimani; CMU; arXiv 2408.14307; Additive Manufacturing journal; CMU news Feb 2026). Cameras capture each layer, a VLM detects defects, and planner and executor agents change process parameters (temperature, flow) in real time. Printed parts showed a 5.06× increase in peak load capacity, and defect identification was comparable to 14 AM experts. It changes the print process, not the CAD. — [CMU news](https://engineering.cmu.edu/news-events/news/2026/02/06-ai-saves-3d-prints.html); [arXiv](https://arxiv.org/html/2408.14307v1); [PrinterChat site](https://sites.google.com/andrew.cmu.edu/printerchat)
- **AgentsCAD** (George, Keefe, Pak, Barati Farimani; CMU; arXiv 2607.02448, July 2026). The architecture combines:
  - a blackboard multi-agent design with Claude Sonnet 4.6 as the reasoner, using MCP tools `check_orientation_overhangs` and `lay_face_to_build_surface`;
  - a GPT-4o verifier in the style of CADCodeVerify, looking at 4 rendered views;
  - a GraphSAGE face classifier trained on MFCAD++ (85.0% accuracy, macro F1 0.785);
  - CadQuery/OCCT and FAISS RAG memory.

  Figure 1 shows FDM printing as the final step, but no printed parts or mechanical tests are reported, so validation is digital only. Without MCP grounding, the LLM hallucinated rotation angles and claimed parts were overhang-free. — [arXiv](https://arxiv.org/html/2607.02448v2)
- **MAMA BEAR** (Brown University / Keith Brown lab; Nature Communications 15, 2024, DOI 10.1038/s41467-024-48534-4). This self-driving lab uses FDM printers, a robot arm and mechanical test instruments. It ran more than 25,000 physical crush tests on generalized cylindrical shells, choosing them with Bayesian optimization over an 11-dimensional space, and reached a record 75.2% energy-absorbing efficiency (the prior best was about 71%). It is a genuinely closed physical loop, but it has no LLM and the geometry is parametric. One secondary source misreports the result as 73.3% and "BEAR". — [arXiv 2308.02315](https://arxiv.org/pdf/2308.02315); [BU](https://ar.bu.edu/2024/ai/self-driving-lab); [DOAJ](https://doaj.org/article/7aa34c5e75924b1c87cf1c9f33b16023)
- **Text2Robot** (Ringel, Charlick, Liu, Xia, Chen; Duke General Robotics Lab; arXiv 2406.19963; ICRA 2025). The pipeline is text-to-3D mesh, then geometric processing into a kinematic quadruped with 8 motors, then evolutionary and RL co-optimization of morphology and gait, then 3D printing and assembly. The authors claim a walking robot within a day. Revision happens in simulation; I found no physical-test-to-redesign loop. Code is at github.com/generalroboticslab/Text2Robot. — [arXiv](https://arxiv.org/html/2406.19963v1); [Duke Pratt](https://pratt.duke.edu/news/text2robot/)
- **print-in-the-loop** (github analogjedi). A human and a coding agent work from a YAML spec to build123d CAD. Automated checks cover valid solids, interference, gear-mesh clearance across phases, swept volumes and export round-trips, and seeded defects confirm the checks fire. Parts are sliced and printed on a Bambu P2S, and "measurements from the real parts go back into the specs."
  - **Built so far:** a printed BLDC motor stand (84 checks, about 2h16m and about 100 g PLA). A 27:1 compound gearbox and a 9:1 winch (365 checks) are CAD-complete but not yet printed.
  - **Rules:** printing requires human approval, and a failed check changes the geometry, never the threshold.
  - **Printer gotchas:** the Bambu Studio CLI does not resolve system presets, and the P2S needs the H2-style `project_file` LAN command.
  - **Missing:** no measured fit results have been published yet. — [GitHub](https://github.com/analogjedi/print-in-the-loop)
- **agentic-3d-design** (github eschmitt88, started 2026-06-16) uses build123d with a "verification ladder": OCCT validity → printability (overhang, thin wall, clearance) → rendered PNG review → CalculiX FEA, via a `cad-khana` Claude Code skill and/or `build123d-mcp`. The project has no printer and no results yet; its first trial, a snap-fit enclosure, is only a proposal. — [GitHub](https://github.com/eschmitt88/agentic-3d-design)
- **agentic-bioinspired-cad** (github crentb) is a local LLM-to-Blender loop with a vision critic, a print-certification gate, and FEA/phase-field fracture checks. No physical tests are reported. — [GitHub](https://github.com/crentb/agentic-bioinspired-cad)
- **LLM-Aided DFM redesign** (arXiv 2609.05559) uses multiple agents to redesign CAD for CNC machinability, compiling each edit and checking it against multi-view renders. It is a preliminary report with no fabrication. — [arXiv](https://arxiv.org/html/2609.05559v1)
- **Related non-LLM co-design with fabrication.** Soft-gripper co-design with neural physics (arXiv 2505.20404) and quality-diversity soft-gripper design (arXiv 2609.20087) both 3D-print their designs and test them on hardware. RobotDesignGPT (arXiv 2601.11801) is VLM-based robot design synthesis, but I did not confirm physical fabrication. — [2505.20404](https://arxiv.org/pdf/2505.20404); [2609.20087](https://arxiv.org/pdf/2609.20087); [2601.11801](https://arxiv.org/pdf/2601.11801)

### Inferences
- Barati Farimani's lab at CMU (LLM-3D Print, AgentsCAD, RocketSmith) is the clear academic center for linking LLM agents to additive manufacturing. Even so, it has not published an agent that revises geometry from physical measurements. RocketSmith comes closest (sim vs. flight comparison), and AgentsCAD's printing step is still aspirational.
- Self-driving labs prove the physical-test half works at scale (25,000 tests), but they search a fixed parametric space. Swapping the Bayesian optimizer for a code-CAD agent that can change topology is an obvious next step that nobody has published.

### Gaps
- I found no paper or project in which an LLM agent prints a part, measures it automatically (calipers, camera metrology or load cell), and autonomously updates the CAD. A targeted search did not surface one.
- I did not find out whether Text2Robot or RocketSmith ran a second design iteration based on physical results.
- I found no CNC-machining analogue of an agent-in-the-loop with physical test.

## Q3: Which feedback signals work, and known failure modes

### Takeaway
The evidence ranks the signals roughly as follows:
1. **Deterministic kernel/validity checks.** These are cheap and essential; they also stop the agent hallucinating success.
2. **FEA.** This is the strongest measured effect: target success went from 22% to 59%.
3. **Multi-view renders with VLM critique.** Gains are moderate, e.g. Box-IoU 0.44→0.59; BenchCAD found vision "at best a clarifier".
4. **Physical tests.** These are almost never used in LLM-CAD work.

The dominant failures are spatial reasoning (wrong load paths, disconnected parts, design-space violations), wrong coupling between parameters, multi-body joins and cuts, and assembly joint frames and mates. Tolerances and print fit are essentially untested.

### Cited Findings
- FEA feedback: target safety-factor success was 59.0% with FEA versus 22.2% without (p = 0.0008). — [Physics-in-the-Loop](https://arxiv.org/html/2605.19717)
- Render plus blueprint feedback: Box-IoU went from 0.444 to 0.592 (S2O) and from 0.397 to 0.505 (Fusion360). — [Son et al.](https://arxiv.org/abs/2605.17448)
- The VLM question-answer loop gave -7.3% point-cloud distance and +5% compile rate. — [CADCodeVerify](https://arxiv.org/abs/2410.05340)
- In BenchCAD's evaluation, "textual instruction does the heavy lifting; the visual signal is at best a clarifier." — [BenchCAD](https://arxiv.org/html/2605.10865v1)
- In AgentsCAD, the VLM verifier returned "inconclusive" on wall-thickness and cross-section checks because render resolution was low. Without deterministic MCP tools, the LLM hallucinated rotations and claimed parts were overhang-free. — [AgentsCAD](https://arxiv.org/html/2607.02448v2)
- In CADEngBench:
  - 58.1% of executable outputs violated at least one engineering or DFM requirement ("Executability substantially overstates...").
  - Edits involving joins, cuts or multiple bodies passed only 40.2–45.8% of the time, versus 247 of 248 for isolated feature insertions.
  - Some programs solve at every parameter value but couple a dimension to the wrong feature.
  - FEA failures came mostly from meshing and boundary conditions (573 of 1,809 attempts), not from the solver.
  - For assembly, the correct entity pair was ranked first in 542 of 960 cases, but only 143 also had the right joint family and order. Pin-slot joints scored 0%.
  - Tolerances were not evaluated. — [CADEngBench](https://arxiv.org/html/2608.09296v1)
- Physics-in-the-Loop: the most common failures were design-space violations and disconnected parts, which the authors attribute to limited spatial reasoning about load paths. Large models over-complicated geometry so it would not mesh, and models over-engineered, clustering near the top of the safety-factor range. — [Physics-in-the-Loop](https://arxiv.org/html/2605.19717)
- Text2CAD-Bench: CD and IoU computed only over executed samples inflate quality when the invalid rate rises. — [Text2CAD-Bench](https://arxiv.org/html/2605.18430v1)
- print-in-the-loop: "Software tolerances only show the model is self-consistent. Print fit is established only by measuring printed parts." The vendor's servo STEP file contained an invalid solid. — [GitHub](https://github.com/analogjedi/print-in-the-loop)
- The overhang rule of thumb is that downward-facing surfaces more than about 45° from vertical need supports or a geometry change. AgentsCAD's own text is inconsistent about the threshold it actually uses. — [AgentsCAD](https://arxiv.org/html/2607.02448v2)

### Inferences
- Feedback signals should be deterministic and numeric wherever possible. VLM review is best treated as a coarse sanity check, not a measurement.
- Because FEA failures come mostly from meshing, a robust meshing and boundary-condition layer is itself a valuable piece of the harness.

### Gaps
- I found no quantitative study of printed-part dimensional accuracy against agent-specified dimensions, or of how tolerance/clearance choices by LLMs fare once printed.

## Q4: The open, unclaimed gap a solo developer could fill

### Takeaway
The unclaimed niche is a **fully autonomous physical loop**: a code-CAD agent with the verification stack above, a networked printer, automated measurement (camera metrology and/or a load-cell or fixture test), and CAD revision driven by the measured error (clearance fit, deflection or failure load). All the pieces exist publicly, but nobody has published them integrated end to end.

### Cited Findings
- None of the 2026 benchmarks includes physical fabrication. CADEngBench's checks are simulated FEA and PyBullet, and its DFM checks are geometric screens that are "not manufacturing certification." — [CADEngBench](https://arxiv.org/html/2608.09296v1)
- Son et al. and Physics-in-the-Loop validate with simulation only. — [2605.17448](https://arxiv.org/abs/2605.17448); [2605.19717](https://arxiv.org/html/2605.19717)
- Existing open-source attempts stop short of autonomy. print-in-the-loop is human-gated and has no printer-control tool. agentic-3d-design has no printer yet. — [print-in-the-loop](https://github.com/analogjedi/print-in-the-loop); [agentic-3d-design](https://github.com/eschmitt88/agentic-3d-design)
- MAMA BEAR shows that automated print, test and iterate works at scale, but with Bayesian optimization over a fixed parametric shell, not an agent writing CAD. — [BU](https://ar.bu.edu/2024/ai/self-driving-lab)
- LLM-3D Print shows that camera plus VLM monitoring of prints and load testing work, but it only changes process parameters. — [CMU](https://engineering.cmu.edu/news-events/news/2026/02/06-ai-saves-3d-prints.html)

### Inferences
- **Concrete gap 1: a tolerance and fit self-calibration loop.** The agent designs mating parts (snap-fit, press-fit, a gear pair). The printer prints a coupon set, a camera or test fixture measures fit, and the agent updates its clearance model and the CAD. No published work quantifies LLM fit and tolerance accuracy after printing.
- **Concrete gap 2: an agent-driven version of MAMA BEAR.** Replace the Bayesian optimizer with a code-CAD agent that can change topology, keep FEA as the pre-screen, and add a cheap load-cell crush or bend rig, then compare sample efficiency against Bayesian optimization.
- **Concrete gap 3: a "physical benchmark".** Publish the first benchmark in which tasks are scored on printed and measured parts, not on renders or FEA. Every 2026 benchmark leaves this open.
- **Feasibility for a solo developer.** The software stack is open: build123d or CadQuery, CalculiX or torch-fem, the build123d-mcp server, and LAN control of Bambu printers (with the documented P2S `project_file` gotchas). Hardware is a printer, a USB camera and an HX711 load cell. The agent loop can follow the Physics-in-the-Loop / Son et al. pattern.

### Gaps
- I cannot rule out unpublished or industrial work (e.g., Zoo/KittyCAD, Autodesk, or startups) doing autonomous print-measure-revise; I did not search the commercial landscape within this budget.
