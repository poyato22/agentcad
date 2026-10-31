// Builds (Plan 5 C1 projects): which builds exist and which one is open (active). The active build
// scopes the part tree (GET /parts?project_id), the History pane's runs and new runs (the core
// files a run without project_id under the profile's active build), so switching writes
// active_project_id back to the profile. Plan 9 D1 removed the build tabs: builds switch through
// Home, and the profile's old open_project_ids is only read (never written), to pick a fallback.
//
// Switching keeps each build's own part selection; the old build's parts never show under the new
// build (a parts answer for a build that is no longer active is dropped).
import { batch, computed, effect, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import type { Build, EmptyTrashRefused } from "../api/builds";
import { CoreError } from "../api/client";
import type { PartSummary } from "../api/types";
import { debug } from "../lib/log";
import { runLabel } from "../lib/names";
import { actions, conn, parts, runs, selectedPartId, selectedRunId } from "./store";

/** Every build the core knows, the trashed ones included. */
export const builds: Signal<readonly Build[]> = signal<readonly Build[]>([]);
export const activeBuildId: Signal<string | null> = signal<string | null>(null);
/** The last failed build action, in plain words (shown under the tabs until the next action). */
export const buildsError: Signal<string | null> = signal<string | null>(null);

export const liveBuilds: ReadonlySignal<Build[]> = computed(() => builds.value.filter((b) => b.trashed_at === null));
export const trashedBuilds: ReadonlySignal<Build[]> = computed(() => builds.value.filter((b) => b.trashed_at !== null));
export const activeBuild: ReadonlySignal<Build | null> = computed(() => builds.value.find((b) => b.id === activeBuildId.value) ?? null);

/** Selected part per build, so coming back to a tab shows what you left there. */
const selectionByBuild = new Map<string, string | null>();
let loadSeq = 0;

function message(e: unknown): string {
  if (e instanceof CoreError) return e.hint ? `${e.message} (${e.hint})` : e.message;
  return e instanceof Error ? e.message : String(e);
}

function fail(what: string, e: unknown): void {
  buildsError.value = `${what}: ${message(e)}`;
  debug("builds", what, e);
}

/** The last Empty Trash the core refused (409): the runs that block it. Cleared by the next try. */
export const trashBlocked: Signal<EmptyTrashRefused | null> = signal<EmptyTrashRefused | null>(null);

/** "The Trash wasn't emptied: 2 runs in trashed builds are still working (designer · arm, …)." */
export function trashRefusalText(r: EmptyTrashRefused, label: (id: string) => string): string {
  const n = r.run_ids.length;
  const shown = r.run_ids.slice(0, 3).map(label).join(", ");
  const who = n === 0 ? "" : ` (${shown}${n > 3 ? ` and ${n - 3} more` : ""})`;
  const runsWord = n === 1 ? "1 run in a trashed build" : `${n} runs in trashed builds`;
  const verb = n === 1 ? "is" : "are";
  if (r.reason === "pending_approval") {
    return `The Trash wasn't emptied: ${runsWord} ${verb} waiting for your approval${who}. Decide ${n === 1 ? "its card" : "their cards"}, then empty it again.`;
  }
  return `The Trash wasn't emptied: ${runsWord} ${verb} still working${who}. Wait for ${n === 1 ? "it" : "them"} to finish or stop ${n === 1 ? "it" : "them"}, then empty it again.`;
}

/** Active first-choice, else the first open live tab, else the first live build. */
export function normalizeTabs(all: readonly Build[], active: string | null, open: readonly string[]): { active: string | null; open: string[] } {
  const live = all.filter((b) => b.trashed_at === null).map((b) => b.id);
  const liveSet = new Set(live);
  const tabs = [...new Set(open)].filter((id) => liveSet.has(id));
  const act = active && liveSet.has(active) ? active : tabs[0] ?? live[0] ?? null;
  if (act && !tabs.includes(act)) tabs.push(act);
  return { active: act, open: tabs };
}

/** Is a parts list the answer for the active build? (sync.ts drops it otherwise.) */
export function isActiveBuild(id: string | null): boolean {
  return id === null || activeBuildId.peek() === null || id === activeBuildId.peek();
}

/** Loads the active build's parts; an answer for a build no longer active is dropped. */
export async function loadParts(id: string | null = activeBuildId.peek()): Promise<void> {
  const mine = ++loadSeq;
  try {
    const list = await actions.client().parts(id);
    if (mine !== loadSeq || !isActiveBuild(id)) return;
    applyParts(list);
  } catch (e) {
    fail("Loading the parts failed", e);
  }
}

function applyParts(list: PartSummary[]): void {
  parts.value = list;
  const sel = selectedPartId.peek();
  if (sel !== null && !list.some((p) => p.part_id === sel)) selectedPartId.value = null;
}

/** Writes the active build to the profile; false (and a plain-words error) when it failed. New
 *  runs don't depend on it (they name their build), only the build reopened at the next launch.
 *  open_project_ids is never written (Plan 9 review m2: without tabs it would only grow). */
async function persist(): Promise<boolean> {
  const active = activeBuildId.peek();
  try {
    await actions.client().setActiveBuild(active);
    return true;
  } catch (e) {
    fail("Saving the open build failed (it won't be reopened next launch)", e);
    return false;
  }
}

/** The build a new run belongs to, for POST /runs: always the active tab, explicitly (I-2), so a
 *  run started right after a tab switch — or after the profile save failed — is filed under the
 *  build on screen, not the profile's. Empty before the builds load (the core then uses its own). */
export function runBuild(): { project_id?: string } {
  const id = activeBuildId.peek();
  return id ? { project_id: id } : {};
}

/** Show the newest run of the build when the selected run belongs to another one. */
/** Called after the active build changes (a tab switch): A4's assembly and A7's spec panel
 *  re-open on the new build. Returns unregister. */
const switchHooks = new Set<(id: string) => void>();
export function onBuildSwitch(cb: (id: string) => void): () => void {
  switchHooks.add(cb);
  return () => { switchHooks.delete(cb); };
}
function announceSwitch(id: string): void {
  for (const cb of [...switchHooks]) {
    try { cb(id); } catch { /* a listener's failure never blocks the switch */ }
  }
}

/** m4: opening a build from Home shows its live lead's conversation (when it has one), whatever
 *  run was selected before (a sub-agent, an outside agent's run). */
export function selectBuildLead(buildId: string): void {
  const lead = [...runs.peek().values()].filter((r) => r.project_id === buildId && r.role === "lead" && !r.archived)
    .sort((a, b) => b.created_at - a.created_at)[0];
  if (lead) selectedRunId.value = lead.id;
}

function followRuns(buildId: string): void {
  const sel = selectedRunId.peek();
  const run = sel ? runs.peek().get(sel) : undefined;
  if (run && (run.project_id ?? null) === buildId) return;
  const mine = [...runs.peek().values()].filter((r) => r.project_id === buildId).sort((a, b) => b.created_at - a.created_at);
  // m4: the build's live lead is its conversation; land there, not on its newest sub-agent
  const lead = mine.find((r) => r.role === "lead" && !r.archived);
  selectedRunId.value = (lead ?? mine[0])?.id ?? null;
}

export const buildActions = {
  /** Reloads the builds and the active build from the core (on every connect). */
  async refresh(): Promise<void> {
    try {
      const c = actions.client();
      const [all, tabs] = await Promise.all([c.listBuilds(), c.buildTabs()]);
      const n = normalizeTabs(all, activeBuildId.peek() ?? tabs.active_project_id, tabs.open_project_ids);
      // the first load: the part list sync fetched without a build is the core's active build,
      // which is the profile's when that is live (else Default) — reload only if we settled on another one.
      // Nothing is written back until the user opens another build.
      const prev = activeBuildId.peek();
      const reload = prev === null ? tabs.active_project_id !== null && n.active !== tabs.active_project_id : n.active !== prev;
      batch(() => {
        builds.value = all;
        activeBuildId.value = n.active;
      });
      if (reload) await loadParts(n.active);
    } catch (e) {
      fail("Loading the builds failed", e);
    }
  },

  /** Re-reads only the builds list (part counts, last edited) — Home refetches it whenever it
   *  shows, so its tiles never lag the core (m1). Tabs are left alone. */
  async reloadList(): Promise<void> {
    try { builds.value = await actions.client().listBuilds(); }
    catch (e) { fail("Loading the builds failed", e); }
  },

  /** Make a build the active (open) one. */
  async switchTo(id: string): Promise<void> {
    buildsError.value = null;
    const target = liveBuilds.peek().find((b) => b.id === id);
    if (!target) return;
    const prev = activeBuildId.peek();
    if (prev === id) return;
    if (prev) selectionByBuild.set(prev, selectedPartId.peek());
    batch(() => {
      activeBuildId.value = id;
      parts.value = [];
      selectedPartId.value = selectionByBuild.get(id) ?? null;
    });
    const saved = persist();
    followRuns(id);
    announceSwitch(id);
    await loadParts(id);
    await saved;
  },

  async create(name: string): Promise<Build | null> {
    buildsError.value = null;
    try {
      const b = await actions.client().createBuild(name);
      builds.value = [...builds.peek(), b];
      await buildActions.switchTo(b.id);
      return b;
    } catch (e) {
      fail("Creating the build failed", e);
      return null;
    }
  },

  async rename(id: string, name: string): Promise<boolean> {
    buildsError.value = null;
    try {
      const b = await actions.client().renameBuild(id, name);
      builds.value = builds.peek().map((x) => (x.id === id ? b : x));
      return true;
    } catch (e) {
      fail("Renaming the build failed", e);
      return false;
    }
  },

  /** Move a build to the Trash (nothing is deleted; Restore brings it back). */
  async trash(id: string): Promise<void> {
    buildsError.value = null;
    try {
      const b = await actions.client().trashBuild(id);
      builds.value = builds.peek().map((x) => (x.id === id ? b : x));
      selectionByBuild.delete(id);
      if (activeBuildId.peek() === id) {
        const next = liveBuilds.peek()[0]?.id ?? null;
        if (next) await buildActions.switchTo(next);
        else {
          // the core starts a fresh Default when every build is in the Trash
          activeBuildId.value = null;
          await buildActions.refresh();
        }
      }
    } catch (e) {
      fail("Moving the build to the Trash failed", e);
    }
  },

  /** Bring a build back from the Trash; `open: false` (Home's Undo) leaves the active build alone. */
  async restore(id: string, opts: { open?: boolean } = {}): Promise<void> {
    buildsError.value = null;
    try {
      const b = await actions.client().restoreBuild(id);
      builds.value = builds.peek().map((x) => (x.id === id ? b : x));
      if (opts.open !== false) await buildActions.switchTo(id);
    } catch (e) {
      fail("Restoring the build failed", e);
    }
  },

  /** Delete one trashed build for good (D30). Refused (nothing deleted) while it has live work. */
  async purge(id: string): Promise<boolean> {
    buildsError.value = null;
    trashBlocked.value = null;
    try {
      const r = await actions.client().purgeBuild(id);
      if ("refused" in r) {
        trashBlocked.value = r.refused;
        buildsError.value = trashRefusalText(r.refused, (rid) => {
          const run = runs.peek().get(rid);
          return run ? runLabel(run, [...runs.peek().values()]) : `run ${rid}`;
        });
        return false;
      }
      const gone = new Set(r.purged);
      builds.value = builds.peek().filter((b) => !gone.has(b.id));
      return true;
    } catch (e) {
      fail("Deleting the build failed", e);
      return false;
    }
  },

  async emptyTrash(): Promise<void> {
    buildsError.value = null;
    trashBlocked.value = null;
    try {
      const r = await actions.client().emptyTrash();
      if ("refused" in r) {
        // nothing was deleted: say which runs hold it up (the ▾ menu lists them to open)
        trashBlocked.value = r.refused;
        buildsError.value = trashRefusalText(r.refused, (id) => {
          const run = runs.peek().get(id);
          return run ? runLabel(run, [...runs.peek().values()]) : `run ${id}`;
        });
        return;
      }
      const gone = new Set(r.purged);
      builds.value = builds.peek().filter((b) => !gone.has(b.id));
    } catch (e) {
      fail("Emptying the Trash failed", e);
    }
  },
};

/** Loads builds whenever the event stream (re)opens. Called once at boot (main.tsx). */
/** True while the app follows the core's builds (startBuilds ran): views that need the active
 *  build wait for it rather than look it up themselves. */
export let buildsTracked = false;

export function startBuilds(): () => void {
  buildsTracked = true;
  let was: string | null = null;
  return effect(() => {
    const now = conn.value;
    if (now === "open" && was !== "open") void buildActions.refresh();
    was = now;
  });
}

/** Back to boot state (tests). */
export function resetBuilds(): void {
  batch(() => {
    builds.value = [];
    activeBuildId.value = null;
    buildsError.value = null;
    trashBlocked.value = null;
  });
  selectionByBuild.clear();
  loadSeq++;
}
