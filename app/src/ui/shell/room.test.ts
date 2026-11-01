import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_LAYOUT, type DockLayout, VIEWPORT_MIN } from "../console/dockLayout";
import { leftCollapsed, leftFolded, setLeftCollapsed, setPartsAutoFold, toggleLeft } from "../pages/router";
import { dockCap, dockMin, leftOuter, partsMustFold } from "./room";

const cols = (view: boolean, sim: boolean, text: boolean): DockLayout => ({ ...DEFAULT_LAYOUT, collapsed: { view: !view, sim: !sim, text: !text } });
const ONE = cols(false, false, true);
const TWO = cols(true, false, true);
const THREE = cols(true, true, true);

afterEach(() => { setPartsAutoFold(false); setLeftCollapsed(false); });

describe("the model's room (P10 FWA)", () => {
  it("caps the dock so the model keeps VIEWPORT_MIN beside the Parts surface", () => {
    for (const win of [1280, 1440, 1920]) {
      for (const parts of ["open", "tab", "none"] as const) {
        const free = win - leftOuter(parts) - (dockCap(win, leftOuter(parts)) + 24);
        expect(free).toBe(VIEWPORT_MIN);
      }
    }
  });

  it("folds the Parts surface only when even the narrowest dock would not fit beside it", () => {
    // 1440: one or two columns shrink beside the open Parts surface; three need its room
    expect(partsMustFold(1440, ONE)).toBe(false);
    expect(partsMustFold(1440, TWO)).toBe(false);
    expect(partsMustFold(1440, THREE)).toBe(true);
    // 1280: one column fits; two and three fold it
    expect(partsMustFold(1280, ONE)).toBe(false);
    expect(partsMustFold(1280, TWO)).toBe(true);
    expect(partsMustFold(1280, THREE)).toBe(true);
    // wide windows keep it open with all three
    expect(partsMustFold(1920, THREE)).toBe(false);
    expect(partsMustFold(0, THREE)).toBe(false);
  });

  it("the narrowest dock is its open columns at their minimum plus the closed tabs", () => {
    expect(dockMin(ONE)).toBe(360 + 60);
    expect(dockMin(TWO)).toBe(520 + 30);
    expect(dockMin(THREE)).toBe(780);
  });
});

describe("the Parts surface folding by itself", () => {
  it("shows as its tab while the dock needs the room and comes back after", () => {
    setPartsAutoFold(true);
    expect(leftFolded.value).toBe(true);
    expect(leftCollapsed.value).toBe(false);          // the user's own choice is untouched
    setPartsAutoFold(false);
    expect(leftFolded.value).toBe(false);
  });

  it("opening the tab meanwhile keeps it open until the room returns; then it folds by itself again", () => {
    setPartsAutoFold(true);
    toggleLeft();
    expect(leftFolded.value).toBe(false);
    setPartsAutoFold(true);                            // still needed: stays open as asked
    expect(leftFolded.value).toBe(false);
    toggleLeft();                                      // folded by the user
    expect([leftFolded.value, leftCollapsed.value]).toEqual([true, true]);
    toggleLeft();
    setPartsAutoFold(false);
    setPartsAutoFold(true);                            // the next squeeze folds it again
    expect(leftFolded.value).toBe(true);
  });

  it("a fold the user chose stays when the room comes back", () => {
    toggleLeft();
    setPartsAutoFold(true);
    setPartsAutoFold(false);
    expect(leftFolded.value).toBe(true);
  });
});
