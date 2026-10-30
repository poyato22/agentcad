// @vitest-environment node
// One list of shortcuts (lib/shortcuts.ts): every registerShortcut combo in src/ is in it, no two
// global ones collide, and docs/shortcuts.md documents each of them.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { SHORTCUTS } from "./shortcuts";

const SRC = resolve(import.meta.dirname, "..");
const DOC = readFileSync(resolve(import.meta.dirname, "../../../docs/shortcuts.md"), "utf8");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return files(p);
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : [];
  });
}

describe("keyboard shortcuts", () => {
  it("every registerShortcut combo is listed", () => {
    const used = new Set<string>();
    for (const f of files(SRC)) {
      for (const m of readFileSync(f, "utf8").matchAll(/registerShortcut\(\s*"([^"]+)"/g)) used.add(m[1]);
    }
    const listed = new Set(SHORTCUTS.map((s) => s.combo));
    expect([...used].filter((c) => !listed.has(c))).toEqual([]);
    expect(used.size).toBeGreaterThan(5);
  });

  it("no two shortcuts share a combo in one scope", () => {
    const seen = new Map<string, string>();
    for (const s of SHORTCUTS) {
      const k = `${s.scope}:${s.combo}`;
      expect(seen.has(k), `${k}: ${seen.get(k)} and ${s.action}`).toBe(false);
      seen.set(k, s.action);
    }
  });

  it("docs/shortcuts.md documents each one", () => {
    for (const s of SHORTCUTS) expect(DOC, `${s.keys} ${s.action}`).toContain(`| ${s.keys} |`);
  });
});
