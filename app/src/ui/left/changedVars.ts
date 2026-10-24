import type { Step } from "../../api/types";

export interface VarChange { name: string; now: number; prev: number | null; runId: string; stepId: number }

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

/**
 * Variables of `partId` changed by mirror_part_set_vars in this run. For each tool_call
 * (name=mirror_part_set_vars, input.part_id=partId) the names come from input.vars; `now` is the value in
 * the following diff step's output.data.vars; `prev` is the latest earlier create/set_vars output value
 * for that part, else null. The latest change per name wins. Calls without a diff yet are skipped.
 */
export function changedVars(partId: string, runSteps: readonly Step[]): Map<string, VarChange> {
  const result = new Map<string, VarChange>();
  const known: Record<string, number> = {};       // latest vars seen so far for this part
  const pending = new Map<string, string[]>();    // call_id → variable names being set
  for (const s of runSteps) {
    if (s.kind === "tool_call") {
      const input = s.payload["input"];
      if (s.payload["name"] === "mirror_part_set_vars" && isRec(input) && input["part_id"] === partId) {
        pending.set(String(s.payload["call_id"]), Object.keys(numVars(input["vars"])));
      }
      continue;
    }
    const data = outData(s);
    if (!data || data["part_id"] !== partId) continue;
    const vars = numVars(data["vars"]);
    const callId = String(s.payload["call_id"]);
    const names = pending.get(callId);
    if (names && s.kind === "diff") {
      for (const name of names) {
        if (!(name in vars)) continue;
        result.set(name, { name, now: vars[name], prev: name in known ? known[name] : null, runId: s.run_id, stepId: s.id });
      }
      pending.delete(callId);
    }
    if (s.payload["name"] === "mirror_part_create" || s.payload["name"] === "mirror_part_set_vars") Object.assign(known, vars);
  }
  return result;
}
