import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { specCheckSchema, specDocSchema, studySchema, type AskItem } from "../../api/spec";
import {
  buildAnswers, computePareto, draftFromSpec, emptyDraft, fmtMeasured, fmtTarget, iterStatus, move, newReqId, niceTicks,
  nextSort, paretoLine, pick, plotPoints, priorityDots, reqViews, setText, sortIterations, specFromDraft, tally, tallyText, unanswered,
} from "./model";

const FIX = resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FIX, n), "utf8"));
const STUDY = z.array(studySchema).parse(read("studies.json"))[0];
const SPEC = specDocSchema.parse(read("spec.json")).spec;
const CHECK = specCheckSchema.parse(read("spec_check.json"));

describe("reading requirements", () => {
  it("formats targets and measurements in their units", () => {
    expect(fmtTarget("max_mass_g", 8)).toBe("≤ 8 g");
    expect(fmtTarget("min_safety_factor", 2)).toBe("SF ≥ 2.00");
    expect(fmtTarget("max_deflection_mm", 0.5)).toBe("≤ 0.5 mm");
    expect(fmtTarget("envelope_mm", [50, 30, 12])).toBe("≤ 50×30×12 mm");
    expect(fmtTarget("mount_pattern", "20x20 M3")).toBe("20x20 M3");
    expect(fmtMeasured("max_mass_g", 5.7458)).toBe("5.75 g");
    expect(fmtMeasured("min_safety_factor", null)).toBe("—");
  });

  it("priority dots", () => {
    expect(priorityDots(5)).toBe("●●●●●");
    expect(priorityDots(2)).toBe("●●○○○");
  });

  it("joins the real spec with the real check, must-haves first", () => {
    const v = reqViews(SPEC, CHECK.requirements);
    expect(v[0].req.id).toBe("mass_budget");
    expect(v.map((x) => x.req.priority)).toEqual([...v.map((x) => x.req.priority)].sort((a, b) => b - a));
    const t = tally(v);
    expect(t).toEqual({ pass: 2, warn: 0, fail: 0, not_checked: 4 });
    expect(tallyText(t)).toBe("2 pass · 4 not checked");
    expect(reqViews(SPEC, null).every((x) => x.status === "not_checked")).toBe(true);
  });
});

describe("study iterations (real core study)", () => {
  it("plots every iteration that has mass and SF", () => {
    expect(plotPoints(STUDY.iterations)).toHaveLength(STUDY.iterations.length);
  });

  it("the Pareto line is the core's flagged points, lightest first, and matches a recompute", () => {
    const pts = plotPoints(STUDY.iterations);
    const line = paretoLine(pts);
    expect(line.map((p) => p.it.n)).toEqual(STUDY.iterations.filter((i) => i.pareto).sort((a, b) => (a.metrics.mass_g ?? 0) - (b.metrics.mass_g ?? 0)).map((i) => i.n));
    expect(line.length).toBeGreaterThan(0);
    expect(new Set(line.map((p) => p.it.n))).toEqual(computePareto(pts));
    for (let i = 1; i < line.length; i++) expect(line[i].mass).toBeGreaterThan(line[i - 1].mass);
  });

  it("checks a variant against the spec with the core's thresholds", () => {
    const best = STUDY.iterations.find((i) => i.n === STUDY.best_id)!;
    const mass = SPEC.requirements.find((r) => r.id === "mass_budget")!;
    const stiff = SPEC.requirements.find((r) => r.id === "stiffness")!;
    expect(iterStatus(mass, best).status).toBe("pass");
    expect(iterStatus(stiff, best).status).toBe("pass");
    const thin = [...STUDY.iterations].sort((a, b) => (a.metrics.mass_g ?? 0) - (b.metrics.mass_g ?? 0))[0];
    expect(thin.feasible).toBe(false);
    expect(iterStatus(stiff, thin).status).toBe("fail");
    expect(iterStatus(SPEC.requirements.find((r) => r.kind === "mount_pattern")!, best).status).toBe("not_checked");
  });

  it("sorts by any column, missing values last", () => {
    const bySf = sortIterations(STUDY.iterations, { key: "min_sf", dir: "desc" });
    for (let i = 1; i < bySf.length; i++) expect(bySf[i - 1].metrics.min_sf! >= bySf[i].metrics.min_sf!).toBe(true);
    const byVar = sortIterations(STUDY.iterations, { key: "var:thickness", dir: "asc" });
    expect(byVar[0].vars.thickness).toBe(Math.min(...STUDY.iterations.map((i) => i.vars.thickness)));
    const withHole = [...STUDY.iterations, { ...STUDY.iterations[0], n: 99, metrics: { ...STUDY.iterations[0].metrics, mass_g: null } }];
    expect(sortIterations(withHole, { key: "mass_g", dir: "desc" }).at(-1)!.n).toBe(99);
    expect(nextSort({ key: "n", dir: "asc" }, "min_sf")).toEqual({ key: "min_sf", dir: "desc" });
    expect(nextSort({ key: "min_sf", dir: "desc" }, "min_sf")).toEqual({ key: "min_sf", dir: "asc" });
  });

  it("nice ticks", () => {
    expect(niceTicks(3.4, 7.7, 5)).toEqual([4, 5, 6, 7]);
    expect(niceTicks(0, 1, 4)).toEqual([0, 0.25, 0.5, 0.75, 1]);
  });
});

