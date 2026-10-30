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

/** A comment pinned on the model. */
export interface FeedbackBody { part_id: string; point: Vec3; face_id?: string; text: string }
/** The run the pin went to (`started`: a new run was made for it). */
export interface FeedbackResult { run_id: string; step_id: number | null; started?: boolean }
export const feedbackResultSchema = z.object({ run_id: z.string(), step_id: z.number().nullable(), started: z.boolean().optional() });
