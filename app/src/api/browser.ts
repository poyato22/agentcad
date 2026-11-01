// Agent browser snapshots, part cards, the build parts list (BOM) and suggested sites: types +
// zod schemas for the Plan 7 (R3/R4/R5) routes of the core (U4c: server/browser_api.py,
// browser/{snapshot,tools,sites}.py, bom/__init__.py, profile.py). Field names are the core's;
// the shapes are checked against real-core dumps (core/scripts/dump_browser_bom_fixtures.py →
// core/tests/fixtures/ui/browser, test/contract.browser.test.ts).
//   · GET /runs/{id}/browser?full=1           → the run's snapshots, oldest first
//   · GET /browser/snapshots/{sid}            → one snapshot
//   · GET /browser/cards/{card_id}            → one part card (part_show's result has ids only)
//   · GET /projects/{id}/bom                  → {project_id, material, items, totals}
//   · POST/PATCH/DELETE /projects/{id}/bom[/{item_id}], GET /projects/{id}/bom.csv
//   · GET /browser/sites/defaults, POST /browser/sites/restore_defaults
//
// The schemas are STRICT about the contract: a missing or mistyped field fails the parse, which
// the client turns into a visible CoreError ("unexpected response shape"), never silent zeros or
// a dropped row. Only safety filtering is lenient: an image whose proxy_url isn't a core path is
// dropped, so the app can never fetch a page's image host.
//
// Untrusted text: everything a snapshot, card or BOM row carries may come from a web page via an
// agent. The UI renders it as plain escaped text only.
import { z } from "zod";

// ---- helpers

/** A core-relative proxy path: same-origin, one leading "/", no scheme, no backslash, no ".."
 *  segment. Images are fetched only through these (never a page-supplied absolute URL). */
