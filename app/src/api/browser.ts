// Agent browser snapshots, part cards and the build parts list (BOM): types + zod schemas for the
// Plan 7 (R3/R4/R5) routes the core (U4c) serves on top of the C5 hardened fetcher.
//   · GET /runs/{id}/browser          → the run's page snapshots, in visit order
//   · GET /browser/snapshots/{sid}    → one snapshot
//   · GET /projects/{id}/bom          → the build's parts list plus totals
//   · POST/PATCH/DELETE /projects/{id}/bom[/{lid}]
//   · GET /projects/{id}/bom.csv
//   · the `part_show` tool's input (R4) → a part card in the thread
//
// Kept apart from types.ts/schemas.ts so the browser/BOM additions stay in one place.
//
// Untrusted text: a snapshot's title, URL and text, a table's cells, a part card's name/vendor/
// specs, and every BOM field typed by an agent are page- or model-authored. The UI renders them as
// plain escaped text only, and images load ONLY through the core's proxy path (never a raw URL).
import { z } from "zod";

// ---- helpers

/** A core-relative proxy path: same-origin, starts with a single "/", no scheme. Images and CSV
 *  are fetched only through these (never a page-supplied absolute URL). */
export function isProxyPath(u: unknown): u is string {
  return typeof u === "string" && u.startsWith("/") && !u.startsWith("//") && !/^\/\/|:\/\//.test(u);
}
const proxyPath = z.string().refine(isProxyPath, "must be a core-relative proxy path");

// ---- agent browser (R3)

/** A proxied image on a snapshot. `proxy_url` is a core path (GET through the client); a raw URL is
 *  dropped at parse time so the app can never fetch a page's image host directly. */
export interface SnapshotImage { proxy_url: string; alt: string | null; width: number | null; height: number | null }
export const snapshotImageSchema = z.object({
  proxy_url: proxyPath,
  alt: z.string().nullish().transform((v) => v ?? null),
  width: z.number().nullish().transform((v) => v ?? null),
  height: z.number().nullish().transform((v) => v ?? null),
});
// a bad/absent proxy_url drops just that image, never the whole snapshot
const imageList = z.array(z.unknown()).default([]).transform((xs) =>
  xs.map((x) => snapshotImageSchema.safeParse(x)).filter((r) => r.success).map((r) => r.data));

/** An extracted table. Headers may be empty (an unlabelled grid). Cells are untrusted text. */
export interface SnapshotTable { headers: string[]; rows: string[][] }
export const snapshotTableSchema = z.object({
  headers: z.array(z.string()).default([]),
  rows: z.array(z.array(z.string())).default([]),
}).or(z.array(z.array(z.string())).transform((rows) => ({ headers: [], rows })));
const tableList = z.array(z.unknown()).default([]).transform((xs) =>
  xs.map((x) => snapshotTableSchema.safeParse(x)).filter((r) => r.success).map((r) => r.data));

/** One stored page snapshot (web_open). Everything here is untrusted data. */
export interface BrowserSnapshot {
  id: string; url: string; title: string; text: string;
  tables: SnapshotTable[]; images: SnapshotImage[]; fetched_at: number;
}
export const browserSnapshotSchema = z.object({
  id: z.string(),
  url: z.string().default(""),
  title: z.string().default(""),
  text: z.string().default(""),
  tables: tableList,
  images: imageList,
  fetched_at: z.number().default(0),
});

/** GET /runs/{id}/browser: the visit list. Accepts a bare array or `{snapshots: [...]}`; a
 *  malformed snapshot is dropped, not the page. */
export const browserListSchema: z.ZodType<BrowserSnapshot[]> = z.union([
  z.array(z.unknown()),
  z.looseObject({ snapshots: z.array(z.unknown()) }).transform((o) => o.snapshots),
]).transform((xs) => xs.map((x) => browserSnapshotSchema.safeParse(x)).filter((r) => r.success).map((r) => r.data));

// ---- part card (R4): the `part_show` tool input

/** One spec row on a part card or BOM line. */
export interface PartSpec { key: string; value: string | number; unit: string | null }
export const partSpecSchema = z.object({
  key: z.string(),
  value: z.union([z.string(), z.number()]),
  unit: z.string().nullish().transform((v) => v ?? null),
});
const specList = z.array(z.unknown()).default([]).transform((xs) =>
  xs.map((x) => partSpecSchema.safeParse(x)).filter((r) => r.success).map((r) => r.data));

/** A part the agent put in front of the user (`part_show`). `image` is a core proxy path or null
 *  (a raw URL is dropped). `catalog_id` present ⇒ the part is catalog-backed and can be inserted. */
export interface PartShown {
  name: string; price: number | null; currency: string | null; vendor: string | null;
  url: string | null; specs: PartSpec[]; image: string | null;
  snapshot_id: string | null; catalog_id: string | null;
}
export const partShownSchema = z.object({
  name: z.string().default(""),
  price: z.number().nullish().transform((v) => v ?? null),
  currency: z.string().nullish().transform((v) => v ?? null),
  vendor: z.string().nullish().transform((v) => v ?? null),
  url: z.string().nullish().transform((v) => v ?? null),
  specs: specList,
  // only a core proxy path is kept, so the card can never pull an image from a page's host
  image: z.unknown().optional().transform((v) => (isProxyPath(v) ? v : null)),
  snapshot_id: z.string().nullish().transform((v) => v ?? null),
  catalog_id: z.string().nullish().transform((v) => v ?? null),
});