describe("interview answers", () => {
  const items: AskItem[] = [
    { id: "use", prompt: "What will it do?", options: ["freestyle", "racing"], multi: false, rank: false, allow_text: true },
    { id: "extras", prompt: "Extras?", options: ["gps", "camera", "led"], multi: true, rank: false, allow_text: true },
    { id: "priorities", prompt: "Rank", options: ["weight", "impact strength", "stiffness"], multi: false, rank: true, allow_text: true },
  ];

  it("single takes the chip or the typed text; multi the chips plus text; rank the order", () => {
    let d = emptyDraft(items);
    expect(unanswered(items, d).map((q) => q.id)).toEqual(["use", "extras"]); // rank starts answered (as listed)
    d = pick(d, items[0], "racing");
    d = pick(d, items[1], "gps");
    d = pick(d, items[1], "led");
    d = pick(d, items[1], "gps");
    d = setText(d, items[1], "a buzzer");
    d = move(d, items[2], "stiffness", -1);
    expect(buildAnswers(items, d)).toEqual({ use: "racing", extras: ["led", "a buzzer"], priorities: ["weight", "stiffness", "impact strength"] });
    d = setText(d, items[0], "cinewhoop");
    expect(buildAnswers(items, d).use).toBe("cinewhoop");
    d = pick(d, items[0], "freestyle");
    expect(buildAnswers(items, d).use).toBe("freestyle");
    expect(unanswered(items, d)).toEqual([]);
  });

  it("moving past either end does nothing", () => {
    const d = emptyDraft(items);
    expect(move(d, items[2], "weight", -1)).toBe(d);
  });
});

describe("spec editor round trip", () => {
  it("an untouched draft gives back the same spec", () => {
    const r = specFromDraft(draftFromSpec(SPEC));
    expect(r.ok && r.spec).toEqual(SPEC);
  });

  it("edits parse, touched rows become the user's, bad values are listed", () => {
    const d = draftFromSpec(SPEC);
    const i = d.requirements.findIndex((r) => r.id === "fits_frame");
    d.requirements[i] = { ...d.requirements[i], target: "60 x 30 x 12", touched: true };
    const ok = specFromDraft(d);
    expect(ok.ok && ok.spec.requirements.find((r) => r.id === "fits_frame")).toMatchObject({ target: [60, 30, 12], source: "user" });

    const m = d.requirements.findIndex((r) => r.id === "mass_budget");
    d.requirements[m] = { ...d.requirements[m], target: "-3" };
    d.load_cases[0] = { ...d.load_cases[0], newtons: "" };
    const bad = specFromDraft(d);
    expect(bad.ok).toBe(false);
    expect(!bad.ok && bad.errors).toEqual(["mass_budget: needs a positive number", "desk_load: force needs a positive number of newtons"]);
  });

  it("new requirement ids never end in a number", () => {
    expect(newReqId("max_mass_g", new Set(["mass"]))).toBe("mass_extra");
    expect(newReqId("custom", new Set())).toBe("custom");
  });
});
