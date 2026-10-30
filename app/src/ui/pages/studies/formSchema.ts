// The settings form, generically: the study types' pydantic JSON schemas (GET /sims/types) →
// fields, defaults and validation. No DOM here (SchemaForm.tsx renders it). The core validates
// every POST /sims again with the same models; this only says the problems before the round trip,
// at the field they belong to.
//
// Supported schema shapes (everything core sim/settings.py emits): $ref into $defs, object,
// number / integer (minimum, maximum, exclusiveMinimum, exclusiveMaximum), string (enum, const,
// minLength, maxLength), boolean, array (items, prefixItems tuples, minItems, maxItems), anyOf
// (nullable fields, and "a direction name or a vector"), and oneOf with a discriminator (loads).
import type { JsonSchema } from "../../../api/studies";

export type Path = readonly (string | number)[];
export interface FieldError { path: string; message: string }

export const pathKey = (p: Path): string => p.join(".");

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** Follows a "#/$defs/Name" reference against the root schema. */
export function deref(node: JsonSchema | undefined, root: JsonSchema): JsonSchema {
  let n: JsonSchema = node ?? {};
  for (let i = 0; i < 8 && typeof n.$ref === "string"; i++) {
    const name = n.$ref.replace(/^#\/\$defs\//, "");
    const defs = isObj(root.$defs) ? root.$defs : {};
    const target = defs[name];
    if (!isObj(target)) return {};
    // keep the referring node's own keys (description, default) over the target's
    const { $ref: _ref, ...own } = n;
    n = { ...target, ...own };
  }
  return n;
}

const branches = (n: JsonSchema, key: "anyOf" | "oneOf"): JsonSchema[] =>
  Array.isArray(n[key]) ? (n[key] as unknown[]).filter(isObj) : [];

/** anyOf [X, {type: "null"}] → X (null allowed); anything else → null. */
export function nullableInner(n: JsonSchema, root: JsonSchema): JsonSchema | null {
  const bs = branches(n, "anyOf");
  if (bs.length !== 2) return null;
  const nonNull = bs.filter((b) => b.type !== "null");
  if (nonNull.length !== 1) return null;
  const inner = deref(nonNull[0], root);
  return { ...inner, ...(n.description !== undefined ? { description: n.description } : {}), ...(n.unit !== undefined ? { unit: n.unit } : {}) };
}

/** anyOf [{enum: ["+x",…]}, {prefixItems: [3 numbers]}]: a direction field. */
export function isDirection(n: JsonSchema): boolean {
  const bs = branches(n, "anyOf");
  return bs.length === 2 && bs.some((b) => Array.isArray(b.enum)) && bs.some((b) => Array.isArray(b.prefixItems));
}

export function directionNames(n: JsonSchema): string[] {
  const b = branches(n, "anyOf").find((x) => Array.isArray(x.enum));
  return b ? (b.enum as unknown[]).map(String) : [];
}

export interface Discriminated { prop: string; options: { value: string; schema: JsonSchema }[] }

/** oneOf + discriminator {propertyName, mapping} → the options by their tag value. */
export function discriminated(n: JsonSchema, root: JsonSchema): Discriminated | null {
  const d = n.discriminator;
  if (!isObj(d) || typeof d.propertyName !== "string" || !isObj(d.mapping)) return null;
  // in the schema's oneOf order (the first is the default kind), not the mapping's
  const refs = branches(n, "oneOf").map((b) => String(b.$ref ?? ""));
  const entries = Object.entries(d.mapping).map(([value, ref]) => ({ value, ref: String(ref) }));
  entries.sort((a, b) => (refs.indexOf(a.ref) + 1 || 999) - (refs.indexOf(b.ref) + 1 || 999));
  const options = entries.map(({ value, ref }) => ({ value, schema: deref({ $ref: ref }, root) }));
  return { prop: d.propertyName, options };
}

export const isFaceKey = (key: string | number | undefined): boolean => key === "face" || key === "impact_face";

/** The object's properties in schema order, with whether each is required. */
export function properties(n: JsonSchema): { key: string; schema: JsonSchema; required: boolean }[] {
  const props = isObj(n.properties) ? n.properties : {};
  const req = new Set(Array.isArray(n.required) ? (n.required as unknown[]).map(String) : []);
  return Object.entries(props).filter(([, v]) => isObj(v)).map(([key, schema]) => ({ key, schema: schema as JsonSchema, required: req.has(key) }));
}

// ---- defaults

/** A value that satisfies the node: its default, else the smallest valid value of its type. */
export function defaultFor(node: JsonSchema, root: JsonSchema, key?: string | number): unknown {
  const n = deref(node, root);
  if ("default" in n) return structuredClone(n.default);
  if ("const" in n) return n.const;
  if (isDirection(n)) return "-z";
  if (nullableInner(n, root)) return null;
  const d = discriminated(n, root);
  if (d && d.options[0]) return defaultFor(d.options[0].schema, root);
  if (Array.isArray(n.enum) && n.enum.length) return n.enum[0];
  if (isFaceKey(key)) return key === "impact_face" ? "bottom" : "top";
  switch (n.type) {
    case "object": {
      const out: Record<string, unknown> = {};
      for (const p of properties(n)) {
        const v = defaultFor(p.schema, root, p.key);
        if (v !== undefined) out[p.key] = v;
      }
      return out;
    }
    case "array": {
      if (Array.isArray(n.prefixItems)) return (n.prefixItems as JsonSchema[]).map((s) => defaultFor(s, root));
      const min = typeof n.minItems === "number" ? n.minItems : 0;
      const items = isObj(n.items) ? (n.items as JsonSchema) : {};
      return Array.from({ length: min }, () => defaultFor(items, root));
    }
    case "number": case "integer": {
      if (typeof n.minimum === "number") return n.minimum;
      if (typeof n.exclusiveMinimum === "number") return n.exclusiveMinimum + 1;
      return 0;
    }
    case "boolean": return false;
    case "string": return typeof n.minLength === "number" && n.minLength > 0 ? "x".repeat(n.minLength) : "";
    default: return undefined;
  }
}

/** Merges `over` onto the schema's defaults (deeply for objects; arrays and scalars replace). */
export function withDefaults(schema: JsonSchema, over: Record<string, unknown> = {}): Record<string, unknown> {
  const base = defaultFor(schema, schema);
  return merge(isObj(base) ? base : {}, over) as Record<string, unknown>;
}

function merge(a: unknown, b: unknown): unknown {
  if (isObj(a) && isObj(b)) {
    const out: Record<string, unknown> = { ...a };
    for (const [k, v] of Object.entries(b)) out[k] = merge(a[k], v);
    return out;
  }
  return b === undefined ? a : structuredClone(b);
}

// ---- reading and writing at a path

export function getAt(v: unknown, p: Path): unknown {
  let cur = v;
  for (const k of p) {
    if (Array.isArray(cur) && typeof k === "number") cur = cur[k];
    else if (isObj(cur)) cur = cur[String(k)];
    else return undefined;
  }
  return cur;
}

/** A copy of `v` with `value` at `p` (objects and arrays on the way are copied, never mutated). */
export function setAt<T>(v: T, p: Path, value: unknown): T {
  if (p.length === 0) return value as T;
  const [k, ...rest] = p;
  if (Array.isArray(v)) {
    const out = [...v];
    out[k as number] = setAt(out[k as number], rest, value);
    return out as T;
  }
  const o: Record<string, unknown> = isObj(v) ? { ...v } : {};
  o[String(k)] = setAt(o[String(k)], rest, value);
  return o as T;
}

// ---- validation

const typeOk = (n: JsonSchema, v: unknown): boolean => {
  switch (n.type) {
    case "number": return typeof v === "number" && Number.isFinite(v);
    case "integer": return typeof v === "number" && Number.isInteger(v);
    case "string": return typeof v === "string";
    case "boolean": return typeof v === "boolean";
    case "object": return isObj(v);
    case "array": return Array.isArray(v);
    case "null": return v === null;
    default: return true;
  }
};

const fmt = (x: number) => String(Number(x.toPrecision(6)));

/** Every problem with `value` under `node`, at its path. Empty = valid. */
export function validate(value: unknown, node: JsonSchema, root: JsonSchema, path: Path = []): FieldError[] {
  const n = deref(node, root);
  const at = pathKey(path);
  const inner = nullableInner(n, root);
  if (inner) return value === null || value === undefined ? [] : validate(value, inner, root, path);
  const anyOf = branches(n, "anyOf");
  if (anyOf.length) {
    if (anyOf.some((b) => validate(value, b, root, path).length === 0)) return [];
    if (isDirection(n)) return [{ path: at, message: `use ${directionNames(n).join(", ")} or a non-zero vector [x, y, z]` }];
    return validate(value, anyOf[0], root, path);
  }
  const d = discriminated(n, root);
  if (d) {
    const tag = isObj(value) ? value[d.prop] : undefined;
    const opt = d.options.find((o) => o.value === tag);
    if (!opt) return [{ path: pathKey([...path, d.prop]), message: `pick one of ${d.options.map((o) => o.value).join(", ")}` }];
    return validate(value, opt.schema, root, path);
  }
  if ("const" in n) return value === n.const ? [] : [{ path: at, message: `must be ${String(n.const)}` }];
  if (value === undefined) return [];
  if (!typeOk(n, value)) {
    const what = n.type === "integer" ? "a whole number" : n.type === "number" ? "a number" : `a ${String(n.type)}`;
    return [{ path: at, message: `must be ${what}` }];
  }
  const out: FieldError[] = [];
  if (Array.isArray(n.enum) && !n.enum.includes(value)) out.push({ path: at, message: `pick one of ${n.enum.join(", ")}` });
  if (typeof value === "number") {
    if (typeof n.minimum === "number" && value < n.minimum) out.push({ path: at, message: `at least ${fmt(n.minimum)}` });
    if (typeof n.maximum === "number" && value > n.maximum) out.push({ path: at, message: `at most ${fmt(n.maximum)}` });
    if (typeof n.exclusiveMinimum === "number" && value <= n.exclusiveMinimum) out.push({ path: at, message: `more than ${fmt(n.exclusiveMinimum)}` });
    if (typeof n.exclusiveMaximum === "number" && value >= n.exclusiveMaximum) out.push({ path: at, message: `less than ${fmt(n.exclusiveMaximum)}` });
  }
  if (typeof value === "string") {
    if (typeof n.minLength === "number" && value.length < n.minLength) out.push({ path: at, message: n.minLength === 1 ? "required" : `at least ${n.minLength} characters` });
    if (typeof n.maxLength === "number" && value.length > n.maxLength) out.push({ path: at, message: `at most ${n.maxLength} characters` });
    if (isFaceKey(path[path.length - 1]) && !isFaceText(value)) out.push({ path: at, message: "a preset (top, bottom, x-min …) or a face id like f3" });
  }
  if (Array.isArray(value)) {
    if (typeof n.minItems === "number" && value.length < n.minItems) out.push({ path: at, message: `at least ${n.minItems}` });
    if (typeof n.maxItems === "number" && value.length > n.maxItems) out.push({ path: at, message: `at most ${n.maxItems}` });
    if (Array.isArray(n.prefixItems)) {
      (n.prefixItems as JsonSchema[]).forEach((s, i) => out.push(...validate(value[i], s, root, [...path, i])));
    } else if (isObj(n.items)) {
      const items = n.items as JsonSchema;
      const faceItems = path[path.length - 1] === "refine_faces";
      value.forEach((x, i) => {
        out.push(...validate(x, items, root, [...path, i]));
        if (faceItems && typeof x === "string" && !isFaceText(x)) out.push({ path: pathKey([...path, i]), message: "a preset or a face id like f3" });
      });
    }
  }
  if (isObj(value)) {
    const props = properties(n);
    for (const p of props) {
      if (p.required && value[p.key] === undefined) out.push({ path: pathKey([...path, p.key]), message: "required" });
      else out.push(...validate(value[p.key], p.schema, root, [...path, p.key]));
    }
    if (n.additionalProperties === false) {
      const known = new Set(props.map((p) => p.key));
      for (const k of Object.keys(value)) if (!known.has(k)) out.push({ path: pathKey([...path, k]), message: "not a setting of this study" });
    }
  }
  return out;
}

export const FACE_PRESETS = ["bottom", "top", "x-min", "x-max", "y-min", "y-max", "z-min", "z-max"] as const;
export const isFaceText = (s: string): boolean => (FACE_PRESETS as readonly string[]).includes(s) || /^f\d{1,5}$/.test(s);

// ---- labels

const UNIT_SUFFIX = /_(m_s|mm|n_mm|n|mpa|c|s|mb|m2|deg|hz|w_m2k|w_m2|g|pct|m|per_k|w_mk|g_cm3|kg_m3)$/;
const LABELS: Record<string, string> = {
  load_cases: "Load cases", supports: "Supports", loads: "Loads", n_modes: "Modes", band_hz: "Excitation band",
  yaw_deg: "Yaw sweep", impact_face: "Impact face", drop_m: "Drop height", k: "Dynamic factor k", mass_g: "Mass",
  e_mpa: "Young's modulus", nu: "Poisson's ratio", yield_mpa: "Yield", density_g_cm3: "Density", k_w_mk: "Conductivity",
  alpha_per_k: "Expansion", hdt_c: "HDT", infill_pct: "Infill", h_w_m2k: "Film coefficient", ambient_c: "Ambient",
  temp_c: "Temperature", w_m2: "Heat flux", n_mm: "Moment", ref_area_m2: "Reference area", refine_faces: "Refine faces",
  refine_factor: "Refine factor", second_order: "Second-order elements", size_mm: "Element size", tol_pct: "Tolerance",
  max_levels: "Max levels", timeout_s: "Timeout", memory_mb: "Memory budget", speed_m_s: "Wind speed",
  air_density: "Air density", air_viscosity: "Air viscosity", domain_multiplier: "Domain size", shape_class: "Shape class",
  fdm: "Print settings", custom: "Custom material", layer_orientation: "Layer orientation", rpm: "Motor speed",
  blades: "Blades", margin_pct: "Band margin", up: "Up (lift axis)", point: "Point",
};

export function labelFor(key: string | number, n: JsonSchema): string {
  if (typeof key === "number") return `#${key + 1}`;
  if (LABELS[key]) return LABELS[key];
  const base = key.replace(UNIT_SUFFIX, "").replace(/_/g, " ").trim() || String(n.title ?? key);
  return base.charAt(0).toUpperCase() + base.slice(1);
}

/** Settings that stay folded under "Advanced" (the rest shows at once). */
export const ADVANCED: ReadonlySet<string> = new Set([
  "mesh", "solver", "convergence", "air_density", "air_viscosity", "domain_multiplier", "turbulence", "iterations",
  "timeout_s", "memory_mb", "shape_class", "up", "ref_area_m2", "environment",
]);
