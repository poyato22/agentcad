// Plan 9 G-D: a presented animation, full window — the controller's scrubbable playback (play /
// pause, scrub, loop, speed) on the real hinge fixture, with a hand-cranked clock.
import { beforeEach, describe, expect, it } from "vitest";
import { AssemblyController } from "./controller";
import { hingeFixture, ManualClock, MemoryAssemblyClient } from "./testClient";

let core: MemoryAssemblyClient;
let clock: ManualClock;
let ctl: AssemblyController;

const HINGE = hingeFixture();
const JOINT = HINGE.joints[0].id;
const [, LID] = HINGE.instances.map((i) => i.id);
const OPEN_SHUT = [{ joint_id: JOINT, from: 0, to: 120, duration_s: 2 }, { joint_id: JOINT, from: 120, to: 0, duration_s: 1.5 }];
const v = () => ctl.values.value[JOINT] as number;

beforeEach(async () => {
  core = new MemoryAssemblyClient();
  clock = new ManualClock();
  ctl = new AssemblyController(() => core, { clock });
  await ctl.open();
});

describe("motion playback (a presented animation)", () => {
  it("starts_at_zero_plays_through_both_steps_and_holds_the_end_without_loop", () => {
    expect(ctl.openMotion(OPEN_SHUT, { loop: false })).toBe(true);
    expect(ctl.motion.value).toMatchObject({ t: 0, duration: 3.5, playing: true, speed: 1, loop: false });
    expect(v()).toBeCloseTo(0, 5);
    clock.tick(2000);
    expect(v()).toBeGreaterThan(110);                        // the first step reached its end
    clock.tick(2000);
    expect(ctl.motion.value).toMatchObject({ t: 3.5, playing: false });   // held at the end
    expect(v()).toBeCloseTo(0, 5);
    expect(ctl.animating.value).toBe(JOINT);                 // still showing the motion
    expect(core.named("drive")).toHaveLength(0);            // a preview: nothing written
  });

  it("scrubs_to_any_time_and_pauses_there", () => {
    ctl.openMotion(OPEN_SHUT, { loop: true });
    ctl.seekMotion(1);                                      // half-way through the opening
    expect(ctl.motion.value).toMatchObject({ t: 1, playing: false });
    expect(v()).toBeCloseTo(60, 0);
    const lid = ctl.currentPose()[LID];
    clock.tick(500);
    expect(ctl.motion.value?.t).toBe(1);                    // paused: nothing moves
    expect(ctl.currentPose()[LID]).toEqual(lid);
    ctl.seekMotion(2 + 0.75);                               // half-way back
    expect(v()).toBeCloseTo(60, 0);
    ctl.seekMotion(99);
    expect(ctl.motion.value?.t).toBe(3.5);                  // clamped to the motion
  });

  it("loops_wrap_around_and_speed_scales_time", () => {
    ctl.openMotion(OPEN_SHUT, { loop: true });
    ctl.setMotion({ speed: 2 });
    clock.tick(1000);
    expect(ctl.motion.value?.t).toBeCloseTo(2, 1);          // 1 s at 2x
    clock.tick(1000);
    const t = ctl.motion.value?.t ?? 0;
    expect(t).toBeGreaterThan(0.3);                         // wrapped: 4 s of motion in a 3.5 s loop
    expect(t).toBeLessThan(0.7);
    expect(ctl.motion.value?.playing).toBe(true);
    ctl.setMotion({ speed: 0.5, playing: false });
    expect(ctl.motion.value).toMatchObject({ speed: 0.5, playing: false });
  });

  it("play_at_the_end_starts_over", () => {
    ctl.openMotion(OPEN_SHUT, { loop: false });
    clock.tick(4000);
    ctl.setMotion({ playing: true });
    expect(ctl.motion.value?.t).toBe(0);
    clock.tick(100);
    expect(ctl.motion.value?.playing).toBe(true);
  });

  it("closing_puts_the_joint_back_and_an_unknown_joint_is_refused", () => {
    ctl.openMotion(OPEN_SHUT, { loop: true });
    clock.tick(700);
    ctl.closeMotion();
    expect(ctl.motion.value).toBeNull();
    expect(ctl.animating.value).toBeNull();
    expect(v()).toBe(90);                                   // the fixture's value
    expect(ctl.openMotion([{ joint_id: "nope", from: 0, to: 1, duration_s: 1 }])).toBe(false);
    expect(ctl.motion.value).toBeNull();
  });

  it("an_agents_live_play_replaces_the_scrubbing", () => {
    ctl.openMotion(OPEN_SHUT, { loop: true });
    ctl.play([{ joint_id: JOINT, from: 10, to: 20, duration_s: 1 }]);
    expect(ctl.motion.value).toBeNull();
    expect(ctl.animating.value).toBe(JOINT);
  });
});
