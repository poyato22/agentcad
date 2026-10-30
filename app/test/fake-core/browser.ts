// Fake core: Plan 7 (R3/R4/R5) agent browser snapshots, part cards, the build parts list (BOM)
// and the suggested-sites routes, mirroring U4c's core (server/browser_api.py, bom/__init__.py,
// browser/{snapshot,tools}.py). Shapes are checked against real-core dumps
// (core/tests/fixtures/ui/browser, test/contract.browser.test.ts):
//   · GET /runs/{id}/browser             → summary rows (no text/tables/results); ?full=1: whole snapshots
//   · GET /browser/snapshots/{sid}       → one snapshot; …/images/{n} → the proxied PNG
//   · GET /runs/{id}/part_cards, GET /browser/cards/{card_id}
//   · GET /browser/sites/defaults, POST /browser/sites/restore_defaults
//   · GET/POST /projects/{id}/bom, PATCH/DELETE /projects/{id}/bom/{item_id}, GET …/bom.csv
// The BOM mirrors the core's rules: rows validated like BomItemIn / BomPatch (extra="forbid",
// qty a whole number ≥ 1, currency a 3-letter code or null = unknown, never assumed), made rows
// without a set cost carry their live estimate, per-currency totals (never summed across
// currencies), and a CSV whose cells can't run a spreadsheet formula.
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import type { ServerResponse } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { BomKind, BomRow, BomSource, BrowserSnapshot, PartCard, PartSpec, SuggestedSite } from "../../src/api/browser";
import { THUMB_PNG } from "./directory";
import { HttpError, mises, RAW, type Route } from "./server";

export const BROWSER_FIXTURES = resolve(dirname(fileURLToPath(import.meta.url)), "../../../core/tests/fixtures/ui/browser");
const fixture = <T>(name: string): T => JSON.parse(readFileSync(resolve(BROWSER_FIXTURES, name), "utf8")) as T;
/** Mises's real default suggested sites (core browser/sites.DEFAULT_SITES, dumped). */
export const DEFAULT_SITES: SuggestedSite[] = fixture<SuggestedSite[]>("sites_defaults.json");

/** A full stored snapshot as the core returns it (public()): project_id and content_type too. */
export type SnapshotSeed = BrowserSnapshot & { project_id?: string | null; content_type?: string; part?: unknown };
/** A parts-list row as the core returns it, plus the build it belongs to. */
export type BomSeed = BomRow & { project_id: string };

export interface BrowserBomScenario {
  snapshots?: SnapshotSeed[];
  cards?: PartCard[];
  bom?: BomSeed[];
}

/** A stored row. `auto_estimate`: a made part's live estimate (the fake has no built geometry). */
interface StoredRow {
  id: string; kind: BomKind; part_id: string | null; catalog_id: string | null; name: string; qty: number;
  unit_cost: number | null; currency: string | null; vendor: string | null; url: string | null;
  specs: PartSpec[]; notes: string; source: BomSource; hidden?: boolean;
  auto_estimate?: { unit_cost: number; note: string } | null;
}

export interface FakeBrowserBom {
  snapshots: Map<string, SnapshotSeed>;
  cards: Map<string, PartCard>;
  bom: Map<string, StoredRow[]>;
}

function stored(r: BomSeed): StoredRow {
  const base: StoredRow = {
    id: r.id, kind: r.kind, part_id: r.part_id, catalog_id: r.catalog_id, name: r.name, qty: r.qty,
    unit_cost: r.unit_cost, currency: r.currency, vendor: r.vendor, url: r.url, specs: structuredClone(r.specs),
    notes: r.notes, source: r.source, auto_estimate: null,
  };
  // a live estimate is not a stored cost: keep it apart, so a PATCH unit_cost:null brings it back
  if (r.estimate && r.unit_cost !== null) Object.assign(base, { unit_cost: null, currency: null, auto_estimate: { unit_cost: r.unit_cost, note: r.estimate_note ?? "estimate" } });
  return base;
}

export function browserBomFrom(sc: BrowserBomScenario | undefined): FakeBrowserBom {
  const bom = new Map<string, StoredRow[]>();
  for (const r of sc?.bom ?? []) bom.set(r.project_id, [...(bom.get(r.project_id) ?? []), stored(r)]);
  return {
    snapshots: new Map((sc?.snapshots ?? []).map((s) => [s.id, structuredClone(s)])),
    cards: new Map((sc?.cards ?? []).map((c) => [c.id, structuredClone(c)])),
    bom,
  };
}

// ---- the view (core bom.view / _finish / totals)

const r2 = (n: number) => Math.round(n * 100) / 100;
export const UNKNOWN = "unknown";

