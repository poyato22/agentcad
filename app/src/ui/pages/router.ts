// Page router for the app shell. One `activePage` signal drives which top-level page shows:
// Design keeps today's layout (left pane + viewer); the others render full-width under the top bar.
// The agent console stays available on every page and can collapse to a rail.
//
// Navigation: the top tabs (ui/topbar/ModeTabs) and ⌘1–⌘5 both call setPage(). A page component
// is just the default/exported component of ui/pages/<id>/index.tsx, rendered by App when active
// (Tasks 6–8 replace the stubs there). To read or change the active page from anywhere, import
// `activePage` / `setPage` / `PAGES` from here.
import { signal, type Signal } from "@preact/signals";
import { registerShortcut } from "../../lib/keys";

export type Page = "design" | "studies" | "sketch" | "drawing" | "print";

export interface PageDef {
  id: Page;
  /** Tab label in the top bar. */
  label: string;
  /** Page header title (PageShell) and document-level heading. */
  title: string;
  /** ⌘<key> digit that navigates to this page. */
  key: "1" | "2" | "3" | "4" | "5";
}

/** Order is the tab order and the ⌘1–⌘5 order. Design is first and is the default. */
export const PAGES: readonly PageDef[] = [
  { id: "design", label: "Design", title: "Design", key: "1" },
  { id: "studies", label: "Studies", title: "Studies", key: "2" },
  { id: "sketch", label: "Sketch", title: "Sketch", key: "3" },
  { id: "drawing", label: "Drawing", title: "Drawing", key: "4" },
  { id: "print", label: "Print", title: "Print", key: "5" },
];

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

/** Registers ⌘1–⌘5 to navigate between pages (capture phase, works inside inputs). Returns an
 *  unregister function; call it on unmount. */
export function installPageShortcuts(): () => void {
  const offs = PAGES.map((p) => registerShortcut(`Meta+${p.key}`, () => setPage(p.id)));
  return () => offs.forEach((off) => off());
}

// ---- Agent console collapse (available on every page) ------------------------------------------

/** Whether the right-hand agent console is collapsed to a 36px rail. */
export const consoleCollapsed: Signal<boolean> = signal<boolean>(false);

export function toggleConsole(): void { consoleCollapsed.value = !consoleCollapsed.value; }
export function setConsoleCollapsed(on: boolean): void { consoleCollapsed.value = on; }