// ---- parts list / BOM (R5)

export type BomKind = "made" | "bought";
export type BomSource = "user" | "agent";

export interface BomLine {
  id: string; kind: BomKind; part_id: string | null; catalog_id: string | null;
  name: string; qty: number; unit_cost: number | null; currency: string;
  vendor: string | null; url: string | null; specs: PartSpec[]; notes: string;
  source: BomSource;
  /** A made part's cost is estimated from mass × material $/kg (shown with an "estimate" label).
   *  Defaults from `kind` when the core doesn't send it. */
  estimated: boolean;
}
export const bomLineSchema = z.object({
  id: z.string(),
  kind: z.enum(["made", "bought"]).catch("bought"),
  part_id: z.string().nullish().transform((v) => v ?? null),
  catalog_id: z.string().nullish().transform((v) => v ?? null),
  name: z.string().default(""),
  qty: z.number().default(1),
  unit_cost: z.number().nullish().transform((v) => v ?? null),
  currency: z.string().default("USD"),
  vendor: z.string().nullish().transform((v) => v ?? null),
  url: z.string().nullish().transform((v) => v ?? null),
  specs: specList,
  notes: z.string().default(""),
  source: z.enum(["user", "agent"]).catch("agent"),
  estimated: z.boolean().optional(),
}).transform((l) => ({ ...l, estimated: l.estimated ?? l.kind === "made" }));

/** A per-vendor subtotal. `null` vendor groups the parts without one. */
export interface VendorTotal { vendor: string | null; amount: number; currency: string }
const vendorTotalSchema = z.object({
  vendor: z.string().nullish().transform((v) => v ?? null),
  amount: z.number().default(0),
  currency: z.string().default("USD"),
});

/** GET /projects/{id}/bom totals: the grand total, per-vendor subtotals and the made/bought split.
 *  `estimated` flags that at least one line's cost is an estimate (made parts). */
export interface BomTotals {
  amount: number; currency: string;
  by_vendor: VendorTotal[]; made: number; bought: number; estimated: boolean;
}
const bomTotalsSchema = z.object({
  amount: z.number().default(0),
  currency: z.string().default("USD"),
  by_vendor: z.array(z.unknown()).default([]).transform((xs) =>
    xs.map((x) => vendorTotalSchema.safeParse(x)).filter((r) => r.success).map((r) => r.data)),
  made: z.number().default(0),
  bought: z.number().default(0),
  estimated: z.boolean().default(false),
});

export interface Bom { lines: BomLine[]; totals: BomTotals }
/** GET /projects/{id}/bom. Accepts `{lines|items, totals}`; a malformed line is dropped. */
export const bomSchema: z.ZodType<Bom> = z.looseObject({
  lines: z.array(z.unknown()).optional(),
  items: z.array(z.unknown()).optional(),
  totals: z.unknown().optional(),
}).transform((o) => {
  const raw = o.lines ?? o.items ?? [];
  const lines = raw.map((x) => bomLineSchema.safeParse(x)).filter((r) => r.success).map((r) => r.data);
  const totals = bomTotalsSchema.safeParse(o.totals ?? {});
  return { lines, totals: totals.success ? totals.data : bomTotalsSchema.parse({}) };
});

/** POST /projects/{id}/bom body: add a bought line by hand, or a catalog/shown part. */
export interface NewBomLine {
  kind: BomKind; name: string; qty?: number; unit_cost?: number | null; currency?: string;
  vendor?: string | null; url?: string | null; specs?: PartSpec[]; notes?: string;
  catalog_id?: string | null; part_id?: string | null; snapshot_id?: string | null; source?: BomSource;
}
/** PATCH /projects/{id}/bom/{lid} body: the user's edits (qty, cost, notes, …). */
export interface BomPatch {
  qty?: number; unit_cost?: number | null; notes?: string; name?: string;
  vendor?: string | null; currency?: string;
}

// ---- suggested sites (profile; R3 preset list)

/** A curated, user-editable site the agent browses without an approval card. `search_url` is a
 *  template with `{q}` where the query goes. `suggested` marks a built-in default. */
export interface SuggestedSite { name: string; domain: string; search_url: string; categories: string[]; suggested: boolean }
export const suggestedSiteSchema = z.object({
  name: z.string().default(""),
  domain: z.string().default(""),
  search_url: z.string().default(""),
  categories: z.array(z.string()).default([]),
  suggested: z.boolean().default(false),
});
export const suggestedSitesSchema = z.array(z.unknown()).default([]).transform((xs) =>
  xs.map((x) => suggestedSiteSchema.safeParse(x)).filter((r) => r.success).map((r) => r.data));