function finish(row: StoredRow): BomRow {
  const out: BomRow = {
    id: row.id, kind: row.kind, part_id: row.part_id, catalog_id: row.catalog_id, name: row.name, qty: row.qty,
    unit_cost: row.unit_cost, currency: row.currency, vendor: row.vendor, url: row.url, specs: structuredClone(row.specs),
    notes: row.notes ?? "", source: row.source, estimate: false, estimate_note: null, line_total: null,
  };
  if (out.kind === "made" && out.unit_cost === null && out.part_id) {
    const est = row.auto_estimate ?? null;
    out.unit_cost = est?.unit_cost ?? null;
    out.estimate_note = est?.note ?? "no cost estimate: the part has not been built";
    out.estimate = out.unit_cost !== null;
    out.currency = "USD";
  }
  out.line_total = out.unit_cost !== null ? r2(out.qty * out.unit_cost) : null;
  return out;
}

const addTo = (d: Record<string, number>, cur: string, v: number) => { d[cur] = r2((d[cur] ?? 0) + v); };

export function totalsOf(items: BomRow[]) {
  const by_currency: Record<string, number> = {}, made: Record<string, number> = {}, bought: Record<string, number> = {}, estimated: Record<string, number> = {};
  const vendors = new Map<string, { vendor: string | null; kind: BomKind; currency: string; total: number; count: number }>();
  let unpriced = 0;
  for (const it of items) {
    const lt = it.line_total;
    if (lt === null) { unpriced += 1; continue; }
    const cur = it.currency || UNKNOWN;
    addTo(by_currency, cur, lt);
    addTo(it.kind === "made" ? made : bought, cur, lt);
    if (it.estimate) addTo(estimated, cur, lt);
    const vendor = it.kind === "bought" ? it.vendor || null : null;
    const key = `${vendor ?? ""}\u0000${cur}`;
    const v = vendors.get(key) ?? { vendor, kind: vendor === null ? it.kind : "bought", currency: cur, total: 0, count: 0 };
    v.total = r2(v.total + lt);
    v.count += 1;
    vendors.set(key, v);
  }
  const by_vendor = [...vendors.values()].sort((a, b) => b.total - a.total || (a.vendor ?? "").localeCompare(b.vendor ?? ""));
  return { by_currency, made, bought, estimated, has_estimates: Object.keys(estimated).length > 0, by_vendor, unpriced,
    rows: items.length, qty: items.reduce((n, i) => n + i.qty, 0) };
}

function view(st: FakeBrowserBom, projectId: string) {
  const items = (st.bom.get(projectId) ?? []).filter((r) => !r.hidden).map(finish);
  items.sort((a, b) => (a.kind === "made" ? 0 : 1) - (b.kind === "made" ? 0 : 1));
  return { project_id: projectId, material: "PLA", items, totals: totalsOf(items) };
}

// ---- validation (core BomItemIn / BomPatch, extra="forbid")

const NEW_KEYS = new Set(["kind", "part_id", "catalog_id", "name", "qty", "unit_cost", "currency", "vendor", "url", "specs", "notes"]);
const PATCH_KEYS = new Set(["name", "qty", "unit_cost", "currency", "vendor", "url", "specs", "notes"]);
const PART_RE = /^[a-z][a-z0-9_]{1,40}$/;
const ITEM_RE = /^(?:bom_[0-9a-f]{12}|auto_[a-z][a-z0-9_]{1,40})$/;

function bad(msgs: string[]): HttpError {
  return mises("invalid_input", `invalid parts list row: ${msgs.join("; ")}`);
}

