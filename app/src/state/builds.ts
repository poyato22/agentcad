// Builds (Plan 5 C1 projects): which builds exist, which are open as tabs and which one is active.
// The active build scopes the part tree (GET /parts?project_id), the History pane's runs and new
// runs (the core files a run without project_id under the profile's active build), so switching
// writes active_project_id / open_project_ids back to the profile.
//
// Switching keeps each build's own part selection; the old build's parts never show under the new
// tab (a parts answer for a build that is no longer active is dropped).
import { batch, computed, effect, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import type { Build } from "../api/builds";
import { CoreError } from "../api/client";
import type { PartSummary } from "../api/types";
import { debug } from "../lib/log";
import { actions, conn, parts, runs, selectedPartId, selectedRunId } from "./store";

/** Every build the core knows, the trashed ones included. */
export const builds: Signal<readonly Build[]> = signal<readonly Build[]>([]);
export const activeBuildId: Signal<string | null> = signal<string | null>(null);
export const openBuildIds: Signal<readonly string[]> = signal<readonly string[]>([]);
/** The last failed build action, in plain words (shown under the tabs until the next action). */
export const buildsError: Signal<string | null> = signal<string | null>(null);

export const liveBuilds: ReadonlySignal<Build[]> = computed(() => builds.value.filter((b) => b.trashed_at === null));
export const trashedBuilds: ReadonlySignal<Build[]> = computed(() => builds.value.filter((b) => b.trashed_at !== null));
export const activeBuild: ReadonlySignal<Build | null> = computed(() => builds.value.find((b) => b.id === activeBuildId.value) ?? null);

/** The open tabs in order (live builds only; the active build is always one of them). */
export const openBuilds: ReadonlySignal<Build[]> = computed(() => {
  const live = new Map(liveBuilds.value.map((b) => [b.id, b]));
  return openBuildIds.value.map((id) => live.get(id)).filter((b): b is Build => b !== undefined);
});

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

function persist(): void {
  const active = activeBuildId.peek();
  void actions.client().setBuildTabs({ active_project_id: active, open_project_ids: [...openBuildIds.peek()] })
    .catch((e: unknown) => fail("Saving the open builds failed", e));
}

/** Show the newest run of the build when the selected run belongs to another one. */
function followRuns(buildId: string): void {
  const sel = selectedRunId.peek();
  const run = sel ? runs.peek().get(sel) : undefined;
  if (run && (run.project_id ?? null) === buildId) return;
  const mine = [...runs.peek().values()].filter((r) => r.project_id === buildId).sort((a, b) => b.created_at - a.created_at);
  selectedRunId.value = mine[0]?.id ?? null;
}

export const buildActions = {
  /** Reloads the builds and the open tabs from the core (on every connect). */
  async refresh(): Promise<void> {
    try {
      const c = actions.client();
      const [all, tabs] = await Promise.all([c.listBuilds(), c.buildTabs()]);
      const n = normalizeTabs(all, activeBuildId.peek() ?? tabs.active_project_id, openBuildIds.peek().length ? openBuildIds.peek() : tabs.open_project_ids);
      const changed = n.active !== activeBuildId.peek();
      const differs = n.active !== tabs.active_project_id || n.open.join() !== tabs.open_project_ids.join();
      batch(() => {
        builds.value = all;
        activeBuildId.value = n.active;
        openBuildIds.value = n.open;
      });
      if (differs) persist();
      if (changed) await loadParts(n.active);
    } catch (e) {
      fail("Loading the builds failed", e);
    }
  },

  /** Make a build the active tab (opening it if it isn't). */
  async switchTo(id: string): Promise<void> {
    buildsError.value = null;
    const target = liveBuilds.peek().find((b) => b.id === id);
    if (!target) return;
    const prev = activeBuildId.peek();
    const open = openBuildIds.peek().includes(id) ? openBuildIds.peek() : [...openBuildIds.peek(), id];
    if (prev === id && open === openBuildIds.peek()) return;
    if (prev) selectionByBuild.set(prev, selectedPartId.peek());
    batch(() => {
      activeBuildId.value = id;
      openBuildIds.value = open;
      if (prev !== id) {
        parts.value = [];
        selectedPartId.value = selectionByBuild.get(id) ?? null;
      }
    });
    persist();
    if (prev !== id) {
      followRuns(id);
      await loadParts(id);
    }
  },

  /** Close a tab; the neighbour to its right (else left) becomes active. The last tab stays. */
  async close(id: string): Promise<void> {
    const open = openBuildIds.peek();
    const i = open.indexOf(id);
    if (i < 0 || open.length <= 1) return;
    const rest = open.filter((x) => x !== id);
    openBuildIds.value = rest;
    if (activeBuildId.peek() === id) await buildActions.switchTo(rest[Math.min(i, rest.length - 1)]);
    else persist();
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
      const open = openBuildIds.peek();
      const i = open.indexOf(id);
      const rest = open.filter((x) => x !== id);
      builds.value = builds.peek().map((x) => (x.id === id ? b : x));
      openBuildIds.value = rest;
      selectionByBuild.delete(id);
      if (activeBuildId.peek() === id) {
        const next = rest[Math.min(Math.max(i, 0), rest.length - 1)] ?? liveBuilds.peek()[0]?.id ?? null;
        if (next) await buildActions.switchTo(next);
        else {
          // the core starts a fresh Default when every build is in the Trash
          activeBuildId.value = null;
          await buildActions.refresh();
        }
      } else persist();
    } catch (e) {
      fail("Moving the build to the Trash failed", e);
    }
  },

  async restore(id: string): Promise<void> {
    buildsError.value = null;
    try {
      const b = await actions.client().restoreBuild(id);
      builds.value = builds.peek().map((x) => (x.id === id ? b : x));
      await buildActions.switchTo(id);
    } catch (e) {
      fail("Restoring the build failed", e);
    }
  },

  async emptyTrash(): Promise<void> {
    buildsError.value = null;
    try {
      const r = await actions.client().emptyTrash();
      const gone = new Set(r.purged);
      builds.value = builds.peek().filter((b) => !gone.has(b.id));
    } catch (e) {
      fail("Emptying the Trash failed", e);
    }
  },
};

/** Loads builds whenever the event stream (re)opens. Called once at boot (main.tsx). */
export function startBuilds(): () => void {
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
    openBuildIds.value = [];
    buildsError.value = null;
  });
  selectionByBuild.clear();
  loadSeq++;
}
