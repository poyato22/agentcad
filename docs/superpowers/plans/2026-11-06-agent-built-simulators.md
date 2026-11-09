# Plan: agents can request new simulators, you approve them, and they build them (2026-11-06)

User request: "make it so the agent can ask for certain simulators and then build them itself. but make it so you can accept or reject and it give probable cost for that simulator"

Motivation: `Mises-demo/reports/sim-gaps-from-projects.md`. The user's projects hit missing physics such as impact, flapping-wing aero, granular media, gear strength and print simulation, and today the agent just says "unverified".

## Requirements

| # | Requirement |
|---|---|
| S1 | **New tool `simulator_request`.** When an agent needs physics no existing study covers, it files a request with: name; what it would compute (inputs, outputs, units); why this build needs it (the failing check or unverified claim); the method (e.g. resistive force theory, explicit dynamic FEA, blade-element flapping model, AGMA 2101); the dependencies it would install (pip/brew/docker, with licence); validation cases with known answers; and a fidelity class. It may not file a request for something an existing study already covers, so `sim_types_list` / `assembly_study_kinds` are checked first. |
| S2 | **Cost estimate on every request.** A probable cost range in API-price dollars and tokens, wall time and machine load, and the share of weekly usage if the user has entered a /usage baseline. It's derived from the new `usage_events` history of comparable builds by complexity class, not invented, and shown as low/likely/high with the basis stated. Dependencies to download are listed with size. |
| S3 | **Decision card.** The request shows in the View tab's "Needs your decision" section (view-tab branch) with Accept / Reject / Ask for changes. It's one-time, idempotent and enforced in the core. Reject records a reason, and the agent continues without the simulator and states the gap. Never auto-approve. Downloading dependencies is part of what the user accepts. |
| S4 | **Building the simulator after Accept.** A builder sub-agent writes it as a plugin under `<MISES_HOME>/simulators/<name>/`, covering the solver code, a study-kind manifest (inputs, outputs, units, fidelity) and tests. It runs only in the existing sandboxed worker (`run_sandboxed`), with no network, no access to the user's files outside the build, and CPU, memory and time limits. Code never runs in the core process. |
| S5 | **Validation gate.** The plugin must pass its own validation cases (known analytic or published answers) before it can be used. Results carry `fidelity: agent_built` plus the validation evidence, and the UI labels them "agent-built simulator, validated against N cases". If validation fails, the plugin stays disabled and the card reports why. |
| S6 | **Budget during the build.** Spending is tracked against the estimate. At 1.5× the high estimate, the build pauses and asks to continue or stop, with spend shown. |
| S7 | **Registry and reuse.** Accepted simulators appear in `sim_types_list` for every future build, can be disabled or deleted in Profile → Simulators, show their version, source, licence and validation status, and can be exported or shared. |
| S8 | **Safety.** Plugin code is untrusted. It's reviewed by an independent reviewer sub-agent before first run (no network, no file writes outside its folder, no subprocess escape), then runs only in the sandbox, behind the Gate. |
| S9 | **Tests (TDD).** Request validation (no duplicates of existing studies); cost estimation from usage history; decision idempotency; a sandbox escape attempt is refused; a failed validation keeps the plugin disabled; budget pause; and the UI card, badge and one-time decision. |

## Dependencies and order
1. Merge `view-tab` (decision section, one-time decisions) and `error-log`. `usage-ui` is already merged.
2. Build S1–S3, S6 and S9 first (request, estimate, decision), then S4, S5 and S8 (build, validate, sandbox), then S7.
3. First real use: the impact-dynamics simulator for the egg-drop projects, as an end-to-end test.

## Usage note
This is a large feature, likely 4–8% of weekly usage. The user's cap is 75%, and usage was 71% on 2026-11-06, so build after the Nov 11 reset unless the user says otherwise.
