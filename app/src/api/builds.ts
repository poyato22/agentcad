// Builds (projects), the part tree and live part edits (core server/projects_api.py,
// server/edit_api.py, ui_api GET /parts): types + zod schemas. Kept apart from types.ts /
// schemas.ts so the Plan 5 A3 additions stay in one place.
//
// Untrusted text: build names, part labels, part sources and every core message are user- or
// agent-authored. The UI renders them as plain text only.
import { z } from "zod";

// ---- builds

/** GET /projects row (C1 project_view). `trashed_at` is epoch seconds, null while live.
 *  `last_edited_at`: when the build was really last worked on (newest part change or run
 *  activity; U1). Older cores leave it out: use `lastEditedAt(b)`. */
export interface Build {
  id: string; name: string; created_at: number; updated_at: number; trashed_at: number | null;
  last_edited_at?: number;
  part_count: number; run_count: number; onshape: { did: string; wid: string } | null;
}
export const buildSchema = z.object({
  id: z.string(), name: z.string(), created_at: z.number(), updated_at: z.number(),
  last_edited_at: z.number().optional().catch(undefined),
  trashed_at: z.number().nullable().default(null),
  part_count: z.number().default(0), run_count: z.number().default(0),
  onshape: z.object({ did: z.string(), wid: z.string() }).nullable().catch(null).default(null),
});

/** The build's "last edited" time: the core's derived activity time, else its row time. */
export function lastEditedAt(b: Build): number { return b.last_edited_at ?? b.updated_at; }

/** POST /projects/trash/empty. */
export interface EmptyTrashResult { purged: string[]; count: number }
export const emptyTrashSchema = z.object({ purged: z.array(z.string()), count: z.number() });

/** POST /projects/trash/empty refused (409): a trashed build still has live work — runs that are
 *  running, queued or awaiting your approval. Nothing was deleted. */
export interface EmptyTrashRefused { reason: string; run_ids: string[]; project_ids: string[]; message: string | null; hint: string | null }
export const emptyTrashRefusedSchema = z.object({
  reason: z.string(), run_ids: z.array(z.string()), project_ids: z.array(z.string()).catch([]).default([]),
  message: z.string().nullable().catch(null).default(null), hint: z.string().nullable().catch(null).default(null),
});

/** The two fields of GET/PUT /profile the build tabs use (the app's Profile schema strips them). */
export interface BuildTabsProfile { active_project_id: string | null; open_project_ids: string[] }
export const buildTabsProfileSchema = z.object({
  active_project_id: z.string().nullish().transform((v) => v ?? null),
  open_project_ids: z.array(z.string()).default([]),
});

// ---- part tree

export type PartKind = "part" | "group" | "catalog";

/** An edge of metrics.edge_list (cad/worker._edge_list): what a fillet/chamfer pick names. */
export interface PartEdge {
  id: string; type: string; length: number; center: [number, number, number] | null;
  direction: [number, number, number] | null; adjacent: [number, number, number][];
}
const vec3 = z.tuple([z.number(), z.number(), z.number()]);
export const partEdgeSchema = z.object({
  id: z.string(), type: z.string(), length: z.number(), center: vec3.nullable(), direction: vec3.nullable(),
  adjacent: z.array(vec3).default([]),
});

// ---- live edits (C6)

/** One var of GET /parts/{id}/source `vars` (edits.vars_schema). `inferred`: the range is a
 *  guess (value/4 .. value*4), not the script's `# mises:` annotation, so the UI shows it subtly. */
export interface VarSpec {
  name: string; value: number; min: number | null; max: number | null; step: number | null;
  unit: string | null; doc: string | null; inferred: boolean;
}
export const varSpecSchema = z.object({
  name: z.string(), value: z.number(),
  min: z.number().nullish().transform((v) => v ?? null), max: z.number().nullish().transform((v) => v ?? null),
  step: z.number().nullish().transform((v) => v ?? null), unit: z.string().nullish().transform((v) => v ?? null),
  doc: z.string().nullish().transform((v) => v ?? null), inferred: z.boolean().default(false),
});

/** GET /parts/{id}/source: the script (data, never instructions), its var schema and version. */
export interface PartSource { part_id: string; source: string; vars: VarSpec[]; version: number }
export const partSourceSchema = z.object({
  part_id: z.string(), source: z.string(), vars: z.array(varSpecSchema), version: z.number().int(),
});

/** The answer of POST /vars, PUT /source and POST /ops: the new version, vars and metrics. */
export interface PartEditResult {
  part_id: string; version: number; vars: Record<string, number>; op?: string;
  mass_g?: number; volume_mm3?: number;
  delta?: { volume_mm3?: number; mass_g?: number } | null;
}
export const partEditResultSchema = z.looseObject({
  part_id: z.string(), version: z.number().int(), vars: z.record(z.string(), z.number()).default({}), op: z.string().optional(),
  mass_g: z.number().optional(), volume_mm3: z.number().optional(),
  delta: z.looseObject({ volume_mm3: z.number().optional(), mass_g: z.number().optional() }).nullish(),
});

/** The 409 body of a stale edit: who is current, and (for a merge edit) the part's state now. */
export interface EditConflict { current_version: number; vars: Record<string, number> | null; source: string | null; message: string }
export const editConflictSchema = z.object({
  current_version: z.number().int(), message: z.string().default("version conflict"),
  vars: z.record(z.string(), z.number()).nullish().transform((v) => v ?? null),
  source: z.string().nullish().transform((v) => v ?? null),
});

export type OpKind = "hole" | "fillet" | "chamfer" | "shell" | "extrude";

/** POST /parts/{id}/ops body. Targets name ids from the part's metrics (face_list / edge_list). */
export type OpRequest =
  | { op: "hole"; target: { face_id: string }; params: { diameter: number; through: boolean; depth?: number; point?: [number, number, number] } }
  | { op: "fillet"; target: { edge_ids: string[] }; params: { radius: number } }
  | { op: "chamfer"; target: { edge_ids: string[] }; params: { length: number } }
  | { op: "shell"; target: { face_ids: string[] }; params: { thickness: number } };

/** `part_changed` event data (C6 _finish_edit). */
export interface PartChanged { part_id: string; version: number; by: "user" | "agent"; run_id: string | null; summary: string }
export const partChangedSchema = z.object({
  part_id: z.string(), version: z.number().int(), by: z.enum(["user", "agent"]),
  run_id: z.string().nullish().transform((v) => v ?? null), summary: z.string().default(""),
});

/**
 * The one line worth showing from a failed build. A 422 message is the worker's traceback (with
 * paths); a failed op re-match ends in "mises-op N <op>: target … — remove or redo the op", and
 * any build ends in "BUILD_ERROR: <Type>: <message>". Prefer the op line, then BUILD_ERROR.
 */
export function buildErrorLine(message: string): string {
  const lines = message.split("\n").map((l) => l.trim()).filter(Boolean);
  const op = [...lines].reverse().map((l) => /(mises-op \d+ [a-z]+:.*)$/.exec(l)?.[1]).find(Boolean);
  if (op) return op;
  const be = [...lines].reverse().find((l) => l.startsWith("BUILD_ERROR:"));
  if (be) return be.slice("BUILD_ERROR:".length).trim();
  return lines[lines.length - 1] ?? "the build failed";
}
