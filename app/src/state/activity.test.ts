import { afterEach, describe, expect, it } from "vitest";
import type { MisesEvent } from "../api/types";
import { activityByRun, activitySummary, parseActivity, recordActivity, resetActivity, seedActivity, startActivity } from "./activity";

function act(run: string, state: string, detail = ""): MisesEvent {
  return { type: "agent_activity", run_id: run, ts: 0, data: { state, detail, since: 1 } };
}

afterEach(() => resetActivity());

describe("seedActivity (C-3)", () => {
  const live = (run: string, state: string, since: number): MisesEvent => ({ type: "agent_activity", run_id: run, ts: 0, data: { state, detail: "", since } });

  it("a snapshot seeds; a newer live event wins; a missing snapshot never erases one", () => {
    seedActivity(new Map([["a", { state: "tool", detail: "x", since: 10 }]]));
    expect(activityByRun.value.get("a")?.state).toBe("tool");
    recordActivity(live("b", "thinking", 20));
    seedActivity(new Map<string, unknown>([["b", null], ["a", { state: "done", detail: "", since: 5 }]]));
    expect(activityByRun.value.get("b")?.state).toBe("thinking");   // null: the fetch predates it
    expect(activityByRun.value.get("a")?.state).toBe("tool");       // an older "done" than what we have
    seedActivity(new Map([["a", { state: "done", detail: "", since: 11 }]]));
    expect(activityByRun.value.has("a")).toBe(false);
  });

  it("drops a finished run's stale working state, not one newer than the list or an idle session", () => {
    recordActivity(live("old", "tool", 1));
    recordActivity(live("new", "tool", 50));
    recordActivity(live("cc", "idle", 1));
    seedActivity(new Map(), ["old", "new", "cc"], 40);
    expect([...activityByRun.value.keys()].sort()).toEqual(["cc", "new"]);
  });
});

describe("parseActivity", () => {
  it("reads the contract fields", () => {
    const a = parseActivity({ type: "agent_activity", run_id: "r1", ts: 0, data: { state: "thinking", detail: "measuring arm", since: 5, tokens_out: 12 } });
    expect(a).toEqual({ run_id: "r1", state: "thinking", detail: "measuring arm", since: 5, tokens_out: 12 });
  });
  it("falls back to data.run_id and rejects unknown states", () => {
    expect(parseActivity({ type: "agent_activity", run_id: null, ts: 0, data: { run_id: "r2", state: "tool" } })?.run_id).toBe("r2");
    expect(parseActivity({ type: "agent_activity", run_id: "r", ts: 0, data: { state: "banana" } })).toBeNull();
    expect(parseActivity({ type: "other", run_id: "r", ts: 0, data: { state: "tool" } })).toBeNull();
  });
});

describe("recordActivity + summary", () => {
  it("counts working and thinking runs", () => {
    recordActivity(act("r1", "thinking"));
    recordActivity(act("r2", "tool"));
    expect(activitySummary.value).toMatchObject({ working: 2, thinking: 1, label: "2 working · 1 thinking" });
  });

  it("drops a run when it is done", () => {
    recordActivity(act("r1", "writing"));
    expect(activityByRun.value.has("r1")).toBe(true);
    recordActivity(act("r1", "done"));
    expect(activityByRun.value.has("r1")).toBe(false);
    expect(activitySummary.value.label).toBeNull();
  });

  it("keeps an idle run (the manager says idle) but never counts it as working or waiting", () => {
    recordActivity(act("r1", "tool"));
    recordActivity(act("r1", "idle"));
    expect(activityByRun.value.get("r1")?.state).toBe("idle");
    expect(activitySummary.value).toMatchObject({ working: 0, waiting: 0, label: null });
  });

  it("a waiting state ends when the run works on (waiting_approval → tool)", () => {
    recordActivity(act("r1", "waiting_approval"));
    recordActivity(act("r2", "waiting_user"));
    expect(activitySummary.value).toMatchObject({ waiting: 2, label: "2 waiting" });
    recordActivity(act("r1", "tool"));
    expect(activitySummary.value).toMatchObject({ working: 1, waiting: 1, label: "1 working" });
  });

  it("labels waiting runs when nothing is working", () => {
    recordActivity(act("r1", "waiting_approval"));
    expect(activitySummary.value).toMatchObject({ working: 0, waiting: 1, label: "1 waiting" });
  });
});

describe("startActivity", () => {
  it("subscribes to the stream and records events", () => {
    const h: { cb: ((e: MisesEvent) => void) | null } = { cb: null };
    const stop = startActivity((fn) => { h.cb = fn; return () => { h.cb = null; }; });
    h.cb?.(act("r9", "starting"));
    expect(activitySummary.value.working).toBe(1);
    stop();
    expect(h.cb).toBeNull();
  });
});
