import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { applyTheme, DEFAULT_THEME, isTheme, setTheme, theme } from "./theme";

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

describe("theme tokens", () => {
  it("dark palette keeps the exact plan values (unchanged)", () => {
    expect(DARK["--bg"]).toBe("#0a0a0a");
    expect(DARK["--ink"]).toBe("#ececec");
    expect(DARK["--blue"]).toBe("#3d8bff");
    expect(DARK["--font-mono"]).toContain("Iosevka");
    expect(DARK["--font-mono"]).not.toMatch(/ibm plex/i);
  });

  it("light palette uses the exact constraint values (the switch flips every token)", () => {
    expect(LIGHT).toMatchObject({
      "--bg": "#f4f4f2", "--paper": "#ffffff", "--paper2": "#ececea",
      "--line": "#d6d6d2", "--line2": "#bdbdb8",
      "--ink": "#111111", "--dim": "#4a4a4a", "--faint": "#7a7a7a",
      "--blue": "#0a5bd8", "--blue2": "#2f6fe0", "--blue-tint": "rgba(10,91,216,.10)",
      "--green": "#1f8a4c", "--amber": "#b7790f", "--red": "#c8321e",
    });
    // every color token present in dark is also redefined for light
    for (const k of Object.keys(DARK)) {
      if (k.startsWith("--font") || k.endsWith("-h") || k.endsWith("-w") || k.startsWith("--fs") || k.startsWith("--chrome") || k.endsWith("-head-h")) continue;
      if (/^--(bg|paper|paper2|head|vp|line|line2|ink|dim|faint|blue|blue2|blue-tint|green|amber|red|grid-strong|grid-faint)$/.test(k)) {
        expect(LIGHT[k], `${k} redefined for light`).toBeDefined();
        expect(LIGHT[k]).not.toBe(DARK[k]);
      }
    }
  });

  it("light-mode text meets WCAG AA (primary and secondary ink ≥ 4.5:1)", () => {
    const surfaces = [LIGHT["--bg"], LIGHT["--paper"], LIGHT["--paper2"]];
    for (const s of surfaces) {
      expect(ratio(LIGHT["--ink"], s)).toBeGreaterThanOrEqual(4.5);
      expect(ratio(LIGHT["--dim"], s)).toBeGreaterThanOrEqual(4.5);
    }
    // links / selected (blue) as text ≥ 4.5 on the main surfaces
    expect(ratio(LIGHT["--blue"], LIGHT["--bg"])).toBeGreaterThanOrEqual(4.5);
    expect(ratio(LIGHT["--blue"], LIGHT["--paper"])).toBeGreaterThanOrEqual(4.5);
    // faint is de-emphasised UI text: at least the 3:1 large-text / UI minimum
    expect(ratio(LIGHT["--faint"], LIGHT["--paper"])).toBeGreaterThanOrEqual(3.0);
  });

  it("light-mode status colors stay distinguishable and ≥ 3:1 on the background", () => {
    const status = { green: LIGHT["--green"], amber: LIGHT["--amber"], red: LIGHT["--red"], blue: LIGHT["--blue"] };
    for (const c of Object.values(status)) expect(ratio(c, LIGHT["--bg"])).toBeGreaterThanOrEqual(3.0);
    const lums = Object.values(status).map(luminance);
    for (let i = 0; i < lums.length; i++) for (let j = i + 1; j < lums.length; j++) {
      expect(Math.abs(lums[i] - lums[j]), "status colors differ in luminance").toBeGreaterThan(0.01);
    }
  });
});

describe("applyTheme / setTheme", () => {
  afterEach(() => { setTheme(DEFAULT_THEME); });

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

  it("setTheme updates the signal (and the attribute), clamping junk to the default", () => {
    setTheme("light");
    expect(theme.value).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    setTheme("bogus" as never);
    expect(theme.value).toBe(DEFAULT_THEME);
  });
});
