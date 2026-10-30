// Home (the launcher): the pure logic behind the build-tile grid — searching, sorting, the
// lead-agent status dot and the "last edited" label. Kept apart from the component so it is
// tested without a DOM. Build names are user text, matched as plain strings, never as markup.
import type { Build } from "../../../api/builds";
import type { LedTone } from "../../common/Led";

export type HomeSort = "edited" | "name" | "created";

export const SORTS: readonly { id: HomeSort; label: string }[] = [
  { id: "edited", label: "Last edited" },
  { id: "name", label: "Name" },
  { id: "created", label: "Date created" },
];

export interface HomeView {
  query: string;
  sort: HomeSort;
  /** Show the Trash instead of the live builds. */
  trash: boolean;
}

export const DEFAULT_VIEW: HomeView = { query: "", sort: "edited", trash: false };

/** The builds a view shows: live or trashed, name-filtered, then sorted. */
export function viewBuilds(builds: readonly Build[], view: HomeView): Build[] {
  const q = view.query.trim().toLowerCase();
  const pool = builds.filter((b) => (view.trash ? b.trashed_at !== null : b.trashed_at === null));
  const hit = q ? pool.filter((b) => b.name.toLowerCase().includes(q)) : pool;
  const sorted = [...hit];
  if (view.sort === "name") sorted.sort((a, b) => a.name.localeCompare(b.name));
  else if (view.sort === "created") sorted.sort((a, b) => b.created_at - a.created_at);
  else sorted.sort((a, b) => b.updated_at - a.updated_at);
  return sorted;
}

/** A run as the status dot needs it (store AgentRun, narrowed for testing). */
export interface RunLike { project_id?: string | null; status: string }

/**
 * The lead-agent status dot for a build, from its runs:
 *   green  — an agent is working on this build now
 *   amber  — an agent is queued to work on it
 *   faint  — idle
 * (Blue stays reserved for "needs you", which is surfaced in the console, not on a tile.)
 */
export function buildStatus(buildId: string, runs: Iterable<RunLike>): { tone: LedTone; label: string } {
  let running = false;
  let queued = false;
  for (const r of runs) {
    if ((r.project_id ?? null) !== buildId) continue;
    if (r.status === "running") running = true;
    else if (r.status === "queued") queued = true;
  }
  if (running) return { tone: "green", label: "An agent is working" };
  if (queued) return { tone: "amber", label: "An agent is queued" };
  return { tone: "faint", label: "Idle" };
}

const MIN = 60;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** A short "last edited" label from an epoch-seconds timestamp. */
export function fmtLastEdited(ts: number, now: number = Date.now() / 1000): string {
  const d = Math.max(0, now - ts);
  if (d < MIN) return "just now";
  if (d < HOUR) return `${Math.floor(d / MIN)} min ago`;
  if (d < DAY) { const h = Math.floor(d / HOUR); return `${h} h ago`; }
  if (d < 7 * DAY) { const n = Math.floor(d / DAY); return `${n} day${n === 1 ? "" : "s"} ago`; }
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** "3 parts" / "1 part". */
export function partsLabel(n: number): string {
  return `${n} part${n === 1 ? "" : "s"}`;
}
