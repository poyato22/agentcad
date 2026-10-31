// Plan 9 D4 (B3): an agent's background jobs — studies (sim_…), design reviews (review_…) and
// design studies (study_…) that keep running after the agent's turn has ended. GET /jobs lists
// the running ones; POST /jobs/{id}/cancel stops one (core server/sim_api.py, jobs.py). A job the
// user stops sends its agent no turn of its own. Labels are core text: render them as text.
import { z } from "zod";

export type JobKind = "sim" | "design_review" | "design_study" | "topopt" | (string & {});

export const backgroundJobSchema = z.looseObject({
  job_id: z.string(),
  kind: z.string(),
  run_id: z.string().nullable(),
  build_id: z.string().nullable(),
  part_id: z.string().nullable().optional(),
  label: z.string().catch(""),
  status: z.string(),
  started_at: z.number(),
  cancel_url: z.string(),
});
export type BackgroundJob = z.infer<typeof backgroundJobSchema>;

export const jobCancelSchema = z.looseObject({ job_id: z.string(), status: z.literal("cancelling") });
export type JobCancel = z.infer<typeof jobCancelSchema>;

/** The background job a study card stands for: its sim / review / study / generative-design id
 *  (from the tool result `{sim_id | review_id | study_id | topopt_id, status}`), or null. */
export function jobIdOf(data: Record<string, unknown> | null | undefined): string | null {
  if (!data) return null;
  for (const k of ["sim_id", "review_id", "study_id", "topopt_id"] as const) {
    const v = data[k];
    if (typeof v === "string" && /^(sim_|review_|study_|to_)[0-9a-f]{12}$/.test(v)) return v;
  }
  return null;
}
