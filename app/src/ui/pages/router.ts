// Page router for the app shell. One `activePage` signal drives which top-level page shows:
// Design keeps today's layout (left pane + viewer); the others render full-width under the top bar.
// The agent console stays available on every page and can collapse to a rail.
//
// Navigation: the top tabs (ui/topbar/ModeTabs) and ⌘1–⌘7 both call setPage(). A page component
// is just the default/exported component of ui/pages/<id>/index.tsx, rendered by App when active
// (Tasks 6–8 replace the stubs there). To read or change the active page from anywhere, import
// `activePage` / `setPage` / `PAGES` from here.
import { signal, type Signal } from "@preact/signals";
import { isTrustedKey, registerShortcut } from "../../lib/keys";

export type Page = "design" | "studies" | "sketch" | "drawing" | "print" | "onshape" | "directory" | "bom";

export interface PageDef {
  id: Page;
  /** Tab label in the top bar. */
  label: string;
  /** Page header title (PageShell) and document-level heading. */
  title: string;
  /** ⌘<key> digit that navigates to this page. */
  key: "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9";
  /** A reachable page that is NOT a top-bar mode tab (its own build-scoped entry opens it).
   *  Keeps the mode-tab strip within its width budget. */
  hidden?: boolean;
}

/** Order is the tab order and the ⌘<digit> order. Design is first and is the default. */
export const PAGES: readonly PageDef[] = [
  { id: "design", label: "Design", title: "Design", key: "1" },
  { id: "studies", label: "Studies", title: "Studies", key: "2" },
  { id: "sketch", label: "Sketch", title: "Sketch", key: "3" },
  { id: "drawing", label: "Drawing", title: "Drawing", key: "4" },
  { id: "print", label: "Print", title: "Print", key: "5" },
  // Plan 5 A6
  { id: "onshape", label: "Onshape", title: "Onshape", key: "6" },
  { id: "directory", label: "Parts", title: "Parts directory", key: "7" },
  // Plan 7 R5 — a build-scoped page reached from the left pane, not a mode tab (the top-bar tab
  // strip is full until U2b removes Sketch; U7 can promote it to a tab then). ⌘8 still opens it.
  { id: "bom", label: "Parts list", title: "Parts list", key: "8", hidden: true },
];

/** The mode tabs shown in the top bar (hidden pages have their own entry points). */
export const TAB_PAGES: readonly PageDef[] = PAGES.filter((p) => !p.hidden);

export const DEFAULT_PAGE: Page = "design";

export const activePage: Signal<Page> = signal<Page>(DEFAULT_PAGE);

export function isPage(v: unknown): v is Page {
  return typeof v === "string" && PAGES.some((p) => p.id === v);
}

export function setPage(p: Page): void {
  if (isPage(p)) activePage.value = p;
}

/** The page a ⌘<digit> shortcut maps to, or null. */
export function pageForDigit(digit: string): Page | null {
  return PAGES.find((p) => p.key === digit)?.id ?? null;
}

export function pageDef(id: Page): PageDef {
  // PAGES covers every Page; the fallback only satisfies the type checker.
  return PAGES.find((p) => p.id === id) ?? PAGES[0];
}

/** True while a modal (setup wizard, Profile, new-run dialog — all `aria-modal="true"`) is open. */
export function modalOpen(doc: Document = document): boolean {
  return doc.querySelector('[aria-modal="true"]') !== null;
}

export interface PageShortcutDeps {
  /** Real user key press (default: lib/keys isTrustedKey — trusted, not auto-repeat). */
  isTrusted?: (e: KeyboardEvent) => boolean;
  /** Whether a modal is open (default: modalOpen()). */
  isModalOpen?: () => boolean;
}

/** Registers ⌘1–⌘7 to navigate between pages (capture phase, works inside inputs). Synthetic or
 *  auto-repeat keys are ignored, and so is everything while a modal is open (the page must not
 *  change under the wizard, Profile or a dialog). Returns an unregister function. */
export function installPageShortcuts(deps: PageShortcutDeps = {}): () => void {
  const trusted = deps.isTrusted ?? isTrustedKey;
  const blocked = deps.isModalOpen ?? (() => modalOpen());
  const offs = PAGES.map((p) => registerShortcut(`Meta+${p.key}`, (e) => {
    if (!trusted(e) || blocked()) return;
    setPage(p.id);
  }));
  return () => offs.forEach((off) => off());
}

// ---- Agent console collapse (available on every page) ------------------------------------------

/** Whether the right-hand agent console is collapsed to a 36px rail. */
export const consoleCollapsed: Signal<boolean> = signal<boolean>(false);

export function toggleConsole(): void { consoleCollapsed.value = !consoleCollapsed.value; }
export function setConsoleCollapsed(on: boolean): void { consoleCollapsed.value = on; }
