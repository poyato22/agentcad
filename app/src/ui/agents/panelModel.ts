// Pure helpers for the Agents panel (R10): one easy view of a build's agents — the long-lived
// lead on top, the sub-agents it spawned indented beneath. The data is GET /projects/{id}/agents
// (U3c run_view nodes with role and task title); everything here is pure so the view stays thin.
import type { AgentRun, RunNode } from "../../api/types";
import { flattenTree, runTitle, type TreeRow } from "./manager";

/** Roots with the lead first, then the rest in the tree's own order. */
export function leadFirst(roots: readonly RunNode[]): RunNode[] {
  return [...roots].sort((a, b) => Number(b.role === "lead") - Number(a.role === "lead"));
}

/** Depth-first rows for a build's agents: the lead, then its sub-tree indented. A collapsed lead
 *  hides its subs. */
export function agentRows(roots: readonly RunNode[], collapsed: ReadonlySet<string> = new Set()): TreeRow[] {
  return flattenTree(leadFirst(roots), collapsed);
}

/** The row's label: a sub-agent's task title ("design the gripper"), else the run's own title. */
export function agentRowTitle(run: AgentRun): string {
  const t = run.task_title?.trim();
  return run.role === "sub" && t ? t : runTitle(run);
}

/** A short word for the row's role, or null for an ordinary run (no role set). */
export function roleWord(run: AgentRun): "lead" | "sub" | null {
  return run.role ?? null;
}
