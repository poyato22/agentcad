# Mises research index

Everything researched for Mises, as of 2026-10-24. Read top to bottom for the full picture; each entry says what it concluded and how far to trust it.

## Reading order

| # | File | What it answers | Bottom line |
|---|---|---|---|
| 1 | `landscape/3d-cad-agents.md` | Who already builds AI agents for 3D/mechanical CAD? | Crowded on software (text-to-cad, Blender/FreeCAD/Onshape MCPs, Zoo, Adam); **nobody closes the print → measure → fix loop**. |
| 2 | `landscape/hardware-agents.md` | Same question across PCB, chips/FPGA, embedded, CAD | Every domain has harnesses; the gap is always the jump from simulation to real hardware. Background only. |
| 3 | `integration/onshape-and-agents.md` | How to connect to Onshape, and how to host any AI agent | Onshape REST does everything needed (features, variables, branches, glTF, webhooks) but allows **2,500 calls/user/year**; host agents through **ACP**, expose one **`mises` MCP server**, use the **Claude Agent SDK** for the orchestrator. |
| 4 | `harness/README.md` | What to borrow from Claude Code, open-multi-agent (OMA) and third-party write-ups | **Enforce every rule inside the Mises core**, not inside agents: gate pipeline, content-hashed approvals, evidence-linked badges, frozen agent policies. Mostly applied to the design doc already. |
| 5 | `models/README.md` | Should Mises ship its own CAD, vision, sketch, physics or world models? | **No models in v1.** Frontier agents + exact geometry tools beat every specialist model; most open CAD models are non-commercial; no surrogate replaces CalculiX. Add cheap seams for later. |

## Folder map

```
research/
  README.md                      ← you are here
  landscape/                     who else is doing this
    3d-cad-agents.md               report (mechanical / 3D CAD)
    hardware-agents.md             report (all hardware domains)
    notes/3d-cad/                  raw researcher notes
    notes/hardware/
  integration/
    onshape-and-agents.md          Onshape API, auth, limits, webhooks; ACP, Agent SDK, MCP
  harness/                       how to build the agent harness itself
    README.md                      synthesis + verdict table (start here)
    official/                      Anthropic sources — authoritative
      agents.md                      subagent definitions in official plugins
      plugins-hooks-skills.md        plugins, skills, hooks, MCP configs
      agent-sdk.md                   Python/TS Agent SDK internals and options
    oma/                           open-multi-agent (MIT) — real code, patterns to port
      core.md                        planner, scheduler, agent loop, checkpoints
      governance-acp.md              approvals, journal, governance, ACP backend
    third-party-unverified/        ⚠ ideas only — see warning below
      ccfs-core-loop-agents.md
      ccfs-memory-hooks-mcp.md
  models/                        AI models for CAD, vision, sketch, physics
    README.md                      report (start here)
    notes/                         five researcher notes
```

## Trust levels

- **Authoritative:** `harness/official/*`, official docs cited in `integration/`.
- **Real code, read directly:** `harness/oma/*` (MIT; keep the notice if any code is ever copied).
- **Third-party, unverified:** `harness/third-party-unverified/*` summarizes "Claude Code from Source", a book reconstructed from Claude Code's accidentally published source maps. ⚠ **Ideas only.** The design forbids using leaked source; nothing from these files goes into code, and any claim used for a decision must be re-checked against official docs.
- **Vendor / self-reported numbers** are flagged inline in each report.

## Not yet applied to the design

Research conclusions that still need your approval are collected in `../docs/design/pending-changes.md`.
