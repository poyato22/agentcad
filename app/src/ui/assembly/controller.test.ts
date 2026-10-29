import { beforeEach, describe, expect, it } from "vitest";
import { AssemblyController, SWEEP_MS, TWEEN_MS, INTERFERENCE_SETTLE_MS } from "./controller";
import { faceOptions } from "./pick";
import { flush, hingeFixture, ManualClock, MemoryAssemblyClient, PROJECT, partsFixture } from "./testClient";

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

describe("drive", () => {
  it("slider_input_poses_at_once_and_drives_the_core", async () => {
    ctl.setJointValue(JOINT, 0);
    // posed locally before the core answers: lid flat beside the base
    expect(ctl.currentPose()[LID][0][3]).toBeCloseTo(40);
    expect(core.named("drive").map((c) => c.args)).toEqual([[PROJECT, JOINT, 0]]);
    await flush();
    expect(ctl.data.value?.joints[0].value).toBe(0);
  });

  it("coalesces_a_fast_slider_to_one_call_in_flight_plus_the_newest", async () => {
    core.holdDrives = true;
    ctl.beginJointGesture();
    for (const v of [80, 70, 60, 50, 40]) ctl.setJointValue(JOINT, v);
    expect(core.named("drive")).toHaveLength(1);
    core.release();
    await flush();
    expect(core.named("drive").map((c) => c.args[2])).toEqual([80, 40]);
    core.release();
    await flush();
    ctl.endJointGesture();
    expect(core.named("drive")).toHaveLength(2);
    expect(core.asm.joints[0].value).toBe(40);
    expect(ctl.values.value[JOINT]).toBe(40);
  });

  it("clamps_to_the_limits_before_sending", () => {
    ctl.setJointValue(JOINT, 400);
    expect(core.named("drive")[0].args[2]).toBe(180);
    expect(ctl.values.value[JOINT]).toBe(180);
  });

  it("an_event_during_a_gesture_waits_for_the_gesture", async () => {
    ctl.beginJointGesture();
    ctl.setJointValue(JOINT, 30);
    await flush();
    const before = core.named("assembly").length;
    ctl.onEvent({ type: "assembly_changed", run_id: null, data: { project_id: PROJECT }, ts: 0 });
    clock.tick(200);
    await flush();
    expect(core.named("assembly").length).toBe(before);
    ctl.endJointGesture();
    await flush();
    expect(core.named("assembly").length).toBe(before + 1);
  });
});

