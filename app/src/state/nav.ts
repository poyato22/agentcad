// Back / forward between the places the user went (Plan 10 E11, navigation). Like a browser's
// history, but over the app's own navigation signals: Home and its sub-views (the grid, Trash,
// Onshape, the parts library), the open build and its page, a part opened alone on Design, a
// sub-agent's thread opened in the Conversation, and the full-window views (a study's immersive
// view, the assembly's Play). Nothing calls into this module to navigate: it watches those signals
// and records each settled change as one step, so every existing opener is covered.
//
// ← / → in the top bar and in Home's header, ⌘← / ⌘→ (not while typing: there they move the
// caret) and the mouse's back / forward buttons step through it. ⌘[ / ⌘] stay the panels' keys.
import { computed, effect, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { isTrustedKey } from "../lib/keys";
import { closePart, openPart, openPartId } from "../ui/assembly/focus";
import { immersive as playView, type Immersive } from "../ui/assembly/state";
import { activePage, modalOpen, setPage, type Page } from "../ui/pages/router";
import { immersive as studyView, type ImmersiveTarget } from "../ui/pages/studies/immersive/state";
import { activeBuildId, buildActions, liveBuilds, selectBuildLead } from "./builds";
import { atHome, homeView, type HomeView } from "./home";
import { runs, selectedRunId } from "./store";

/** One place in the history. */
export interface NavEntry {
  home: boolean;
  homeView: HomeView;
  buildId: string | null;
  page: Page;
  /** Design: the part open on its own (null: the assembly). */
  partId: string | null;
  /** A sub-agent's thread shown in the Conversation (null: the lead's, the default). */
  threadId: string | null;
  /** The raw run selection, to tell the dock's own first pick (from none) from a navigation. */
  runId: string | null;
  study: ImmersiveTarget | null;
  play: Immersive | null;
}

/** How many steps are kept (the oldest drop off). */
export const NAV_LIMIT = 100;

const entries: Signal<NavEntry[]> = signal<NavEntry[]>([]);
const index: Signal<number> = signal<number>(-1);

export const canGoBack: ReadonlySignal<boolean> = computed(() => index.value > 0);
export const canGoForward: ReadonlySignal<boolean> = computed(() => index.value < entries.value.length - 1);

function threadOf(runId: string | null): string | null {
  if (runId === null) return null;
  const r = runs.peek().get(runId);
  return r && r.role !== "lead" ? runId : null;
}

/** Where the app is now (signals read with peek: the watcher subscribes separately). */
export function currentEntry(): NavEntry {
  const runId = selectedRunId.peek();
  return {
    home: atHome.peek(), homeView: homeView.peek(), buildId: activeBuildId.peek(), page: activePage.peek(),
    partId: openPartId.peek(), threadId: threadOf(runId), runId, study: studyView.peek(), play: playView.peek(),
  };
}

/** Two entries are the same place (what is shown, not every detail: stepping through the studies
 *  of one immersive view, or the dock picking the shown run, is not a new step). */
export function samePlace(a: NavEntry, b: NavEntry): boolean {
  if ((a.study === null) !== (b.study === null) || (a.play === null) !== (b.play === null)) return false;
  if (a.home !== b.home || a.buildId !== b.buildId) return false;
  if (a.home) return a.homeView === b.homeView;
  if (a.page !== b.page) return false;
  if (a.page === "design" && a.partId !== b.partId) return false;
  return a.threadId === b.threadId || (a.runId === null || b.runId === null);
}

let applying = 0;
let scheduled = false;

/** Record the settled state as a step (or refresh the current step when it is the same place). */
export function recordNow(): void {
  if (applying > 0) return;
  const now = currentEntry();
  const list = entries.peek();
  const i = index.peek();
  if (i >= 0 && samePlace(list[i], now)) {
    entries.value = [...list.slice(0, i), now, ...list.slice(i + 1)];
    return;
  }
  const next = [...list.slice(0, i + 1), now].slice(-NAV_LIMIT);
  entries.value = next;
  index.value = next.length - 1;
}

function schedule(): void {
  if (scheduled) return;
  scheduled = true;
  // one navigation often sets several signals (open a build: its id, the page, Home off, the lead)
  queueMicrotask(() => { scheduled = false; recordNow(); });
}

async function apply(e: NavEntry): Promise<void> {
  applying++;
  try {
    if (e.buildId !== null && e.buildId !== activeBuildId.peek() && liveBuilds.peek().some((b) => b.id === e.buildId)) {
      await buildActions.switchTo(e.buildId);
    }
    if (activePage.peek() !== e.page) setPage(e.page);
    homeView.value = e.homeView;
    atHome.value = e.home;
    if (e.partId === null) closePart();
    else if (openPartId.peek() !== e.partId) openPart(e.partId);
    const thread = threadOf(selectedRunId.peek());
    if (e.threadId !== null && runs.peek().has(e.threadId)) selectedRunId.value = e.threadId;
    else if (e.threadId === null && thread !== null && activeBuildId.peek()) selectBuildLead(activeBuildId.peek() as string);
    studyView.value = e.study;
    playView.value = e.play;
  } finally {
    // the changes above schedule a record (a microtask); it runs before this one and is skipped
    queueMicrotask(() => { applying--; });
  }
}

/** Step back (false when there is nowhere to go). */
export function goBack(): boolean {
  const i = index.peek();
  if (i <= 0) return false;
  index.value = i - 1;
  void apply(entries.peek()[i - 1]);
  return true;
}

/** Step forward (false when there is nowhere to go). */
export function goForward(): boolean {
  const i = index.peek();
  if (i >= entries.peek().length - 1) return false;
  index.value = i + 1;
  void apply(entries.peek()[i + 1]);
  return true;
}

/** Typing in a field: ⌘← / ⌘→ move the caret there, so they are left alone. */
export function isTextField(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable === true);
}

