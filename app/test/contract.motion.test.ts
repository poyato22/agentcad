// @vitest-environment node
// Contract (Plan 9 G-D): the real core's presented animation and section sweep (core/scripts/
// dump_motion_fixtures.py → core/tests/fixtures/ui/motion) parse with the app's schemas into
// motion cards whose steps name the dumped assembly's own joint, and the thumbnails they name are
// served by the fake core.
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { assemblySchema } from "../src/api/assembly";
import { stepSchema } from "../src/api/schemas";
import type { Step } from "../src/api/types";
import { g4Announcement } from "../src/ui/console/cards/critique/announce";
import { parseAnimateSpec, parseAnimateSteps } from "../src/state/assemblyAnimation";
import { SCENARIO_DIR } from "./fake-core/server";

const DIR = resolve(import.meta.dirname, "../../core/tests/fixtures/ui/motion");
const real = (name: string): unknown => JSON.parse(readFileSync(resolve(DIR, name), "utf8"));
const steps = (real("motion_steps.json") as unknown[]).map((s) => stepSchema.parse(s) as Step);
const asm = assemblySchema.parse(real("motion_assembly.json"));
const views = steps.flatMap((s) => { const a = g4Announcement(s); return a?.kind === "view_presented" ? [a.view] : []; });
const thumbId = (v: (typeof views)[number]): string | null => (v.thumbnail && "id" in v.thumbnail ? v.thumbnail.id : null);

describe("contract: the real core's motion cards ↔ the app", () => {
  it("the assembly and every thread step parse", () => {
    expect(asm.joints).toHaveLength(1);
    expect(steps.length).toBeGreaterThan(2);
  });

  it("the presented animation is a motion card on the assembly's own joint", () => {
    const v = views.find((x) => x.spec.kind === "animation");
    expect(v, "an animation card").toBeTruthy();
    const a = v!.spec.animation!;
    expect(a.name).toBe("Open and shut the lid");
    expect(a.steps.map((s) => s.joint_id)).toEqual([asm.joints[0].id, asm.joints[0].id]);
    expect(a.steps.map((s) => [s.from, s.to])).toEqual([[0, 120], [120, 0]]);
    expect(a.duration_s).toBe(3.5);
    expect(a.loops).toBe(2);
    expect(v!.spec.caption).toMatch(/swings 120°/);
    const builds = (real("motion_projects.json") as { id: string }[]).map((p) => p.id);
    expect(builds).toContain(v!.spec.target.project_id);
  });

  it("the presented sweep spans the assembly's box along X", () => {
    const v = views.find((x) => x.spec.kind === "section_sweep");
    expect(v?.spec.sweep).toEqual({ axis: "x", from: -20, to: 60, duration_s: 4 });
    expect(v?.spec.section?.normal).toEqual([1, 0, 0]);
  });

  it("the animate event still drives the live chip (assembly_animate, now with its animation_id)", () => {
    const lines = readFileSync(resolve(DIR, "motion_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
    const ev = lines.find((e) => e.type === "assembly_animate");
    expect(ev.data.animation_id).toMatch(/^[0-9a-f]{12}$/);
    const spec = parseAnimateSpec({ ...ev, ts: 0 });
    expect(spec?.steps).toHaveLength(2);
    expect(spec?.loops).toBe(2);
  });

  it("the saved animations route parses into playable steps on the same joint", () => {
    const saved = real("motion_animations.json") as { name: string; steps: unknown; loops: number }[];
    expect(saved.map((a) => a.name)).toEqual(["Open and shut the lid"]);
    expect(parseAnimateSteps(saved[0].steps).map((s) => s.joint_id)).toEqual([asm.joints[0].id, asm.joints[0].id]);
  });

  it("the fake core serves both thumbnails", () => {
    for (const v of views) {
      const id = thumbId(v);
      expect(id).toBeTruthy();
      expect(existsSync(resolve(SCENARIO_DIR, "renders", `${id}.png`)), String(id)).toBe(true);
    }
    expect(views.map((v) => v.spec.kind).sort()).toEqual(["animation", "section_sweep"]);
  });
});
