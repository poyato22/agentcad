import { beforeEach, describe, expect, it } from "vitest";
import { AssemblyController, TWEEN_MS, INTERFERENCE_SETTLE_MS } from "./controller";
import { flush, hingeFixture, ManualClock, MemoryAssemblyClient, PROJECT } from "./testClient";

let core: MemoryAssemblyClient;
let clock: ManualClock;
let ctl: AssemblyController;

const HINGE = hingeFixture();
const JOINT = HINGE.joints[0].id;
const [BASE, LID] = HINGE.instances.map((i) => i.id);

beforeEach(async () => {
  core = new MemoryAssemblyClient();
  clock = new ManualClock();
  ctl = new AssemblyController(() => core, { clock });
  await ctl.open();
});

describe("open", () => {
  it("finds_the_default_build_and_loads_its_parts_and_assembly", () => {
    expect(ctl.projectId.value).toBe(PROJECT);
    expect(ctl.data.value?.instances.map((i) => i.name)).toEqual(["base", "lid"]);
    expect(ctl.values.value[JOINT]).toBe(90);
    expect(ctl.currentPose()[LID][0][3]).toBe(20);
  });
});

describe("play (an agent's assembly_animate, R19)", () => {
  const run = (ms: number) => { const seen: number[] = []; for (let t = 0; t < ms; t += 16) { clock.tick(16); seen.push(ctl.values.value[JOINT] as number); } return seen; };

  it("plays_each_step_from_to_over_its_duration_in_order_then_puts_the_joint_back", () => {
    let ended = 0;
    expect(ctl.play([{ joint_id: JOINT, from: 0, to: 45, duration_s: 1 }, { joint_id: JOINT, from: 45, to: 10, duration_s: 1 }], { onEnd: () => { ended++; } })).toBe(true);
    expect(ctl.animating.value).toBe(JOINT);
    const first = run(1000);
    expect(Math.max(...first)).toBeCloseTo(45, 0);          // the first step reaches its end…
    expect(Math.min(...first)).toBeCloseTo(0, 0);           // …from its start, not the full range
    const second = run(900);
    expect(second[second.length - 1]).toBeLessThan(20);     // the second step heads to 10
    run(200);
    expect(ctl.animating.value).toBeNull();
    expect(ended).toBe(1);
    expect(ctl.values.value[JOINT]).toBe(90);               // back where it was
    expect(core.named("drive")).toHaveLength(0);            // a preview: nothing written
  });

  it("loops_repeat_the_sequence", () => {
    ctl.play([{ joint_id: JOINT, from: 0, to: 30, duration_s: 0.5 }], { loops: 3 });
    const seen = run(1400);
    expect(ctl.animating.value).toBe(JOINT);                // still in its 3rd pass
    const restarts = seen.filter((v, i) => i > 0 && v < seen[i - 1] - 10).length;
    expect(restarts).toBe(2);
    run(200);
    expect(ctl.animating.value).toBeNull();
  });

  it("stop_restores_every_joint_it_touched_and_a_new_play_replaces_the_old_one", () => {
    ctl.play([{ joint_id: JOINT, from: 0, to: 45, duration_s: 2 }]);
    run(500);
    expect(ctl.play([{ joint_id: JOINT, from: 10, to: 20, duration_s: 2 }])).toBe(true);   // never toggles off
    expect(ctl.animating.value).toBe(JOINT);
    run(300);
    ctl.stopAnimate();
    expect(ctl.animating.value).toBeNull();
    expect(ctl.values.value[JOINT]).toBe(90);
  });

  it("skips_unknown_joints_and_refuses_when_none_is_left", () => {
    expect(ctl.play([{ joint_id: "nope", from: 0, to: 1, duration_s: 1 }])).toBe(false);
    expect(ctl.animating.value).toBeNull();
    expect(ctl.play([{ joint_id: "nope", from: 0, to: 1, duration_s: 1 }, { joint_id: JOINT, from: 0, to: 9, duration_s: 1 }])).toBe(true);
  });
});

