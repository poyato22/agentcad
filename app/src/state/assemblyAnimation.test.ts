import { afterEach, describe, expect, it, vi } from "vitest";
import type { MisesEvent } from "../api/types";
import {
  agentAnimation, parseAnimateSpec, recordAssemblyAnimation, resetAssemblyAnimation, setAnimationPlayer, stopAgentAnimation,
} from "./assemblyAnimation";

afterEach(() => { resetAssemblyAnimation(); setAnimationPlayer(null); });

const ev = (data: Record<string, unknown>, runId: string | null = "r1", ts = 10): MisesEvent =>
  ({ type: "assembly_animate", run_id: runId, data, ts });

describe("parseAnimateSpec", () => {
  it("parses a joint sweep with from/to/duration/loop", () => {
    const s = parseAnimateSpec(ev({ project_id: "b1", joint_id: "j1", from: 0, to: 90, duration_s: 3, loop: true }));
    expect(s).toEqual({ project_id: "b1", joint_id: "j1", sequence: null, from: 0, to: 90, duration_s: 3, loop: true });
  });
  it("parses a named sequence and defaults the duration", () => {
    const s = parseAnimateSpec(ev({ sequence: "walk cycle" }));
    expect(s?.sequence).toBe("walk cycle");
    expect(s?.duration_s).toBe(2);
    expect(s?.loop).toBe(false);
  });
  it("needs a joint or a sequence, and ignores other events", () => {
    expect(parseAnimateSpec(ev({ duration_s: 2 }))).toBeNull();
    expect(parseAnimateSpec({ type: "run_started", run_id: "r1", data: {}, ts: 1 })).toBeNull();
  });
});

describe("recordAssemblyAnimation", () => {
  it("tracks the active animation and drives the registered player", () => {
    const play = vi.fn(); const stop = vi.fn();
    setAnimationPlayer({ play, stop });
    recordAssemblyAnimation(ev({ joint_id: "j1", from: 0, to: 45 }));
    expect(agentAnimation.value?.spec.joint_id).toBe("j1");
    expect(play).toHaveBeenCalledOnce();
    stopAgentAnimation();
    expect(agentAnimation.value).toBeNull();
    expect(stop).toHaveBeenCalledOnce();
  });
  it("clears when the driving run finishes", () => {
    recordAssemblyAnimation(ev({ joint_id: "j1" }, "r1"));
    recordAssemblyAnimation({ type: "run_finished", run_id: "r1", data: { status: "done" }, ts: 20 });
    expect(agentAnimation.value).toBeNull();
  });
});
