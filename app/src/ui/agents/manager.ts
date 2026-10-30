// Agent manager state and pure helpers. The manager is a slide-over reachable from every page:
// the top-bar activity pill, the run switcher's "Manage", and ⇧⌘A (⌘6/⌘7 are the Onshape and Parts pages). It shows each
// build's lead and sub-agents (GET /projects/{id}/agents); behind "Advanced", every run as the
// orchestrator → worker tree (GET /runs/tree) and the custom agent definitions.
// Everything here is pure or a signal, so the view stays thin and the rules are unit-tested.
import { signal, type Signal } from "@preact/signals";
import type { AgentRun, DeleteRefused, ModelsInfo, RunNode } from "../../api/types";
import { isTrustedKey, registerShortcut } from "../../lib/keys";
import { taskWords } from "../../lib/names";
import { modalOpen } from "../pages/router";

// "agents" is the simple per-build Agents panel (R10) and the only view by default; behind
// "Advanced" are "runs" (the dense run tree) and "definitions" (custom agent definitions).
export type ManagerTab = "agents" | "runs" | "definitions";

export const agentManagerOpen: Signal<boolean> = signal<boolean>(false);
export const agentManagerTab: Signal<ManagerTab> = signal<ManagerTab>("agents");

/** Remembered per Mac: the manager shows its Runs and Definitions tabs (I7). */
export const ADVANCED_KEY = "mises.agentManager.advanced";
function readAdvanced(): boolean {
  try { return localStorage.getItem(ADVANCED_KEY) === "1"; } catch { return false; }
}
export const managerAdvanced: Signal<boolean> = signal<boolean>(readAdvanced());
export function setManagerAdvanced(on: boolean): void {
  managerAdvanced.value = on;
  try { localStorage.setItem(ADVANCED_KEY, on ? "1" : "0"); } catch { /* storage unavailable: this session only */ }
  if (!on) agentManagerTab.value = "agents";
}

export function openAgentManager(tab: ManagerTab = "agents"): void {
  agentManagerTab.value = tab;
  if (tab !== "agents" && !managerAdvanced.peek()) setManagerAdvanced(true);
  agentManagerOpen.value = true;
}
export function closeAgentManager(): void { agentManagerOpen.value = false; }

/** ⇧⌘A toggles the manager (a real, non-repeat key; ignored while another modal is open). */
export function installManagerShortcut(deps: { isTrusted?: (e: KeyboardEvent) => boolean; isModalOpen?: () => boolean } = {}): () => void {
  const trusted = deps.isTrusted ?? isTrustedKey;
  const blocked = deps.isModalOpen ?? (() => modalOpen());
  return registerShortcut("Meta+Shift+A", (e) => {
    if (!trusted(e)) return;
    if (agentManagerOpen.peek()) { closeAgentManager(); return; }
    if (blocked()) return;
    openAgentManager();
  });
}

// ---- tree

/** One visible row of the run tree. */
export interface TreeRow { run: RunNode; depth: number; hasChildren: boolean; expanded: boolean }

/** Pinned first, then most recently active. */
export function sortRuns<T extends AgentRun>(list: readonly T[]): T[] {
  return [...list].sort((a, b) =>
    Number(!!b.pinned) - Number(!!a.pinned) || lastActive(b) - lastActive(a));
}

export function lastActive(r: AgentRun): number { return r.last_activity_at ?? r.updated_at; }

/** Depth-first rows; a collapsed node hides its workers. Workers are expanded by default. */
export function flattenTree(nodes: readonly RunNode[], collapsed: ReadonlySet<string>, depth = 0, out: TreeRow[] = []): TreeRow[] {
  for (const n of sortRuns(nodes)) {
    const hasChildren = n.children.length > 0;
    const expanded = hasChildren && !collapsed.has(n.id);
    out.push({ run: n, depth, hasChildren, expanded });
    if (expanded) flattenTree(n.children, collapsed, depth + 1, out);
  }
  return out;
}

/** Every id in a subtree (the node and its workers). */
export function subtreeIds(n: RunNode): string[] {
  return [n.id, ...n.children.flatMap(subtreeIds)];
}

/** Hide archived roots unless asked; a worker shows with its (visible) parent. */
export function visibleRoots(nodes: readonly RunNode[], showArchived: boolean): RunNode[] {
  return showArchived ? [...nodes] : nodes.filter((n) => !n.archived);
}

export interface BuildGroup { projectId: string | null; name: string; roots: RunNode[] }

