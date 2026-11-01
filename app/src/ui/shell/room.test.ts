import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_LAYOUT, type DockLayout, VIEWPORT_MIN } from "../console/dockLayout";
import { leftCollapsed, leftFolded, setLeftCollapsed, setPartsAutoFold, toggleLeft } from "../pages/router";
import { dockCap, dockMin, leftOuter, partsMustFold } from "./room";

// G1: the top area (View or Simulations) and the Conversation
const cols = (view: boolean, sim: boolean, text: boolean): DockLayout => ({ ...DEFAULT_LAYOUT, active: sim && !view ? "sim" : "view", collapsed: { top: !(view || sim), text: !text } });
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

  it("P11 F2: one dock width whatever is open, so the Parts surface never has to fold at 1280+", () => {
    for (const l of [ONE, TWO, THREE]) {
      expect(dockMin(l)).toBe(360);
      for (const win of [1280, 1440, 1920]) expect(partsMustFold(win, l)).toBe(false);
    }
    // a window narrower than the shell's minimum still folds it (the rule itself is unchanged)
    expect(partsMustFold(1100, THREE)).toBe(true);
    expect(partsMustFold(0, THREE)).toBe(false);
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
