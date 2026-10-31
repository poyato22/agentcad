// Page router for a build. One `activePage` signal drives which page of the open build shows:
// Design keeps the left pane + viewer; the others render full-width under the top bar. The agent
// console stays available on every page and can collapse to a rail.
//
// Navigation: the top tabs (ui/topbar/ModeTabs) and ⌘1–⌘4 both call setPage(). A page component
// is just the default/exported component of ui/pages/<id>/index.tsx, rendered by App when active.
// To read or change the active page from anywhere, import `activePage` / `setPage` / `PAGES` from here.
//
// Plan 9 (D9, D21, D22): a build has exactly four pages — Design · Studies · Print · BOM. Drawings
// are a mode of Print (ui/pages/print/mode.ts); Onshape and the parts library are Home sub-views
// (state/home.ts homeView), not build pages. Every shortcut in the app is listed in
// lib/shortcuts.ts and docs/shortcuts.md:
//   ⌘1 Design · ⌘2 Studies · ⌘3 Print · ⌘4 BOM · ⌘0 Home (U1)
//   ⌥⌘A flips Part / Assembly on the Design page (ui/assembly/mode).
import { signal, type Signal } from "@preact/signals";
import { isTrustedKey, registerShortcut } from "../../lib/keys";
import { atHome } from "../../state/home";

export type Page = "design" | "studies" | "print" | "bom";

export interface PageDef {
  id: Page;
  /** Tab label in the top bar. */
  label: string;
  /** Page header title (PageShell) and document-level heading. */
  title: string;
  /** ⌘<key> digit that navigates to this page. */
  key: "1" | "2" | "3" | "4";
}

/** Order is the tab order and the ⌘<digit> order. Design is first and is the default. */
export const PAGES: readonly PageDef[] = [
  { id: "design", label: "Design", title: "Design", key: "1" },
  { id: "studies", label: "Studies", title: "Studies", key: "2" },
  // D21: drawings live inside Print (its 3D print | Drawing switch)
  { id: "print", label: "Print", title: "Print", key: "3" },
  // Plan 7 R5: the build's parts list (D9: it takes History's place in the bar)
  { id: "bom", label: "BOM", title: "Parts list (BOM)", key: "4" },
];

/** The mode tabs shown in the top bar: every build page (D9). */
export const TAB_PAGES: readonly PageDef[] = PAGES;

export const DEFAULT_PAGE: Page = "design";

export const activePage: Signal<Page> = signal<Page>(DEFAULT_PAGE);

export function isPage(v: unknown): v is Page {
  return typeof v === "string" && PAGES.some((p) => p.id === v);
}

export function setPage(p: Page): void {
  if (!isPage(p)) return;
  activePage.value = p;
  atHome.value = false;     // ⌘1–⌘4 on Home opens that page of the open build (m1)
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

/** Registers ⌘1–⌘4 to navigate between pages (capture phase, works inside inputs). Synthetic or
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
