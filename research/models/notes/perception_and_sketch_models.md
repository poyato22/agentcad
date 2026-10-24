# Perception and sketch models for Mises (as of 2026-10-24)

Scope: models that let Mises agents *see and check* CAD geometry (design §7.2 fidelity check, §7.4 `render_views` / `measure` / `section` / `interference`) and power **Sketch to part** (§7.9: strokes or photo → constrained 2D sketch with dimensions → Onshape part, ≤3 questions). Design anti-goals that matter here: no "checks they didn't run", evidence-bound badges, cheap free tier, commercial product.

Research budget note: about 15 searches and fetches. Numbers are the authors' own unless marked otherwise. Most 2026 items are arXiv preprints that have not been checked for peer review.

---

## 1. How good are frontier VLMs at 3D/CAD spatial reasoning (2025–2026)?

### Takeaway
Frontier VLMs reach only about 50–60% on visual QA over CAD renders, and on BenchCAD o3 scored below a blank-image baseline. Giving the same models the CAD **code** raises scores to about 82–84%. They are fairly good at reading dimension text on 2D drawings (85–88%) but weak on projection and multi-view consistency (54–62%). Counting and spatial localization are known, vendor-acknowledged weak spots. For Mises, a picture alone is not a reliable check. Numbers from the geometry kernel have to be the source of truth.

