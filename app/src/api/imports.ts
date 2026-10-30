// Drawing import (core server/imports_api.py + imports/drawing_import.py): POST /imports/drawing
// (multipart), GET /imports/{id}, GET /imports/{id}/original. Kept apart from types.ts/schemas.ts.
//
// Untrusted text: `file_name`, `notes` and each row's `text` come from the user's drawing (or a
// model that read it). The UI renders them as plain text only.
import { z } from "zod";

export type ImportKind = "pdf" | "png" | "jpeg" | "dxf";
/** reading → building → (reading again while the agent fixes a mismatch) → done | failed */
export type ImportStatus = "reading" | "building" | "done" | "failed";
export type RowStatus = "match" | "mismatch" | "missing" | "unchecked";

/** One row of the dimension match table: what the drawing states vs the built part (mm, or
 *  degrees for an angle). A "callout" row is a hole note ("4x Ø6 THRU") found or not. */
export interface MatchRow {
  id: string; kind: string; text: string; expected: number | null; measured: number | null;
  tol_plus: number | null; tol_minus: number | null; status: RowStatus; source: string | null;
  /** PDF imports with a text layer: whether the stated value is a number the drawing prints. */
  evidence?: "printed" | "not printed";
}
/** What the table proves: "drawing" (a DXF: its dimensions vs its geometry) or "reading" (a PDF /
 *  image: the built part vs the model's own reading of the drawing). */
export type ImportBasis = "drawing" | "reading";
/** Independent check from a vector PDF's printed numbers. */
export interface TextLayerCheck {
  available: boolean; note?: string; ok?: boolean;
  supported?: string[]; unsupported?: string[]; built_unsupported?: string[];
}
/** A second opinion: the model compared the original with Mises's drawing of the part. */
export interface SecondLook {
  status: "ok" | "issues" | "unavailable"; note?: string; summary?: string;
  mismatches?: { feature: string; drawing: string; part: string }[];
}
export interface MatchTable {
  rows: MatchRow[]; matches: number; mismatches: number; missing: number; unchecked: number; ok: boolean;
}
export interface DrawingImport {
  import_id: string; kind: ImportKind; file_name: string; status: ImportStatus; stage: string;
  error: string | null; project_id: string | null; part_id: string | null; run_id: string | null;
  attempts: number; max_attempts: number; table: MatchTable | null; result: "match" | "mismatch" | null;
  notes: string[]; pages: number; original_url: string; drawing_url: string | null;
  basis: ImportBasis; checks: { text_layer: TextLayerCheck | null; second_look: SecondLook | null };
}

const num = z.number().nullable().default(null);
export const matchRowSchema = z.object({
  id: z.string(), kind: z.string(), text: z.string().default(""), expected: num, measured: num,
  tol_plus: num, tol_minus: num,
  status: z.enum(["match", "mismatch", "missing", "unchecked"]), source: z.string().nullable().default(null),
  evidence: z.enum(["printed", "not printed"]).optional(),
});
const strs = z.array(z.string()).optional();
export const textLayerCheckSchema = z.object({
  available: z.boolean(), note: z.string().optional(), ok: z.boolean().optional(),
  supported: strs, unsupported: strs, built_unsupported: strs,
});
export const secondLookSchema = z.object({
  status: z.enum(["ok", "issues", "unavailable"]), note: z.string().optional(), summary: z.string().optional(),
  mismatches: z.array(z.object({ feature: z.string(), drawing: z.string(), part: z.string() })).optional(),
});
export const matchTableSchema = z.object({
  rows: z.array(matchRowSchema), matches: z.number(), mismatches: z.number(), missing: z.number(),
  unchecked: z.number(), ok: z.boolean(),
});
export const drawingImportSchema: z.ZodType<DrawingImport> = z.object({
  import_id: z.string(), kind: z.enum(["pdf", "png", "jpeg", "dxf"]), file_name: z.string(),
  status: z.enum(["reading", "building", "done", "failed"]), stage: z.string().default(""),
  error: z.string().nullable().default(null), project_id: z.string().nullable().default(null),
  part_id: z.string().nullable().default(null), run_id: z.string().nullable().default(null),
  attempts: z.number(), max_attempts: z.number(), table: matchTableSchema.nullable().default(null),
  result: z.enum(["match", "mismatch"]).nullable().default(null), notes: z.array(z.string()).default([]),
  pages: z.number().default(0), original_url: z.string(), drawing_url: z.string().nullable().default(null),
  basis: z.enum(["drawing", "reading"]),
  checks: z.object({
    text_layer: textLayerCheckSchema.nullable().default(null), second_look: secondLookSchema.nullable().default(null),
  }).default({ text_layer: null, second_look: null }),
});

/** Accepted by the core (sniffed by content there; the extension is only a first check here). */
export const DRAWING_EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg", ".dxf"] as const;
export const MAX_DRAWING_BYTES = 20 * 1024 * 1024;
