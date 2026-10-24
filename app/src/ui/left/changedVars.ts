import type { Step } from "../../api/types";

export interface VarChange { name: string; now: number; prev: number | null; runId: string; stepId: number; ts: number }

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);

/** output.data of a result step (tool_result / diff / ...), or null. */
function outData(s: Step): Rec | null {
  const out = s.payload["output"];
  if (!isRec(out)) return null;
  const d = out["data"];
  return isRec(d) ? d : null;
}

function numVars(v: unknown): Record<string, number> {
  const r: Record<string, number> = {};
  if (isRec(v)) for (const [k, x] of Object.entries(v)) if (typeof x === "number") r[k] = x;
  return r;
}

const callIdOf = (s: Step): string | null => (typeof s.payload["call_id"] === "string" ? s.payload["call_id"] : null);

/**
 * Every variable change in one pass over a run's steps, by the diff step that made it. For each
 * tool_call (name=mirror_part_set_vars) the names come from input.vars; `now` is the value in the
 * following diff step's output.data.vars (paired by call_id; steps without a call_id never pair);
 * `prev` is the latest earlier create/set_vars output value for that part, else null.
 */
export function varChangesByStep(runSteps: readonly Step[], onlyPart?: string): Map<number, VarChange[]> {
  const out = new Map<number, VarChange[]>();
  const known = new Map<string, Record<string, number>>();             // part → latest vars seen
  const pending = new Map<string, { part: string; names: string[] }>(); // call_id → vars being set
  for (const s of runSteps) {
    const callId = callIdOf(s);
    if (s.kind === "tool_call") {
      const input = s.payload["input"];
      const part = isRec(input) ? input["part_id"] : undefined;
      if (callId && s.payload["name"] === "mirror_part_set_vars" && isRec(input) && typeof part === "string" && (onlyPart === undefined || part === onlyPart)) {
        pending.set(callId, { part, names: Object.keys(numVars(input["vars"])) });
      }
      continue;
    }
    const data = outData(s);
    const part = data ? data["part_id"] : undefined;
    if (!data || typeof part !== "string" || (onlyPart !== undefined && part !== onlyPart)) continue;
    const vars = numVars(data["vars"]);
    const seen = known.get(part) ?? {};
    const p = callId ? pending.get(callId) : undefined;
    if (p && callId && p.part === part && s.kind === "diff") {
      const changes = p.names.filter((n) => n in vars)
        .map((name) => ({ name, now: vars[name], prev: name in seen ? seen[name] : null, runId: s.run_id, stepId: s.id, ts: s.ts }));
      if (changes.length) out.set(s.id, changes);
      pending.delete(callId);
    }
    if (s.payload["name"] === "mirror_part_create" || s.payload["name"] === "mirror_part_set_vars") known.set(part, { ...seen, ...vars });
  }
  return out;
}

/** Variables of `partId` changed by mirror_part_set_vars in this run; the latest change per name wins. */
export function changedVars(partId: string, runSteps: readonly Step[]): Map<string, VarChange> {
  const result = new Map<string, VarChange>();
  for (const changes of varChangesByStep(runSteps, partId).values()) for (const c of changes) result.set(c.name, c);
  return result;
}
