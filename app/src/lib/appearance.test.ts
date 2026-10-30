import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { appearanceName, fromPreset, materialOf, PRESETS, swatchStyle, withColor } from "./appearance";

describe("appearance (U8)", () => {
  it("the preset table is the core's (mises/appearance.py PRESETS)", () => {
    const py = readFileSync(resolve(import.meta.dirname, "../../../core/src/mises/appearance.py"), "utf8");
    const re = /"(\w+)": \{"name": "([^"]+)", "metalness": ([\d.]+), "roughness": ([\d.]+),\s*"opacity": ([\d.]+), "color": (None|"#[0-9a-f]{6}")\}/g;
    const core = [...py.matchAll(re)].map((m) => ({
      id: m[1], name: m[2], metalness: Number(m[3]), roughness: Number(m[4]), opacity: Number(m[5]), color: m[6] === "None" ? null : m[6].slice(1, -1),
    }));
    expect(core).toEqual(PRESETS.map((p) => ({ ...p })));
  });

  it("materialOf: a plain part is matte and not shiny; an appearance wins over the color", () => {
    expect(materialOf({ color: "#ff0000" })).toEqual({ color: "#ff0000", metalness: 0, roughness: 0.9, opacity: 1, shiny: false });
    const a = fromPreset("polished_metal", "#ff0000");
    expect(materialOf({ color: "#ff0000", appearance: a })).toMatchObject({ color: "#e6e8eb", metalness: 1, shiny: true });
  });

  it("an untinted preset keeps the part's color; a color pick keeps the finish", () => {
    expect(fromPreset("glossy_plastic", "#123456").color).toBe("#123456");
    expect(fromPreset("rubber", "#123456").color).toBe("#2e2f31");
    expect(withColor(fromPreset("anodized", null), "#c2463d")).toMatchObject({ preset: "anodized", color: "#c2463d" });
    expect(withColor(null, "#c2463d")).toBeNull();
  });

  it("names and swatches", () => {
    expect(appearanceName(null)).toBeNull();
    expect(appearanceName(fromPreset("clear", null))).toBe("Clear");
    expect(appearanceName({ preset: null, color: null, metalness: 0.3, roughness: 0.3, opacity: 1 })).toBe("Custom");
    expect(swatchStyle(materialOf({ color: "#123456" }))).toEqual({ background: "#123456" });
    const shiny = swatchStyle(materialOf({ appearance: fromPreset("polished_metal", null) }));
    expect(shiny.background).toContain("radial-gradient");
  });
});
