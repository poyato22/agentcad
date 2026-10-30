// @vitest-environment node
// Every study route's real-core answer (core scripts/dump_study_fixtures.py) parses with the
// app's schemas, and the cards / review in the agent's tool_result steps do too.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import {
  designReviewSchema, studyCardSchema, studyChartsSchema, studyCompareSchema, studyMaterialSchema, studyMaterialsSchema, studyProbeSchema,
  studyProgressSchema, studyRecordSchema, studySectionSchema, studySolversSchema, studySummarySchema, studyTemplateSchema, studyTypeInfoSchema,
  STUDY_TYPES,
} from "./studies";

const DIR = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const read = (name: string): unknown => JSON.parse(readFileSync(resolve(DIR, name), "utf8"));

describe("study schemas parse the real core's answers", () => {
  it.each([
    ["study_types.json", z.array(studyTypeInfoSchema)], ["study_solvers.json", studySolversSchema],
    ["study_list.json", z.array(studySummarySchema)], ["study_templates.json", z.array(studyTemplateSchema)],
    ["study_template_add.json", studyTemplateSchema], ["study_materials.json", studyMaterialsSchema],
    ["study_material_add.json", studyMaterialSchema], ["study_compare.json", studyCompareSchema],
    ["study_probe.json", studyProbeSchema], ["study_probe_wind.json", studyProbeSchema],
    ["study_section.json", studySectionSchema], ["study_section_wind.json", studySectionSchema],
    ["study_section_wind_velocity.json", studySectionSchema], ["study_cancelled.json", studyRecordSchema],
  ] as const)("%s", (name, schema) => {
    const r = (schema as z.ZodType).safeParse(read(name));
    expect(r.success ? [] : r.error.issues.slice(0, 3)).toEqual([]);
  });

  it.each(STUDY_TYPES)("the %s record, its card and its charts", (t) => {
    const rec = studyRecordSchema.parse(read(`study_${t}.json`));
    expect(rec.type).toBe(t);
    expect(rec.card?.sim_id).toBe(rec.sim_id);
    const ch = studyChartsSchema.parse(read(`study_${t}_charts.json`));
    expect(ch.charts.length).toBeGreaterThan(0);
  });

  it("every agent tool_result carries a card (sim_run) or a review (design_review)", () => {
    const steps = read("study_agent_steps.json") as { kind: string; payload: { name?: string; output?: { data?: Record<string, unknown> } } }[];
    const results = steps.filter((s) => s.kind !== "tool_call" && s.payload.output);
    const cards = results.filter((s) => s.payload.name === "sim_run").map((s) => studyCardSchema.parse(s.payload.output?.data?.card));
    expect(new Set(cards.map((c) => c.type))).toEqual(new Set(STUDY_TYPES));
    const review = results.find((s) => s.payload.name === "design_review");
    expect(designReviewSchema.parse(review?.payload.output?.data).table.length).toBeGreaterThan(0);
  });

  it("sim_progress events carry phase, pct and detail", () => {
    const lines = readFileSync(resolve(DIR, "study_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as { type: string; data: unknown });
    const prog = lines.filter((e) => e.type === "sim_progress").map((e) => studyProgressSchema.parse(e.data));
    expect(prog.length).toBeGreaterThan(5);
    const phases = new Set(prog.map((p) => p.phase));
    for (const ph of ["mesh", "solve", "post"]) expect(phases).toContain(ph);
    expect(prog.every((p) => typeof p.pct === "number" && typeof p.detail === "string")).toBe(true);
  });
});
