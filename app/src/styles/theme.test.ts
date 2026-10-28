import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { applyTheme, DEFAULT_THEME, isTheme, readCachedTheme, setTheme, theme, THEME_STORAGE_KEY } from "./theme";

const CSS = readFileSync(join(import.meta.dirname, "tokens.css"), "utf8");

/** Pulls the `--name: value;` declarations out of the first CSS block matching `selector`. */
function palette(selector: string): Record<string, string> {
  const start = CSS.indexOf(selector);
  if (start < 0) throw new Error(`no block for ${selector}`);
  const open = CSS.indexOf("{", start);
  const close = CSS.indexOf("}", open);
  const body = CSS.slice(open + 1, close);
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/gi)) out[m[1]] = m[2].trim();
  return out;
}

const DARK = palette(":root {");
const LIGHT = palette(':root[data-theme="light"]');
const SYSTEM_LIGHT = palette(':root[data-theme="system"]');

// ---- WCAG contrast
function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}
function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const n = parseInt(h, 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}
function ratio(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}


/** Hue in degrees (0–360) of a #rrggbb color. */
function hue(hex: string): number {
  const n = parseInt(hex.replace("#", ""), 16);
  const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  if (d === 0) return 0;
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return (h * 60 + 360) % 360;
}
const hueGap = (a: number, b: number) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b));

describe("theme tokens", () => {
  it("dark palette keeps the exact plan values (unchanged)", () => {
    expect(DARK).toMatchObject({
      "--bg": "#0a0a0a", "--paper": "#111111", "--paper2": "#161616", "--head": "#141414", "--vp": "#0d0d0d",
      "--line": "#242424", "--line2": "#333333", "--ink": "#ececec", "--dim": "#9a9a9a", "--faint": "#5e5e5e",
      "--blue": "#3d8bff", "--blue2": "#8dbaff", "--blue-tint": "rgba(61,139,255,.10)",
      "--green": "#4fd18b", "--amber": "#f2b134", "--red": "#ff5a4a",
      "--mesh-face": "#2a2a2a", "--mesh-edge": "#e6e6e6",
    });
    expect(DARK["--font-mono"]).toContain("Iosevka");
    expect(DARK["--font-mono"]).not.toMatch(/ibm plex/i);
  });

  it("light palette values (constraint surfaces + ruled text tones); the switch flips every color token", () => {
    expect(LIGHT).toMatchObject({
      "--bg": "#f4f4f2", "--paper": "#ffffff", "--paper2": "#ececea",
      "--line": "#d6d6d2", "--line2": "#bdbdb8",
      "--ink": "#111111", "--dim": "#4a4a4a", "--faint": "#6a6a6a",
      "--blue": "#0a5bd8", "--blue-tint": "rgba(10,91,216,.10)", "--red": "#c8321e",
    });
    const colorTokens = /^--(bg|paper|paper2|head|vp|line|line2|ink|dim|faint|blue|blue2|blue-tint|green|amber|red|grid-strong|grid-faint|mesh-face|mesh-edge)$/;
    for (const k of Object.keys(DARK).filter((x) => colorTokens.test(x))) {
      expect(LIGHT[k], `${k} redefined for light`).toBeDefined();
      expect(LIGHT[k], k).not.toBe(DARK[k]);
    }
  });

  it("the system-light block is identical to the light block (parity)", () => {
    expect(SYSTEM_LIGHT).toEqual(LIGHT);
    // and both switch the UA color-scheme to light; the dark :root says dark
    expect(CSS.match(/color-scheme:\s*light;/g)).toHaveLength(2);
    expect(CSS).toMatch(/:root \{[^}]*color-scheme:\s*dark;/);
  });

  it("light-mode text meets WCAG AA: ink, dim, faint and every status color used as text ≥ 4.5:1", () => {
    const surfaces = { bg: LIGHT["--bg"], paper: LIGHT["--paper"], paper2: LIGHT["--paper2"] };
    const text = ["--ink", "--dim", "--faint", "--blue", "--blue2", "--green", "--amber", "--red"];
    const fails: string[] = [];
    for (const t of text) for (const [name, s] of Object.entries(surfaces)) {
      const r = ratio(LIGHT[t], s);
      if (r < 4.5) fails.push(`${t} on ${name}: ${r.toFixed(2)}`);
    }
    expect(fails).toEqual([]);
  });

  it("filled buttons keep AA text-on-fill contrast in both themes (btn-primary, btn-ink)", () => {
    // A2: .btn-primary text (--on-blue) on --blue, and .btn-ink text (--bg) on --ink.
    const pairs: Array<[string, string]> = [["--on-blue", "--blue"], ["--bg", "--ink"]];
    for (const [palName, pal] of [["dark", DARK], ["light", LIGHT]] as const) {
      for (const [text, fill] of pairs) {
        expect(pal[text], `${text} defined (${palName})`).toBeDefined();
        expect(ratio(pal[text], pal[fill]), `${text} on ${fill} (${palName})`).toBeGreaterThanOrEqual(4.5);
      }
    }
    // the on-blue text flips between themes (dark fill is light; light fill is dark)
    expect(LIGHT["--on-blue"]).not.toBe(DARK["--on-blue"]);
  });

  it("light-mode status colors stay distinguishable by hue (pass / warn / fail / blue)", () => {
    const hues = ["--green", "--amber", "--red", "--blue"].map((k) => hue(LIGHT[k]));
    for (let i = 0; i < hues.length; i++) for (let j = i + 1; j < hues.length; j++) {
      expect(hueGap(hues[i], hues[j])).toBeGreaterThanOrEqual(25);
    }
  });
});

describe("applyTheme / setTheme / cache", () => {
  afterEach(() => { setTheme(DEFAULT_THEME); localStorage.removeItem(THEME_STORAGE_KEY); });

  it("isTheme guards the three values", () => {
    expect(isTheme("light")).toBe(true);
    expect(isTheme("dark")).toBe(true);
    expect(isTheme("system")).toBe(true);
    expect(isTheme("neon")).toBe(false);
    expect(isTheme(undefined)).toBe(false);
  });

  it("applyTheme writes data-theme on <html>", () => {
    applyTheme("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    applyTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("setTheme updates the signal, the attribute and the cache, clamping junk to the default", () => {
    setTheme("light");
    expect(theme.value).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    setTheme("bogus" as never);
    expect(theme.value).toBe(DEFAULT_THEME);
  });

  it("readCachedTheme survives junk and a throwing storage", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "neon");
    expect(readCachedTheme()).toBe(DEFAULT_THEME);
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(readCachedTheme()).toBe(DEFAULT_THEME);
    spy.mockRestore();
  });

  it("no flash: a fresh import applies the cached theme to <html> synchronously", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    document.documentElement.removeAttribute("data-theme");
    vi.resetModules();
    await import("./theme");                                   // what main.tsx does first
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});
