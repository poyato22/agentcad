import { afterEach, describe, expect, it } from "vitest";
import type { AgentActivity } from "../../api/types";
import { runBusy } from "../../state/activity";
import {
  DEFAULT_LAYOUT, dockLayout, LAYOUT_KEY, parseLayout, resetDockLayout, resizeStream, resizeWidth, setDockLayout, STREAM_MIN, TEXT_MIN,
  toggleStream, WIDTH_MAX, WIDTH_MIN,
} from "./dockLayout";
import { stopTargets, waitingOnYou } from "./stopTarget";
import { run } from "./testSteps";
import { markSeen, resetUnread, unreadCount, unreadOf } from "./unread";

afterEach(() => { try { localStorage.removeItem(LAYOUT_KEY); } catch { /* none */ } resetDockLayout(); resetUnread(); });

describe("dock layout (Plan 9 D4)", () => {
  it("resizes a stream within its bounds and keeps the Conversation's room", () => {
    const l = DEFAULT_LAYOUT;
    expect(resizeStream(l, "view", 40, 900).view).toBe(l.view + 40);
    expect(resizeStream(l, "view", -1000, 900).view).toBe(STREAM_MIN);
    // 900 tall, sim 180: the view may grow to 900 - TEXT_MIN - 180
    expect(resizeStream(l, "view", 5000, 900).view).toBe(900 - TEXT_MIN - l.sim);
    expect(resizeStream(l, "sim", 5000, 900).sim).toBe(900 - TEXT_MIN - l.view);
  });

  it("keeps the width between 360 and 640 (dragging left widens)", () => {
    expect(DEFAULT_LAYOUT.width).toBe(440);
    expect(resizeWidth(DEFAULT_LAYOUT, -60).width).toBe(500);
    expect(resizeWidth(DEFAULT_LAYOUT, -1000).width).toBe(WIDTH_MAX);
    expect(resizeWidth(DEFAULT_LAYOUT, 1000).width).toBe(WIDTH_MIN);
  });

  it("persists sizes and collapsed streams, and reads a bad record as the default", () => {
    const l = toggleStream(resizeStream(DEFAULT_LAYOUT, "sim", 30, 900), "view");
    setDockLayout(l);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string)).toEqual(l);
    dockLayout.value = DEFAULT_LAYOUT;
    resetDockLayout();
    expect(dockLayout.value).toEqual(l);
    expect(dockLayout.value.collapsed).toEqual({ view: true, sim: false, text: false });
    // a drag in progress is not saved until it ends
    setDockLayout({ ...l, view: 222 }, false);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string).view).toBe(l.view);
    expect(parseLayout({ width: "wide", view: -5, collapsed: { sim: "yes" } })).toEqual({ ...DEFAULT_LAYOUT, view: STREAM_MIN });
    expect(parseLayout(null)).toEqual(DEFAULT_LAYOUT);
    expect(parseLayout({ width: 9999 }).width).toBe(WIDTH_MAX);
    localStorage.setItem(LAYOUT_KEY, "{not json");
    resetDockLayout();
    expect(dockLayout.value).toEqual(DEFAULT_LAYOUT);
  });
});

describe("unread counts of collapsed streams", () => {
  it("counts rows that arrived since the stream was last open", () => {
    expect(unreadCount(["a", "b", "c"], new Set(["a"]))).toBe(2);
    markSeen("r1", "sim", ["a", "b"]);
    expect(unreadOf("r1", "sim", ["a", "b", "c", "d"])).toBe(2);
    // a stream never seen open starts with what it holds as read
    expect(unreadOf("r1", "view", ["x", "y"])).toBe(0);
    expect(unreadOf("r1", "view", ["x", "y", "z"])).toBe(1);
    // per run
    expect(unreadOf("r2", "sim", ["a", "b", "c", "d"])).toBe(0);
  });
});

describe("what Stop stops (Plan 9 D2)", () => {
  const act = (state: AgentActivity["state"]): AgentActivity => ({ run_id: "x", state, detail: "", since: 1 });
  it("a run is busy while it works or waits on you, not once its turn ended", () => {
    expect(runBusy({ status: "running" }, undefined)).toBe(true);
    expect(runBusy({ status: "running" }, act("tool"))).toBe(true);
    expect(runBusy({ status: "running" }, act("waiting_approval"))).toBe(true);
    expect(runBusy({ status: "running" }, act("idle"))).toBe(false);
    expect(runBusy({ status: "queued" }, undefined)).toBe(true);
    expect(runBusy({ status: "done" }, act("tool"))).toBe(false);
  });

  it("is the build lead's tree: the lead and any busy sub-agent, at any depth", () => {
    const lead = run("L", { role: "lead" });
    const sub = run("S", { parent_id: "L", role: "sub" });
    const subsub = run("SS", { parent_id: "S", role: "sub" });
    const other = run("O");
    const idle = new Map([["L", act("idle")]]);
    expect(stopTargets([other, subsub, sub, lead], null, new Map()).map((r) => r.id)).toEqual(["L", "S", "SS"]);
    // the lead between turns, a sub-agent still working: Stop is offered for the sub-agent
    expect(stopTargets([lead, sub, other], null, idle).map((r) => r.id)).toEqual(["S"]);
    expect(stopTargets([lead, { ...sub, status: "done" }], null, idle)).toEqual([]);
    // no live lead: the shown run's tree
    expect(stopTargets([other, run("C", { parent_id: "O" })], other, new Map()).map((r) => r.id)).toEqual(["O", "C"]);
    expect(stopTargets([], null, new Map())).toEqual([]);
  });

  it("fix round 1 (I2): a busy shown run outside the lead's tree, and its sub-agents, get Stop too", () => {
    const lead = run("L", { role: "lead" });
    const other = run("O");
    const child = run("OC", { parent_id: "O" });
    const idleLead = new Map([["L", act("idle")]]);
    expect(stopTargets([lead, other, child], other, idleLead).map((r) => r.id)).toEqual(["O", "OC"]);
    // both trees busy: the lead's first, then the shown run's, each once
    expect(stopTargets([lead, other, child], other, new Map()).map((r) => r.id)).toEqual(["L", "O", "OC"]);
    expect(stopTargets([lead, other], lead, new Map()).map((r) => r.id)).toEqual(["L"]);
  });

  it("Esc spares a run that waits on your approval or answer (the button still stops it)", () => {
    expect(waitingOnYou(act("waiting_approval"))).toBe(true);
    expect(waitingOnYou(act("waiting_user"))).toBe(true);
    expect(waitingOnYou(act("tool"))).toBe(false);
    expect(waitingOnYou(undefined)).toBe(false);
  });
});
