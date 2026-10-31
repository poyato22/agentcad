// Where each announcement card jumps (R16 / I5). The surface that owns a target registers its
// resolver here (the assembly view registers assembly_added), so the console never imports a
// page; kinds whose page lands with another task are listed in PENDING_JUMPS until it does.
import type { AnnounceKind, AnnounceRef } from "../../api/types";
import { actions } from "../../state/store";
import { setPage } from "../pages/router";

export interface JumpInput { kind: string; ref: AnnounceRef; projectId: string | null; jointId: string | null }
export interface Jump { label: string; go: () => void }
type Resolver = (a: JumpInput) => Jump | null;

const resolvers = new Map<string, Resolver>();

/** A surface's own jump for `kind` (it wins over the defaults). Returns an unregister. */
export function registerAnnounceJump(kind: string, r: Resolver): () => void {
  resolvers.set(kind, r);
  return () => { if (resolvers.get(kind) === r) resolvers.delete(kind); };
}

const showPart = (id: string): Jump => ({ label: "Show part", go: () => { setPage("design"); actions.selectPart(id); } });
const openAgent = (id: string): Jump => ({ label: "Open agent", go: () => actions.selectRun(id) });

/** The defaults, for targets the console can reach itself. */
const DEFAULTS: Partial<Record<AnnounceKind, Resolver>> = {
  agent_spawned: (a) => (a.ref.run_id ? openAgent(a.ref.run_id) : null),
  part_created: (a) => (a.ref.part_id ? showPart(a.ref.part_id) : null),
  // the instance itself once the assembly view registers its resolver; the part until then
  assembly_added: (a) => (a.ref.part_id ? showPart(a.ref.part_id) : null),
  drawing_imported: (a) => (a.ref.part_id ? showPart(a.ref.part_id) : null),
};

/** Kinds with no in-app target yet, and the task that brings it (an explicit U7 item, never a
 *  silent gap). Each still jumps to its part when the ref names one. */
export const PENDING_JUMPS: Readonly<Partial<Record<AnnounceKind, string>>> = {
  bom_added: "U4a: the parts list row (ref.bom_id)",
  part_shown: "U4a: the part card in the thread (ref.snapshot_id / catalog_id)",
  drawing_imported: "U5: the import result card (ref.import_id) when no part was built",
  view_presented: "Plan 8 G1: the presented view",
  critique_round: "Plan 8 G1: the critique round",
  concept_sketch: "Plan 9 G-A: the card is the sketch itself (it opens the full-window review)",
  concept_pitch: "Plan 9 G-A: the card is the pitch itself (pick or mix on it)",
};

export function jumpFor(a: JumpInput): Jump | null {
  const own = resolvers.get(a.kind)?.(a) ?? null;
  if (own) return own;
  const d = DEFAULTS[a.kind as AnnounceKind]?.(a) ?? null;
  if (d) return d;
  if (a.ref.part_id) return showPart(a.ref.part_id);
  return null;
}
