# Pending design changes

Proposals from research and UI review that are **not yet in** `2026-10-24-mises-design.md`. Approve, reject or edit each; approved items get folded into the design doc.

## From the UI review (2026-10-24)

1. **Bloomberg-inspired terminal layer** (`mockups/terminal-core-loop.html`): command line with short function codes (`SIM`, `TOPO`, `CFD`, `MAT`, `PRT`, `AGT`, `MRG` + GO), numbered panels with 1–9 jump keys, dense right-aligned tables with previous-value and delta columns, 7-day filament price change, version history with safety-factor trend line, scrolling event feed, function-key strip. Palette stays greyscale + blue. *Status: shown to you, awaiting feedback.*

## From `research/models/README.md`

2. **No learned models in v1** — state explicitly as a non-goal; frontier BYO agents + exact geometry tools instead.
3. **`fidelity_class` on every sim/estimate result** (`fem` | `rigid_dynamics` | `learned_surrogate` | `learned_critic`), plus `solver`, `version`, `inputs_hash`, `validated_against`. A "sim passed" badge requires `fem` (or a surrogate validated against a FEM run on the same geometry).
4. **Honest CalculiX error band:** coarse mesh first, refine, show the change in peak stress as the result's error band; cache meshes and reuse factorization across the environment × material matrix.
5. **Fixtures and loads as structured presets** agents select, not free-form inputs (meshing/boundary-condition setup causes most FEA failures).
6. **Render bundle for agents:** iso + ≥3 orthographic views, labelled, with face/hole ID overlays matching `measure` output, always sent with a text payload (mass, volume, bbox, hole list, features, build123d script). Vision verdicts are shown as unverified claims.
7. **Measure-and-validate tool vocabulary** modelled on build123d-mcp (Apache-2.0): render, measure, find holes, printability, compare.
8. **Typed requirements schema** for what the user asked (loads, keep-in/keep-out, fits), so checks can be scored.
9. **Solver-neutral environment file** (loads, fixtures, temperature, vibration/shock, contacts, optional scene) — feeds CalculiX now, MuJoCo or a surrogate later.
10. **Model-backend seam** on `mises` tools: `local_mlx | local_onnx | cloud`, cloud only behind the paid-tier check.
11. **Opt-in data capture** of approved merges, rejected proposals and sim runs (mesh + inputs hash) — the only clean future training set.
12. **Offline nightly regression suite** on the local mirror (validity → IoU/Chamfer/bbox/mass → hole/feature recall → editability ±10–20% → sim/slicer pass → process cost), seeded with BenchCAD (CC-BY-4.0).
13. **Sketch-to-part (M9) as a staged pipeline:** strokes → VLM reads dimensions on crops → rule-based constraints + solver → ≤3 questions. eDOCr2 (MIT) for formal drawings.
14. **Optional sibling server:** tell users about Onshape's FeatureScript MCP for authoring reusable features, but never route Mises's own edits through it (it bypasses the ledger and approvals).
15. **Later, paid tier:** per-study surrogates during generative sweeps (PhysicsNeMo, Apache-2.0) with solver re-verification; MuJoCo/Genesis "shake test" labelled `rigid_dynamics`.

## From `research/harness/README.md`

16. **Anti-goal wording:** add "third-party leaked-source write-ups (CCFS) are for ideas only; no code or pseudocode from them." (The rest of that report's proposals are already in the design doc: gate pipeline, AgentPolicy, content-hashed approvals, typed errors, underscore tool names, never auto-approve.)

## Licence watch-list (from both reports)

- Avoid non-commercial models/data: CAD-Recode, cadrille, BrepGen, Text2CAD, DeepCAD/ABC/Fusion 360 Gallery corpora (without legal review), multimodal Llama (EU exclusion), Hunyuan3D (EU/UK/KR exclusion).
- CalculiX and OpenFOAM are GPL: ship as separate executables, never linked.
