import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MisesEvent } from "../api/types";
import {
  agentAnimation, MAX_LOOPS, parseAnimateSpec, recordAssemblyAnimation, resetAssemblyAnimation, setAnimationPlayer, stopAgentAnimation,
  watchAgentAnimation, type AnimationPlayer,
} from "./assemblyAnimation";
import { activeBuildId } from "./builds";

/** The real U3c events (core scripts/dump_agents_fixtures.py): a looping joint, then a sequence. */
const REAL: MisesEvent[] = readFileSync(resolve(import.meta.dirname, "../../../core/tests/fixtures/ui/plan7/animate_events.jsonl"), "utf8")
  .trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);

function player(over: Partial<AnimationPlayer> = {}): AnimationPlayer & { ends: (() => void)[] } {
  const ends: (() => void)[] = [];
  return {
    ends,
    play: vi.fn((_s, onEnd: () => void) => { ends.push(onEnd); return true; }),
    watch: vi.fn(async (_s, onEnd: () => void) => { ends.push(onEnd); return true; }),
    stop: vi.fn(),
    ...over,
  };
}
const ev = (data: Record<string, unknown>, runId: string | null = "r1"): MisesEvent => ({ type: "assembly_animate", run_id: runId, data, ts: 1 });

beforeEach(() => { activeBuildId.value = "p"; });
afterEach(() => { resetAssemblyAnimation(); setAnimationPlayer(null); activeBuildId.value = null; });

describe("parseAnimateSpec (the real U3c event)", () => {
  it("a single joint becomes one step; loop with no count plays MAX_LOOPS times", () => {
    const s = parseAnimateSpec(REAL[0]);
    expect(s?.project_id).toBe("p");
    expect(s?.steps).toEqual([{ joint_id: REAL[0].data.joint_id, from: 0, to: 90, duration_s: 3 }]);
    expect(s?.loops).toBe(REAL[0].data.loops ?? MAX_LOOPS);
  });
  it("a sequence keeps its steps in order", () => {
    const s = parseAnimateSpec(REAL[1]);
    expect(s?.steps.map((x) => [x.from, x.to])).toEqual([[0, 45], [45, 0]]);
    expect(s?.loops).toBe(1);
  });
  it("needs a playable step; ignores other events and non-numbers", () => {
    expect(parseAnimateSpec(ev({ project_id: "p", duration_s: 2 }))).toBeNull();
    expect(parseAnimateSpec(ev({ project_id: "p", joint_id: "j", to: "90" }))).toBeNull();
    expect(parseAnimateSpec({ type: "run_started", run_id: "r1", data: {}, ts: 1 })).toBeNull();
    expect(parseAnimateSpec(ev({ joint_id: "j", to: 10, loops: 3 }))?.loops).toBe(3);
  });
});

describe("recordAssemblyAnimation", () => {
  it("plays through the registered player, then clears the chip when the motion ends by itself", () => {
    const p = player();
    setAnimationPlayer(p);
    recordAssemblyAnimation(REAL[1]);
    expect(p.play).toHaveBeenCalledOnce();
    expect(agentAnimation.value?.playing).toBe(true);
    p.ends[0]();
    expect(agentAnimation.value).toBeNull();
  });
  it("a repeated event replays — it never toggles the motion off", () => {
    const p = player();
    setAnimationPlayer(p);
    recordAssemblyAnimation(REAL[0]);
    recordAssemblyAnimation(REAL[0]);
    expect(p.play).toHaveBeenCalledTimes(2);
    expect(p.stop).not.toHaveBeenCalled();
    expect(agentAnimation.value?.playing).toBe(true);
  });
  it("another build's motion is ignored", () => {
    const p = player();
    setAnimationPlayer(p);
    recordAssemblyAnimation(ev({ project_id: "other", joint_id: "j", from: 0, to: 9 }));
    expect(p.play).not.toHaveBeenCalled();
    expect(agentAnimation.value).toBeNull();
  });
  it("when the assembly isn't on screen the chip offers Watch, which plays it there", async () => {
    const p = player({ play: vi.fn(() => false) });
    setAnimationPlayer(p);
    recordAssemblyAnimation(REAL[1]);
    expect(agentAnimation.value?.playing).toBe(false);
    await watchAgentAnimation();
    expect(p.watch).toHaveBeenCalledOnce();
    expect(agentAnimation.value?.playing).toBe(true);
  });
  it("Stop clears it and puts the joints back; the driving run finishing does too", () => {
    const p = player();
    setAnimationPlayer(p);
    recordAssemblyAnimation(REAL[0]);
    stopAgentAnimation();
    expect(agentAnimation.value).toBeNull();
    expect(p.stop).toHaveBeenCalledOnce();
    recordAssemblyAnimation({ ...REAL[0], run_id: "r9" });
    recordAssemblyAnimation({ type: "run_finished", run_id: "r9", data: { status: "done" }, ts: 2 });
    expect(agentAnimation.value).toBeNull();
  });
});
