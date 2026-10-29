// Parts directory: pure helpers for the Directory and Onshape pages (labels, spec formatting, safe
// external links, mount-pattern geometry, the review form's diff). No DOM, no signals.
import type { CatalogEdit, CatalogItem, CatalogMount, CatalogSpec } from "../../../api/directory";

export const CATEGORY_LABELS: Record<string, string> = {
  fastener: "Fasteners", bearing: "Bearings", motor: "Motors", prop: "Props", battery: "Batteries",
  flight_controller: "Flight controllers", servo: "Servos", stepper: "Steppers", extrusion: "Extrusions",
  board: "Boards", other: "Other",
};

export function categoryLabel(c: string): string {
  return CATEGORY_LABELS[c] ?? humanKey(c);
}

/** "mount_pattern" → "Mount pattern". */
export function humanKey(key: string): string {
  const s = key.replace(/_/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : key;
}

function fmtNum(v: number): string {
  if (!Number.isFinite(v)) return String(v);
  const r = Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 100) / 100;
  return r.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function fmtSpecValue(s: Pick<CatalogSpec, "value" | "unit">): string {
  const v = typeof s.value === "number" ? fmtNum(s.value) : s.value;
  return s.unit ? `${v} ${s.unit}` : v;
}

/** A link we may open in the system browser: https only, no credentials; with its host shown. */
export function httpsLink(raw: string | null | undefined): { href: string; host: string } | null {
  if (!raw) return null;
  let u: URL;
  try { u = new URL(raw); } catch { return null; }
  if (u.protocol !== "https:" || u.username || u.password || !u.hostname) return null;
  return { href: u.href, host: u.hostname };
}

const ONSHAPE_ID = /^[0-9a-f]{24}$/;

/** The document in Onshape's web UI (cad.onshape.com), or null for an id that isn't one. */
export function onshapeDocUrl(did: string, wid?: string | null): string | null {
  if (!ONSHAPE_ID.test(did)) return null;
  return wid && ONSHAPE_ID.test(wid) ? `https://cad.onshape.com/documents/${did}/w/${wid}` : `https://cad.onshape.com/documents/${did}`;
}

/** The URL the item's "Open online" opens: its own page for a link import, else its first cited source. */
export function onlineLink(item: Pick<CatalogItem, "url" | "sources">): { href: string; host: string } | null {
  return httpsLink(item.url) ?? item.sources.map((s) => httpsLink(s.url)).find((l) => l !== null) ?? null;
}

/** Short spec lines for a card (mass is shown on its own line). */
export function keySpecs(item: Pick<CatalogItem, "specs">, n = 3): CatalogSpec[] {
  return item.specs.filter((s) => !/^(mass|weight)$/i.test(s.key)).slice(0, n);
}

/** The mass as the directory states it, or that it will be an estimate on insert. */
export function massInfo(item: Pick<CatalogItem, "dims" | "mass_source">): { text: string; estimate: boolean } {
  const g = item.dims.mass_g;
  if (item.mass_source === "catalog" && typeof g === "number") return { text: `${fmtNum(g)} g`, estimate: false };
  if (typeof g === "number") return { text: `${fmtNum(g)} g`, estimate: true };
  return { text: "from volume × density on insert", estimate: true };
}

// ---- mount pattern geometry (same placement as core catalog/geometry.py)

export interface MountGeometry {
  holes: { x: number; y: number; r: number }[];
  /** Board outline (length × width, centred) when the item has one. */
  outline: { w: number; h: number } | null;
  /** The bolt circle of an "opposite" (motor) pattern. */
  circle: number | null;
  /** Labelled spacings: [a] or [a, b] in mm. */
  spacing: number[];
  /** Half extent in mm (for the drawing's scale). */
  extent: number;
}

export function mountGeometry(mount: CatalogMount | null, dims: Record<string, number> = {}): MountGeometry | null {
  if (!mount) return null;
  const r = (mount.hole_d ?? 3) / 2;
  const sp = (mount.spacing_mm ?? []).filter((v) => Number.isFinite(v) && v > 0);
  const a = sp[0];
  const b = sp[1] ?? sp[0];
  let holes: { x: number; y: number }[] = [];
  let circle: number | null = null;
  if (mount.holes_mm?.length) {
    holes = mount.holes_mm.map(([x, y]) => ({ x, y }));
  } else if (a !== undefined) {
    const layout = mount.layout ?? (sp.length === 1 ? "inline" : "rect");
    if (layout === "opposite") {
      // each pair opposite on a circle of the pattern's diameter, at 45°
      const c = Math.SQRT1_2;
      const ra = a / 2, rb = b / 2;
      holes = [{ x: ra * c, y: ra * c }, { x: -ra * c, y: -ra * c }, { x: -rb * c, y: rb * c }, { x: rb * c, y: -rb * c }];
      circle = Math.max(a, b);
    } else if (layout === "inline") {
      holes = [{ x: -a / 2, y: 0 }, { x: a / 2, y: 0 }];
    } else {
      holes = [{ x: a / 2, y: b / 2 }, { x: -a / 2, y: b / 2 }, { x: a / 2, y: -b / 2 }, { x: -a / 2, y: -b / 2 }];
    }
  }
  if (!holes.length) return null;
  const outline = typeof dims.length_mm === "number" && typeof dims.width_mm === "number" && mount.holes_mm?.length
    ? { w: dims.length_mm, h: dims.width_mm } : null;
  let extent = Math.max(...holes.map((h) => Math.max(Math.abs(h.x), Math.abs(h.y)) + r), circle ? circle / 2 + r : 0);
  if (outline) extent = Math.max(extent, outline.w / 2, outline.h / 2);
  return { holes: holes.map((h) => ({ ...h, r })), outline, circle, spacing: sp.slice(0, 2), extent };
}

// ---- review form

export interface ReviewDraft {
  name: string; category: string; vendor: string;
  specs: { key: string; value: string; unit: string; note?: string }[];
  dims: { key: string; value: string }[];
}

export function draftFrom(item: CatalogItem): ReviewDraft {
  return {
    name: item.name, category: item.category, vendor: item.vendor ?? "",
    specs: item.specs.map((s) => ({ key: s.key, value: String(s.value), unit: s.unit ?? "", ...(s.note ? { note: s.note } : {}) })),
    dims: Object.entries(item.dims).map(([key, v]) => ({ key, value: String(v) })),
  };
}

const DIM_KEY = /^[a-z][a-z0-9_]{0,39}$/;

/** Turns a draft back into item fields; `errors` lists what the core would refuse. */
export function parseDraft(d: ReviewDraft): { fields: Required<Pick<CatalogEdit, "name" | "category" | "vendor" | "specs" | "dims">>; errors: string[] } {
  const errors: string[] = [];
  const name = d.name.trim();
  if (!name) errors.push("Give the item a name.");
  const specs: CatalogSpec[] = [];
  for (const s of d.specs) {
    const key = s.key.trim();
    const raw = s.value.trim();
    if (!key && !raw) continue;
    if (!key) { errors.push("Every spec needs a name."); continue; }
    const n = Number(raw);
    specs.push({ key: key.slice(0, 80), value: raw !== "" && Number.isFinite(n) ? n : raw.slice(0, 200), unit: s.unit.trim().slice(0, 20) || null, ...(s.note ? { note: s.note.slice(0, 200) } : {}) });
  }
  const dims: Record<string, number> = {};
  for (const x of d.dims) {
    const key = x.key.trim().toLowerCase();
    if (!key && !x.value.trim()) continue;
    const n = Number(x.value);
    if (!DIM_KEY.test(key)) errors.push(`"${key || "(empty)"}" is not a dimension name (lowercase, e.g. length_mm).`);
    else if (x.value.trim() === "" || !Number.isFinite(n)) errors.push(`${key} needs a number.`);
    else dims[key] = n;
  }
  return { fields: { name, category: d.category, vendor: d.vendor.trim() || null, specs, dims }, errors };
}

/** Only the fields that changed, for PATCH /catalog/items/{id}. */
export function editDiff(item: CatalogItem, fields: ReturnType<typeof parseDraft>["fields"]): CatalogEdit {
  const out: CatalogEdit = {};
  if (fields.name !== item.name) out.name = fields.name;
  if (fields.category !== item.category) out.category = fields.category;
  if ((fields.vendor ?? null) !== (item.vendor ?? null)) out.vendor = fields.vendor;
  const norm = (s: CatalogSpec[]) => JSON.stringify(s.map((x) => [x.key, x.value, x.unit ?? null]));
  if (norm(fields.specs) !== norm(item.specs)) out.specs = fields.specs;
  const sortDims = (d: Record<string, number>) => JSON.stringify(Object.entries(d).sort(([a], [b]) => a.localeCompare(b)));
  if (sortDims(fields.dims) !== sortDims(item.dims)) out.dims = fields.dims;
  return out;
}
