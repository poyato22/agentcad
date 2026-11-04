# Text/image-to-CAD benchmarks for Mises (2026-11-02)

Goal: pick public benchmarks Mises (agents write build123d/OCCT -> B-rep, STEP/STL export) can be scored on, next to the internal `core/evals/design/` set (20 design-taste prompts, contact sheet + pairwise preference; no geometric ground truth).

Sourcing note: figures come from the pages cited. Where a fetch returned only an abstract or a PDF I could not read, the cell says "not verified". Per-prompt Claude Code usage in section 5 is my estimate, not a measurement.

## 1. Summary table

| Benchmark | Task | Size | Ground truth | Metrics | Licence / location | Adapt to Mises |
|---|---|---|---|---|---|---|
| **CADGenBench** (Hugging Face) | image(+text)->CAD (49 gen) and STEP edit (32 edit) | 81 fixtures (49+32) | Private STEP; scored only on the HF Space | Validity gate, then surface-distance F1 + volume IoU, interface keep-in/keep-out, Betti numbers | Code Apache-2.0; inputs ODC-BY. github.com/huggingface/cadgenbench, HF `HuggingAI4Engineering/cadgenbench-data` | Easy to produce, impossible to score locally |
| **CADBench** (Doris et al., MIT DeCoDE) | image/mesh->CadQuery | 18,000 (6 families x 3,000) | STEP (DeepCAD, Fusion360, ABC) or mesh (MCB, Objaverse) | Volumetric IoU, Chamfer, surface IoU, valid-shape rate, after Procrustes alignment | Repo MIT; each data source keeps its own licence. github.com/anniedoris/CADBench, HF `DeCoDELab/CADBench` | Best fully local, scriptable option |
| **CADPrompt / CADTestBench** | text->CadQuery | 200 prompts (abstract + detailed); 5,937 tests | Expert code (CADPrompt); executable CadQuery tests (CADTestBench) | CADPrompt: point-cloud distance, compile rate. CADTestBench: test pass rate | CADTestBench repo MIT; HF `dimitrismallis/CADTestBench` | Easy, with one catch (tests call CadQuery API) |
| **Text2CAD-Bench** | text->CadQuery | 600, levels L1-L4 | Not stated | Invalidity rate, CD x1e3 (30k pts, unit bbox), IoU (256^3 voxels), VLM judge on L4 | Paper CC BY 4.0; no dataset URL yet (leaderboard "[URL]", code "after acceptance") | Not usable yet |
| **MUSE** | text->multi-part B-rep assembly (CadQuery) | Not stated | Design Specs + reference Physical Assembly Graphs | Sandbox success, 4 OCCT checks (watertight, manifold, no self-intersection, no overlap), Gemini-3.1-Pro rubric (function, manufacturability, assemblability) | Dataset CC BY 4.0; HF `dongxiaoyu/MUSE`, github.com/dong7313/muse | Most relevant assembly benchmark; scores not published on the page |
| **Hephaestus-CCX** (FEA-feedback agents paper) | engineering brief -> multi-part STEP, checked by FEA | 50 (20 single, 30 multi) | Typed pass/fail requirements, CalculiX | Strict pass, mean requirement pass | Paper CC BY-SA 4.0; release URL not found in the text I read (appendix cut off) | Closest to Mises' differentiator; must wait for the release |
| **BenchCAD** | image->CadQuery, code edit, design QA | 17,900 programs, 106 part families | Execution-verified CadQuery | Mixed (not verified) | Project page benchcad.github.io/BenchCAD_webpage | Part-family/standards flavour; details not verified |
| **CADWorld** | GUI agents in FreeCAD | 200 tasks, 11 categories (assembly, CAM, FEM, drawing...) | Executable checks on saved FreeCAD files | Success rate | github.com/Zdong104/CADWORLD | Wrong interface (screenshot/GUI), not a fit |
| **CAD-Bench Lab** | AI CAD agents incl. mates, kinematics, FEA | 70 tasks / 343-task suite (page is inconsistent) | Not stated | Score /100, Pass@1 | evals-for-ai-cads.vercel.app | Page says most data is a "synthetic baseline" with only 9 live runs; do not trust |
| **Text2CAD** (Khan et al.) | text->CAD sequence | ~170K DeepCAD models, ~660K annotations | DeepCAD sequences | Training-oriented | HF-hosted annotations; licence file not identified | Training data, not an LLM-agent eval |
| **DeepCAD** | CAD sequences | Size not on repo page (commonly 178K, not verified) | JSON op sequences; STEP via `export2step.py` | Used by others | Code MIT; dataset licence unstated (Onshape/ABC) | Use via CADBench-B |
| **Fusion 360 Gallery (reconstruction)** | sketch+extrude reconstruction | 8,625 sequences | Op sequences; extrude volumes | Used by others | `LICENSE.md` not summarised | Use via CADBench-F |
| **CAD-Recode** | point cloud -> CadQuery | 1M synthetic training programs; evals on DeepCAD, Fusion360, CC3D | CadQuery | CD, IoU, invalidity ratio (numbers not on abstract page) | arXiv licence; hosting unverified | Specialist model, not a benchmark for agents |
| **CAD-Coder** | text->CadQuery | 110K triplets + 1.5K CoT | CadQuery | CD reward | Not stated | Training set; skip |
| **CAD-MLLM / Omni-CAD** | multimodal -> CAD sequence | ~450K (453,220) models | Command sequences | FluxEE, others | Code release unclear | Skip |

