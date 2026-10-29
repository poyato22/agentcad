// Fake core: Plan 5 C5 Onshape browser, live budget and parts directory (core
// server/onshape_api.py, catalog_api.py, onshape/browse.py, catalog/*), served from the real-core
// fixtures in test/fixtures/core-ui (dumped by core/scripts/dump_onshape_catalog_fixtures.py), so
// the shapes are the core's. Same rules and error answers as the core:
//   · Onshape routes answer 403 "Onshape is not connected" without keys, before any call;
//   · every "real" Onshape request (a documents page, an elements list, an uncached thumbnail,
//     each of an import's 2 calls) bumps the ledger and is followed by an `onshape_budget` event;
//   · POST /catalog/import is https only, saves at once and returns the item (page text in
//     raw_text); a URL already in the directory comes back unchanged with `existing: true` unless
//     `refresh`; PATCH edits name/category/vendor/specs/dims/mount only; built-ins are read-only.
// GET /projects (C1) answers here too, with the builds the scenario names, for the build pickers.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { CatalogItem, OnshapeDoc } from "../../src/api/directory";
import { asBody, HttpError, mises, RAW, type EventIn, type Route } from "./server";

const FX = resolve(dirname(fileURLToPath(import.meta.url)), "../fixtures/core-ui");
const fx = <T>(name: string): T => JSON.parse(readFileSync(resolve(FX, name), "utf8")) as T;

/** A real 1×1 PNG (the core proxies Onshape's image bytes). */
export const THUMB_PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4/58BAAT/Af9jgNErAAAAAElFTkSuQmCC", "base64");
const PAGE_MAX = 20;
const ID_RE = /^[0-9a-f]{24}$/;
const DIM_KEY = /^[a-z][a-z0-9_]{0,39}$/;
const EDIT_KEYS = new Set(["name", "category", "vendor", "specs", "dims", "mount"]);
const DOMAIN_RE = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

export interface DirectoryScenario {
  /** Onshape keys present (default: the scenario's status.onshape === "connected"). */
  onshape?: boolean;
  /** Extra generated documents after the fixture's three (for paging). */
  extra_docs?: number;
  /** Calls already used this year (default: the scenario status's onshape_calls_year). */
  used_year?: number;
  limit_year?: number;
  /** Builds for GET /projects (default: one "Default"). */
  builds?: { id: string; name: string }[];
  active_build?: string | null;
  allowed_domains?: string[];
  /** URL → which fixture page it reads: "motor" (a spec table) or "injection" (a prompt-injection page). */
  pages?: Record<string, "motor" | "injection">;
}

export interface FakeDirectory {
  connected: boolean;
  docs: OnshapeDoc[];
  usedYear: number; limitYear: number;
  /** Calls per run id ("" = no run). */
  usedRun: Map<string, number>;
  thumbsCached: Set<string>;
  items: Map<string, CatalogItem>;
  builtin: Set<string>;
  builds: { id: string; name: string }[];
  activeBuild: string | null;
  allowed: string[];
  pages: Record<string, "motor" | "injection">;
  /** Element imports already mirrored, "did/wid/eid@build" → part id. */
  imported: Map<string, string>;
  inserted: Map<string, string>;
}

const DEFAULT_PAGES: Record<string, "motor" | "injection"> = {
  "https://shop.example.com/p/falcon-2207": "motor",
  "https://shop.example.com/p/sketchy": "injection",
};

