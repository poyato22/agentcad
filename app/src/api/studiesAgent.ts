// The studies agent's build context (Plan 9 U-D; core sim/studies_agent.py, GET
// /studies/context?project_id=): parts with material and mass, the assembly, loads known, the
// spec's requirements with status per part, past studies, calibrations, solvers with their
// validated accuracy bands, and the proposed next studies (the studies agent's own when it saved
// some with `studies_propose`, else the core's rule proposals). Loose objects: the fields the
// Studies page reads are typed, the rest passes through. Labels, why texts and reasons were
// written by users or agents: render them as text.
import { z } from "zod";
import { STUDY_TYPES } from "./studies";

const num = z.number().nullable();

export const studyProposalSchema = z.looseObject({
  id: z.string(),
  part_id: z.string(),
  type: z.enum(STUDY_TYPES),
  title: z.string(),
  why: z.string(),
  settings: z.record(z.string(), z.unknown()),
  source: z.enum(["agent", "rules"]).catch("rules"),
});
export type StudyProposal = z.infer<typeof studyProposalSchema>;

export const contextPartSchema = z.looseObject({
  part_id: z.string(), label: z.string(), kind: z.string(), built: z.boolean(),
  material: z.string().nullish(), mass_g: num.catch(null), mass_source: z.string().nullish(), mass_note: z.string().nullish(),
});
export type ContextPart = z.infer<typeof contextPartSchema>;

export const contextStudySchema = z.looseObject({
  sim_id: z.string().nullish(), part_id: z.string().nullish(), type: z.string().nullish(), status: z.string().nullish(),
  stale: z.boolean().catch(false), verdict: z.enum(["pass", "warn", "fail"]).nullish().catch(null),
  fidelity_note: z.string().nullish(), summary: z.string().catch(""),
});

export const contextSolverSchema = z.looseObject({
  id: z.string(), name: z.string(), available: z.boolean(), types: z.array(z.string()).catch([]),
  accuracy: z.string().nullish(), validated_band: z.record(z.string(), z.number()).nullish().catch(null),
});
export type ContextSolver = z.infer<typeof contextSolverSchema>;

export const studiesContextSchema = z.looseObject({
  project_id: z.string(),
  build_name: z.string().nullish(),
  default_material: z.string().nullish(),
  parts: z.array(contextPartSchema),
  assembly: z.looseObject({ instances: z.number(), total_mass_g: num.optional() }).nullish(),
  spec: z.looseObject({ requirements: z.array(z.looseObject({ id: z.string(), kind: z.string() })).catch([]) }).nullish(),
  studies: z.array(contextStudySchema).catch([]),
  studies_total: z.number().catch(0),
  solvers: z.array(contextSolverSchema).catch([]),
  /** Per-item catch: one proposal from a newer core that does not parse never hides the others. */
  proposals: z.array(z.unknown()).transform((xs) => xs.flatMap((x) => {
    const p = studyProposalSchema.safeParse(x);
    return p.success ? [p.data] : [];
  })),
  proposals_source: z.enum(["agent", "rules"]).catch("rules"),
  proposals_run_id: z.string().nullish(),
});
export type StudiesContext = z.infer<typeof studiesContextSchema>;
