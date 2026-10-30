// The sketch follows the colour-token rules (final review M-8): status colours (red/amber/green)
// only for status, blue only for selected / agent-changing / needs-you and the inference guides.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/ui/pages/sketch/sketch.css", "utf8");
const rule = (sel: string): string => {
  const at = css.indexOf(`${sel} {`) >= 0 ? css.indexOf(`${sel} {`) : css.indexOf(`${sel},`);
  expect(at, `${sel} has a rule`).toBeGreaterThanOrEqual(0);
  return css.slice(at, css.indexOf("}", at));
};

describe("sketch colours", () => {
  it("draws the X/Y axes in a neutral token, never a status colour", () => {
    const axes = rule(".sk-axis-x, .sk-axis-y");
    expect(axes).toContain("var(--dim)");
    expect(css).not.toMatch(/\.sk-axis-[xy]\s*\{[^}]*var\(--(red|green|amber)/);
  });

  it("shades a closed profile without blue", () => {
    const fill = rule(".sk-profile");
    expect(fill).not.toMatch(/--blue/);
    expect(fill).toContain("var(--ink)");
  });
});
