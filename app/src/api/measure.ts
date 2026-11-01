// Plan 7 U2a: the viewer's measure (POST /parts/{id}/measure, core server/measure_api.py) and
// feedback pins (POST /projects/{id}/feedback). Measure values are the core's exact B-rep numbers
// (cad/worker.py --measure), never mesh math.
import { z } from "zod";
import type { Vec3 } from "./types";

const vec3 = z.tuple([z.number(), z.number(), z.number()]);

/** One pick: a face or edge by its metrics id, a vertex, or a point on the part (mm). */
export type MeasureRef = { face_id: string } | { edge_id: string } | { vertex_id: string } | { point: Vec3 };

/** One measured number: "Diameter 4.2 mm". */
export interface MeasureValue { key: string; label: string; value: number; unit: string }
export const measureValueSchema = z.object({ key: z.string(), label: z.string(), value: z.number(), unit: z.string() });

/** What a ref resolved to on the exact model (only the fields its kind has). */
export interface MeasureItem {
  kind: "face" | "edge" | "vertex" | "point"; id?: string; type?: string;
  area?: number; length?: number; radius?: number; arc_deg?: number;
  center?: Vec3; normal?: Vec3; axis?: Vec3; axis_point?: Vec3; start?: Vec3; end?: Vec3; direction?: Vec3; point?: Vec3;
}
export const measureItemSchema = z.object({
  kind: z.enum(["face", "edge", "vertex", "point"]), id: z.string().optional(), type: z.string().optional(),
  area: z.number().optional(), length: z.number().optional(), radius: z.number().optional(), arc_deg: z.number().optional(),
  center: vec3.optional(), normal: vec3.optional(), axis: vec3.optional(), axis_point: vec3.optional(),
  start: vec3.optional(), end: vec3.optional(), direction: vec3.optional(), point: vec3.optional(),
});

export interface MeasureResult { part_id: string; version: number; items: MeasureItem[]; values: MeasureValue[] }
export const measureResultSchema = z.object({
  part_id: z.string(), version: z.number(), items: z.array(measureItemSchema), values: z.array(measureValueSchema),
});

/** Measure mode opened: the core loads the part in its warm measure worker ahead of the first pick. */
export const measureWarmSchema = z.object({ part_id: z.string(), warm: z.boolean() });

/** A comment pinned on the model. */
export interface FeedbackBody {
  part_id: string; point: Vec3; face_id?: string; text: string;
  /** Plan 10 E35: a pin dropped in the assembly view: the instance and the point in world mm
   *  (`point` stays in the part's own frame). */
  instance_id?: string; world_point?: Vec3;
}
/** The build's lead the pin went to (core orchestrator/lead.submit_feedback, U3c): `step_id` is
 *  the stored pin (a decision step {feedback}); `queued` when the lead was busy. */
export interface FeedbackResult { run_id: string; step_id: number | null; queued?: boolean; position?: number | null }
export const feedbackResultSchema = z.object({
  run_id: z.string(), step_id: z.number().nullable(), queued: z.boolean().optional(), position: z.number().nullish(),
});

/** A pin as the core stores it: `feedback` on the decision step the route returns, on the user's
 *  message step once the lead takes it, and on a queued message meanwhile. `part` (the tree label)
 *  and `nearby_hole` ("Ø4.2 hole, 1.9 mm away") are there from U2a's merge on. */
export interface FeedbackPin {
  part_id: string; point: Vec3; text: string; face_id?: string | null; part?: string | null; nearby_hole?: string | null;
  nearby_feature?: { face_id?: string | null; type?: string | null; area_mm2?: number | null; center?: Vec3 | null } | null;
  /** Plan 10 E35: an assembly pin's instance (id, name) and world point. */
  instance_id?: string | null; instance?: string | null; world_point?: Vec3 | null;
}
export const feedbackPinSchema = z.object({
  part_id: z.string(), point: vec3, text: z.string(), face_id: z.string().nullish(), part: z.string().nullish(), nearby_hole: z.string().nullish(),
  nearby_feature: z.object({ face_id: z.string().nullish(), type: z.string().nullish(), area_mm2: z.number().nullish(), center: vec3.nullish() }).nullish(),
  instance_id: z.string().nullish(), instance: z.string().nullish(), world_point: vec3.nullish(),
});

/** The pin on a step's payload, if it carries a well-formed one. */
export function pinOf(payload: Record<string, unknown>): FeedbackPin | null {
  const p = feedbackPinSchema.safeParse(payload["feedback"]);
  return p.success ? p.data : null;
}
