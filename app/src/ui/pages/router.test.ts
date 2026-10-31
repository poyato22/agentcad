import { afterEach, describe, expect, it } from "vitest";
import { atHome } from "../../state/home";
import {
  activePage, consoleCollapsed, DEFAULT_PAGE, DOCK_OPEN_DEFAULT, installPageShortcuts, installSurfaceShortcuts, isPage, leftCollapsed,
  modalOpen, PAGES, pageForDigit, resetDockMemory, setConsoleCollapsed, setLeftCollapsed, setPage, toggleConsole,
} from "./router";

afterEach(() => { setPage(DEFAULT_PAGE); setConsoleCollapsed(false); setLeftCollapsed(false); resetDockMemory(); atHome.value = false; });

describe("page router", () => {
  it("has the pages in tab/shortcut order, Design first", () => {
    // D9/D21/D22: exactly Design · Studies · Print · BOM (Drawing is in Print; Onshape and the
    // parts library are on Home)
    expect(PAGES.map((p) => p.id)).toEqual(["design", "studies", "print", "bom"]);
    expect(PAGES.map((p) => p.key)).toEqual(["1", "2", "3", "4"]);
    expect(DEFAULT_PAGE).toBe("design");
  });

  it("isPage / pageForDigit", () => {
    expect(isPage("studies")).toBe(true);
    expect(isPage("sketch")).toBe(false);   // Plan 7 U2b: the Sketch page is gone
    expect(isPage("nope")).toBe(false);
    expect(pageForDigit("3")).toBe("print");
    expect(isPage("drawing")).toBe(false);
    expect(isPage("onshape")).toBe(false);
    expect(isPage("directory")).toBe(false);
    expect(pageForDigit("4")).toBe("bom");
    expect(pageForDigit("5")).toBeNull();
  });

  it("setPage changes activePage and ignores junk", () => {
    setPage("print");
    expect(activePage.value).toBe("print");
    setPage("bogus" as never);
    expect(activePage.value).toBe("print");
  });

  it("⌘ shortcuts ignore synthetic keys (default isTrusted check)", () => {
    const off = installPageShortcuts();
    // dispatchEvent makes isTrusted === false: must not navigate
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "3", code: "Digit3", metaKey: true, bubbles: true, cancelable: true }));
    expect(activePage.value).toBe("design");
    off();
  });

  it("⌘ shortcuts are ignored while a modal is open", () => {
    let modal = true;
    const off = installPageShortcuts({ isTrusted: () => true, isModalOpen: () => modal });
    const press = (key: string) =>
      window.dispatchEvent(new KeyboardEvent("keydown", { key, code: `Digit${key}`, metaKey: true, bubbles: true, cancelable: true }));
    press("3");
    expect(activePage.value).toBe("design");
    modal = false;
    press("3");
    expect(activePage.value).toBe("print");
    off();
  });

  it("modalOpen sees any aria-modal dialog", () => {
    expect(modalOpen()).toBe(false);
    const d = document.createElement("div");
    d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true");
    document.body.append(d);
    expect(modalOpen()).toBe(true);
    d.remove();
  });

  it("⌘1–⌘4 navigate and unregister cleanly", () => {
    const off = installPageShortcuts({ isTrusted: () => true, isModalOpen: () => false });
    const press = (key: string) =>
      window.dispatchEvent(new KeyboardEvent("keydown", { key, code: `Digit${key}`, metaKey: true, bubbles: true, cancelable: true }));
    press("2");
    expect(activePage.value).toBe("studies");
    press("3");
    expect(activePage.value).toBe("print");
    off();
    press("1");
    expect(activePage.value).toBe("print"); // no longer listening
  });

  it("console collapse toggles", () => {
    expect(consoleCollapsed.value).toBe(false);
    toggleConsole();
    expect(consoleCollapsed.value).toBe(true);
    setConsoleCollapsed(false);
    expect(consoleCollapsed.value).toBe(false);
  });

  it("the dock starts open on Design only; each page keeps its own state (FW-layout)", () => {
    expect(DOCK_OPEN_DEFAULT).toEqual({ design: true, studies: false, print: false, bom: false });
    expect(consoleCollapsed.value).toBe(false);
    setPage("studies");            // Studies has its own agent: the dock is a slim tab
    expect(consoleCollapsed.value).toBe(true);
    setPage("print");
    expect(consoleCollapsed.value).toBe(true);
    setConsoleCollapsed(false);      // the user opens it on Print
    setPage("bom");
    expect(consoleCollapsed.value).toBe(true);
    setPage("print");              // Print remembers
    expect(consoleCollapsed.value).toBe(false);
    setPage("design");             // Design was left open
    expect(consoleCollapsed.value).toBe(false);
    toggleConsole();                 // folded on Design, it stays folded there
    setPage("bom");
    setPage("design");
    expect(consoleCollapsed.value).toBe(true);
  });

  it("⌘[ folds the parts surface on Design, ⌘] the dock; never on Home or under a modal", () => {
    let modal = false;
    const off = installSurfaceShortcuts({ isTrusted: () => true, isModalOpen: () => modal });
    const press = (key: "[" | "]") =>
      window.dispatchEvent(new KeyboardEvent("keydown", { key, code: key === "[" ? "BracketLeft" : "BracketRight", metaKey: true, bubbles: true, cancelable: true }));
    press("[");
    expect(leftCollapsed.value).toBe(true);
    press("]");
    expect(consoleCollapsed.value).toBe(true);
    press("[");
    press("]");
    expect([leftCollapsed.value, consoleCollapsed.value]).toEqual([false, false]);
    modal = true;                    // the immersive views step with ⌘[ / ⌘] (they are modal)
    press("[");
    press("]");
    expect([leftCollapsed.value, consoleCollapsed.value]).toEqual([false, false]);
    modal = false;
    atHome.value = true;
    press("]");
    expect(consoleCollapsed.value).toBe(false);
    atHome.value = false;
    setPage("bom");                // the parts surface is Design's: ⌘[ does nothing elsewhere
    press("[");
    expect(leftCollapsed.value).toBe(false);
    off();
  });
});