describe("agent changes", () => {
  it("assembly_changed_rereads_and_glides_the_parts_to_the_new_pose", async () => {
    core.asm.joints[0].value = 0;     // an agent drove the hinge flat
    ctl.onEvent({ type: "assembly_changed", run_id: "r1", data: { project_id: PROJECT }, ts: 0 });
    clock.tick(80);
    await flush();
    expect(ctl.arriving.value).toBe(true);
    clock.tick(TWEEN_MS / 2);
    const mid = ctl.currentPose()[LID][0][3];
    expect(mid).toBeGreaterThan(20);
    expect(mid).toBeLessThan(40);
    clock.tick(TWEEN_MS);
    expect(ctl.arriving.value).toBe(false);
    expect(ctl.currentPose()[LID][0][3]).toBeCloseTo(40);
    expect(ctl.values.value[JOINT]).toBe(0);
  });

  it("ignores_other_builds", async () => {
    const n = core.named("assembly").length;
    ctl.onEvent({ type: "assembly_changed", run_id: null, data: { project_id: "other" }, ts: 0 });
    clock.tick(200);
    await flush();
    expect(core.named("assembly").length).toBe(n);
  });
});

describe("interference", () => {
  it("checks_after_the_assembly_settles_and_maps_names_to_instances", async () => {
    core.interferenceAnswer = [{ a: "base", b: "lid", volume_mm3: 180 }];
    clock.tick(INTERFERENCE_SETTLE_MS + 20);
    await flush();
    expect(ctl.interference.value.status).toBe("done");
    expect([...ctl.interference.value.ids].sort()).toEqual([LID, BASE].sort());
    // an agent drives the hinge flat: re-checked once the glide lands
    core.interferenceAnswer = [];
    core.asm.joints[0].value = 0;
    ctl.onEvent({ type: "assembly_changed", run_id: "r1", data: { project_id: PROJECT }, ts: 0 });
    clock.tick(80);
    await flush();
    clock.tick(TWEEN_MS + INTERFERENCE_SETTLE_MS + 40);
    await flush();
    expect(ctl.interference.value.items).toEqual([]);
    expect(core.named("interference").length).toBe(2);
  });
});

describe("while an animation plays", () => {
  it("an_agent_change_waits_for_the_end_then_lands", async () => {
    ctl.play([{ joint_id: JOINT, from: 0, to: 45, duration_s: 0.5 }]);
    core.asm.joints[0].value = 0;
    ctl.onEvent({ type: "assembly_changed", run_id: "r1", data: { project_id: PROJECT }, ts: 0 });
    clock.tick(100);
    await flush();
    expect(ctl.data.value?.joints[0].value).toBe(90);       // not yet: the animation owns the pose
    clock.tick(600);
    await flush();
    clock.tick(TWEEN_MS + 40);
    expect(ctl.values.value[JOINT]).toBe(0);
    expect(ctl.currentPose()[LID][0][3]).toBeCloseTo(40);
  });

  it("taking_over_during_an_agent_glide_clears_the_blue_edges", async () => {
    core.asm.joints[0].value = 0;
    ctl.onEvent({ type: "assembly_changed", run_id: "r1", data: { project_id: PROJECT }, ts: 0 });
    clock.tick(80);
    await flush();
    clock.tick(TWEEN_MS / 3);
    expect(ctl.arriving.value).toBe(true);
    ctl.play([{ joint_id: JOINT, from: 0, to: 45, duration_s: 1 }]);
    expect(ctl.arriving.value).toBe(false);
  });

  it("pause_stops_it_and_puts_the_core_pose_back", () => {
    ctl.play([{ joint_id: JOINT, from: 0, to: 45, duration_s: 2 }]);
    clock.tick(500);
    expect(ctl.busy.value).toBe(true);
    ctl.pause();
    expect(ctl.busy.value).toBe(false);
    expect(ctl.values.value[JOINT]).toBe(90);
    expect(ctl.currentPose()[LID][0][3]).toBe(20);
  });
});

describe("selection", () => {
  it("a_click_selects_an_instance_and_a_removed_one_is_forgotten", async () => {
    ctl.select(LID);
    expect(ctl.selected.value).toBe(LID);
    core.asm.instances = core.asm.instances.filter((i) => i.id !== LID);
    core.asm.joints = [];
    await ctl.refresh(false);
    expect(ctl.selected.value).toBeNull();
  });
});