### Cited Findings
- **BenchCAD (arXiv 2605.10865, 11 May 2026).** 17,900 CadQuery parts and a 2,400-question QA bank. The questions are numeric (ratios, integer counts, ordinals), e.g. the number of gear teeth or the number of chamfers. Image-QA totals: Gemini 3.1 Pro 0.587, Claude Opus 4.7 (thinking) 0.530, Claude Opus 4.7 0.526, GPT-5.3 (thinking) 0.514, GPT-4o 0.464, OpenAI o3 0.327. The **blank-image baseline is 0.375**. — [BenchCAD](https://arxiv.org/html/2605.10865v1)
- BenchCAD, same questions but with the CAD code supplied: top models reach about 0.82–0.84 (Gemini 3.1 Pro thinking 0.838, Claude Opus 4.7 thinking 0.829, GPT-5.3 chat 0.823), against a blank baseline of 0.223. The authors conclude that **visual recognition is the main bottleneck, and spatial reasoning stays weak in both modalities**. — [BenchCAD](https://arxiv.org/html/2605.10865v1)
- BenchCAD failure modes: missed fine detail (threads, chamfers), wrong workplane or extrude direction (XY vs XZ), dropped operations (twist-extrude), standard parameters flattened (DIN 2095 spring end-coil pitch), and sweeps or lofts replaced with simple sketch-and-extrude. Data is CC-BY-4.0 on Hugging Face; eval code is MIT. — [BenchCAD](https://arxiv.org/html/2605.10865v1)
- **MechVQA (arXiv 2605.30794, 29 May 2026).** A benchmark on 2D mechanical drawings. Scores below are Identification & Counting / Dimension & Annotation / Projection & Multi-view / Total, in %:
  - Gemini-3-Pro-Preview: 76.7 / 87.7 / 58 / 77.3
  - GPT-5: 69.8 / 85.0 / 60 / 75.4
  - Claude-Sonnet-4.5: 67.4 / 78.7 / 54 / 71.2
  - GPT-4o: 62.8 / 75.9 / 44 / 63.1
  - Qwen3-VL-32B: 86.1 / 86.7 / 48 / 76.5
  - GLM-4.6V: 88.4 / 86.7 / 62 / 78.9
  - Qwen3-VL-30B-A3B: 74.4 / 78.0 / 40 / 64.5
  - Qwen3-VL-4B: 63 / 77 / 20 / 60.2

  The authors fine-tuned MechVL-4B-RL (from Qwen3-VL-4B) to reach 88.4 / 90.7 / 64 / 84.85. — [MechVQA](https://arxiv.org/html/2605.30794v1)
- MechVQA failure modes: decisive cues missed in dense, symbol-cluttered drawings; weak priors on projection conventions and tolerance standards; multi-view inconsistency; and **"answers that look numerically plausible but violate geometric constraints."** The paper gives no repo, dataset license or weight license. — [MechVQA](https://arxiv.org/html/2605.30794v1)
- **HiCAD (arXiv 2610.15681, 11 Oct 2026).** Hand-drawn sketch → CadQuery, IoU on synthetic sketches: GPT-4o 20.5%, Gemini-2.5 23.7%, Claude-3.7 19.5%, against fine-tuned specialists cadrille 66.5% and HiCAD 69.5%. General VLMs "produce geometries that break basic topology." The general-VLM baselines are older (2025) models. — [HiCAD](https://arxiv.org/html/2610.15681)
- **AECV-Bench** (architectural and engineering drawings): spatial reasoning is "moderate," and symbol-centric understanding, especially reliable counting of doors and windows, "remains unsolved." — [AECV-Bench (ResearchGate)](https://www.researchgate.net/publication/399595749_AECV-Bench_Benchmarking_Multimodal_Models_on_Architectural_and_Engineering_Drawings_Understanding)
- Other relevant 2025–2026 benchmarks, read at search-snippet level only:
  - **OmniCAD** (arXiv 2608.22637): 25k robotics assemblies, about 12 parts each, 21 mate types; tasks are pose estimation and mate identification. License CC BY-NC-SA 4.0. The body text could not be read, so no results are given here. — [OmniCAD](https://arxiv.org/pdf/2608.22637)
  - **MultiView-Bench** (arXiv 2607.08970). — [MultiView-Bench](https://arxiv.org/pdf/2607.08970)
  - **ViewSpatial-Bench** (arXiv 2505.21500). — [ViewSpatial-Bench](https://arxiv.org/html/2505.21500v2)
  - **DrawingVQA** (arXiv 2607.15418, construction drawings). — [DrawingVQA](https://arxiv.org/html/2607.15418v1)
- **Anthropic's vision docs** (current, Claude Opus 5.5 era) list these limitations:
  - "Spatial reasoning: Claude's coordinate and localization outputs are approximate… verify outputs before relying on them."
  - "Counting: Claude can give approximate counts… might not always be precisely accurate, especially with large numbers of small objects."
  - Images that are low quality, rotated or under 200 px cause errors.
  - "Do not use Claude for tasks requiring perfect precision… without human oversight."

  — [Claude vision docs](https://platform.claude.com/docs/en/build-with-claude/vision)

### Inferences
- **Hole counting and dimension checks should come from OCCT or build123d topology queries, not from a VLM.** The evidence: Anthropic's own counting caveat, BenchCAD counts at around 0.5, and o3 below the blank baseline. A VLM is useful as a "does this look like the intent?" reviewer and to spot gross errors (wrong extrude direction, missing feature). That role fits the design's "unverified claim" badge, not a "verified" one.
- Interference detection by vision has no benchmark support. Keep it with OCCT boolean and distance checks, as §7.2 already says.
- The code-vs-image gap in BenchCAD (+25–30 points) suggests passing the agent the build123d script and the feature list *alongside* renders. It is the single cheapest boost.
- Open models (Qwen3-VL-32B, GLM-4.6V) match or beat closed models on 2D-drawing QA in MechVQA, so local or cheap reviewers are viable for drawing reading.

### Gaps
- No benchmark found that tests VLMs specifically on **interference/clearance detection** from renders, or on **hole counting in 3D renders** with per-model numbers.
- No published CAD-render results yet for the newest models (Claude Opus 5.x, GPT-5.5+, Gemini 3.5) as of this search. BenchCAD's Opus 4.7, GPT-5.3 and Gemini 3.1 Pro are the latest found.
- OmniCAD results could not be extracted (the PDF body was unreadable).
- Not checked: OpenAI and Google vision docs for their stated limitations.

---

## 2. Best practices for feeding geometry to a VLM, and do render-and-critique loops help?

### Takeaway
Loops help, but the gains are modest. CADCodeVerify, the best-established result (ICLR 2025), reports about 7% lower point-cloud distance and about 5% higher success with GPT-4. The 2026 preprints commonly use 3 standard views (top, front, iso), sometimes fused with point clouds, and use VLM critique only in early rounds to save tokens. Geometric or FEA verifiers give stronger signals than VLM judgement.

### Cited Findings
- **CADCodeVerify (Alrashedy et al., ICLR 2025, arXiv 2410.05340).**
  - Loop: the VLM generates CadQuery, renders it, writes yes/no verification questions about the object, answers them from the renders, and then writes feedback to fix the code. No human and no geometric solver.
  - Result with GPT-4: −7.30% point-cloud distance and +5.0% success rate. The ICLR text reports a 5.5% gain in compile rate instead, so the figure varies by version.
  - Released the CADPrompt benchmark (200 objects with expert code) at github.com/Kamel773/CAD_Code_Generation.

  — [arXiv](https://arxiv.org/pdf/2410.05340); [ICLR proceedings](https://proceedings.iclr.cc/paper_files/paper/2025/file/81a934cd364e18ea6fdeaf57a93c17d4-Paper-Conference.pdf)
- **"Foundation Models for Automatic CAD Generation"** (arXiv 2607.05573): a Qwen2.5-VL-72B critic scores **three rendered views (top, isometric, front)** and returns a semantic-match score plus a text critique. VLM critique runs only on early refinement rounds, to limit tokens. — [arXiv 2607.05573](https://arxiv.org/pdf/2607.05573) (snippet level)
- **CADReasoner** (arXiv 2603.29847): a closed-loop CadQuery program editor for reverse engineering that feeds the rendered mesh back each step and **fuses multi-view renders with point clouds**. — [CADReasoner](https://arxiv.org/html/2603.29847) (snippet level)
- **CADDesigner** (arXiv 2508.01031): a general-purpose agent that analyses multi-view images and sends explicit feedback to code generation when the check fails. — [CADDesigner](https://arxiv.org/pdf/2508.01031) (snippet level)
- **Ortho2CAD** (arXiv 2607.08891): orthographic drawings → CAD with two feedback-and-regenerate loops. A final VLM call compares each candidate's rendered projections against the input drawing and picks the best one. This is directly relevant to "photo of drawing → part." — [Ortho2CAD](https://arxiv.org/html/2607.08891v1) (snippet level)
- **FEA as feedback** (arXiv 2605.17448): "Self-Improving CAD Generation Agents with Finite Element Analysis as Feedback." — [arXiv 2605.17448](https://arxiv.org/pdf/2605.17448) (title and snippet only)
- A 2026 survey stresses that **compile success is necessary but weaker than verified geometric validity, constraint satisfaction and editability.** — [Beyond NL2Code survey](https://arxiv.org/pdf/2606.15932)
- **Claude input mechanics** (relevant to `render_views`):
  - Images cost ⌈w/28⌉×⌈h/28⌉ visual tokens.
  - Claude 4.7+ models run in a high-resolution tier: long edge up to 2,576 px and 4,784 tokens per image. Other models: 1,568 px.
  - Up to 600 images per request (100 for models with a 200k context). Requests with more than 20 images get a stricter per-image size cap; resizing to ≤2000 px avoids it.
  - Put images before the text and label them "Image 1:", "Image 2:".
  - Use the Files API (`file_id`) in agent loops so images aren't resent as base64 every turn.
  - Example price: a 1000×1000 render costs about $5.18 per 1,000 images on Opus 5.5.

  — [Claude vision docs](https://platform.claude.com/docs/en/build-with-claude/vision)

### Inferences
- **Recommended `render_views` bundle** (synthesis, not a tested recipe):
  - 3–6 labelled orthographic plus iso views at about 1000–1500 px, image-first, flat shading with black edge lines.
  - Section views through features of interest.
  - Optionally, a colour-coded overlay that labels faces and holes with IDs matching the `measure` output, so the VLM can refer to the IDs instead of guessing coordinates.
  - The text payload `{mass, volume, bbox, hole list with diameters and positions, feature list, script}`.
- The BenchCAD code-vs-image gap backs this up: structured text carries most of the signal.
- Use the VLM as a *critic of intent match*, given the user's sketch or prompt next to the renders, in early iterations only. Treat its verdict as advisory, and gate "fits" or "verified" badges on kernel checks.
- A local critic (Qwen-VL class) could take the place of the CADCodeVerify-style question loop at zero API cost, keeping the free tier cheap.

### Gaps
- No found study that isolates the value of **depth or normal maps** or **dimension-callout overlays** for VLM CAD critique. These remain plausible but unproven.
- Per-paper effect sizes for CADReasoner, Ortho2CAD and the 2607.05573 critic were not extracted.

---

## 3. 3D-native encoders and LLMs (B-rep, point cloud)

### Takeaway
Active research with 2026 preprints exists: BrepCoder and BrepLLM read B-rep, BRepCLIP is a contrastive B-rep encoder, and CAD-Recode and cadrille go from point cloud (+ views) to CadQuery. These are research checkpoints trained on DeepCAD-style data, not products. For Mises, they are better treated as optional reverse-engineering or embedding components than as the agent's main eyes. Mises already has exact geometry via OCCT, and the B-rep can be queried exactly.

### Cited Findings
- **BrepCoder** (arXiv 2602.22284, 2026): a multimodal LLM that takes B-rep as its core input and aligns it with Python-like CAD code through two-stage training, for multi-task B-rep reasoning. — [BrepCoder](https://arxiv.org/html/2602.22284)
- **BRepCLIP** (arXiv 2606.05515): contrastive multimodal pretraining on B-rep primitives for CAD understanding. — [BRepCLIP](https://arxiv.org/pdf/2606.05515)
- **CAD-Recode** (ICCV 2025): point cloud → CadQuery code using a pre-trained LLM. Its CadQuery corpus was later reused by HiCAD. — [CAD-Recode](https://openaccess.thecvf.com/content/ICCV2025/papers/Rukhovich_CAD-Recode_Reverse_Engineering_CAD_Code_from_Point_Clouds_ICCV_2025_paper.pdf); [HiCAD](https://arxiv.org/html/2610.15681)
- **cadrille** (ICLR 2026, arXiv 2505.22914): one VLM framework taking point clouds, multi-view images and text → CAD. On real human sketches it fell to 17.7% IoU with an 87% invalid rate, against 66.5% on synthetic sketches (as measured by the HiCAD authors). — [cadrille](https://arxiv.org/pdf/2505.22914); [HiCAD](https://arxiv.org/html/2610.15681)
- **GenCAD-3D** (arXiv 2509.15246), **FutureCAD / B-rep grounding** (arXiv 2603.11831) and **NURBGen** (arXiv 2511.06194) are generation-side relatives. — [GenCAD-3D](https://arxiv.org/pdf/2509.15246); [2603.11831](https://arxiv.org/html/2603.11831v2); [NURBGen](https://arxiv.org/pdf/2511.06194)
- **ShapeLLM** is described as "the first 3D multimodal LLM designed for embodied interaction" on point clouds. **PointLLM** (ECCV 2024) is a point-cloud LLM. Neither is CAD- or B-rep-specific. — [search result citing ShapeLLM and PointLLM](https://arxiv.org/pdf/2505.22914)
- **MiCADangelo** (arXiv 2510.23429): reconstructs *constrained* CAD models from 3D scans and claims to be the first CAD reverse-engineering method that generates parametric constraints. — [MiCADangelo](https://arxiv.org/html/2510.23429v1)

### Inferences
- In Mises the agent already has the exact B-rep through build123d/OCCT. Text-serialised topology (faces, holes, axes, distances from `measure`) is more reliable than a learned embedding, and works with any agent over MCP. 3D-native LLMs add the most value where the geometry *isn't* known: scan or photo reverse engineering, and similarity search over parts.
- UV-Net and BRepNet (2021-era, Autodesk-affiliated) are classic B-rep encoders for face segmentation and classification. They would need fine-tuning for any Mises task. They are not covered by sources fetched in this run (see Gaps).

### Gaps
- Licenses and weight availability for BrepCoder, BRepCLIP, CAD-Recode, cadrille, ShapeLLM, PointLLM, UV-Net and BRepNet were **not verified** in this run. Several CAD datasets (DeepCAD, Fusion 360 Gallery, ABC) carry their own terms, which may restrict commercial use of trained weights. Check them before shipping.
- No benchmarks found that compare 3D-native LLMs with frontier VLMs on the same CAD QA.

---

## 4. Sketch and drawing understanding

### Takeaway
- **Freehand → constrained 2D sketch:** a small body of research (PICASSO, ViT primitive and constraint parsing, Vitruvion, PPI-Net). None of it is product-grade, and accuracy drops sharply on real (vs synthetic) sketches.
- **Freehand → 3D CAD code:** the best 2026 result, HiCAD (fine-tuned Qwen3-VL-4B), reaches only 43% IoU on real human sketches.
- **Engineering-drawing parsing** is more mature. Hybrid detector, OCR and VLM pipelines reach about 0.96 F1 on dimensions and GD&T. eDOCr2 is MIT-licensed. Werk24 is a paid API with vendor-reported 98% accuracy on price-driving features.

For Mises §7.9, the realistic pipeline: (a) vectorise strokes, or detect lines and arcs in the photo, with classical CV or a primitive-inference model; (b) read the handwritten numbers with a frontier or local VLM over crops; (c) solve constraints with Onshape's or a local sketch solver; (d) ask about the lowest-confidence items (the ≤3 questions).

### Cited Findings
- **Primitive and constraint inference from hand-drawn CAD sketches:**
  - arXiv 2407.00410 (ViT): parses primitives and constraints together as a set-prediction problem.
  - **PICASSO** (arXiv 2407.13394, 2024): feed-forward parameterisation of hand-drawn and precise sketch images, trained with rendering self-supervision.
  - **PPI-Net** (arXiv 2308.01521): primitives only; constraints are added by hand in CAD.
  - **Vitruvion** (ICLR 2022): generates primitives, then constraints, conditioned on hand-drawn images, matching the constraint-graph form used in CAD software.

  — [2407.00410](https://arxiv.org/html/2407.00410); [PICASSO](https://arxiv.org/abs/2407.13394); [PPI-Net](https://arxiv.org/pdf/2308.01521); [Vitruvion](https://arxiv.org/pdf/2109.14124)
- SketchGraphs can be used to build paired hand-drawn and clean sketch images for training. — [GDL for CAD survey](https://arxiv.org/pdf/2402.17695)
- **HiCAD** (Fudan, 11 Oct 2026):
  - Inputs: hand-drawn sketch, text, or both. Output: CadQuery. Built on Qwen3-VL-4B with SFT plus GSPO RL.
  - Synthetic sketches: 69.5% IoU with a 0.37% invalid rate. **Real volunteer sketches: 43.3% IoU, 3% invalid.**
  - The synthetic sketches omit hidden lines. Failures: complex structures and cluttered or non-standard perspectives.
  - Project page zqwlearning.github.io/HiCAD; no code or weight license stated.

  — [HiCAD](https://arxiv.org/html/2610.15681)
- A 2026 journal paper, "Hand-drawn sketch to parametric 3D CAD using a multimodal LLM and an intermediate representation" (Int. J. Adv. Manuf. Technol.), exists. The content is paywalled and was not read. — [Springer](https://link.springer.com/article/10.1007/s00170-026-19035-z)
- **eDOCr2** (Machines 2025, 13(3):254):
  - Combines traditional OCR and image processing to segment mechanical drawings into info blocks, dimensions and feature control frames, with **93.75% text recall**.
  - Adds VLMs (Qwen2-VL-7B, GPT-4o) after segmentation.
  - Code at github.com/javvi51/edocr2, **MIT license**.

  — [MDPI](https://www.mdpi.com/2075-1702/13/3/254); [GitHub](https://github.com/javvi51/edocr2)
- **Hybrid multi-view drawing interpretation** (arXiv 2510.21862, Oct 2025): YOLO layout and annotation detectors plus two Donut OCR-free VLMs. The numerical (dimension/GD&T) VLM reaches **F1 0.963**; the alphabetical (title block/notes) VLM reaches F1 0.672. Output is unified JSON. — [arXiv 2510.21862](https://arxiv.org/pdf/2510.21862)
- A fine-tuned VLM for GD&T extraction (arXiv 2411.03707) argues that Tesseract-style OCR fails on composite tolerances, modifiers and rotated text. — [arXiv 2411.03707](https://arxiv.org/pdf/2411.03707)
- **Werk24** (commercial API):
  - Reads 2D mechanical drawings, "including acceptable scans and hand-drawn engineering drawings," and returns JSON with field-level confidence.
  - Pricing is a tier fee plus per page, from **€490/month (Essential, 500 pages)**.
  - Customer Laserhub reports **98% accuracy on price-driving features**. This is a customer case study, not an independent test.

  — [Werk24](https://werk24.io/); [Laserhub case study](https://werk24.io/case-studies/laserhub)
- **Ortho2CAD** (2607.08891): VLM pipeline from orthographic drawings to 3D CAD, with render-compare selection. — [Ortho2CAD](https://arxiv.org/html/2607.08891v1)
- On 2D drawings, the best general VLMs read dimensions and annotations at 85–88% (Gemini-3-Pro 87.7, GPT-5 85.0, Qwen3-VL-32B 86.7), but projection reasoning is 48–62%. — [MechVQA](https://arxiv.org/html/2605.30794v1)

### Inferences
- **Sketch to part should not be end-to-end.** End-to-end sketch→CAD tops out at about 43% IoU on real sketches, which is too low for "constrained part with ≤3 questions." Use a staged pipeline:
  - Geometry from strokes (pen strokes are already vectors, which is much easier than photos).
  - Number reading by VLM on cropped regions, where 85%+ dimension reading is attainable.
  - Constraint inference by rules (snap to horizontal, vertical, tangent, equal, concentric) plus a solver.
  - The VLM proposes *intent* (which number belongs to which line). The solver checks consistency, and inconsistencies become the clarifying questions.
- For photos of paper sketches, perspective-correct and binarise before the VLM, and send crops at high resolution. Claude's docs warn about rotated or small images.
- Werk24 targets formal drawings for quoting. At €490/month it conflicts with the cheap free tier, so at most it could be an optional paid-tier import. eDOCr2 (MIT) is the commercially safe open option for formal drawings.

### Gaps
- **Sketch2CAD (2020) and Free2CAD (SIGGRAPH 2022)** were not fetched this run, so their accuracy, code availability and licenses are unverified. Both are 3D sketch-based modelling research from the same academic group, and neither outputs Onshape-style constrained 2D sketches.
- No benchmark found for **handwritten dimension digits on photographed paper sketches** specifically. The closest data points are drawing-OCR F1 and MechVQA DA.
- PICASSO and 2407.00410 accuracy figures, code and licenses were not extracted.
- No independent Werk24 accuracy evaluation was found.

---

## 5. Local on an M5 Pro / 24 GB vs cloud; licenses

### Takeaway
On 24 GB, the practical local tier is small-to-mid VLMs (Qwen3-VL 4B or 8B class, MechVL-style 4B fine-tunes) and MoE models like Qwen3-VL-30B-A3B at 4-bit, which is tight. Those MoE models are Apache-2.0 on community MLX builds. Frontier-level CAD critique (Gemini 3.x, Claude Opus, GPT-5.x) needs cloud APIs. Notably, Qwen3-VL-32B and GLM-4.6V match closed models on MechVQA, but 32B dense at 4-bit is about 18–20 GB, which is borderline on 24 GB.

### Cited Findings
- An LM Studio community 4-bit MLX build of **Qwen3-VL-30B-A3B-Instruct** exists, is tagged **apache-2.0** and is optimised for Apple Silicon. — [Hugging Face](https://huggingface.co/lmstudio-community/Qwen3-VL-30B-A3B-Instruct-MLX-4bit/blob/main/README.md)
- A third-party guide recommends at least 32 GB unified memory for Qwen3-VL-30B-A3B, with 16 GB only under aggressive quantisation. It reports about 68 tok/s on an M4 Max at 4-bit. Secondary and unverified. — [Codersera](https://codersera.com/blog/run-qwen3-vl-30b-a3b-thinking-on-macos-installation-guide)
- MechVQA scores of small models that fit locally: Qwen3-VL-4B 60.2 total, MiniCPM-V-4.5 60.5, MiMo-VL-7B 59.7. Fine-tuned **MechVL-4B-RL scores 84.85**, beating every closed model tested. MechVL weights and license were not stated. — [MechVQA](https://arxiv.org/html/2605.30794v1)
- HiCAD (Qwen3-VL-4B base) would fit locally, but has no stated license. — [HiCAD](https://arxiv.org/html/2610.15681)
- Commercially usable components found: eDOCr2 (MIT); BenchCAD data (CC-BY-4.0) and eval code (MIT); Qwen3-VL-30B-A3B MLX build (Apache-2.0). — [eDOCr2](https://github.com/javvi51/edocr2); [BenchCAD](https://arxiv.org/html/2605.10865v1); [HF](https://huggingface.co/lmstudio-community/Qwen3-VL-30B-A3B-Instruct-MLX-4bit/blob/main/README.md)
- **Not commercially safe:** OmniCAD is CC BY-NC-SA 4.0. — [OmniCAD](https://arxiv.org/pdf/2608.22637)
- Cloud vision: Claude's per-image cost formula and limits are given in §2. Claude 4.7+ get the high-res tier automatically. Anthropic says it does not train on uploaded images. — [Claude vision docs](https://platform.claude.com/docs/en/build-with-claude/vision)

### Inferences
- **Free tier:** a local Qwen3-VL-8B or 4B-class critic, plus kernel-derived numbers, plus eDOCr2 for drawings. Zero marginal cost, in line with the "free tier must not cost us money" anti-goal.
- **BYO-key or paid tier:** the user's frontier model (Gemini 3.x currently leads image-CAD QA; Claude Opus 4.7 is close) for intent critique and sketch reading.
- Running a local VLM at the same time as CalculiX, gmsh, the OCCT mirror and the UI on 24 GB will compete for memory. Plan for a 4–8B VLM, not 30B.
- A MechVL-style RL fine-tune of a 4B model on Mises' own sketches is the highest-leverage local option, given that 4B fine-tunes beat frontier models on MechVQA. That needs a training dataset Mises doesn't yet have.

### Gaps
- Not verified in this run:
  - The official Qwen3-VL-8B or 4B license and the availability of MLX VL builds.
  - GLM-4.6V's license.
  - Memory figures on an M5 Pro specifically.
  - llama.cpp multimodal support status for Qwen3-VL.
- Licenses for UV-Net, BRepNet, PointLLM, ShapeLLM, CAD-Recode and cadrille were not verified, nor training-data terms (DeepCAD, ABC, Fusion 360 Gallery) that may limit commercial use of derived weights.