I found nothing at all on "Text2CQ" or a standalone "CAD-MLLM benchmark" beyond the Omni-CAD dataset.

## 2. Findings per benchmark

### CADGenBench (the 86.9% number)
- Source of 86.9%: BenchLM's snapshot dated 2026-10-28 lists "build123d-mcp 0.3.85 MCP server + Claude Opus 5.5 (max effort) r2" at 86.9%, rank 1 of 25, linked to commit 3a274b9 in github.com/pzfreo/cadgenbench-build123d. Godela is second at 84.5%. https://benchlm.ai/md/benchmarks/cadgenbenchgeneration.md
- That repo's README (as I read it) does not mention the Opus 5.5 / r2 run, any attempt cap, tokens or cost. Treat 86.9% as a self-submitted, "unvalidated" row (HF Space publishes rows as unvalidated until a maintainer review). It is also far above the next harness-free entries (Opus 4.8 + Claude Code + text-to-cad skill 28.0%; Opus 4.8 alone 27.4%; GPT-5.6 Sol xhigh + build123d-mcp chain 45.1%; Opus 5 xhigh + build123d-mcp 59.6%), so the jump deserves a look at the commit before quoting it.
- Scale conflict: the build123d-mcp PyPI page cites a June 2026 board where GPT-5.5 through build123d-mcp scored 0.457 vs 0.360 without it (validity 88% -> 100%); LLM Stats says top is Grok 4.7 at 0.444. These are on a 0-1 scale and from different dates; BenchLM shows percentages. https://pypi.org/project/build123d-mcp/0.3.68/ , https://llm-stats.com/benchmarks/cadgenbench
- Protocol: tool-agnostic. Input = text prompt plus engineering-drawing PNG(s) for generation; text + STEP + mesh sidecar + 4 views for editing. Output = one `output.step` per fixture, zipped with a root `meta.json`, uploaded to the Space; scoring happens only there (ground truth is in a private repo `cadgenbench-data-gt`). Local `sanity_check_submission.py` checks format only. Repo rules: report on `splits/test.txt` only, keep prompts generic per task type, never encode a fixture's answer. https://github.com/huggingface/cadgenbench , https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-data
- Weights, normalisation and alignment: in `docs/metrics.md`, which I did not read.
- Mises fit: producing STEP is trivial (we export STEP). Cost: 49 gen + 32 edit = 81 runs, one upload. The gen task is drawing->part, which exercises vision, not Mises' design/assembly differentiator. Worth one run for a public number.

### CADBench (Doris, Faez Ahmed lab) https://arxiv.org/abs/2605.10873 , https://github.com/anniedoris/CADBench
- Six families x 3,000: B (DeepCAD), F (Fusion 360), E and A (ABC), M (MCB, mechanical), O (Objaverse, organic). B/F/E/A stratified low/med/high by B-rep face count. Five input modalities (single-view, 4-view, PBR render, clean mesh, noisy mesh).
- Metrics: volumetric IoU, Chamfer, surface IoU, valid shape rate; also token and op counts. Procrustes alignment before geometry metrics.
- Published scores (clean single-view grayscale, aggregate): Claude Opus 4.7 IoU 0.412, CD 0.101, VSR 0.798; Gemini 3.1 Pro IoU 0.382, CD 0.087, VSR 0.729; GPT-5.4 IoU 0.158, CD 0.109, VSR 0.513. Mesh-to-CAD CADFit 0.859 IoU on clean meshes. Models were run 3x each.
- Output must be CadQuery code with a named solid variable (`--variable-name`); GT STEP/mesh included. Mises writes build123d, so either (a) bypass the code and feed the exported STEP to their metric functions, or (b) prompt for CadQuery. Option (a) is cleaner: re-implement IoU/CD ourselves (section 4) using their STEP GT. I did not check how their pipeline handles a pre-made solid.
- Licence: repo MIT, data inherits source licences (`DATASET_LICENSE.md`); DeepCAD/ABC provenance is Onshape public data, so keep the use internal/eval-only.