describe("animate", () => {
  it("sweeps_between_the_limits_locally_and_puts_the_joint_back", () => {
    const seen: number[] = [];
    ctl.animate(JOINT);
    expect(ctl.animating.value).toBe(JOINT);
    for (let t = 0; t < SWEEP_MS + 100; t += 16) { clock.tick(16); seen.push(ctl.values.value[JOINT] as number); }
    expect(Math.max(...seen)).toBeCloseTo(180, 0);
    expect(Math.min(...seen)).toBeCloseTo(0, 0);
    expect(ctl.animating.value).toBeNull();
    expect(ctl.values.value[JOINT]).toBe(90);
    expect(core.named("drive")).toHaveLength(0);
  });

  it("a_second_press_stops_it", () => {
    ctl.animate(JOINT);
    clock.tick(300);
    ctl.animate(JOINT);
    expect(ctl.animating.value).toBeNull();
    expect(ctl.values.value[JOINT]).toBe(90);
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

describe("joint creation", () => {
  it("pick_two_faces_choose_kind_and_limits_then_create", async () => {
    const opts = faceOptions(partsFixture()[0].metrics);
    const plusX = opts.find((o) => o.axis[0] === 1 && o.key.startsWith("f"));
    const minusX = opts.find((o) => o.axis[0] === -1 && o.key.startsWith("f"));
    if (!plusX || !minusX) throw new Error("fixture faces");
    await ctl.removeJoint(JOINT);
    ctl.startJoint("revolute");
    expect(ctl.selected.value).toBeNull();
    ctl.pickFace(BASE, plusX);
    ctl.pickFace(LID, minusX);
    // preview: the lid's −X face mates the base's +X face before anything is written
    expect(ctl.currentPose()[LID][0][3]).toBeCloseTo(40);
    ctl.updateDraft({ lo: 0, hi: 120 });
    expect(await ctl.createJoint()).toBe(true);
    const [call] = core.named("addJoint");
    expect(call.args[1]).toMatchObject({
      kind: "revolute", limits: [0, 120], value: 0,
      a: { instance_id: BASE, frame: { origin: plusX.origin, axis: plusX.axis } },
      b: { instance_id: LID, frame: { origin: minusX.origin, axis: [1, 0, 0] } },
    });
    expect(ctl.draft.value).toBeNull();
    expect(ctl.data.value?.joints).toHaveLength(1);
  });

  it("refuses_an_incomplete_draft_and_shows_the_core_refusal", async () => {
    ctl.startJoint("slider");
    expect(await ctl.createJoint()).toBe(false);
    expect(ctl.draftError.value).toMatch(/first part/);
    const o = faceOptions(partsFixture()[0].metrics)[0];
    ctl.pickFace(BASE, o);
    ctl.pickFace(LID, o);          // a second joint between base and lid closes a loop
    expect(await ctl.createJoint()).toBe(false);
    expect(ctl.draftError.value).toMatch(/loop/);
    ctl.cancelJoint();
    expect(ctl.currentPose()[LID][0][3]).toBe(20);
  });
});

describe("instances", () => {
  it("add_part_needs_a_unique_name_and_grounds_the_first_part", async () => {
    expect(await ctl.addInstance("mount_plate", "Lid")).toMatch(/already in this assembly/);
    expect(await ctl.addInstance("mount_plate", "cover")).toBeNull();
    const [call] = core.named("addInstance");
    expect(call.args[1]).toMatchObject({ part_id: "mount_plate", name: "cover", fixed: false });
    expect(ctl.selected.value).toBe(ctl.data.value?.instances[2].id);
  });

  it("a_drag_commits_one_patch_with_the_released_transform", async () => {
    await ctl.setFixed(BASE, false);
    ctl.beginDrag();
    const t = (x: number) => [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
    ctl.dragInstance(BASE, t(3));
    ctl.dragInstance(BASE, t(5));
    // the hinged lid follows the dragged base
    expect(ctl.currentPose()[LID][0][3]).toBeCloseTo(25);
    await ctl.commitInstance(BASE, t(5));
    expect(core.named("patchInstance").filter((c) => c.args[1] === BASE && "transform" in (c.args[2] as object))).toHaveLength(1);
    expect(ctl.currentPose()[BASE][0][3]).toBe(5);
  });
});

describe("interference", () => {
  it("checks_after_the_assembly_settles_and_maps_names_to_instances", async () => {
    core.interferenceAnswer = [{ a: "base", b: "lid", volume_mm3: 180 }];
    clock.tick(INTERFERENCE_SETTLE_MS + 20);
    await flush();
    expect(ctl.interference.value.status).toBe("done");
    expect([...ctl.interference.value.ids].sort()).toEqual([LID, BASE].sort());
    core.interferenceAnswer = [];
    ctl.setJointValue(JOINT, 0);
    await flush();
    clock.tick(INTERFERENCE_SETTLE_MS + 20);
    await flush();
    expect(ctl.interference.value.items).toEqual([]);
    expect(core.named("interference").length).toBe(2);
  });
});

describe("fix round 1", () => {
  async function twoJoints(): Promise<string> {
    core.asm.instances.push({ id: "cover1", part_id: "mount_plate", name: "cover", fixed: false, transform: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]] });
    core.asm.joints.push({
      id: "slide1", kind: "slider", value: 0, limits: [0, 10],
      a: { instance_id: BASE, frame: { origin: [0, 0, 3], axis: [0, 0, 1], face_id: null } },
      b: { instance_id: "cover1", frame: { origin: [0, 0, -3], axis: [0, 0, 1], face_id: null } },
    });
    await ctl.open();
    return "slide1";
  }

  it("a_queued_drive_survives_another_joint_moving_and_the_core_stays_the_truth", async () => {
    const SLIDE = await twoJoints();
    core.holdDrives = true;
    ctl.setJointValue(JOINT, 80);       // in flight
    ctl.setJointValue(JOINT, 70);       // queued
    ctl.setJointValue(SLIDE, 5);        // queued too; must not displace 70
    for (let i = 0; i < 3; i++) { core.release(); await flush(); }
    expect(core.named("drive").map((c) => [c.args[1], c.args[2]])).toEqual([[JOINT, 80], [JOINT, 70], [SLIDE, 5]]);
    expect(core.asm.joints.find((j) => j.id === JOINT)?.value).toBe(70);
    // nothing local is left over: a refresh shows the core's values, and a local solve its pose
    core.holdDrives = false;
    await ctl.refresh(false);
    expect(ctl.values.value[JOINT]).toBe(70);
    expect(ctl.values.value[SLIDE]).toBe(5);
    ctl.startJoint(); ctl.cancelJoint();  // forces a local predict()
    const want = (await core.assembly(PROJECT)).instances;
    for (const i of want) for (let r = 0; r < 3; r++) expect(ctl.currentPose()[i.id][r][3]).toBeCloseTo(i.transform[r][3], 9);
  });

  it("pause_mid_drag_ends_the_gesture_and_puts_the_core_pose_back", async () => {
    await ctl.setFixed(BASE, false);
    ctl.beginDrag();
    ctl.dragInstance(BASE, [[1, 0, 0, 7], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]);
    expect(ctl.busy.value).toBe(true);
    ctl.pause();                        // mode switched while the gizmo is held
    await flush();
    expect(ctl.busy.value).toBe(false);
    expect(ctl.currentPose()[BASE][0][3]).toBe(0);
    expect(core.named("patchInstance").filter((c) => "transform" in (c.args[2] as object))).toHaveLength(0);
    const n = core.named("assembly").length;
    ctl.onEvent({ type: "assembly_changed", run_id: null, data: { project_id: PROJECT }, ts: 0 });
    clock.tick(100);
    await flush();
    expect(core.named("assembly").length).toBe(n + 1);
    // the interference check runs at most once; it doesn't keep re-arming
    const k = core.named("interference").length;
    clock.tick(INTERFERENCE_SETTLE_MS * 6);
    await flush();
    expect(core.named("interference").length - k).toBeLessThanOrEqual(1);
  });

  it("after_a_held_gesture_the_view_snaps_to_the_core's_answer", async () => {
    ctl.beginJointGesture();
    ctl.setJointValue(JOINT, 30);
    await flush();                      // answered while still held
    ctl.endJointGesture();
    const lid = (await core.assembly(PROJECT)).instances.find((i) => i.id === LID);
    expect(ctl.currentPose()[LID]).toEqual(lid?.transform);
  });

  it("taking_over_during_an_agent_glide_clears_the_blue_edges", async () => {
    core.asm.joints[0].value = 0;
    ctl.onEvent({ type: "assembly_changed", run_id: "r1", data: { project_id: PROJECT }, ts: 0 });
    clock.tick(80);
    await flush();
    clock.tick(TWEEN_MS / 3);
    expect(ctl.arriving.value).toBe(true);
    ctl.setJointValue(JOINT, 45);
    expect(ctl.arriving.value).toBe(false);
  });
});
