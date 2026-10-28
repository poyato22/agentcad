import { afterEach, describe, expect, it } from "vitest";
import {
  activePage, consoleCollapsed, DEFAULT_PAGE, installPageShortcuts, isPage, PAGES,
  pageForDigit, setConsoleCollapsed, setPage, toggleConsole,
} from "./router";

afterEach(() => { setPage(DEFAULT_PAGE); setConsoleCollapsed(false); });

describe("page router", () => {
  it("has the five pages in tab/shortcut order, Design first", () => {
    expect(PAGES.map((p) => p.id)).toEqual(["design", "studies", "sketch", "drawing", "print"]);
    expect(PAGES.map((p) => p.key)).toEqual(["1", "2", "3", "4", "5"]);
    expect(DEFAULT_PAGE).toBe("design");
  });

  it("isPage / pageForDigit", () => {
    expect(isPage("sketch")).toBe(true);
    expect(isPage("nope")).toBe(false);
    expect(pageForDigit("4")).toBe("drawing");
    expect(pageForDigit("9")).toBeNull();
  });

  it("setPage changes activePage and ignores junk", () => {
    setPage("print");
    expect(activePage.value).toBe("print");
    setPage("bogus" as never);
    expect(activePage.value).toBe("print");
  });

  it("⌘1–⌘5 navigate and unregister cleanly", () => {
    const off = installPageShortcuts();
    const press = (key: string) =>
      window.dispatchEvent(new KeyboardEvent("keydown", { key, code: `Digit${key}`, metaKey: true, bubbles: true, cancelable: true }));
    press("2");
    expect(activePage.value).toBe("studies");
    press("5");
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
});
