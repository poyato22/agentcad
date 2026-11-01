// Assemblies (core server/assembly_api.py, assembly/{model,solve}.py): types + zod schemas for the
// /projects/{id}/assembly routes. Kept apart from types.ts/schemas.ts like sims.ts.
//
// Core rulings this follows (Plan 5 C2):
// - GET /assembly returns each instance's SOLVED world transform (the assembled pose).
// - A joint frame's local Z is its `axis`; local X is a deterministic perpendicular.
// - `value` is polymorphic by joint kind: revolute deg, slider mm, cylindrical [deg, mm],
//   ball [rx, ry, rz] deg; fixed ignores it. `limits` [lo, hi] clamp the scalar and each component.
// Transforms are 4x4 row-major, mm, in the CAD frame (Z up).
import { z } from "zod";
import { appearanceSchema, partMetricsSchema } from "./schemas";

export const JOINT_KINDS = ["fixed", "revolute", "slider", "cylindrical", "ball"] as const;
export type JointKind = (typeof JOINT_KINDS)[number];

export type Mat4 = number[][];
export type JointValue = number | number[];

const vec3 = z.tuple([z.number(), z.number(), z.number()]);
const mat4 = z.array(z.array(z.number()).length(4)).length(4);

export const frameSchema = z.object({
  origin: vec3.nullish(), axis: vec3.nullish(), face_id: z.string().nullish(),
});
export type Frame = z.infer<typeof frameSchema>;

export const jointRefSchema = z.object({ instance_id: z.string(), frame: frameSchema });
export type JointRef = z.infer<typeof jointRefSchema>;

export const instanceSchema = z.object({
  id: z.string(), part_id: z.string(), name: z.string(), transform: mat4, fixed: z.boolean(),
});
export type Instance = z.infer<typeof instanceSchema>;

export const jointSchema = z.object({
  id: z.string(), kind: z.enum(JOINT_KINDS), a: jointRefSchema, b: jointRefSchema,
  value: z.union([z.number(), z.array(z.number())]), limits: z.tuple([z.number(), z.number()]).nullable(),
});
export type Joint = z.infer<typeof jointSchema>;

export const assemblySchema = z.object({
  instances: z.array(instanceSchema), joints: z.array(jointSchema),
  dof_remaining: z.number().default(0), overconstrained: z.array(z.array(z.string())).default([]),
  // Plan 9 B2: instances x part mass; parts with no known mass are listed (older cores: absent)
  total_mass_g: z.number().nullish().catch(null),
  mass_unknown: z.array(z.object({ part_id: z.string(), count: z.number(), reason: z.string() })).catch([]).default([]),
});
export type Assembly = z.infer<typeof assemblySchema>;

export const driveResultSchema = z.object({
  applied: z.union([z.number(), z.array(z.number())]), transforms: z.record(z.string(), mat4),
});
export type DriveResult = z.infer<typeof driveResultSchema>;

/** `a` / `b` are instance NAMES (the worker labels shapes by name, falling back to the id).
 *  G2 (core assembly/interference.py, the list assembly_health reports): the instance ids, where
 *  the overlap is (world mm) and, for a declared contact, `intended` (its reason). */
export const interferenceSchema = z.object({
  a: z.string(), b: z.string(), volume_mm3: z.number(),
  a_id: z.string().optional(), b_id: z.string().optional(),
  intended: z.string().nullable().optional().catch(null),
  center: vec3.optional().catch(undefined),
  bbox: z.object({ min: vec3, max: vec3 }).optional().catch(undefined),
});
export type Interference = z.infer<typeof interferenceSchema>;

export const instanceDeletedSchema = z.object({ removed_instance: z.string(), removed_joints: z.array(z.string()) });
export const jointDeletedSchema = z.object({ removed_joint: z.string() });

/** A build's part row as the assembly needs it (GET /parts?project_id=). Lenient on purpose: the
 *  part tree fields are Plan 5 C1's, and older rows carry only part_id/vars/metrics/mtime. */
export const assemblyPartSchema = z.object({
  part_id: z.string(), label: z.string().nullish(), color: z.string().nullish(), kind: z.string().nullish(),
  metrics: partMetricsSchema.nullable(), mtime: z.number().nullish().transform((v) => v ?? 0),   // the layout skeleton row has none (G3): one row must not fail the list
  // U8 finish, so Assembly view draws a polished part shiny as the Part viewer does (I5)
  appearance: appearanceSchema.nullish().catch(null),
});
export type AssemblyPart = z.infer<typeof assemblyPartSchema>;

/** The slice of GET /projects the assembly needs to find the active build. */
export const projectRowSchema = z.object({
  id: z.string(), name: z.string(), created_at: z.number(), trashed_at: z.number().nullable(),
});
export type ProjectRow = z.infer<typeof projectRowSchema>;

export interface NewInstanceBody { part_id: string; name: string; transform?: Mat4; fixed?: boolean }
export interface InstancePatch { name?: string; transform?: Mat4; fixed?: boolean }
export interface NewJointBody { kind: JointKind; a: JointRef; b: JointRef; value?: JointValue; limits?: [number, number] | null }
export interface JointPatch { kind?: JointKind; value?: JointValue; limits?: [number, number] }