### CADPrompt and CADCodeVerify https://arxiv.org/abs/2410.05340
- 200 NL prompts with expert-annotated scripting code. Metrics: point-cloud distance, compile success. Paper reports only GPT-4 deltas (7.30% point-cloud-distance reduction, 5.0% success-rate gain over prior work); no dataset link on the abstract page.
- Practical path is CADTestBench, which re-bases the same 200 items with abstract and detailed prompts and 5,937 executable tests (about 15 per item). It ships GPT-5.2 and Claude 4.6 Sonnet baselines (viewable in its viewer; no numbers on the README). MIT; run: `cadtestbench evaluate <BASELINE_DIR> --partition abstract|detailed --limit N`; input is `generated_models/<id>/gpt_generated.py` defining `create_cad()` and `final_result` in CadQuery. Output STL written per sample. https://github.com/dimitrismallis/CADTestBench
- Mises fit: tests are CadQuery snippets that query the generated model, so our build123d solid must be converted. Build123d and CadQuery both wrap OCP `TopoDS_Shape`, so `cq.Shape.cast(b3d_solid.wrapped)` should work (untested). Alternatively ask the Mises agent for CadQuery in this one run. Reported finding: detailed prompts score lower, and dimensional, volumetric, topological requirements remain hard.

### Text2CAD-Bench https://arxiv.org/abs/2605.18430
- 600 curated prompts, L1-L4 (L4: real-world, non-mechanical, judged by GLM-4.6V on 8 views). CadQuery 2.4 only. Models: GPT-5.2, Claude-4.5-Sonnet, Gemini-3-Flash, DeepSeek-V3.2, Qwen3-max, MiniMax-M2.1, GLM-4.7 plus Text2CAD, Text2CADQuery, CADFusion. I could not read the result tables. 60 s timeout, 30k points, unit bounding box, CD on successes only (survivorship caveat the authors state).
- No usable dataset or leaderboard URL as of the paper. Re-check later.

### MUSE https://arxiv.org/abs/2605.28579 , https://dong7313.github.io/muse-benchmark/
- Multi-component, editable B-rep assemblies from text + Design Specification. Four dimensions: executability, geometric validity, structure/interface fidelity (SIE with Physical Assembly Graphs, interface clearances), functional consistency. Headline finding: high execution success does not imply correct assembly structure. Leaderboard has 10 metrics; evaluated models in the paper were mostly open-weight (GLM-5.1, MiniMax-M2.7, Qwen); closed models lead. CC BY 4.0 data on HF `dongxiaoyu/MUSE`.
- The OCCT checks (watertight, manifold, self-intersection, overlap) are reproducible locally. The Gemini judge needs API spend. Output is CadQuery assemblies; Mises' assembly export (STEP with named components) should map, but the sandbox contract (script format) needs reading from the repo. I did not open the repo.

### Hephaestus-CCX (FEA as feedback) https://arxiv.org/abs/2605.17448
- 50 briefs from patents, datasheets, NASA-STD, ECSS, AISC, MIL-STD, FIA, competitions. Pipeline: gmsh mesh -> CalculiX -> typed checks (diameter, von Mises limit, deflection, buckling load factor). Agents tested: Codex GPT-5.5/5.4, Claude Code Opus-4.7 and Sonnet-4.6, OpenCode with GPT-5.5, DeepSeek-V4-Pro, Kimi-K2.6, GLM-5.1.
- Results: zero strict passes first attempt; mean requirement pass 21.6-32.7% single-part, 6.2-15.0% multi-part. One FEA-feedback retry: roughly 29-42% single, up to 39.2% multi. Longest run (GPT-5.5/high, up to 10 attempts): mean 38.8% -> 60.5%, 9/50 strict; about 68 min per item vs about 10 min for two attempts. No per-task cost reported.
- This is the only public benchmark that matches Mises' pitch (CAD that survives simulation). Release URL not in the text I read; check Appendix H / authors. Mises ships CalculiX-class FEA, so its checker kits could run natively if released.