function check(raw: unknown, keys: Set<string>, creating: boolean): Record<string, unknown> {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw mises("invalid_input", "body must be a JSON object");
  const b = raw as Record<string, unknown>;
  const errs: string[] = [];
  for (const k of Object.keys(b)) if (!keys.has(k)) errs.push(`${k}: Extra inputs are not permitted`);
  const isStr = (v: unknown, max: number, min = 0) => typeof v === "string" && v.length >= min && v.length <= max;
  const nullable = (k: string, ok: (v: unknown) => boolean) => { if (k in b && b[k] !== null && !ok(b[k])) errs.push(`${k}: Input should be valid`); };
  if (creating && !("name" in b)) errs.push("name: Field required");
  if ("name" in b && !(b.name === null && !creating) && !isStr(b.name, 200, 1)) errs.push("name: String should have at least 1 character");
  if ("kind" in b && b.kind !== "made" && b.kind !== "bought") errs.push("kind: Input should be 'made' or 'bought'");
  nullable("qty", (v) => Number.isInteger(v) && (v as number) >= 1 && (v as number) <= 100_000);
  nullable("unit_cost", (v) => typeof v === "number" && v >= 0 && v < 1e7);
  nullable("currency", (v) => typeof v === "string" && /^[A-Z]{3}$/.test(v));
  nullable("part_id", (v) => typeof v === "string" && PART_RE.test(v));
  nullable("catalog_id", (v) => isStr(v, 64));
  nullable("vendor", (v) => isStr(v, 200));
  nullable("notes", (v) => isStr(v, 2000));
  nullable("url", (v) => typeof v === "string" && (v.trim() === "" || /^https:\/\//.test(v)));
  nullable("specs", (v) => Array.isArray(v) && v.length <= 40 && v.every((s) => typeof s === "object" && s !== null
    && Object.keys(s).every((k) => k === "key" || k === "value" || k === "unit")
    && isStr((s as PartSpec).key, 80, 1) && (typeof (s as PartSpec).value === "number" || isStr((s as PartSpec).value, 200))));
  if (errs.length) throw bad(errs);
  return b;
}

// ---- CSV (core bom.to_csv)

const CSV_COLUMNS = ["kind", "name", "qty", "unit_cost", "currency", "line_total", "estimate", "vendor", "url", "part_id", "catalog_id", "specs", "notes", "source"];
const MONEY = new Set(["unit_cost", "line_total"]);
const FORMULA = ["=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠"];

/** Fixed decimals: 2, or up to 4 when a unit price carries them; never scientific notation. */
export function moneyText(v: number): string {
  const s = v.toFixed(4).replace(/0+$/, "");
  const [whole, frac = ""] = s.split(".");
  return `${whole}.${frac.padEnd(2, "0")}`;
}

function cell(v: unknown, money = false): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "yes" : "";
  if (typeof v === "number") return money ? moneyText(v) : String(v);
  const s = String(v);
  // a spreadsheet must never run a formula a web page put in a name or note
  return FORMULA.some((f) => s.trimStart().startsWith(f)) ? `'${s}` : s;
}

/** Python csv.writer's minimal quoting. */
const quote = (s: string) => (/[",\r\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s);

export function toCsv(v: ReturnType<typeof view>): string {
  const lines = [CSV_COLUMNS.join(",")];
  for (const it of v.items) {
    const notes = [it.notes, it.estimate_note].filter(Boolean).join(" | ");
    const specs = it.specs.map((s) => `${s.key}: ${s.value}${s.unit ? ` ${s.unit}` : ""}`).join("; ");
    const row: Record<string, unknown> = { ...it, specs, notes, currency: it.currency || UNKNOWN };
    lines.push(CSV_COLUMNS.map((c) => quote(cell(row[c], MONEY.has(c)))).join(","));
  }
  for (const [cur, total] of Object.entries(v.totals.by_currency)) {
    lines.push(["total", "", "", "", quote(cell(cur)), moneyText(total), v.totals.estimated[cur] !== undefined ? "yes" : "", "", "", "", "", "", "", ""].join(","));
  }
  return `${lines.join("\r\n")}\r\n`;
}

// ---- routes

const SUMMARY_KEYS = ["id", "run_id", "kind", "url", "final_url", "domain", "title", "fetched_at", "site", "blocked"] as const;

function summary(s: SnapshotSeed): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of SUMMARY_KEYS) out[k] = (s as unknown as Record<string, unknown>)[k] ?? null;
  out.image_count = s.images.length;
  out.result_count = s.results?.length ?? 0;
  out.has_part = s.part !== null && s.part !== undefined;
  return out;
}

export interface BrowserDeps {
  get: () => FakeBrowserBom;
  hasRun: (id: string) => boolean;
  /** The fake profile's suggested sites (restore_defaults writes them). */
  setSites: (sites: SuggestedSite[]) => void;
}

export function browserRoutes(d: BrowserDeps): Route[] {
  const run = (id: string) => { if (!d.hasRun(id)) throw mises("not_found", `unknown run: ${id}`); };
  return [
    { method: "GET", re: /^\/runs\/([^/]+)\/browser$/, h: (m, q) => {
      const id = decodeURIComponent(m[1]);
      run(id);
      const recs = [...d.get().snapshots.values()].filter((s) => s.run_id === id).sort((a, b) => a.fetched_at - b.fetched_at);
      return q.get("full") && q.get("full") !== "0" ? recs : recs.map(summary);
    } },
    { method: "GET", re: /^\/runs\/([^/]+)\/part_cards$/, h: (m) => {
      const id = decodeURIComponent(m[1]);
      run(id);
      return [...d.get().cards.values()].filter((c) => c.run_id === id).sort((a, b) => a.created_at - b.created_at);
    } },
    { method: "GET", re: /^\/browser\/snapshots\/([^/]+)$/, h: (m) => {
      const s = d.get().snapshots.get(decodeURIComponent(m[1]));
      if (!s) throw mises("not_found", `no browser snapshot '${decodeURIComponent(m[1])}'`, "snapshot ids come from web_open / web_search");
      return s;
    } },
    { method: "GET", re: /^\/browser\/snapshots\/([^/]+)\/images\/(\d+)$/, h: (m, _q, _b, _r, res: ServerResponse) => {
      const s = d.get().snapshots.get(decodeURIComponent(m[1]));
      const n = Number(m[2]);
      if (!s || n >= s.images.length) throw mises("not_found", "no such image");
      res.writeHead(200, { "Content-Type": "image/png", "Content-Length": THUMB_PNG.length, "X-Content-Type-Options": "nosniff" });
      res.end(THUMB_PNG);
      return RAW;
    } },
    { method: "GET", re: /^\/browser\/cards\/([^/]+)$/, h: (m) => {
      const c = d.get().cards.get(decodeURIComponent(m[1]));
      if (!c) throw mises("not_found", `no part card '${decodeURIComponent(m[1])}'`);
      return c;
    } },
    { method: "GET", re: /^\/browser\/sites\/defaults$/, h: () => structuredClone(DEFAULT_SITES) },
    { method: "POST", re: /^\/browser\/sites\/restore_defaults$/, h: () => {
      d.setSites(structuredClone(DEFAULT_SITES));
      return structuredClone(DEFAULT_SITES);
    } },
  ];
}

export function bomRoutes(get: () => FakeBrowserBom): Route[] {
  const rows = (pid: string): StoredRow[] => {
    let cur = get().bom.get(pid);
    if (!cur) { cur = []; get().bom.set(pid, cur); }
    return cur;
  };
  const one = (pid: string, id: string): BomRow => {
    const it = view(get(), pid).items.find((r) => r.id === id);
    if (!it) throw mises("not_found", `no parts list row '${id}'`);
    return it;
  };
  const find = (pid: string, id: string): StoredRow => {
    const r = ITEM_RE.test(id) ? rows(pid).find((x) => x.id === id && !x.hidden) : undefined;
    if (!r) throw mises("not_found", `no parts list row '${id}'`);
    return r;
  };
  return [
    { method: "GET", re: /^\/projects\/([^/]+)\/bom$/, h: (m) => view(get(), decodeURIComponent(m[1])) },
    { method: "GET", re: /^\/projects\/([^/]+)\/bom\.csv$/, h: (m, _q, _b, _r, res: ServerResponse) => {
      const csv = toCsv(view(get(), decodeURIComponent(m[1])));
      res.writeHead(200, { "Content-Type": "text/csv; charset=utf-8", "Content-Length": Buffer.byteLength(csv), "X-Content-Type-Options": "nosniff" });
      res.end(csv);
      return RAW;
    } },
    { method: "POST", re: /^\/projects\/([^/]+)\/bom$/, h: (m, _q, raw) => {
      const pid = decodeURIComponent(m[1]);
      const b = check(raw, NEW_KEYS, true);
      const row: StoredRow = {
        id: `bom_${randomBytes(6).toString("hex")}`, kind: (b.kind as BomKind) ?? "bought",
        part_id: (b.part_id as string) ?? null, catalog_id: (b.catalog_id as string) ?? null, name: b.name as string,
        qty: (b.qty as number) ?? 1, unit_cost: (b.unit_cost as number) ?? null, currency: (b.currency as string) ?? null,
        vendor: (b.vendor as string) ?? null, url: (b.url as string) || null, specs: (b.specs as PartSpec[]) ?? [],
        notes: (b.notes as string) ?? "", source: "user",
      };
      rows(pid).push(row);
      return one(pid, row.id);
    } },
    { method: "PATCH", re: /^\/projects\/([^/]+)\/bom\/([^/]+)$/, h: (m, _q, raw) => {
      const pid = decodeURIComponent(m[1]);
      const id = decodeURIComponent(m[2]);
      const b = check(raw, PATCH_KEYS, false);
      const row = find(pid, id);
      if (row.source === "auto") row.source = "user";      // an edited automatic row is stored
      for (const [k, v] of Object.entries(b)) {
        // like the core: these are not nullable, so a null leaves them as they are
        if (v === null && ["name", "qty", "currency", "specs", "notes"].includes(k)) continue;
        (row as unknown as Record<string, unknown>)[k] = k === "url" ? (v as string) || null : v;
      }
      return one(pid, id);
    } },
    { method: "DELETE", re: /^\/projects\/([^/]+)\/bom\/([^/]+)$/, h: (m) => {
      const pid = decodeURIComponent(m[1]);
      const id = decodeURIComponent(m[2]);
      const row = find(pid, id);
      // a part still in the build is hidden (it would come back automatically otherwise)
      if (row.part_id) row.hidden = true;
      else rows(pid).splice(rows(pid).indexOf(row), 1);
      return { ok: true };
    } },
  ];
}
