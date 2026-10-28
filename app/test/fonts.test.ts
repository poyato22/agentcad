// @vitest-environment node
// The one UI typeface is Iosevka (SIL OFL), bundled locally. IBM Plex (the old face) and
// Bloomberg's proprietary terminal face must not appear anywhere in the app source or deps.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = resolve(import.meta.dirname, "..");

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.css$/.test(name)) out.push(p); // font-family lives in CSS; .ts comments explain the rule
  }
  return out;
}

describe("fonts", () => {
  it("package.json bundles Iosevka and no IBM Plex", () => {
    const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8")) as { dependencies: Record<string, string> };
    expect(pkg.dependencies["@fontsource/iosevka"]).toBeDefined();
    for (const name of Object.keys(pkg.dependencies)) expect(name).not.toMatch(/ibm-plex/i);
  });

  it("no stylesheet names IBM Plex or Bloomberg's proprietary face", () => {
    const offenders: string[] = [];
    for (const file of walk(join(ROOT, "src"))) {
      const text = readFileSync(file, "utf8");
      if (/ibm[\s-]?plex/i.test(text)) offenders.push(`${file}: IBM Plex`);
      if (/bloomberg/i.test(text)) offenders.push(`${file}: Bloomberg`);
    }
    expect(offenders).toEqual([]);
  });

  it("styles/fonts.ts imports Iosevka weights only", () => {
    const text = readFileSync(join(ROOT, "src/styles/fonts.ts"), "utf8");
    expect(text).toMatch(/@fontsource\/iosevka/);
    expect(text).not.toMatch(/ibm-plex/i);
  });
});
