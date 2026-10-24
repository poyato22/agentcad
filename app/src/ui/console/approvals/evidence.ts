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

const same = (x: unknown, y: unknown): boolean => JSON.stringify(x) === JSON.stringify(y);

/** The call's input agrees with the approval payload on every key it sets (the payload is the
 *  gate's model_dump of the input, so it may add defaults). */
function matchesPayload(call: Step, payload: Record<string, unknown>): boolean {
  const input = rec(call.payload.input);
  if (!input) return false;
  return Object.entries(input).every(([k, v]) => same(payload[k], v));
}

/**
 * The run's evidence steps between the previous call of the same action and the call that asked
 * for this approval (matched by payload, else the latest such call). If that call isn't loaded
 * yet, every step so far counts as before it.
 */
export function evidenceFor(a: Approval, runSteps: readonly Step[]): EvidenceLink[] {
  const mine = runSteps.filter((s) => s.run_id === a.run_id);
  const calls = mine.filter((s) => s.kind === "tool_call" && s.payload.name === a.action).sort((x, y) => x.id - y.id);
  const matching = calls.filter((c) => matchesPayload(c, a.payload));
  const own = matching.length ? matching[matching.length - 1] : calls[calls.length - 1];
  const upper = own ? own.id : Infinity;
  const lower = own ? calls.filter((c) => c.id < own.id).reduce((m, c) => Math.max(m, c.id), -Infinity) : -Infinity;
  return mine
    .filter((s) => EVIDENCE_KINDS.has(s.kind) && s.id < upper && s.id > lower)
    .sort((x, y) => x.id - y.id)
    .map((s) => ({ stepId: s.id, kind: s.kind, label: `${s.kind} · ${subject(s)}`, hash8: shortHash(s.evidence_hash) }));
}