### Others
- BenchCAD (17,900 CadQuery programs, 106 families, ~half tied to ISO/DIN/EN/ASME standards; image->code, edit, design QA): only secondary summaries read. https://arxiv.org/abs/2605.10865
- CADWorld (FreeCAD GUI agents; best agent 17.5% vs experts 87.0% per a summary; one subset 25.0%): GUI-based, wrong interface for Mises. https://arxiv.org/abs/2609.16251
- CAD-Bench Lab (evals-for-ai-cads.vercel.app): leaderboard lists Zoo Text-to-CAD 71.8, o4 -> CadQuery 70.0, Claude Opus 4.7 -> CadQuery 62.1, human Mech-E 86.0; page admits mostly synthetic baseline data. Ignore.
- Physics-in-the-Loop (IJCAI 2026, Generate-Simulate-Refine): abstract-level only; PDF unreadable. https://www.ijcai.org/proceedings/2026/0714.pdf
- No benchmark found that tests mechanisms (kinematics, mates, motion) with simulation scoring in a verified, downloadable form. MUSE (assemblability, function) and Hephaestus-CCX (FEA) are the nearest.
- Mechanical-engineering QA benches (reasoning without CAD output): not searched in depth; BenchCAD includes a design-QA task. Low priority, since Mises output is geometry.

## 3. Recommended suite

Keep four layers, cheap to expensive, each run on a fixed seed-pinned subset and reported with validity next to quality (to avoid survivorship bias).

| # | Benchmark | Subset | Why |
|---|---|---|---|
| 1 | **CADBench-M and -F/-B** (local IoU/CD) | 60 prompts: 20 from M (mechanical), 20 from F, 20 from B stratified by face count; 1 run (not 3) | Only large, local, STEP-grounded set; published frontier numbers to compare to (Opus 4.7 IoU 0.412 aggregate) |
| 2 | **CADTestBench** (CADPrompt) | 50 items, detailed partition, plus the same 50 abstract if budget allows | Pure text->part with executable requirement tests; cheap; Claude 4.6 Sonnet baseline ships with repo |
| 3 | **CADGenBench** | Full 49 generation fixtures once; skip 32 edits initially (add later, edit matters for Mises) | Public leaderboard number, quotable; scoring on the HF Space |
| 4 | **MUSE** subset + **Mises-Functional** (section 5) | 25 MUSE tasks + 20 Mises tasks | Assemblies and function, the differentiator |

Add Hephaestus-CCX as soon as the checkers are released. Keep `core/evals/design/` for taste; it has no geometric GT so do not mix scores.

Total prompts per full pass: about 60 + 50 + 49 + 45 = about 205.

## 4. Scoring scripts needed (`core/evals/cad_bench/`, proposed; nothing created)

1. `export.py`: run a Mises build, export the part or assembly to STEP and a watertight STL (tolerance 0.01 mm-ish); record wall time, token/usage cost, stop reason.
2. `validity.py`: OCCT `BRepCheck_Analyzer`, closed shell/solid count, volume > 0, mesh watertight + manifold (trimesh), self-intersection for assemblies, inter-part overlap volume. Emit `valid` and an invalidity rate.
3. `align.py`: scale/translate normalization to unit bounding box (Text2CAD-Bench convention) for CD, and Procrustes/PCA + 24 axis-flip candidates for IoU (CADBench convention), take best IoU. Note that normalization hides absolute-size errors; log raw bbox error too (the Mises prompts state dimensions).
4. `metrics.py`: Chamfer on 30k surface samples (x1e3 for comparability), volumetric IoU (voxel 256^3 or OCCT boolean common/fuse volume), surface-distance F1 at a stated threshold, Betti numbers b0/b1/b2 (optional).
5. `cadtest_adapter.py`: wrap the Mises solid into a CadQuery shape and call CADTestBench's evaluator, or use the repo's `evaluate` with a generated `gpt_generated.py`.
6. `cadgenbench_pack.py`: write `<id>/output.step` + `meta.json`; run their `sanity_check_submission.py`; zip for upload. Ground truth is private, so scores arrive after upload.
7. `report.py`: one table per benchmark: validity rate, median/mean IoU and CD over valid and over all (invalid = 0 IoU), cost, wall time; plus rerun variance.

## 5. Budget and Claude Code usage (estimates, not measured)

I have no measurements of Mises runs. Rough planning figures for an agent doing write-run-inspect loops until valid:
- Simple single-part prompt (CADPrompt/CADBench style): 5-15 tool turns, about 100k-300k input tokens including cache reads, 10-25k output tokens, 2-6 min.
- Drawing-based CADGenBench part or MUSE assembly: 15-40 turns, about 0.5-1.5M cached input tokens, 30-60k output tokens, 8-25 min. For scale, the Hephaestus-CCX authors report 68 min per item for a 10-attempt agent and about 10 min for two attempts, which fits this range.
- Rule of thumb for the budget: one "prompt" is about 5-10% of a 5-hour Claude Code window on a Max plan for simple parts, 15-30% for assemblies (guess, depends on the account). So the 205-prompt pass is roughly 10-25 simple-part-windows plus the hard ones, i.e. expect several days of quota or a metered API run. Run the cheap layers (1, 2) in CI-like cadence and layers 3-4 per release.
- Measure first: run 5 prompts from each layer, record `result.json` cost fields (run_eval.py already stores cost and time) and replace these guesses before committing to the full pass.

