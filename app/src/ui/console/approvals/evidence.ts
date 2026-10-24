// Evidence for an approval card. The core has no evidence field: the evidence is the run's
// measure / verify / sim_run / diff steps recorded before the tool call that asked for approval.
import type { Approval, Step, StepKind } from "../../../api/types";
import { shortHash } from "../../../lib/format";
import { partLabel } from "../../../lib/names";

export interface EvidenceLink { stepId: number; kind: StepKind; label: string; hash8: string }

const EVIDENCE_KINDS: ReadonlySet<StepKind> = new Set<StepKind>(["measure", "verify", "sim_run", "diff"]);

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const rec = (v: unknown): Record<string, unknown> | null =>
  (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

function subject(s: Step): string {
  const data = rec(rec(s.payload.output)?.data);
  const partId = str(data?.part_id) ?? str(rec(s.payload.input)?.part_id);
  if (partId) return partLabel(partId);
  return str(s.payload.name) ?? "step";
}

export function evidenceFor(a: Approval, runSteps: readonly Step[]): EvidenceLink[] {
  const mine = runSteps.filter((s) => s.run_id === a.run_id);
  const calls = mine.filter((s) => s.kind === "tool_call" && s.payload.name === a.action).map((s) => s.id);
  // the tool_call step may not be loaded yet: then every step so far came before it
  const bound = calls.length ? Math.max(...calls) : Infinity;
  return mine
    .filter((s) => EVIDENCE_KINDS.has(s.kind) && s.id < bound)
    .sort((x, y) => x.id - y.id)
    .map((s) => ({ stepId: s.id, kind: s.kind, label: `${s.kind} · ${subject(s)}`, hash8: shortHash(s.evidence_hash) }));
}
