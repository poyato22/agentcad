// Pure helpers for the Agents panel (R10): one easy view of a build's agents — the long-lived
// lead on top, the sub-agents it spawned indented beneath, then the build's other runs. The data
// is GET /projects/{id}/agents (U3c lead.agents_view: {project_id, lead, others}).
import type { AgentNode, AgentsView } from "../../api/types";

/** One visible row: a node, its depth under its group's root, and its fold state. */
export interface AgentRow { node: AgentNode; depth: number; hasChildren: boolean; expanded: boolean }

/** Depth-first rows of some roots; a collapsed node hides its sub-agents (open by default). */
export function treeRows(roots: readonly AgentNode[], collapsed: ReadonlySet<string> = new Set(), depth = 0, out: AgentRow[] = []): AgentRow[] {
  for (const n of roots) {
    const hasChildren = n.children.length > 0;
    const expanded = hasChildren && !collapsed.has(n.id);
    out.push({ node: n, depth, hasChildren, expanded });
    if (expanded) treeRows(n.children, collapsed, depth + 1, out);
  }
  return out;
}

/** The lead's rows (it and its sub-tree) and the other runs' rows, as the panel groups them.
 *  Archived other runs are left out unless asked for. */
export function viewRows(view: AgentsView, collapsed: ReadonlySet<string> = new Set(), showArchived = false): { lead: AgentRow[]; others: AgentRow[]; earlier: AgentRow[] } {
  const others = showArchived ? view.others : view.others.filter((o) => !o.archived);
  // I2: a lead retired by the core (e.g. after a restart it can't reach) is archived; it stays in
  // the panel as an "earlier" lead with its sub-agents, so the build's history isn't lost.
  const earlier = showArchived ? [] : view.others.filter((o) => o.archived && o.role === "lead");
  return { lead: view.lead ? treeRows([view.lead], collapsed) : [], others: treeRows(others, collapsed), earlier: treeRows(earlier, collapsed) };
}

/** The row's label: the user's rename, else the core's task title (a sub-agent's "Design motor
 *  mount", a lead's first prompt line), else the agent name. */
export function agentRowTitle(n: Pick<AgentNode, "title" | "task" | "agent">): string {
  return n.title?.trim() || n.task?.trim() || n.agent;
}

/** Every node of a view (lead tree and others), for lookups by id. */
export function allNodes(view: AgentsView): AgentNode[] {
  const out: AgentNode[] = [];
  const walk = (ns: readonly AgentNode[]) => { for (const n of ns) { out.push(n); walk(n.children); } };
  walk(view.lead ? [view.lead, ...view.others] : view.others);
  return out;
}

/** "2 messages waiting", or null when the lead has none. */
export function waitingText(n: Pick<AgentNode, "queued">): string | null {
  const k = n.queued?.length ?? 0;
  return k ? `${k} message${k === 1 ? "" : "s"} waiting` : null;
}