/** Roots grouped by build, in build-name order; runs without a build go last. */
export function groupByBuild(roots: readonly RunNode[], names: ReadonlyMap<string, string>): BuildGroup[] {
  const groups = new Map<string | null, RunNode[]>();
  for (const r of roots) {
    const k = r.project_id ?? null;
    groups.set(k, [...(groups.get(k) ?? []), r]);
  }
  return [...groups.entries()]
    .map(([projectId, list]) => ({
      projectId, roots: list,
      name: projectId === null ? "No build" : names.get(projectId) ?? "Build",
    }))
    .sort((a, b) => Number(a.projectId === null) - Number(b.projectId === null) || a.name.localeCompare(b.name));
}

// ---- labels

/** The user's title, else the task words. */
export function runTitle(r: AgentRun): string {
  const t = r.title?.trim();
  return t ? t : taskWords(r.task) || r.agent;
}

/** "$0.04 · 12.3k tokens", "12 tokens", or "—". */
export function costText(r: AgentRun): string {
  const usd = r.cost ? r.cost.usd : r.usd;
  const tokens = r.cost ? r.cost.tokens : r.input_tokens + r.output_tokens;
  const parts: string[] = [];
  if (typeof usd === "number" && usd > 0) parts.push(usd < 0.01 ? "<$0.01" : `$${usd.toFixed(2)}`);
  if (tokens > 0) parts.push(`${tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : tokens} tokens`);
  return parts.length ? parts.join(" · ") : "—";
}

/** "just now", "4 min ago", "3 h ago", "2 d ago". */
export function agoText(ts: number, nowS: number = Date.now() / 1000): string {
  const s = Math.max(0, nowS - ts);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** The approval wording, shared by every refusal that involves a pending approval. */
export const REJECTED_NOT_APPLIED = "the pending change is then rejected, never applied";

function names(ids: readonly string[], label: (id: string) => string): string {
  const shown = ids.slice(0, 3).map(label).join(", ");
  return ids.length > 3 ? `${shown} and ${ids.length - 3} more` : shown;
}

/** Why a delete was refused, in plain words. `label` names a run id; `awaitingApproval` tells a
 *  blocking worker that waits for your approval (the core reports those as "running" too) from
 *  one that is still working. */
export function refusalText(refused: DeleteRefused, label: (id: string) => string,
  awaitingApproval: (id: string) => boolean = () => false): string {
  if (refused.reason === "pending_approval") {
    return `This run is waiting for your approval. Decide its card first, or use Cancel and delete — ${REJECTED_NOT_APPLIED}.`;
  }
  const kids = refused.children;
  if (kids.length === 0) return "This run is still working. Stop it first, or use Cancel and delete.";
  const waiting = kids.filter(awaitingApproval);
  const working = kids.filter((k) => !awaitingApproval(k));
  const parts: string[] = [];
  if (working.length) parts.push(`${working.length === 1 ? "is" : "are"} still working (${names(working, label)})`);
  if (waiting.length) parts.push(`${waiting.length === 1 ? "is" : "are"} waiting for your approval (${names(waiting, label)})`);
  const subject = (n: number) => (n === 1 ? "A worker of this run" : `${n} workers of this run`);
  const sentence = working.length && waiting.length
    ? `${subject(working.length)} ${parts[0]}, and ${waiting.length === 1 ? "one" : waiting.length} ${parts[1]}.`
    : `${subject(kids.length)} ${parts[0]}.`;
  if (waiting.length) return `${sentence} Stop or decide ${kids.length === 1 ? "it" : "them"} first, or use Cancel and delete — ${REJECTED_NOT_APPLIED}.`;
  return `${sentence} Stop ${kids.length === 1 ? "it" : "them"} first, or use Cancel and delete.`;
}

/** Every model ref the pickers offer ("inherit" first), from GET /models. */
export function modelRefs(models: ModelsInfo | null): string[] {
  const out = new Set<string>(["inherit"]);
  for (const v of models?.vendors ?? []) {
    for (const m of v.models ?? []) out.add(v.vendor === "claude-code" && !m.includes("/") ? `claude-code/${m}` : m);
  }
  if (models?.default) out.add(models.default);
  return [...out];
}

/** Agent names: the core's AGENT_NAME_RE (lowercase, digits, - and _, starting with a letter, 2–41 chars). */
export const AGENT_NAME_RE = /^[a-z][a-z0-9_-]{1,40}$/;
