// I2: a message to a build's lead goes through POST /runs, and the core may answer with another
// run: after a core restart (or any time the old lead can't be reached) it archives that lead and
// starts a new one (`new_lead`). The console then follows the new run, so the message the user
// just sent stays on screen and the next one doesn't hit the archived run ("unknown run").
import type { AgentRun, CreateRunResult } from "../../api/types";
import { actions } from "../../state/store";
import { noteRun } from "./queued";

export const NEW_LEAD_NOTE = "a new lead took over this build · the earlier conversation is kept under Agents";

/** Should the console switch to `res.run_id` after messaging `from`? */
export function leadChanged(from: Pick<AgentRun, "id">, res: Pick<CreateRunResult, "run_id" | "new_lead">): boolean {
  return res.run_id !== from.id || res.new_lead === true;
}

/** Follow the lead POST /runs answered with. Returns the one-line note to show, or null when the
 *  same lead took the message. */
export function followLead(from: Pick<AgentRun, "id">, res: Pick<CreateRunResult, "run_id" | "new_lead">): string | null {
  if (!leadChanged(from, res)) return null;
  actions.selectRun(res.run_id);
  noteRun(res.run_id);          // shown at once, even before run_started reaches the store
  actions.setFollow(true);
  return res.new_lead ? NEW_LEAD_NOTE : null;
}

/** A lead this core can't reach (owned by an earlier core process, or already archived): the next
 *  message starts a new lead, so the command line says so instead of naming the old one. Only
 *  decided when the core's pid is known (the shell reports it); a browser session can't tell. */
export function leadUnreachable(run: AgentRun, pid: number | null): boolean {
  if (run.role !== "lead") return false;
  if (run.archived) return true;
  if (run.provider === "claude-code") return false;  // the core resumes its session
  return pid !== null && run.owner_pid != null && run.owner_pid !== pid;
}
