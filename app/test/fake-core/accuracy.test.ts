// @vitest-environment node
// Fake core, Plan 6: S3 (real shapes) and the assumed S1 / S2 additions parse with the app's own
// schemas (api/accuracy.ts); the S3 routes behave like core server/sim_api.py.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { calibrationFor, fatigueOf, layersOf, nonlinearOf, provenance } from "../../src/api/accuracy";
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
const CASE = { name: "tip", supports: [{ kind: "fixed", face: "x-min" }], loads: [{ kind: "force", face: "x-max", newtons: 10, direction: "-z" }], note: "" };
async function runStatic(settings: Record<string, unknown>) {
  const { run_id } = await client.startStudy({ type: "static", part_id: "beam", settings });
  await until(async () => (await client.listStudies({ runId: run_id }))[0]?.status === "done");
  const [s] = await client.listStudies({ runId: run_id });
  return client.getStudy(s.sim_id);
}

beforeEach(async () => {
  core = await startFakeCore({ port: 0, token: "t", scenario: scenario() });
  client = new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false });
});
afterEach(async () => { await core.stop(); });

describe("fake core Plan 6", () => {
  it("the schemas: S3 anisotropy, printer and fatigue (real); nonlinear and openfoam (assumed)", async () => {
    const types = await client.studyTypes();
    const st = types.find((t) => t.id === "static")!;
    const props = st.schema.properties as Record<string, unknown>;
    expect(Object.keys(props)).toEqual(expect.arrayContaining(["nonlinear", "fatigue"]));
    const ms = (st.schema.$defs as Record<string, { properties: Record<string, unknown> }>).MaterialSettings.properties;
    expect(Object.keys(ms)).toEqual(expect.arrayContaining(["anisotropy", "printer", "calibrated"]));
    const wind = types.find((t) => t.id === "wind")!;
    expect((wind.schema.properties as Record<string, { enum?: string[] }>).solver.enum).toContain("openfoam");
  });

  it("calibrate PLA: a fit previews and saves nothing; saving applies it to the study on that printer", async () => {
    const body = {
      material: "PLA", printer: "MK4",
      tensile: [1300, 1180, 1240].map((f) => ({ force_n: f, width_mm: 10, thickness_mm: 4 })),
      bend: [{ span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: 40, deflection_mm: 2 }], part: [],
    };
    const p = await client.fitCalibration(body);
    expect(p.saved).toBe(false);
    expect(p.fit.strength?.value_mpa).toBeCloseTo(31, 6);
    expect(p.fit.e?.value_mpa).toBeCloseTo(2048, 6);
    expect(p.basis).toBe("calibrated (4 tests)");
    expect(p.material.yield_mpa).toBeCloseTo(31, 6);
    expect(await client.calibrations()).toEqual([]);
    const c = await client.saveCalibration(body);
    expect(c.tests.tensile[0]).toMatchObject({ force_n: 1300, orientation: "xy", area_mm2: null, note: "" });
    const cals = await client.calibrations();
    expect(cals.map((x) => x.id)).toEqual([c.id]);
    // the raw material list stays "datasheet / estimate"; the badge comes from the calibration that applies
    const pla = (await client.studyMaterials()).builtin.find((m) => m.name === "PLA")!;
    expect(provenance(pla).label).toBe("datasheet / estimate");
    expect(provenance(pla, calibrationFor(cals, "PLA", "MK4")).label).toBe("calibrated (4 tests)");
    expect(calibrationFor(cals, "PLA", null)).toBeNull();
    const rec = await runStatic({ load_cases: [CASE], material: { name: "PLA", printer: "MK4" } });
    expect(provenance(rec.material as never).label).toBe("calibrated (4 tests)");
    const other = await runStatic({ load_cases: [CASE], material: { name: "PLA" } });
    expect(provenance(other.material as never).label).toBe("datasheet / estimate");
  });

  it("bad bodies are refused like pydantic: extra keys, no cross-section, a bad printer key", async () => {
    for (const b of [
      { material: "PLA", printer_profile: "MK4", tensile: [{ force_n: 1, width_mm: 1, thickness_mm: 1 }] },
      { material: "PLA", printer: "MK4", tensile: [{ force_n: 5 }] },
      { material: "PLA", printer: "-bad", tensile: [{ force_n: 1, width_mm: 1, thickness_mm: 1 }] },
      { material: "PLA", printer: "MK4", tensile: [{ break_n: 1, width_mm: 1, thickness_mm: 1 }] },
    ]) {
      await expect(client.fitCalibration(b as never)).rejects.toBeInstanceOf(CoreError);
    }
  });

  it("OpenFOAM: missing until its image is installed", async () => {
    const row = () => client.studySolvers().then((s) => s.cfd?.find((r) => r.id === "openfoam"));
    expect((await row())?.status).toBe("missing");
    expect((await client.systemInstall("openfoam")).ok).toBe(true);
    await until(async () => (await row())?.status === "ok");
  });

  it("an orthotropic study with fatigue reports S3's results; nonlinear adds the assumed block", async () => {
    const rec = await runStatic({
      load_cases: [CASE], material: { name: "PLA", anisotropy: { print_direction: "+x" } },
      fatigue: { cycles: 2e6, cycle: "zero_to_max" },
      nonlinear: { large_deflection: true, plasticity: true, steps: 10, contact: [{ kind: "rigid", face: "bottom", friction: 0.3 }] },
    });
    expect(fatigueOf(rec.results)?.cycles_required).toBe(2e6);
    expect(fatigueOf(rec.results)?.sn_curve?.basis).toBe("estimate");
    expect(layersOf(rec.results)?.governing_failure).toBe("across layers (Z tension)");
    expect(nonlinearOf(rec.results)?.contact_pairs).toBe(1);
    const probe = await client.studyProbe(rec.sim_id, [1, 1, 1]);
    expect(Object.keys(probe.values)).toEqual(expect.arrayContaining(["layer_tension", "fatigue_safety_factor", "plastic_strain", "contact_pressure"]));
    // without them, no S3 blocks
    const plain = await runStatic({ load_cases: [CASE] });
    expect(fatigueOf(plain.results)).toBeNull();
    expect(layersOf(plain.results)).toBeNull();
  });
});
