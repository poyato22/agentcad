// G4: the user's ornicopter (real-core fixtures: core/scripts/dump_ornicopter_fixtures.py on a
// copy of their build). Its saved "flap cycle" drives only the motor's pinion; the gears, cranks,
// links and the four wings move through the step's `follow` values (gear couplings and four
// loop-closed linkages the app's tree solve can't close itself). Before G4 the app dropped
// `follow`: only the 1 mm pinion turned and the animation looked dead.
import { beforeEach, describe, expect, it } from "vitest";
import animations from "../../../test/fixtures/ornicopter/animations.json";
import asmJson from "../../../test/fixtures/ornicopter/assembly.json";
import partsJson from "../../../test/fixtures/ornicopter/parts.json";
import { assemblyPartSchema, assemblySchema } from "../../api/assembly";
import { materialOf } from "../../lib/appearance";
import { parseAnimateSteps } from "../../state/assemblyAnimation";
import { AssemblyController } from "./controller";
import { ManualClock, MemoryAssemblyClient } from "./testClient";

const ASM = assemblySchema.parse(asmJson);
// (the dump keeps only each part's box of its metrics: the joints name no faces)
const PARTS = (partsJson as Record<string, unknown>[]).map((p) => assemblyPartSchema.parse({ ...p, metrics: null }));
const SAVED = animations as { name: string; steps: unknown; loops: number }[];
const id = (name: string) => ASM.instances.find((i) => i.name === name)?.id as string;
const WING_FL = "41166c4ae788";          // fuselage → root_fl, a loop-closed hinge
const MOTOR = "479b4ee45cff";            // the driven pinion

let ctl: AssemblyController;
let clock: ManualClock;

beforeEach(async () => {
  clock = new ManualClock();
  ctl = new AssemblyController(() => new MemoryAssemblyClient(ASM, PARTS), { clock });
  await ctl.open("000b9ce75f54");
});

describe("the ornicopter's flap cycle (G4)", () => {
  const steps = parseAnimateSteps(SAVED[0].steps);

  it("keeps the step's follow values: every coupled and loop joint", () => {
    expect(SAVED[0].name).toBe("flap cycle");
    expect(steps).toHaveLength(1);
    expect(steps[0].joint_id).toBe(MOTOR);
    expect(steps[0].follow?.map((f) => f.joint_id)).toContain(WING_FL);
    expect(steps[0].follow).toHaveLength(12);
  });

  it("flaps the wings: the hinge follows its solved values and the wing root turns", () => {
    const rest = ctl.currentPose()[id("root_fl")];
    expect(ctl.openMotion(steps, { loop: true, autoplay: false })).toBe(true);
    const wing = steps[0].follow?.find((f) => f.joint_id === WING_FL)?.values as number[];
    ctl.seekMotion(0.75);                                     // a quarter: whole turns run linear
    expect(ctl.values.value[WING_FL]).toBeCloseTo(wing[4], 6);
    expect(ctl.values.value[MOTOR]).toBeCloseTo(-9720 / 4, 6);
    const flapped = ctl.currentPose()[id("root_fl")];
    const tilt = (m: number[][]) => Math.atan2(m[0][2], m[0][0]) * 180 / Math.PI;
    expect(Math.abs(tilt(flapped) - tilt(rest))).toBeGreaterThan(20);
    // the spar fixed in the root's socket goes with it
    expect(ctl.currentPose()[id("spar_fl")]).toEqual(flapped);
  });

  it("puts every follower back when it closes", () => {
    const before = { ...ctl.values.value };
    ctl.openMotion(steps, { loop: true, autoplay: false });
    ctl.seekMotion(1.1);
    ctl.closeMotion();
    for (const f of steps[0].follow ?? []) expect(ctl.values.value[f.joint_id]).toBe(before[f.joint_id]);
    expect(ctl.values.value[MOTOR]).toBe(before[MOTOR]);
  });
});

describe("the ornicopter's colors (G4)", () => {
  it("parts nobody gave a look show their material's or the concept's (Teal PLA, carbon, metals)", () => {
    const look = (pid: string) => materialOf(PARTS.find((p) => p.part_id === pid) ?? {});
    expect(look("fuselage_top")).toMatchObject({ color: "#12a6a0", shiny: true });
    expect(look("spar_rod_left").color).toBe("#1e2024");
    expect(look("coreless_motor").metalness).toBe(1);
    expect(look("motor_pinion").color).toBe("#c9a24a");
  });
});
