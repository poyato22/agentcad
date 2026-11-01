import { afterEach, describe, expect, it } from "vitest";
import type { AgentActivity } from "../../api/types";
import { runBusy } from "../../state/activity";
import {
  DEFAULT_LAYOUT, dockLayout, dockWidth, HOVER_GROW, LAYOUT_KEY, openRow, openStreams, parseLayout, resetDockLayout, resizeSplit,
  resizeWidth, rowGrow, setDockLayout, SPLIT_MAX, SPLIT_MIN, toggleStream, WIDTH_MAX, WIDTH_MIN,
} from "./dockLayout";
import { stopTargets, waitingOnYou } from "./stopTarget";
import { run } from "./testSteps";
import { markSeen, resetUnread, unreadCount, unreadOf } from "./unread";

afterEach(() => { try { localStorage.removeItem(LAYOUT_KEY); } catch { /* none */ } resetDockLayout(); resetUnread(); });

describe("dock layout: the Conversation on top, View | Simulations under it (P11 F2)", () => {
  it("opens all three by default, one width whatever is open", () => {
    expect(openStreams(DEFAULT_LAYOUT)).toEqual(["view", "sim", "text"]);
    expect(openRow(DEFAULT_LAYOUT)).toEqual(["view", "sim"]);
    expect(dockWidth(DEFAULT_LAYOUT)).toBe(560);
    expect(dockWidth(toggleStream(DEFAULT_LAYOUT, "view"))).toBe(560);
  });

  it("View and Simulations share the row 50/50; a hovered one takes ~90%, the other never 0", () => {
    expect(rowGrow(DEFAULT_LAYOUT, null)).toEqual({ view: 1, sim: 1 });
    expect(rowGrow(DEFAULT_LAYOUT, "view")).toEqual({ view: HOVER_GROW, sim: 1 });
    expect(rowGrow(DEFAULT_LAYOUT, "sim")).toEqual({ view: 1, sim: HOVER_GROW });
    expect(HOVER_GROW / (HOVER_GROW + 1)).toBeCloseTo(0.9);
    // the Conversation's hover or a half closed: no widening
    expect(rowGrow(DEFAULT_LAYOUT, "text")).toEqual({ view: 1, sim: 1 });
    expect(rowGrow(toggleStream(DEFAULT_LAYOUT, "sim"), "view")).toEqual({ view: 1, sim: 1 });
  });

  it("any combination can be open, but never none", () => {
    const viewOnly = toggleStream(toggleStream(DEFAULT_LAYOUT, "sim"), "text");
    expect(openStreams(viewOnly)).toEqual(["view"]);
    expect(toggleStream(viewOnly, "view")).toBe(viewOnly);
    expect(openRow(toggleStream(toggleStream(DEFAULT_LAYOUT, "view"), "sim"))).toEqual([]);
  });

  it("the left edge sets the width within its bounds and the window; the bar trades heights", () => {
    expect(resizeWidth(DEFAULT_LAYOUT, -60).width).toBe(620);
    expect(resizeWidth(DEFAULT_LAYOUT, 1000).width).toBe(WIDTH_MIN);
    expect(resizeWidth(DEFAULT_LAYOUT, -5000).width).toBe(WIDTH_MAX);
    expect(resizeWidth(DEFAULT_LAYOUT, -500, 700).width).toBe(700);
    expect(resizeSplit(DEFAULT_LAYOUT, 100, 1000).split).toBeCloseTo(0.66);
    expect(resizeSplit(DEFAULT_LAYOUT, 5000, 1000).split).toBe(SPLIT_MAX);
    expect(resizeSplit(DEFAULT_LAYOUT, -5000, 1000).split).toBe(SPLIT_MIN);
    expect(resizeSplit(DEFAULT_LAYOUT, 10, 0)).toBe(DEFAULT_LAYOUT);
  });

  it("persists the layout and reads a bad or old record as the default", () => {
    const l = resizeSplit(resizeWidth(toggleStream(DEFAULT_LAYOUT, "sim"), -40), 50, 1000);
    setDockLayout(l);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string)).toEqual(l);
    dockLayout.value = DEFAULT_LAYOUT;
    resetDockLayout();
    expect(dockLayout.value).toEqual(l);
    // a drag in progress is not saved until it ends
    setDockLayout({ ...l, width: 400 }, false);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string).width).toBe(l.width);
    expect(parseLayout({ width: "wide", split: 9, collapsed: { sim: "yes" } }))
      .toEqual({ ...DEFAULT_LAYOUT, split: SPLIT_MAX });
    expect(parseLayout(null)).toEqual(DEFAULT_LAYOUT);
    expect(parseLayout({ collapsed: { view: true, sim: true, text: true } }).collapsed.text).toBe(false);
    // the P10 columns record lives under another key: never read
    localStorage.setItem("mises.dock.columns", JSON.stringify({ collapsed: { view: true, sim: true, text: false } }));
    resetDockLayout();
    expect(dockLayout.value).toEqual(l);
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
