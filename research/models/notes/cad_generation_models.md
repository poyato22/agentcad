# Generative models for editable, parametric CAD (as of 2026-10-24), judged for fit with Mises

Scope: models and services that output **parametric CAD** (code or feature sequences) rather than meshes, judged on whether they can feed the Mises loop. In that loop an agent edits Onshape features and Variable Studio variables on its own branch, and it iterates on a local build123d/OpenCascade mirror (design doc §4, §6.1, §7.2, §7.9). Licenses matter because Mises is commercial.

License legend: **OW-commercial** = open weights, commercial use allowed · **OW-NC** = open weights, non-commercial only · **API** = hosted only · **Vendor-native** = inside a CAD product.

Method note: this was about 18 tool calls. I fetched license files and Hugging Face metadata directly with `curl` from raw GitHub and the HF API; those are marked "(verified LICENSE / HF API)". Benchmark numbers come from the papers' own tables, fetched this session, unless a line says otherwise.

---

## 1. CAD-as-code models (CadQuery / build123d / OpenSCAD / FeatureScript / KCL / feature JSON)

### Takeaway
The open CAD-code models with the best published numbers for image or point-cloud input write **CadQuery**: CAD-Recode, cadrille and CAD-Coder. The strongest ones are licensed **CC BY-NC 4.0**, so Mises cannot ship them. The only commercially usable open checkpoints I found are a 7B text-to-CadQuery CAD-Coder (Apache-2.0) and CADFusion (MIT code and weights, but it outputs a DeepCAD-style sequence and is weak on newer benchmarks). For hosted services, Zoo's Text-to-CAD/Zookeeper outputs editable KCL (its own language, not Onshape). Onshape's own AI path is the **FeatureScript MCP Server** (Aug 2026), which brings your own LLM to write custom features rather than whole parts.

### Cited Findings

**Inventory table**