/** `status`: the scenario's /status, so the ledger starts where its ONSHAPE light says. */
export function directoryFrom(sc: DirectoryScenario | undefined, connectedByStatus: boolean, status?: { onshape_calls_year: number; onshape_cap_year: number }): FakeDirectory {
  const docs = structuredClone(fx<OnshapeDoc[]>("onshape_documents.json"));
  for (let i = 0; i < (sc?.extra_docs ?? 0); i++) {
    const did = (0x100 + i).toString(16).padStart(24, "0");
    const wid = (0x800 + i).toString(16).padStart(24, "0");
    const href = `https://cad.onshape.com/api/thumbnails/d/${did}/w/${wid}/s/300x170`;
    docs.push({ did, name: `Bracket ${i}`, modified_at: `2026-09-${String(1 + (i % 28)).padStart(2, "0")}T10:00:00Z`, owner: "Ada Lovelace",
      thumbnail_url: `/onshape/thumbnail?href=${encodeURIComponent(href)}`, default_wid: wid });
  }
  const all = fx<CatalogItem[]>("catalog_all.json");
  return {
    connected: sc?.onshape ?? connectedByStatus,
    docs,
    usedYear: sc?.used_year ?? status?.onshape_calls_year ?? 0, limitYear: sc?.limit_year ?? status?.onshape_cap_year ?? 2500,
    usedRun: new Map(), thumbsCached: new Set(),
    items: new Map(all.map((i) => [i.id, structuredClone(i)])), builtin: new Set(all.map((i) => i.id)),
    builds: sc?.builds ?? [{ id: "b_default", name: "Default" }], activeBuild: sc?.active_build ?? null,
    allowed: [...(sc?.allowed_domains ?? [])], pages: { ...DEFAULT_PAGES, ...(sc?.pages ?? {}) },
    imported: new Map(), inserted: new Map(),
  };
}

function nextJan1(): number {
  const y = new Date().getFullYear();
  return new Date(y + 1, 0, 1).getTime() / 1000;
}

export function budgetOf(d: FakeDirectory, runId: string | null) {
  return { used_year: d.usedYear, limit_year: d.limitYear, used_run: d.usedRun.get(runId ?? "") ?? 0,
    warn_at: Math.floor(d.limitYear * 0.8), reset_at: nextJan1(), run_id: runId };
}

/** Core catalog._haystack + search ranking (name matches first). */
function search(items: Iterable<CatalogItem>, q: string, category: string | null, limit: number): CatalogItem[] {
  const words = q.toLowerCase().split(/[\s_]+/).filter(Boolean).slice(0, 12);
  const out: [number, string, string, CatalogItem][] = [];
  for (const it of items) {
    if (category && it.category !== category) continue;
    const parts = [it.id, it.name, it.category, it.vendor ?? ""];
    for (const s of it.specs) parts.push(String(s.key), String(s.value), String(s.unit ?? ""));
    parts.push(String(it.mount?.pattern ?? ""));
    const hay = parts.join(" ").toLowerCase().replaceAll("_", " ");
    if (!words.every((w) => hay.includes(w))) continue;
    const name = it.name.toLowerCase();
    out.push([-words.filter((w) => name.includes(w)).length, it.category, name, it]);
  }
  out.sort((a, b) => a[0] - b[0] || a[1].localeCompare(b[1]) || a[2].localeCompare(b[2]));
  return out.slice(0, Math.max(1, Math.min(limit, 200))).map(([, , , it]) => {
    const { raw_text: _drop, ...rest } = it;
    void _drop;
    return structuredClone(rest);
  });
}

/** Core importer.check_url (the parts the app can hit). */
function checkUrl(raw: unknown): string {
  if (typeof raw !== "string" || !raw.trim() || raw.length > 2048) throw mises("invalid_input", "url is required (at most 2048 characters)");
  let u: URL;
  try { u = new URL(raw.trim()); } catch { throw mises("invalid_input", "not a valid URL"); }
  if (u.protocol !== "https:") throw mises("invalid_input", "only https links can be imported", "use the https:// address of the page");
  if (!u.hostname || u.username || u.password) throw mises("invalid_input", "the link needs a host and no user:password");
  if (u.hostname === "localhost" || /\.(localhost|local|internal)$/.test(u.hostname)) throw mises("invalid_input", "local addresses cannot be imported");
  u.hash = "";
  return u.toString();
}

