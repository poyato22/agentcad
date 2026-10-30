import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { bindClient, resetStore } from "../../state/store";
import { applyBackground, backgroundStyle, BG_STORAGE_KEY, choiceOf, isBackground, isLight, saveBackground, THEME_BACKGROUND, viewerBackground } from "./background";

beforeEach(() => { resetStore(); localStorage.clear(); applyBackground(THEME_BACKGROUND); });
afterEach(() => { vi.restoreAllMocks(); });

describe("viewer background (U8, R21)", () => {
  it("validates like the core (theme, 1 solid color, 2 gradient stops)", () => {
    expect(isBackground({ mode: "theme", colors: [] })).toBe(true);
    expect(isBackground({ mode: "solid", colors: ["#ffffff"] })).toBe(true);
    expect(isBackground({ mode: "solid", colors: [] })).toBe(false);
    expect(isBackground({ mode: "gradient", colors: ["#ffffff"] })).toBe(false);
    expect(isBackground({ mode: "plaid", colors: [] })).toBe(false);
  });

  it("styles the sheet: nothing for the theme, a color, or one gradient over the whole sheet", () => {
    expect(backgroundStyle(THEME_BACKGROUND)).toBeUndefined();
    expect(backgroundStyle({ mode: "solid", colors: ["#000000"] })).toEqual({ backgroundColor: "#000000", backgroundImage: "none" });
    expect(backgroundStyle({ mode: "gradient", colors: ["#ffffff", "#c9d3df"] })).toMatchObject({ backgroundSize: "100% 100%" });
    expect(isLight({ mode: "solid", colors: ["#ffffff"] })).toBe(true);
    expect(isLight({ mode: "gradient", colors: ["#4b5059", "#15171a"] })).toBe(false);
    expect(isLight(THEME_BACKGROUND)).toBeNull();
    expect(choiceOf({ mode: "solid", colors: ["#FFFFFF"] })?.name).toBe("White");
  });

  it("saves to the profile and caches it; a refusal puts the old one back", async () => {
    const updateProfile = vi.fn(async () => ({}));
    bindClient({ updateProfile } as unknown as CoreClient);
    const soft = { mode: "gradient" as const, colors: ["#fdfdfe", "#c9d3df"] };
    expect(await saveBackground(soft)).toBe(true);
    expect(updateProfile).toHaveBeenCalledWith({ viewer_background: soft });
    expect(JSON.parse(localStorage.getItem(BG_STORAGE_KEY) ?? "null")).toEqual(soft);
    updateProfile.mockRejectedValueOnce(new Error("422"));
    expect(await saveBackground({ mode: "solid", colors: ["#000000"] })).toBe(false);
    expect(viewerBackground.value).toEqual(soft);
  });
});