| Model / service | Date | Input → output | Weights / license | Size / base | Headline result |
|---|---|---|---|---|---|
| CAD-Recode (Univ. Luxembourg + Artec3D, ICCV 2025) | arXiv Dec 2024; v1.5 Mar 2025 | point cloud → CadQuery Python | **OW-NC**: repo LICENSE.md is CC BY-NC 4.0; HF model `filapro/cad-recode-v1.5` and dataset tagged `cc-by-nc-4.0` (verified LICENSE / HF API) | Qwen2-1.5B + one extra linear layer | DeepCAD: mean Chamfer distance (CD) 3.43→0.30 vs CAD-SIGNet; IoU 77.6%→92.0% (secondary summary). Trained on 1M synthetic CadQuery sequences |
| cadrille (multimodal follow-up from the same group) | HF model updated 2025-06-02 | point cloud / images / text → CadQuery | **OW-NC**: HF `maksimko123/cadrille` tagged `cc-by-nc-4.0` (verified HF API) | n/a | Numbers not fetched (gap) |
| CAD-Coder, text version (`gudo7208/CAD-Coder`, NeurIPS 2025 "Text-to-CAD Generation with Chain-of-Thought and Geometric Reward") | arXiv 2505.19713; HF updated 2026-01-09 | text → CadQuery | **OW-commercial**: Apache-2.0 (verified HF API) | Qwen2.5-7B-Instruct fine-tune; 110K text–CadQuery–3D triplets + 1.5K CoT samples | DeepCAD L3 split: median CD 0.17, mean 6.54, invalidity ratio (IR) 1.45%, as reported by CAD-Judge |
| CAD-Coder, image version (CADCODER org, GenCAD-Code dataset) | arXiv 2505.14646; HF 2025-07 | image → CadQuery | HF model and dataset have **no license tag** (verified HF API), so treat as unknown / not usable | VLM; GenCAD-Code has >163k image–code pairs | Claims 100% valid syntax and beats GPT-4.5 and Qwen2.5-VL-72B on 3D solid similarity |
| Text-to-CadQuery | arXiv 2505.06507 (May 2025) | text → CadQuery | not checked | fine-tunes on 170K CadQuery annotations | top-1 exact match 58.8%→69.3% |
| CADFusion (Microsoft, ICML 2025) | 2025 | text → DeepCAD-style sketch/extrude sequence, refined with visual feedback | **OW-commercial**: GitHub LICENSE MIT; HF `microsoft/CADFusion` tagged MIT (verified) | LLM fine-tune | Text2CAD-Bench L1: CD 229.62, IR 60%, IoU 0.03, i.e. poor against frontier LLMs |
| Text2CAD (DFKI, NeurIPS 2024) | arXiv Sept 2024 | text (4 skill levels) → DeepCAD sequence | **OW-NC / share-alike**: repo "Text2CAD Community License", CC BY-NC-SA 4.0; HF dataset `cc-by-nc-sa-4.0` (verified) | transformer | DeepCAD L3: median CD 0.37, IR 2.38% (via CAD-Judge); CAD-Coder's summary gives IR 3.75%, a conflict likely from a different eval setup. Text2CAD-Bench L1: CD 219.57, IoU 0.08 |
| CAD-MLLM | arXiv 2411.04954 (Nov 2024) | text / image / point cloud → command sequence | repo LICENSE not found (404) → unknown | MLLM | Abstract claims it beats prior conditional methods; no numbers retrieved |
| CADCodeVerify + CADPrompt (ICLR 2025) | arXiv 2410.05340 (Oct 2024) | text → CadQuery, with a VLM self-verify loop over rendered images | code/data at GitHub `Kamel773/CAD_Code_Generation` (license not checked) | method around GPT-4 / other VLMs | CADPrompt = 200 prompts with expert CadQuery. On GPT-4: point-cloud distance −7.30%, compile rate +5.5% (earlier versions said +5.0%) |
| **Zoo Text-to-CAD / Zookeeper** (formerly KittyCAD; "ML-ephant" API) | Text-to-CAD launched ~2023–24; Zookeeper agent current | text (and conversation) → **KCL** (Zoo's own CAD language) plus STEP/STL export | **API-only**, closed model; Design Studio app is open source but the geometry engine is not; text-to-cad-ui repo is MIT | n/a | No public benchmark. FAQ: "Every model remains fully editable through familiar sketch and feature-tree workflows, with human-readable KCL as its source of truth." STEP needs a separate modeling/export step |
| **Onshape FeatureScript MCP Server** (PTC, Onshape Labs) | announced 2026-08-13 | NL → FeatureScript custom features via *your* LLM (Claude, ChatGPT/Copilot, Gemini, Perplexity) | **Vendor-native, BYO-LLM**; via Onshape App Store link; plan or pricing not stated | n/a | PTC says AI "build[s], test[s], debug[s], and refine[s]" the feature; resulting features are parametric, configurable, deterministic and use no tokens once built. Vendor claims only |
| Onshape AI Advisor | Apr 2025; embedded in workspace Oct 2025 | NL Q&A guidance (Amazon Bedrock) | Vendor-native | n/a | A competitor's blog quotes Onshape's page as saying it does **not** generate designs. PTC listed FeatureScript generation and design agents as future plans |
| Autodesk "neural CAD" (Fusion/Forma; successor to Project Bernini) | shown at AU, Oct 2025 | text / sketch / "geometric prompts" → "fully editable CAD" (two variants: curved product shapes, prismatic) | Vendor-native, **not commercially available** as of the 2025 coverage | n/a | Bernini (2024) made meshes. No 2026 launch found |

**Sources for the rows above**
- CAD-Recode: Qwen2-1.5B base, CadQuery output, v1.5 March 2025, ICCV acceptance June 2025, claims SOTA on DeepCAD, Fusion360 and CC3D — [GitHub filaPro/cad-recode](https://github.com/filaPro/cad-recode); license file — [LICENSE.md](https://raw.githubusercontent.com/filaPro/cad-recode/main/LICENSE.md); HF license tag — [HF model](https://huggingface.co/filapro/cad-recode-v1.5); CD and IoU — [ICCV paper PDF](https://orbilu.uni.lu/bitstream/10993/67036/1/CADRecode_ICCV_2025.pdf), [alphaxiv summary](https://alphaxiv.org/overview/2412.14042v2)
- cadrille license — [HF maksimko123/cadrille](https://huggingface.co/maksimko123/cadrille)
- CAD-Coder (text) Apache-2.0, Qwen2.5-7B, 110K triplets — [HF gudo7208/CAD-Coder](https://huggingface.co/gudo7208/CAD-Coder); paper — [NeurIPS 2025 poster](https://neurips.cc/virtual/2025/poster/118098); numbers — [CAD-Judge arXiv 2508.04002](https://arxiv.org/pdf/2508.04002)
- CAD-Coder (image) / GenCAD-Code — [arXiv 2505.14646](https://arxiv.org/abs/2505.14646)
- Text-to-CadQuery — [arXiv 2505.06507](https://arxiv.org/pdf/2505.06507)
- CADFusion — [ICML 2025 poster](https://icml.cc/virtual/2025/poster/46007); [MSR page](https://www.microsoft.com/en-us/research/?p=1140514); MIT license — [GitHub LICENSE](https://raw.githubusercontent.com/microsoft/CADFusion/main/LICENSE)
- Text2CAD license — [GitHub LICENSE](https://raw.githubusercontent.com/SadilKhan/Text2CAD/main/LICENSE); IR conflict — [CAD-Judge](https://arxiv.org/pdf/2508.04002) vs [CAD-Coder alphaxiv](https://www.alphaxiv.org/overview/2505.19713v3)
- CAD-MLLM — [HF papers 2411.04954](https://huggingface.co/papers/2411.04954)
- CADCodeVerify / CADPrompt — [arXiv 2410.05340v2](https://arxiv.org/html/2410.05340v2); [ICLR 2025](https://www.iclr.cc/virtual/2025/poster/30576)
- Zoo output and editability — [Zoo FAQ](https://zoo.dev/docs/faq); endpoints (text-to-cad, copilot websocket, custom models backed by org datasets, KCL migration) — [Zoo ML API docs](https://zoo.dev/docs/developer-tools/api/ml); Text-to-CAD built on the ML-ephant API, 40 free API minutes/month then $0.50/min — [Zoo docs FAQ](https://docs.zoo.dev/docs/faq); an older post said 20 free minutes — [billing blog](https://zoo.dev/blog/turning-on-billing-for-text-to-cad) (conflict, likely changed over time); the current FAQ says billing is per second and failed calls are free — [Zoo FAQ](https://zoo.dev/docs/faq)
- FeatureScript MCP — [PTC press release (Nasdaq)](https://www.nasdaq.com/press-release/ptc-advances-leadership-ai-driven-design-launch-onshape-featurescript-mcp-server-2026); [Onshape blog, 2026-09-30](https://www.onshape.com/en/blog/featurescript-mcp-server-use-cases-text-code-cad); [Embedded Computing](https://embeddedcomputing.com/technology/software-and-os/ptcs-onshape-featurescript-mcp-server-delivers-ai-text-to-code-to-cad)
- AI Advisor — [DE247 (Oct 2025)](https://www.digitalengineering247.com/article/onshape-ai-advisor-now-embedded-directly-in-the-design-environment); [Develop3D](https://develop3d.com/cad/onshape-release-ai-advisor-for-real-time-guidance/); the "does not generate designs" claim comes from a competitor — [getleo.ai](https://www.getleo.ai/blog/built-in-ai-cad-plm-comparison-2026)
- Autodesk neural CAD — [AEC Magazine](https://aecmag.com/ai/autodesk-shows-its-ai-hand/); [DE247](https://www.digitalengineering247.com/article/autodesk-doubles-down-on-ai)

### Inferences
- **Language fit.** CadQuery and build123d both sit on OCCT, and build123d grew out of the CadQuery ecosystem. CadQuery output from CAD-Recode-style models could therefore run, or be ported, inside the Mises mirror (§7.2), but only for research or internal evaluation under CC BY-NC. Nothing I found emits build123d natively.
- **Commercial open options are thin.** CAD-Coder-text (Apache-2.0, 7B) is the only commercially licensed open CadQuery generator found. It was trained on DeepCAD-derived data, though, and the CC BY-NC-SA Text2CAD annotations may be in its lineage; check before relying on it (see §5).
- **Zoo is a parallel ecosystem, not an Onshape bridge.** KCL is parametric, but turning KCL into Onshape features would need a translator Mises would have to write. Zoo is useful mainly as a comparison point or an optional generator into STEP, which arrives as dumb solids in Onshape.
- **FeatureScript MCP is the most Onshape-native AI path,** and it matches Mises' bring-your-own-agent model (ACP + MCP). Mises agents could use it alongside the `mises` server to author custom features. It targets reusable feature *tools*, not whole-part generation from a prompt.
- For M9 (sketch to part), image→CadQuery models such as CAD-Coder-image and cadrille are the closest research analogues, but their licenses are NC or unknown. A frontier VLM writing sketch + extrude code is the more practical path.

### Gaps
- cadrille and CAD-MLLM benchmark numbers were not fetched. CAD-MLLM's license is unknown (no LICENSE at the guessed repo path).
- Zoo's model architecture, current pricing page, and terms on output IP or training on user data: the FAQ fields were unrendered template placeholders.
- The list of tools the FeatureScript MCP Server exposes, its plan requirements, and whether its calls count against API allowances (relevant to the Mises ledger, §7.1) are not documented on the pages fetched.
- I found no model that emits Onshape feature JSON or Onshape Variable Studio variables directly.

---

## 2. B-rep and sketch-and-extrude sequence generators: can outputs become editable features?

### Takeaway
Sequence models (DeepCAD → SkexGen → HNC-CAD → Text2CAD/CADFusion) output sketch + extrude command sequences. These map fairly directly to Onshape Sketch + Extrude features, but they quantize dimensions and cover only sketch/extrude operations. Direct B-rep generators (BrepGen, BrepDiff) output surfaces and topology with **no feature history**, so in Onshape they are dumb imported solids. BrepGen is also non-commercial.

### Cited Findings
- DeepCAD (2021, background) code is MIT (verified [LICENSE](https://raw.githubusercontent.com/ChrisWu1997/DeepCAD/master/LICENSE)). SkexGen (2022, background) is MIT (verified [LICENSE](https://raw.githubusercontent.com/samxuxiang/SkexGen/main/LICENSE)).
- BrepGen (2024): a diffusion model that "directly outputs a Boundary representation (B-rep)", with topology encoded by node duplication in a hierarchical tree — [arXiv 2401.15563](https://arxiv.org/abs/2401.15563v3). License: "code, data and the model weights … are not allowed for commercial usage"; research use under GPL v3 — [BrepGen LICENSE](https://raw.githubusercontent.com/samxuxiang/BrepGen/main/LICENSE) (verified).
- BrepDiff (2025): single-stage diffusion over masked UV grids. It argues that multi-stage B-rep pipelines mean the final geometry cannot take user edits — [search summary / catalyzex](https://www.catalyzex.com/paper/brepgen-a-b-rep-generative-diffusion-model) (secondary).
- B-repLer (Aug 2025): text-driven editing of B-rep models **without construction history**; introduces BrepEDIT-240K — [alphaxiv 2508.10201](https://www.alphaxiv.org/abs/2508.10201.md).
- A 2026 paper finds CADFusion and CAD-LLaMA predict command *types* well but do much worse on CD and IR. Command-type accuracy is therefore a misleading proxy for usable geometry — [arXiv 2603.11831](https://arxiv.org/pdf/2603.11831).
- Text2CAD-Bench: "CadQuery code outperforms DeepCAD-style command sequences in both validity and accuracy" — [arXiv 2605.18430](https://arxiv.org/html/2605.18430v1).

### Inferences
- A DeepCAD-format sequence (sketch profile of lines, arcs and circles, then extrude with extent) could be translated into Onshape `newSketch` + `extrude` feature JSON with a small adapter. Dimensions come out as literals, not named variables, so an agent would still need to lift them into Variable Studio. Topology names (face references) would also need rebuilding.
- For the M10 rebuild of topology-optimized shapes into features (§7.6), B-rep diffusion models do not help. They emit history-free B-reps, so the design doc's "rough or manual" plan (agent traces sketch + extrude/loft) stays correct.

### Gaps
- I did not verify HNC-CAD (2023), HoLa-BRep, DTGBrepGen or other 2025–26 B-rep successors; the search did not surface the last two by name.
- I found no published converter from DeepCAD JSON to Onshape features, though Onshape-sourced DeepCAD data originally came *from* Onshape feature lists, which suggests the reverse is feasible.

---

## 3. How well frontier LLMs/VLMs write CAD code (2025–2026 benchmarks)

### Takeaway
In 2026 benchmarks, general frontier models (GPT-5.2, Claude Sonnet 4.5, Gemini 3 Flash) clearly **beat** fine-tuned CAD models on text-to-CadQuery geometry. Absolute quality is still low: about half of programs execute, and fewer than a third meet engineering constraints. **Editing an existing parametric model is far easier than generating from scratch**, which matches Mises' edit-centric design.

### Cited Findings
**CADEngBench** ([arXiv 2608.09296, 10 Aug 2026](https://arxiv.org/html/2608.09296v1)). CadQuery tasks require a `build(params=None)` that exports STEP. Physics uses CalculiX + Gmsh, the same stack as Mises' simulator (§7.5). Data comes from BenchCAD and Fusion 360 Gallery.

| Model | L0 executes (%) | L1 meets reqs (%) | L2-E edit (%) | L3 FEA pair pass (%) |
|---|---|---|---|---|
| GPT-5.2 | 41.3 | 22.0 | 70.3 | 32.8 |
| Claude Sonnet 4.5 | **58.0** | 28.3 | 66.7 | 35.8 |
| Gemini 3 Flash | 52.7 | **30.0** | **72.3** | **46.3** |
| Kimi K2.5 | 53.3 | 24.3 | 67.0 | 34.8 |
| Llama 4 Maverick | 48.0 | 12.7 | 60.7 | 27.5 |
| Qwen3.5-35B-A3B | 13.3 | 5.0 | 61.7 | 36.6 |

- 1,030 of 2,400 programs execute; only 432 also pass L1. About 58.1% of executable outputs violate an engineering or DFM requirement. Holm-corrected tests do not separate the top models on L0 and L1 — [CADEngBench](https://arxiv.org/html/2608.09296v1).
- Editing vs generation: 1,071 item–model pairs pass only editing, against 231 that pass only generation (4.64×). Adding an independent body succeeds 247/248 times, but edits to histories with joins, cuts or multiple bodies pass only 40.2–45.8% — [CADEngBench](https://arxiv.org/html/2608.09296v1).
- FEA: of 1,809 attempts CalculiX failed only 5 times. Meshing or boundary-condition setup failed 573 times, and 554 runs solved but disagreed with the reference. Testing only default parameters hid verdict changes in 22/435 cases — [CADEngBench](https://arxiv.org/html/2608.09296v1).
- Assembly: the correct entity pair is ranked first in 542/960 requests, but only 11.5% pass end to end. Generation and editing ranks correlate only ρ=0.214. "No model performing best throughout the benchmark" — [CADEngBench](https://arxiv.org/html/2608.09296v1).

**Text2CAD-Bench** ([arXiv 2605.18430, May 2026](https://arxiv.org/html/2605.18430v1)), CadQuery, geometric prompts, CD ×10³:

| Model | L1 CD↓ | L1 IR↓ | L1 IoU↑ | L3 CD↓ | L3 IR↓ | L3 IoU↑ |
|---|---|---|---|---|---|---|
| GPT-5.2 | 44.31 | 11.1% | 0.59 | 93.46 | 68.0% | 0.23 |
| Claude-4.5-Sonnet | 52.62 | 20.3% | 0.54 | 70.13 | 70.0% | 0.25 |
| Gemini-3-Flash | 66.82 | 17.7% | 0.44 | 91.61 | 12.8% | 0.20 |
| DeepSeek-V3.2 | 53.15 | 13.3% | 0.54 | 101.23 | 69.0% | 0.17 |
| Text2CAD (fine-tuned) | 219.57 | 11.0% | 0.08 | 234.00 | 2.0% | 0.04 |
| CADFusion (fine-tuned) | 229.62 | 60.0% | 0.03 | 209.79 | 53.0% | 0.03 |
| Text2CADQuery (fine-tuned) | 227.46 | 44.0% | 0.07 | 255.42 | 80.0% | 0.02 |

- Invalidity climbs sharply at L3 (sweeps, lofts, shells). CD and IoU are computed only on executed samples, so a high IR flatters geometry scores — [Text2CAD-Bench](https://arxiv.org/html/2605.18430v1).
- Other 2026 benchmarks exist but were not read in detail: P3D-Bench (OpenSCAD / CadQuery 2.7.0 / Three.js; [arXiv 2606.11152](https://arxiv.org/pdf/2606.11152)), CADBench ([2605.10873](https://arxiv.org/pdf/2605.10873)), BenchCAD ([2605.10865](https://arxiv.org/abs/2605.10865)), MUSE (manufacturability / assemblability, 2605.28579), CADTests (2605.07807), and test-time consensus selection for CAD ([2608.09706](https://arxiv.org/pdf/2608.09706)) — [search listing](https://arxiv.org/html/2605.18430v1).
- CADCodeVerify (2024): a render-and-critique VLM loop improved GPT-4's compile rate by 5.5% and point-cloud distance by 7.30% on CADPrompt — [arXiv 2410.05340](https://arxiv.org/html/2410.05340v2).

### Inferences
- The evidence supports Mises' choice to bring frontier agents (Claude, Codex, Gemini) rather than a specialist model. The specialist open models lose badly on harder benchmarks, and the strong ones are non-commercial.
- The "editing ≫ generation" finding, and the fragility of edits to coupled histories, argue for keeping agents on **variable changes** and simple feature additions. They also argue for the mass/bbox numeric checks after every mutation (§7.4), the fidelity check (§7.2), and render-and-verify loops in the CADCodeVerify style (`mirror.render_views`).
- CADEngBench's FEA findings (meshing and boundary-condition setup fail, not the solver) are directly relevant to the `sim.*` tools. Fixtures and loads should be presets or structured inputs, not something agents write freely.
- The benchmarks tested Claude Sonnet 4.5, not newer Claude models. Current Claude results are unmeasured in these papers.

### Gaps
- No benchmark found tests **FeatureScript** or Onshape feature-JSON generation, or **build123d** specifically.
- No results for the newest frontier models (post Sonnet 4.5 / GPT-5.2) on these benchmarks.
- I did not read the P3D-Bench, CADBench or BenchCAD results (P3D PDF exceeded the fetch size limit).

---

## 4. Which outputs map onto Onshape features + named variables vs dumb solids

### Takeaway
Only code or sequence outputs can become Onshape features. Ranked by how directly they map: **FeatureScript (native) > CadQuery/build123d code (needs a CadQuery→Onshape feature translator; parameters exist as Python variables) > DeepCAD-style sequences (sketch + extrude, literal values) > KCL (parametric but Zoo-only) > B-rep / STEP / mesh (dumb solids).**

### Cited Findings
- The FeatureScript MCP Server outputs native parametric features that "can be configured, patterned, and shared", not one-off macros — [Onshape blog](https://www.onshape.com/en/blog/featurescript-mcp-server-use-cases-text-code-cad).
- Zoo KCL is "fully editable through familiar sketch and feature-tree workflows" in Zoo's own app. STEP export is a separate step — [Zoo FAQ](https://zoo.dev/docs/faq).
- CADEngBench tasks require a `build(params=None)` function, i.e. CadQuery parts that take parameters, which mirrors Mises' variable-driven mirror — [CADEngBench](https://arxiv.org/html/2608.09296v1).
- B-rep generators and B-repLer operate without construction history — [BrepGen](https://arxiv.org/abs/2401.15563v3), [B-repLer](https://www.alphaxiv.org/abs/2508.10201.md).

### Inferences
- **Recommended path for Mises:** keep Onshape (features + Variable Studio) as the source of truth. Have agents write build123d mirrors with named parameters matching the Onshape variable names, and push only variable values and simple feature JSON through `onshape.propose`. A generic "CadQuery → Onshape features" translator is a research project; restricting it to sketch / extrude / revolve / fillet / hole would cover most printable parts.
- **Point cloud / scan to part** (a possible future feature): CAD-Recode-class models are the state of the art, but they are NC-licensed. Commercial use would need a license from the University of Luxembourg / Artec3D, or training a model on commercially clean data.

### Gaps
- I found no evidence of any model producing Onshape Variable Studio variables or named dimensions. Mapping literal dimensions to named variables is unaddressed in the literature I reviewed.

---

## 5. Datasets and licenses (commercial use?)

### Takeaway
Nearly every key CAD training dataset is either **non-commercial** or of **unclear provenance** for commercial training. The Fusion 360 Gallery and Text2CAD are explicitly NC. ABC/DeepCAD come from public Onshape documents whose copyright stays with the creators. Synthetic, procedurally generated CadQuery data (CAD-Recode's approach) is the cleanest route for a commercial model, but CAD-Recode's own release is CC BY-NC.

### Cited Findings
- **Fusion 360 Gallery (Autodesk):** "only for non-commercial research purposes"; no redistribution of the full dataset — [LICENSE.md](https://raw.githubusercontent.com/AutodeskAILab/Fusion360GalleryDataset/master/LICENSE.md) (verified).
- **ABC (NYU, 1M Onshape models, background 2019):** the dataset is labeled MIT, but "copyright of these CAD models is owned by their creators", with a pointer to Onshape Terms of Use 1.g.ii — [NYU archive record](https://archive.nyu.edu/handle/2451/44320?mode=full).
- **DeepCAD (about 178k sketch + extrude sequences from ABC/Onshape):** code is MIT — [LICENSE](https://raw.githubusercontent.com/ChrisWu1997/DeepCAD/master/LICENSE). The underlying models inherit the ABC/Onshape creator-copyright caveat (inference from the above).
- **Text2CAD dataset (text annotations on DeepCAD):** CC BY-NC-SA 4.0 — [HF dataset](https://huggingface.co/datasets/SadilKhan/Text2CAD), [LICENSE](https://raw.githubusercontent.com/SadilKhan/Text2CAD/main/LICENSE) (verified).
- **CAD-Recode dataset (1M synthetic CadQuery programs):** CC BY-NC 4.0 — [HF dataset](https://huggingface.co/datasets/filapro/cad-recode-v1.5) (verified HF API).
- **GenCAD-Code (163k image–CadQuery pairs):** no license tag on HF — [HF dataset](https://huggingface.co/datasets/CADCODER/GenCAD-Code) (verified HF API).
- **BrepGen data and weights:** non-commercial — [LICENSE](https://raw.githubusercontent.com/samxuxiang/BrepGen/main/LICENSE).
- **Zoo** offers custom ML models "backed by one or more org datasets", i.e. fine-tuning on your own data, API-only — [Zoo ML API](https://zoo.dev/docs/developer-tools/api/ml).

### Inferences
- If Mises ever fine-tunes its own CAD model, the data should be synthetic (procedural build123d programs it generates itself) or user-consented Onshape data under appropriate terms. Mixing in Fusion 360 Gallery, Text2CAD or CAD-Recode data would block commercial use.
- Apache or MIT *model* licenses (CAD-Coder-text, CADFusion) do not by themselves clear the training-data question. Both were trained on DeepCAD-derived data, and Text2CAD's NC-SA annotations may be part of that. Legal review is advisable before shipping either.

### Gaps
- I did not retrieve Onshape Terms of Use §1.g.ii, so whether public Onshape documents can be used for commercial ML training is unresolved.
- Licenses for SketchGraphs, CC3D, BenchCAD and the CADEngBench / Text2CAD-Bench benchmark data were not checked; the papers show only arXiv paper licenses.
