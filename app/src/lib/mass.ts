// Plan 9 B2 (D8): one place that reads a part's mass. The core weighs every tree row
// (core mises.mass): a built part is volume x its material's density, a group or parent is the
// sum of its subtree, a catalog part keeps its stated mass; anything else is null with a reason.
// An older core sends no `mass_g`: the build's own metrics.mass_g is used then.
import type { PartSummary } from "../api/types";
import { fmtGrams } from "./format";

const DASH = "—";

/** The part's mass in g, or null when unknown. */
export function partMassG(p: Pick<PartSummary, "mass_g" | "metrics">): number | null {
  if (p.mass_g !== undefined) return p.mass_g ?? null;
  const m = p.metrics?.mass_g;
  return typeof m === "number" && Number.isFinite(m) ? m : null;
}

/** "—" when unknown; with `digits`, fixed grams ("12.40 g") up to 1 kg; without, about three
 *  significant figures: "180 g", "12.3 g", "0.42 g". From 1 kg on: "1.24 kg", "12.5 kg". */
export function fmtMass(g: number | null, digits?: number): string {
  if (g === null || !Number.isFinite(g)) return DASH;
  const a = Math.abs(g);
  if (a >= 1000) return `${(g / 1000).toFixed(a >= 10_000 ? 1 : 2)} kg`;
  if (digits !== undefined) return fmtGrams(g, digits);
  if (a >= 100) return `${Math.round(g)} g`;
  if (a >= 10) return `${g.toFixed(1)} g`;
  return `${g.toFixed(a >= 1 ? 1 : 2)} g`;
}

/** The text a mass cell shows: the mass, "Σ" in front for a group's / parent's sum, or "—". */
export function partMassText(p: Pick<PartSummary, "mass_g" | "metrics" | "mass_source">, digits?: number): string {
  const g = partMassG(p);
  if (g === null) return DASH;
  return p.mass_source === "sum" ? `Σ ${fmtMass(g, digits)}` : fmtMass(g, digits);
}

/** Why the mass is what it is (hover text): the core's note, or a fallback for an older core. */
export function partMassNote(p: Pick<PartSummary, "mass_g" | "metrics" | "mass_note" | "kind">): string | null {
  if (p.mass_note) return p.mass_note;
  if (partMassG(p) !== null) return null;
  if (p.kind === "group") return "empty group";
  return p.metrics ? null : "not built yet";
}

/** The material a part's mass uses: its own, else the build default (`fallback`). */
export function partMaterialText(p: Pick<PartSummary, "material">, fallback: string | null): string {
  if (p.material) return p.material;
  return fallback === null ? DASH : `${fallback} (build default)`;
}

/** Plan 9 B2: the assembly total's hover text: which parts it leaves out, and why. */
export function assemblyMassTitle(unknown: readonly { part_id: string; count: number; reason: string }[]): string {
  if (unknown.length === 0) return "Total mass: every instance x its part's mass";
  return "Total mass leaves out " + unknown.map((u) => `${u.part_id}${u.count > 1 ? ` (x${u.count})` : ""}: ${u.reason}`).join("; ");
}
