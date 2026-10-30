// @vitest-environment node
// Fake core, Plan 6 (assumed shapes): the e2e runs against these, so they parse with the app's
// own schemas (api/accuracy.ts) and behave like the S1-S3 contracts in the plan.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { fatigueOf, nonlinearOf, provenance } from "../../src/api/accuracy";
import { CoreClient, CoreError } from "../../src/api/client";
import type { PartSummary } from "../../src/api/types";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./server";

const DIR = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
let core: FakeCore;
let client: CoreClient;

const scenario = (sims: Record<string, unknown> = {}): Scenario => ({
  ...loadScenario("base"), parts: JSON.parse(readFileSync(resolve(DIR, "study_parts.json"), "utf8")) as PartSummary[],
  sims: { studies: true, step_ms: 5, ...sims },
});
const until = async (cond: () => Promise<boolean>, ms = 3000) => {
  const end = Date.now() + ms;
  while (!(await cond()) && Date.now() < end) await new Promise((r) => setTimeout(r, 10));
  expect(await cond()).toBe(true);
};

beforeEach(async () => {
  core = await startFakeCore({ port: 0, token: "t", scenario: scenario() });
  client = new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false });
});
afterEach(async () => { await core.stop(); });

describe("fake core Plan 6", () => {
  it("the schemas carry nonlinear, fatigue, print direction and the openfoam solver", async () => {
    const types = await client.studyTypes();
    const st = types.find((t) => t.id === "static")!;
    const props = st.schema.properties as Record<string, unknown>;
    expect(Object.keys(props)).toEqual(expect.arrayContaining(["nonlinear", "fatigue", "print_direction"]));
    expect(st.channels).toEqual(expect.arrayContaining(["plastic_strain", "contact_pressure"]));
    const wind = types.find((t) => t.id === "wind")!;
    expect((wind.schema.properties as Record<string, { enum?: string[] }>).solver.enum).toContain("openfoam");
  });

  it("calibrate PLA: fit, apply, the material is calibrated (n tests)", async () => {
    const cal = await client.saveCalibration({
      material: "PLA", printer_profile: "MK4",
      tests: [
        { kind: "tensile", break_n: 1300, width_mm: 10, thickness_mm: 4 },
        { kind: "tensile", break_n: 1200, width_mm: 10, thickness_mm: 4 },
        { kind: "bend", span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: 40, deflection_mm: 2 },
      ],
    });
    expect(cal.fit?.yield_mpa).toBeCloseTo(31.25, 6);
    expect(cal.fit?.e_mpa).toBeCloseTo(2048, 6);
    expect(cal.fit?.n).toBe(3);
    expect(cal.fit?.per_test).toHaveLength(3);
    expect(cal.applied).toBe(false);
    const ap = await client.applyCalibration(cal.id);
    expect(ap.calibration.applied).toBe(true);
    const pla = (await client.studyMaterials()).builtin.find((m) => m.name === "PLA")!;
    expect(provenance(pla).label).toBe("calibrated (3 tests)");
    expect(pla.e_mpa).toBe(2048);
    expect((await client.calibrations()).map((c) => c.id)).toEqual([cal.id]);
    await client.deleteCalibration(cal.id);
    expect(provenance((await client.studyMaterials()).builtin.find((m) => m.name === "PLA")!).calibrated).toBe(false);
  });

  it("an unknown material is refused", async () => {
    await expect(client.saveCalibration({ material: "Unobtainium", printer_profile: "MK4", tests: [{ kind: "tensile", break_n: 1, width_mm: 1, thickness_mm: 1 }] }))
      .rejects.toBeInstanceOf(CoreError);
  });

  it("OpenFOAM: missing until its image is installed", async () => {
    const row = () => client.studySolvers().then((s) => s.cfd?.find((r) => r.id === "openfoam"));
    expect((await row())?.status).toBe("missing");
    expect((await client.systemInstall("openfoam")).ok).toBe(true);
    await until(async () => (await row())?.status === "ok");
  });

  it("a nonlinear static study with fatigue reports both", async () => {
    const { run_id } = await client.startStudy({
      type: "static", part_id: "beam", settings: {
        load_cases: [{ name: "tip", supports: [{ kind: "fixed", face: "x-min" }], loads: [{ kind: "force", face: "x-max", newtons: 10, direction: "-z" }], note: "" }],
        nonlinear: { large_deflection: true, plasticity: true, steps: 10, contact: [{ kind: "rigid", face: "bottom", friction: 0.3 }] },
        fatigue: { cycles: 1e6, cycle: "fully_reversed", min_ratio: 0, mean_correction: "goodman" },
        print_direction: "+z",
      },
    });
    await until(async () => (await client.listStudies({ runId: run_id }))[0]?.status === "done");
    const [s] = await client.listStudies({ runId: run_id });
    const rec = await client.getStudy(s.sim_id);
    expect(nonlinearOf(rec.results)?.converged).toBe(true);
    expect(nonlinearOf(rec.results)?.contact_pairs).toBe(1);
    expect(fatigueOf(rec.results)?.label).toBe("fatigue estimate");
    expect(rec.card?.fidelity_class).toBe("fem_nonlinear");
    const probe = await client.studyProbe(s.sim_id, [1, 1, 1]);
    expect(Object.keys(probe.values)).toEqual(expect.arrayContaining(["plastic_strain", "contact_pressure"]));
  });
});