export function isProxyPath(u: unknown): u is string {
  if (typeof u !== "string" || !u.startsWith("/") || u.startsWith("//")) return false;
  if (u.includes("\\") || u.includes("://")) return false;
  return !u.split(/[?#]/)[0].split("/").some((seg) => seg === ".." || seg === ".");
}

const nstr = z.string().nullable();
/** Keeps the images whose proxy_url is a core path; drops (never fetches) anything else. */
const proxiedImages = z.array(z.object({ proxy_url: z.string(), alt: z.string().nullable().default(null) }))
  .transform((xs) => xs.filter((x) => isProxyPath(x.proxy_url)));

// ---- agent browser (R3)

export type SnapshotKind = "page" | "search";

export interface SnapshotImage { proxy_url: string; alt: string | null }
/** A table is rows of cells (the core keeps no separate header row). */
export interface SnapshotTable { rows: string[][] }
/** One result of a site search page (kind "search"). */
export interface SearchResult { title: string; url: string; snippet: string; site: string }

/** One stored page snapshot (web_open, or a web_search results page). Untrusted data. */
export interface BrowserSnapshot {
  id: string; run_id: string; kind: SnapshotKind; url: string; final_url: string; domain: string;
  title: string; text: string; fetched_at: number;
  tables: SnapshotTable[]; images: SnapshotImage[];
  /** The suggested site a search page came from (search snapshots only). */
  site: string | null;
  /** "site blocked automated access (...)" when the page was a bot wall. */
  blocked: string | null;
  results: SearchResult[];
}
const searchResultSchema = z.object({ title: z.string(), url: z.string(), snippet: z.string(), site: z.string() });
export const browserSnapshotSchema: z.ZodType<BrowserSnapshot> = z.object({
  id: z.string(), run_id: z.string(), kind: z.enum(["page", "search"]),
  url: z.string(), final_url: z.string(), domain: z.string(),
  title: z.string(), text: z.string(), fetched_at: z.number(),
  tables: z.array(z.object({ rows: z.array(z.array(z.string())) })),
  images: proxiedImages,
  site: nstr.default(null),
  blocked: nstr.default(null),
  results: z.array(searchResultSchema).default([]),
});
/** GET /runs/{id}/browser?full=1. */
export const browserListSchema = z.array(browserSnapshotSchema);

// ---- part card (R4)

/** One spec row on a part card or BOM row. */
export interface PartSpec { key: string; value: string | number; unit: string | null }
export const partSpecSchema = z.object({ key: z.string(), value: z.union([z.string(), z.number()]), unit: nstr.default(null) });

/** A part an agent put in front of the user (GET /browser/cards/{id}). `currency` null means the
 *  page didn't say (never assumed); `image` is a core proxy path or null; `project_id` is the
 *  build the card belongs to (its actions target that build). */
export interface PartCard {
  id: string; run_id: string; project_id: string | null;
  name: string; price: number | null; currency: string | null; vendor: string | null;
  url: string | null; domain: string | null; specs: PartSpec[]; image: string | null;
  snapshot_id: string | null; catalog_id: string | null; catalog_backed: boolean; created_at: number;
}
export const partCardSchema: z.ZodType<PartCard> = z.object({
  id: z.string(), run_id: z.string(), project_id: nstr,
  name: z.string(), price: z.number().nullable(), currency: nstr, vendor: nstr,
  url: nstr, domain: nstr.default(null), specs: z.array(partSpecSchema),
  // only a core proxy path is kept, so the card can never pull an image from a page's host
  image: nstr.transform((v) => (isProxyPath(v) ? v : null)),
  snapshot_id: nstr, catalog_id: nstr, catalog_backed: z.boolean(), created_at: z.number(),
});

/** `part_show`'s result data: ids only (the card's text is page data, fetched by id). */
export const partShowResultSchema = z.object({
  card_id: z.string(), catalog_id: nstr.default(null), snapshot_id: nstr.default(null),
  // P10 E15 (core C1): the part is put in the build / the parts list by itself
  part_id: nstr.default(null), item_id: nstr.default(null), bom_id: nstr.default(null),
});

// ---- parts list / BOM (R5)

export type BomKind = "made" | "bought";
/** "auto": a made part (or inserted catalog part) the build lists by itself. */
export type BomSource = "user" | "agent" | "auto";

export interface BomRow {
  id: string; kind: BomKind; part_id: string | null; catalog_id: string | null;
  name: string; qty: number; unit_cost: number | null;
  /** ISO 4217 code, or null: unknown (never assumed). */
  currency: string | null;
  vendor: string | null; url: string | null; specs: PartSpec[]; notes: string;
  source: BomSource;
  /** The cost is a live estimate (a made part: mass × material $/kg); `estimate_note` says how. */
  estimate: boolean; estimate_note: string | null;
  line_total: number | null;
  /** Plan 10 E14: the part in this build that draws it (its own part, or the catalog part an
   *  agent inserted for it), and the catalog item's geometry. Older cores: absent. */
  geometry_part_id?: string | null;
  geometry?: string | null;
}
export const bomRowSchema: z.ZodType<BomRow> = z.object({
  id: z.string(), kind: z.enum(["made", "bought"]), part_id: nstr, catalog_id: nstr,
  name: z.string(), qty: z.number().int(), unit_cost: z.number().nullable(), currency: nstr,
  vendor: nstr, url: nstr, specs: z.array(partSpecSchema), notes: z.string(),
  source: z.enum(["user", "agent", "auto"]),
  estimate: z.boolean(), estimate_note: nstr, line_total: z.number().nullable(),
  geometry_part_id: z.string().nullish(), geometry: z.string().nullish(),
});

/** Per-currency amounts ("USD", "EUR", …, and "unknown" for rows nobody gave a currency).
 *  Amounts in different currencies are never added together. */
export type ByCurrency = Record<string, number>;
const byCurrency = z.record(z.string(), z.number());

export interface VendorTotal { vendor: string | null; kind: BomKind; currency: string; total: number; count: number }
export interface BomTotals {
  by_currency: ByCurrency; made: ByCurrency; bought: ByCurrency; estimated: ByCurrency;
  has_estimates: boolean; by_vendor: VendorTotal[];
  /** Rows with no price (not in any total). */
  unpriced: number; rows: number; qty: number;
}
export const bomTotalsSchema: z.ZodType<BomTotals> = z.object({
  by_currency: byCurrency, made: byCurrency, bought: byCurrency, estimated: byCurrency,
  has_estimates: z.boolean(),
  by_vendor: z.array(z.object({ vendor: nstr, kind: z.enum(["made", "bought"]), currency: z.string(), total: z.number(), count: z.number().int() })),
  unpriced: z.number().int(), rows: z.number().int(), qty: z.number(),
});

export interface Bom { project_id: string; material: string; items: BomRow[]; totals: BomTotals }
export const bomSchema: z.ZodType<Bom> = z.object({
  project_id: z.string(), material: z.string(), items: z.array(bomRowSchema), totals: bomTotalsSchema,
});

/** The name the core's totals and CSV use for an unknown currency. */
export const UNKNOWN_CURRENCY = "unknown";
const CURRENCY_RE = /^[A-Z]{3}$/;
export const isCurrency = (c: unknown): c is string => typeof c === "string" && CURRENCY_RE.test(c);

/** POST /projects/{id}/bom body (core BomItemIn, extra="forbid"). */
export interface NewBomRow {
  kind?: BomKind; part_id?: string | null; catalog_id?: string | null; name: string;
  qty?: number; unit_cost?: number | null; currency?: string | null; vendor?: string | null;
  url?: string | null; specs?: PartSpec[]; notes?: string;
}
/** PATCH /projects/{id}/bom/{item_id} body (core BomPatch, extra="forbid"). */
export interface BomPatch {
  name?: string; qty?: number; unit_cost?: number | null; currency?: string | null;
  vendor?: string | null; url?: string | null; specs?: PartSpec[]; notes?: string;
}
const NEW_ROW_KEYS = ["kind", "part_id", "catalog_id", "name", "qty", "unit_cost", "currency", "vendor", "url", "specs", "notes"] as const;
const PATCH_KEYS = ["name", "qty", "unit_cost", "currency", "vendor", "url", "specs", "notes"] as const;

/** Exactly the fields the core accepts (it refuses any other key), with the currency only when
 *  it is a 3-letter code (null otherwise: "unknown"). */
export function bomBody(src: NewBomRow | BomPatch, kind: "new" | "patch"): Record<string, unknown> {
  const keys: readonly string[] = kind === "new" ? NEW_ROW_KEYS : PATCH_KEYS;
  const out: Record<string, unknown> = {};
  for (const k of keys) {
    const v = (src as Record<string, unknown>)[k];
    if (v !== undefined) out[k] = v;
  }
  if ("currency" in out && out.currency !== null && !isCurrency(out.currency)) out.currency = null;
  return out;
}

// ---- suggested sites (profile; R3 preset list)

/** A curated, user-editable site agents read without an approval card. `search_url` is an https
 *  template on the site's own domain with one `{q}`; null: the site is opened (web_open) but
 *  never searched (e.g. a datasheet host). `suggested` marks one of Mises's defaults. */
export interface SuggestedSite { name: string; domain: string; search_url: string | null; categories: string[]; suggested: boolean }
export const suggestedSiteSchema: z.ZodType<SuggestedSite> = z.object({
  name: z.string(), domain: z.string(), search_url: nstr, categories: z.array(z.string()), suggested: z.boolean(),
});
export const suggestedSitesSchema = z.array(suggestedSiteSchema);

/** Core sites.on_domain: the host is the domain or one of its subdomains. */
export function onDomain(host: string, domain: string): boolean {
  const h = host.toLowerCase().replace(/\.$/, "");
  const d = domain.toLowerCase().replace(/\.$/, "");
  return h === d || h.endsWith(`.${d}`);
}

/** The "browser fetches per run" limit's range (core profile.Profile). */
export const FETCHES_MIN = 1;
export const FETCHES_MAX = 1000;