export interface DirectoryDeps {
  get: () => FakeDirectory;
  emit: (ev: EventIn) => void;
}

export function directoryRoutes({ get, emit }: DirectoryDeps): Route[] {
  /** One real Onshape request: ledger it, then the budget event (like Runtime.publish_onshape_budget). */
  function spend(runId: string | null = null) {
    const d = get();
    d.usedYear++;
    d.usedRun.set(runId ?? "", (d.usedRun.get(runId ?? "") ?? 0) + 1);
    emit({ type: "onshape_budget", run_id: runId, data: budgetOf(d, runId) });
  }
  function connected(): FakeDirectory {
    const d = get();
    if (!d.connected) throw mises("denied", "Onshape is not connected", "add the Onshape API keys in Settings");
    return d;
  }
  const elements = () => fx<unknown[]>("onshape_elements.json");

  return [
    // ---- Onshape
    {
      method: "GET", re: /^\/onshape\/documents$/, h: (_m, q) => {
        const d = connected();
        const offset = Number(q.get("offset") ?? 0);
        const limit = Number(q.get("limit") ?? PAGE_MAX);
        if (!(offset >= 0) || !(limit >= 1 && limit <= PAGE_MAX)) throw mises("invalid_input", `offset >= 0 and 1 <= limit <= ${PAGE_MAX}`);
        const words = (q.get("q") ?? "").toLowerCase().slice(0, 200);
        spend();
        return d.docs.filter((x) => !words || x.name.toLowerCase().includes(words)).slice(offset, offset + limit);
      },
    },
    {
      method: "GET", re: /^\/onshape\/documents\/([^/]+)\/elements$/, h: (m, q) => {
        connected();
        const did = decodeURIComponent(m[1]).toLowerCase();
        const wid = (q.get("wid") ?? "").toLowerCase();
        if (!ID_RE.test(did)) throw mises("invalid_input", "did must be a 24-character Onshape id", "copy it from the document URL");
        if (!ID_RE.test(wid)) throw mises("invalid_input", "wid must be a 24-character Onshape id", "copy it from the document URL");
        spend();
        return elements();
      },
    },
    {
      method: "GET", re: /^\/onshape\/thumbnail$/, h: (_m, q, _b, _r, res) => {
        const d = connected();
        const href = q.get("href") ?? "";
        let ok: boolean;
        try {
          const u = new URL(href);
          ok = u.protocol === "https:" && (u.hostname === "onshape.com" || u.hostname.endsWith(".onshape.com"))
            && u.pathname.startsWith("/api/") && u.pathname.includes("/thumbnails/") && !u.pathname.includes("..");
        } catch { ok = false; }
        if (!ok) throw mises("invalid_input", "not an Onshape thumbnail URL", "pass a thumbnail_url from GET /onshape/documents");
        if (!d.thumbsCached.has(href)) { d.thumbsCached.add(href); spend(); }
        res.writeHead(200, { "Content-Type": "image/png", "Content-Length": THUMB_PNG.length, "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff" });
        res.end(THUMB_PNG);
        return RAW;
      },
    },
    {
      method: "POST", re: /^\/onshape\/import$/, h: (_m, _q, body) => {
        const d = connected();
        const b = asBody(body);
        for (const k of ["did", "wid", "eid"] as const) {
          if (typeof b[k] !== "string" || !/^[0-9a-fA-F]{24}$/.test(b[k] as string)) {
            throw new HttpError(422, { detail: [{ type: "string_pattern_mismatch", loc: ["body", k], msg: "String should match pattern '^[0-9a-fA-F]{24}$'", input: b[k] ?? null }] });
          }
        }
        const project = typeof b.project_id === "string" ? b.project_id : null;
        const ref = `${String(b.did).toLowerCase()}/${String(b.wid).toLowerCase()}/${String(b.eid).toLowerCase()}`;
        const out = structuredClone(fx<{ part_id: string; existing: boolean; meta: Record<string, unknown> }>("onshape_import.json"));
        const el = (elements() as { eid: string; name: string }[]).find((e) => e.eid === String(b.eid).toLowerCase());
        spend();                                            // the element list
        if (!el) throw mises("not_found", "no Part Studio or Assembly with that eid", "pick an element from GET /onshape/documents/{did}/elements");
        const key = `${ref}@${project ?? ""}`;
        const had = d.imported.get(key);
        if (had) return { ...out, part_id: had, existing: true };
        spend();                                            // the bounding box
        d.imported.set(key, out.part_id);
        out.meta = { ...out.meta, project_id: project, onshape_ref: ref, label: el.name };
        emit({ type: "part_changed", run_id: null, data: { part_id: out.part_id, version: 1, by: "user", run_id: null, summary: `imported ${el.name} from Onshape` } });
        return out;
      },
    },
    {
      method: "GET", re: /^\/onshape\/budget$/, h: (_m, q) => budgetOf(get(), q.get("run_id")),
    },

    // ---- parts directory
    { method: "GET", re: /^\/catalog\/categories$/, h: () => fx<string[]>("catalog_categories.json") },
    {
      method: "GET", re: /^\/catalog\/search$/, h: (_m, q) =>
        search(get().items.values(), q.get("q") ?? "", q.get("category") || null, Number(q.get("limit") ?? 50)),
    },
    {
      method: "GET", re: /^\/catalog\/items\/([^/]+)$/, h: (m) => {
        const it = get().items.get(decodeURIComponent(m[1]));
        if (!it) throw mises("not_found", `no catalog item '${decodeURIComponent(m[1])}'`, "search the directory with catalog_search");
        return structuredClone(it);
      },
    },
    {
      method: "POST", re: /^\/catalog\/import$/, h: (_m, _q, body) => {
        const d = get();
        const b = asBody(body);
        const url = checkUrl(b.url);
        const page = d.pages[url.replace(/\?.*$/, "")] ?? d.pages[url];
        const old = [...d.items.values()].find((i) => i.source === "url" && i.url === url);
        if (old && b.refresh !== true) return { ...structuredClone(old), existing: true };
        if (!page) throw mises("transient", "could not connect to the page", null);
        const base = structuredClone(fx<CatalogItem & { existing?: boolean }>(page === "motor" ? "catalog_import.json" : "catalog_import_injection.json"));
        delete base.existing;
        const id = old?.id ?? `url_${Buffer.from(url).toString("hex").slice(-16).padStart(16, "0")}`;
        const item: CatalogItem = { ...base, id, url, project_id: old ? old.project_id : (typeof b.project_id === "string" ? b.project_id : null),
          // core importer: {title, url} only
          sources: [{ title: `Imported from ${new URL(url).hostname}`, url } as CatalogItem["sources"][number]] };
        delete (item as { edited_by_user?: boolean }).edited_by_user;
        d.items.set(id, item);
        return { ...structuredClone(item), existing: false };
      },
    },
    {
      method: "PATCH", re: /^\/catalog\/items\/([^/]+)$/, h: (m, _q, body) => {
        const d = get();
        const id = decodeURIComponent(m[1]);
        const it = d.items.get(id);
        if (!it) throw mises("not_found", `no catalog item '${id}'`, "search the directory with catalog_search");
        if (it.source !== "url") throw mises("invalid_input", "built-in items cannot be edited");
        if (typeof body !== "object" || body === null || Array.isArray(body)) throw mises("invalid_input", "body must be an object");
        const patch = body as Record<string, unknown>;
        const bad = Object.keys(patch).filter((k) => !EDIT_KEYS.has(k));
        if (bad.length) throw mises("invalid_input", `bad item edit: extra fields not permitted: ${bad.join(", ")}`, "editable: name, category, vendor, specs, dims, mount");
        if (patch.dims !== undefined) {
          const dims = patch.dims as Record<string, unknown>;
          if (typeof dims !== "object" || dims === null || Object.entries(dims).some(([k, v]) => !DIM_KEY.test(k) || typeof v !== "number")) {
            throw mises("invalid_input", "bad item edit: dims must map names like length_mm to numbers", "editable: name, category, vendor, specs, dims, mount");
          }
        }
        const next = { ...it, ...(patch as Partial<CatalogItem>), edited_by_user: true, edited_at: Date.now() / 1000 } as CatalogItem;
        if (patch.dims !== undefined) next.mass_source = "mass_g" in (patch.dims as object) ? "catalog" : "estimate";
        d.items.set(id, next);
        return structuredClone(next);
      },
    },
    {
      method: "DELETE", re: /^\/catalog\/items\/([^/]+)$/, h: (m) => {
        const d = get();
        const id = decodeURIComponent(m[1]);
        if (d.builtin.has(id)) throw mises("invalid_input", "built-in items cannot be deleted", "only items imported from a link can be deleted");
        if (!d.items.delete(id)) throw mises("not_found", `no catalog item '${id}'`);
        return { ok: true };
      },
    },
    {
      method: "POST", re: /^\/catalog\/items\/([^/]+)\/insert$/, h: (m, _q, body) => {
        const d = get();
        const id = decodeURIComponent(m[1]);
        const it = d.items.get(id);
        if (!it) throw mises("not_found", `no catalog item '${id}'`, "search the directory with catalog_search");
        const b = asBody(body);
        const project = typeof b.project_id === "string" ? b.project_id : d.activeBuild ?? d.builds[0]?.id ?? null;
        const key = `${id}@${project ?? ""}`;
        const out = structuredClone(fx<{ part_id: string; existing: boolean; metrics: Record<string, unknown> | null; meta: Record<string, unknown> }>("catalog_insert.json"));
        const had = d.inserted.get(key);
        if (had) return { ...out, part_id: had, existing: true };
        d.inserted.set(key, id);
        const est = it.mass_source === "estimate";
        out.metrics = { ...out.metrics, mass_source: it.mass_source, ...(est ? { mass_note: "estimate: volume × density" } : {}) };
        out.meta = { ...out.meta, catalog_item_id: id, project_id: project, label: it.name, mass_source: it.mass_source };
        emit({ type: "part_changed", run_id: null, data: { part_id: id, version: 1, by: "user", run_id: null, summary: `inserted ${id}` } });
        return { ...out, part_id: id, existing: false };
      },
    },
    { method: "GET", re: /^\/catalog\/allowed_domains$/, h: () => [...get().allowed] },
    {
      method: "POST", re: /^\/catalog\/allowed_domains$/, h: (_m, _q, body) => {
        const d = get();
        const raw = asBody(body).domain;
        const dom = typeof raw === "string" ? raw.trim().toLowerCase().replace(/\.$/, "") : "";
        if (!DOMAIN_RE.test(dom)) throw mises("invalid_input", `${JSON.stringify(raw)} is not a domain name`, "e.g. shop.example.com");
        if (!d.allowed.includes(dom)) d.allowed.push(dom);
        return [...d.allowed];
      },
    },
    {
      method: "DELETE", re: /^\/catalog\/allowed_domains\/([^/]+)$/, h: (m) => {
        const d = get();
        const dom = decodeURIComponent(m[1]).toLowerCase();
        d.allowed = d.allowed.filter((x) => x !== dom);
        return [...d.allowed];
      },
    },

    // ---- builds (C1 GET /projects; the pickers only)
    {
      method: "GET", re: /^\/projects$/, h: () => get().builds.map((b) => ({
        id: b.id, name: b.name, created_at: 1790000000, updated_at: 1790000000, trashed_at: null, part_count: 0, run_count: 0, onshape: null,
      })),
    },
  ];
}