/** A dialog is open that the history doesn't own (a full-window view is a step: back closes it). */
function blockedByModal(): boolean {
  if (!modalOpen()) return false;
  return studyView.peek() === null && playView.peek() === null;
}

export interface NavDeps {
  isTrusted?: (e: KeyboardEvent) => boolean;
  isBlocked?: () => boolean;
}

/** Starts recording and installs ⌘← / ⌘→ and the mouse back / forward buttons. Returns cleanup. */
export function installNav(deps: NavDeps = {}): () => void {
  const trusted = deps.isTrusted ?? isTrustedKey;
  const blocked = deps.isBlocked ?? blockedByModal;
  const stop = effect(() => {
    // subscribe to every navigation signal; the record itself reads them with peek
    void atHome.value; void homeView.value; void activeBuildId.value; void activePage.value;
    void openPartId.value; void selectedRunId.value; void studyView.value; void playView.value;
    schedule();
  });
  const onKey = (e: KeyboardEvent) => {
    if (!e.metaKey || e.shiftKey || e.altKey || e.ctrlKey) return;
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    if (e.defaultPrevented || isTextField(e.target) || !trusted(e) || blocked()) return;
    e.preventDefault();
    if (e.key === "ArrowLeft") goBack(); else goForward();
  };
  // mouse buttons 3 / 4 are back / forward; the press is swallowed so nothing else reads it
  const onMouse = (e: MouseEvent) => {
    if (e.button !== 3 && e.button !== 4) return;
    e.preventDefault();
    if (e.type !== "mouseup" || blocked()) return;
    if (e.button === 3) goBack(); else goForward();
  };
  window.addEventListener("keydown", onKey);
  window.addEventListener("mousedown", onMouse, true);
  window.addEventListener("mouseup", onMouse, true);
  return () => {
    stop();
    window.removeEventListener("keydown", onKey);
    window.removeEventListener("mousedown", onMouse, true);
    window.removeEventListener("mouseup", onMouse, true);
  };
}

/** Tests: forget the history. */
export function resetNav(): void {
  entries.value = [];
  index.value = -1;
  applying = 0;
  scheduled = false;
}

/** Tests: the recorded steps and where the user is among them. */
export function navState(): { entries: readonly NavEntry[]; index: number } {
  return { entries: entries.peek(), index: index.peek() };
}
