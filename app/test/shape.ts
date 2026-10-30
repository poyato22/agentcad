// Recursive shape diff between the real core's answers (fixtures) and the fake core's, shared by
// the contract tests (contract.test.ts, contract.print.test.ts).
export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

function typeOf(v: unknown): string {
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  return typeof v;
}

/** Objects whose keys are data (variable names, tool inputs, approval payloads), not schema. */
const OPEN_MAPS = [/\.vars$/, /\.deltas$/, /^approvals\[\]\.payload$/, /\.edited_payload$/];

/** Keys only some elements of a list carry by design: U2a's `radius` is on cylinder faces and
 *  circle edges only, so a fake face list paired with a real part that has none is not drift. */
function optionalKey(path: string, key: string): boolean {
  return key === "radius" && /\.(face_list|edge_list)\[\]$/.test(path);
}

/** A step / event pairs with the fixture element of the same kind and tool (or role). */
export function discriminator(v: unknown): string | null {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const p = (typeof o.payload === "object" && o.payload !== null ? o.payload : {}) as Record<string, unknown>;
  // an announcement step (U3c / G1 cards, {"agent_announce": {kind, ...}}) pairs by its card kind
  const ann = p.agent_announce as Record<string, unknown> | undefined;
  if (typeof o.kind === "string" && ann && typeof ann.kind === "string") return `${o.kind}:announce:${ann.kind}`;
  if (typeof o.kind === "string") return `${o.kind}:${String(p.name ?? p.role ?? "")}`;
  // /models vendors and /keys rows: each pairs with the same vendor (claude-code has `models`)
  if (typeof o.vendor === "string") return `vendor:${o.vendor}`;
  return null;
}

/** Paths where `actual` (fake core) differs in keys or value types from `expected` (real core). null on either side is
 *  accepted (nullable fields; zod checks those). */
export function shapeDiff(expected: unknown, actual: unknown, path = "$", out: string[] = []): string[] {
  const te = typeOf(expected);
  const ta = typeOf(actual);
  if (te === "null" || ta === "null") return out;
  if (te !== ta) { out.push(`${path}: ${ta} (real core: ${te})`); return out; }
  if (te === "array") {
    const e = expected as unknown[];
    const a = actual as unknown[];
    if (e.length === 0 || a.length === 0) return out;
    a.forEach((item, i) => {
      const d = discriminator(item);
      // no discriminator: the real element it differs from least (a key only some real elements
      // carry, like an iteration's `sims_removed`, must not read as drift on the others)
      const match = d === null ? closest(e, item, `${path}[]`) : e.find((x) => discriminator(x) === d);
      if (match === undefined) { out.push(`${path}[${i}]: ${d} has no counterpart in the real core's answer`); return; }
      shapeDiff(match, item, `${path}[]`, out);
    });
    return out;
  }
  if (te === "object") {
    const e = expected as Record<string, unknown>;
    const a = actual as Record<string, unknown>;
    if (OPEN_MAPS.some((re) => re.test(path))) {
      const sample = Object.values(e).find((v) => v !== null);
      if (sample !== undefined) for (const [k, v] of Object.entries(a)) shapeDiff(sample, v, `${path}.${k}`, out);
      return out;
    }
    for (const k of Object.keys(e)) if (!(k in a)) out.push(`${path}.${k}: missing (real core has it)`);
    for (const k of Object.keys(a)) if (!(k in e) && !optionalKey(path, k)) out.push(`${path}.${k}: extra (real core lacks it)`);
    for (const k of Object.keys(e)) if (k in a) shapeDiff(e[k], a[k], `${path}.${k}`, out);
  }
  return out;
}

function closest(candidates: unknown[], item: unknown, path: string): unknown {
  // a typed element (a face or edge: `type` "cylinder", "circle" ...) pairs with a real one of the
  // same type when there is one, so a key only that type carries (U2a's `radius`) is compared
  // with its own kind, not with a plane
  const t = typeof item === "object" && item !== null ? (item as Record<string, unknown>).type : undefined;
  if (typeof t === "string") {
    const same = candidates.filter((c) => typeof c === "object" && c !== null && (c as Record<string, unknown>).type === t);
    if (same.length) candidates = same;
  }
  let best = candidates[0];
  let bestN = Infinity;
  for (const c of candidates.length > 50 ? candidates.slice(0, 50) : candidates) {
    const n = shapeDiff(c, item, path).length;
    if (n < bestN) { best = c; bestN = n; }
    if (n === 0) break;
  }
  return best;
}
