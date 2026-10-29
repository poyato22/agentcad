// Onshape browser, live budget and parts directory (core server/onshape_api.py, catalog_api.py,
// onshape/browse.py, catalog/*): types + zod schemas for the /onshape/* browse routes and the
// /catalog/* routes. Kept apart from types.ts/schemas.ts so the Onshape and Directory pages'
// additions stay in one place.
//
// Untrusted text: document names, owners, element names and everything in a URL-imported item
// (name, vendor, specs, raw_text) is user- or page-authored. The UI renders it as plain text only.
import { z } from "zod";

// ---- Onshape

/** GET /onshape/documents row. `thumbnail_url` is a core-relative proxied path (or null). */
export interface OnshapeDoc {
  did: string; name: string; modified_at: string | null; owner: string;
  thumbnail_url: string | null; default_wid: string | null;
}
export const onshapeDocSchema = z.object({
  did: z.string(), name: z.string(), modified_at: z.string().nullable(), owner: z.string(),
  thumbnail_url: z.string().nullable(), default_wid: z.string().nullable(),
});

export type OnshapeElementType = "partstudio" | "assembly";
export interface OnshapeElement { eid: string; name: string; type: OnshapeElementType; microversion: string | null }
export const onshapeElementSchema = z.object({
  eid: z.string(), name: z.string(), type: z.enum(["partstudio", "assembly"]), microversion: z.string().nullable(),
});

/** GET /onshape/budget and the `onshape_budget` event data. `used_run` is that run's calls, or with
 *  no run the app's own browsing since the core started; `reset_at` is epoch seconds (1 January). */
export interface OnshapeBudget {
  used_year: number; limit_year: number; used_run: number; warn_at: number; reset_at: number; run_id: string | null;
}
export const onshapeBudgetSchema = z.object({
  used_year: z.number(), limit_year: z.number(), used_run: z.number(), warn_at: z.number(), reset_at: z.number(),
  run_id: z.string().nullable().default(null),
});

/** Onshape's page size maximum (core browse.PAGE_MAX); a shorter page is the last one. */
export const ONSHAPE_PAGE = 20;

/** POST /onshape/import and POST /catalog/items/{id}/insert: the part made (or found). */
export interface PartResult {
  part_id: string; existing: boolean;
  metrics: { mass_g?: number; mass_source?: MassSource; mass_note?: string; bbox?: { size?: number[] } } & Record<string, unknown> | null;
  meta: Record<string, unknown> | null;
}
export const partResultSchema = z.object({
  part_id: z.string(), existing: z.boolean(),
  metrics: z.looseObject({
    mass_g: z.number().optional(), mass_source: z.enum(["catalog", "estimate"]).optional(), mass_note: z.string().optional(),
    bbox: z.looseObject({ size: z.array(z.number()).optional() }).optional(),
  }).nullable(),
  meta: z.record(z.string(), z.unknown()).nullable(),
});

// ---- Parts directory (catalog)

export type MassSource = "catalog" | "estimate";
export type CatalogSource = "builtin" | "url";

export interface CatalogSpec { key: string; value: string | number; unit: string | null; note?: string | null }
export const catalogSpecSchema = z.object({
  key: z.string(), value: z.union([z.string(), z.number()]), unit: z.string().nullable().default(null), note: z.string().nullish(),
});

/** `hole_d`: the hole in the part (nominal for a tapped hole). `layout`: "opposite" (pairs across a
 *  circle of the pattern's diameter, motors), "square" / "rect" (corners), "inline" (two holes);
 *  `holes_mm` (boards) are explicit positions and win over the layout. */
export interface CatalogMount {
  pattern: string | null; hole_d: number | null; spacing_mm: number[] | null;
  thread: string | null; layout: string | null; holes_mm: [number, number][] | null;
}
export const catalogMountSchema = z.object({
  pattern: z.string().nullish().transform((v) => v ?? null),
  hole_d: z.number().nullish().transform((v) => v ?? null),
  spacing_mm: z.array(z.number()).nullish().transform((v) => v ?? null),
  thread: z.string().nullish().transform((v) => v ?? null),
  layout: z.string().nullish().transform((v) => v ?? null),
  holes_mm: z.array(z.tuple([z.number(), z.number()])).nullish().transform((v) => v ?? null),
});

export interface CatalogSourceRef { title: string; url: string | null; note: string | null }
const sourceRefSchema = z.object({
  title: z.string().default(""), url: z.string().nullish().transform((v) => v ?? null), note: z.string().nullish().transform((v) => v ?? null),
});

export interface CatalogItem {
  id: string; name: string; category: string; source: CatalogSource; url: string | null; vendor: string | null;
  specs: CatalogSpec[]; dims: Record<string, number>; mount: CatalogMount | null; geometry: string | null;
  /** "catalog": a stated mass (dims.mass_g); "estimate": computed from volume × density on insert. */
  mass_source: MassSource;
  sources: CatalogSourceRef[]; project_id: string | null;
  /** URL items, GET /catalog/items/{id} and POST /catalog/import only: the page text (untrusted). */
  raw_text?: string;
  /** POST /catalog/import: the URL was already in the directory (returned unchanged). */
  existing?: boolean;
  edited_by_user?: boolean;
}
export const catalogItemSchema = z.object({
  id: z.string(), name: z.string(), category: z.string(), source: z.enum(["builtin", "url"]),
  url: z.string().nullish().transform((v) => v ?? null), vendor: z.string().nullish().transform((v) => v ?? null),
  specs: z.array(catalogSpecSchema).default([]), dims: z.record(z.string(), z.number()).default({}),
  mount: catalogMountSchema.nullish().transform((v) => v ?? null), geometry: z.string().nullish().transform((v) => v ?? null),
  // a missing mass_source is shown as an estimate: never claim a catalog mass we weren't told about
  mass_source: z.enum(["catalog", "estimate"]).catch("estimate"),
  sources: z.array(sourceRefSchema).default([]), project_id: z.string().nullish().transform((v) => v ?? null),
  raw_text: z.string().optional(), existing: z.boolean().optional(), edited_by_user: z.boolean().optional(),
});

/** PATCH /catalog/items/{id}: the user's review edits (core catalog.ItemEdit). */
export interface CatalogEdit {
  name?: string; category?: string; vendor?: string | null; specs?: CatalogSpec[];
  dims?: Record<string, number>; mount?: Partial<CatalogMount> | null;
}

/** GET /projects row, only the fields these pages use (the build pickers). */
export interface BuildRef { id: string; name: string; trashed_at: number | null }
export const buildRefSchema = z.object({ id: z.string(), name: z.string(), trashed_at: z.number().nullable().default(null) });

/** The one field of GET /profile the build pickers need (C1's active build). */
export const activeBuildSchema = z.object({ active_project_id: z.string().nullish().transform((v) => v ?? null) });
