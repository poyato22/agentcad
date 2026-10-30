// Plan 8 G3's layout skeleton as the app sees it (I4b). The skeleton is a part-tree row of kind
// "skeleton" with no model, so the viewer never loads a GLB for it; it shows its parameters, datums
// and envelope instead. The core has no read route for it, so its data comes from the latest
// skeleton_set / skeleton_get result in the steps the app has loaded (output.data is
// skeleton_tools._view: {project_id, skeleton_id, version, skeleton: {params, datums, envelope, curves}}).
import type { Step } from "../../api/types";

export interface SkeletonDatum { kind: "plane" | "axis" | "point"; origin: number[]; normal?: number[] | null; direction?: number[] | null }
export interface SkeletonView {
  version: number;
  params: [string, number][];
  datums: [string, SkeletonDatum][];
  envelope: { min: number[]; max: number[] } | null;
  curves: string[];
}

const isRec = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const nums = (v: unknown): number[] | null => (Array.isArray(v) && v.every((x) => typeof x === "number") ? v as number[] : null);

/** The envelope as min/max corners, from either {min, max} or {center, size}. */
function envelopeOf(e: unknown): SkeletonView["envelope"] {
  if (!isRec(e)) return null;
  const min = nums(e["min"]); const max = nums(e["max"]);
  if (min && max) return { min, max };
  const c = nums(e["center"]); const s = nums(e["size"]);
  if (c && s && c.length === 3 && s.length === 3) return { min: c.map((x, i) => x - s[i] / 2), max: c.map((x, i) => x + s[i] / 2) };
  return null;
}

/** Parse a skeleton tool result's data; null when it isn't one for `skeletonId`. */
export function parseSkeleton(data: unknown, skeletonId: string): SkeletonView | null {
  if (!isRec(data) || data["skeleton_id"] !== skeletonId) return null;
  const sk = data["skeleton"];
  const version = typeof data["version"] === "number" ? data["version"] : 0;
  if (!isRec(sk)) return { version, params: [], datums: [], envelope: null, curves: [] };
  const params = isRec(sk["params"]) ? Object.entries(sk["params"]).filter((e): e is [string, number] => typeof e[1] === "number") : [];
  const datums = isRec(sk["datums"])
    ? Object.entries(sk["datums"]).flatMap(([k, d]): [string, SkeletonDatum][] => {
      if (!isRec(d) || (d["kind"] !== "plane" && d["kind"] !== "axis" && d["kind"] !== "point")) return [];
      return [[k, { kind: d["kind"], origin: nums(d["origin"]) ?? [0, 0, 0], normal: nums(d["normal"]), direction: nums(d["direction"]) }]];
    })
    : [];
  const curves = isRec(sk["curves"]) ? Object.keys(sk["curves"]) : [];
  return { version, params, datums, envelope: envelopeOf(sk["envelope"]), curves };
}

const SKELETON_TOOLS = new Set(["skeleton_set", "skeleton_get"]);

/** The newest skeleton the loaded steps show for `skeletonId` (highest version, then newest step). */
export function skeletonFromSteps(all: Iterable<readonly Step[]>, skeletonId: string): SkeletonView | null {
  let best: SkeletonView | null = null;
  let bestId = -1;
  for (const list of all) {
    for (const s of list) {
      if (s.kind !== "tool_result" || !SKELETON_TOOLS.has(String(s.payload["name"]))) continue;
      const out = s.payload["output"];
      const v = parseSkeleton(isRec(out) ? out["data"] : null, skeletonId);
      if (!v) continue;
      if (!best || v.version > best.version || (v.version === best.version && s.id > bestId)) { best = v; bestId = s.id; }
    }
  }
  return best;
}

const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/0+$/, "").replace(/\.$/, ""));
export const vec = (v: readonly number[] | null | undefined): string => (v ? `(${v.map(fmt).join(", ")})` : "—");
export const num = fmt;