## 6. Mises-Functional suite (proposal, 20 tasks to start)

No public benchmark scores mechanisms or simulation-backed engineering with downloadable checkers except possibly MUSE and Hephaestus-CCX once released. Build a small, auto-checkable set, each task = prompt + a checker script on the exported STEP/assembly. Pass/fail with partial credit per requirement (the Hephaestus "mean requirement pass" convention).

Categories (4-5 tasks each):
- **Fit and mates**: pin in hole with H7/g6 clearance; bolted flange (bolt circle, clearance holes, thread engagement); snap-fit clip. Check: named parts present, interface clearances in range, no interpenetration (OCCT common volume ~ 0), mating faces coincident.
- **Mechanisms**: four-bar linkage with given link lengths, rack-and-pinion, spur gear pair of given module/teeth, hinge with travel limit. Check: assemble at N poses, sweep joint, no collisions beyond tolerance, output/input ratio within 2%, range of motion matches spec.
- **Structural (FEA)**: cantilever bracket must carry a stated load with von Mises below yield at SF 2 and mass below a limit; shelf bracket under UDL; pressure-vessel wall thickness. Check: CalculiX run on the exported solid with fixed BCs, compare to limits; also compare hand-calc within 15%. (Same style as Hephaestus-CCX typed checks.)
- **Manufacturability**: part for 3-axis CNC (no undercuts, min internal radius), 3D-print (min wall, overhang), sheet-metal bend. Check: geometric analysis scripts.
- **Edit/regression**: change a hole pattern or thickness on an existing Mises part without touching other features; check unchanged-feature volume IoU > 0.99 and requested delta met.

Scoring: requirement pass fraction, strict pass, validity, wall time/cost, plus number of agent repair loops. Authoring all 20 checkers is the real cost (about 1-2 days); start with 5 per category's easiest task. Keep it separate from public numbers, but align the report format with Hephaestus-CCX so numbers can be compared once it is released.

## 7. Open items

- Read cadgenbench `docs/metrics.md` and the pzfreo commit 3a274b9 to learn weights, scoring normalization, and what the 86.9% run's prompt/harness did (and whether it used the scorer's feedback). Do not copy its prompts into Mises without checking the "generic prompts" rule.
- Find the Hephaestus-CCX release (Appendix H) and the Text2CAD-Bench data URL.
- Read the MUSE repo to confirm its sandbox contract and local geometry checks.
- Confirm DeepCAD, Fusion 360 and Text2CAD data licences (all unverified) before redistributing any derived files; internal evaluation only for now.
- Check whether `cq.Shape.cast(build123d_solid.wrapped)` round-trips for the CADTestBench adapter.

## Sources
- https://benchlm.ai/md/benchmarks/cadgenbenchgeneration.md
- https://github.com/huggingface/cadgenbench , https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-data , https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-submissions
- https://github.com/pzfreo/cadgenbench-build123d , https://pypi.org/project/build123d-mcp/0.3.68/ , https://llm-stats.com/benchmarks/cadgenbench
- https://arxiv.org/abs/2605.10873 , https://github.com/anniedoris/CADBench
- https://arxiv.org/abs/2410.05340 , https://arxiv.org/abs/2605.07807 , https://github.com/dimitrismallis/CADTestBench
- https://arxiv.org/abs/2605.18430 , https://arxiv.org/abs/2605.28579 , https://dong7313.github.io/muse-benchmark/ , https://github.com/dong7313/muse
- https://arxiv.org/abs/2605.17448 , https://arxiv.org/abs/2605.10865 , https://arxiv.org/abs/2609.16251 , https://evals-for-ai-cads.vercel.app/ , https://www.ijcai.org/proceedings/2026/0714.pdf
- https://arxiv.org/abs/2409.17106 , https://github.com/SadilKhan/Text2CAD , https://arxiv.org/abs/2412.14042 , https://arxiv.org/abs/2505.19713 , https://arxiv.org/abs/2411.04954
- https://github.com/ChrisWu1997/DeepCAD , https://github.com/AutodeskAILab/Fusion360GalleryDataset
