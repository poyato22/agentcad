import { afterEach, describe, expect, it } from "vitest";
import type { AgentActivity } from "../../api/types";
import { runBusy } from "../../state/activity";
import {
  DEFAULT_LAYOUT, dockLayout, dockWidth, LAYOUT_KEY, openStreams, parseLayout, resetDockLayout, resizeSplit,
  resizeWidth, setDockLayout, showStream, SPLIT_MAX, SPLIT_MIN, togglePart, WIDTH_MAX, WIDTH_MIN,
} from "./dockLayout";
import { stopTargets, waitingOnYou } from "./stopTarget";
import { run } from "./testSteps";
import { markSeen, resetUnread, unreadCount, unreadOf } from "./unread";

afterEach(() => { try { localStorage.removeItem(LAYOUT_KEY); } catch { /* none */ } resetDockLayout(); resetUnread(); });

describe("dock layout: View or Simulations on top, the Conversation under it (G1)", () => {
  it("shows View and the Conversation by default, one width whatever is open", () => {
    expect(openStreams(DEFAULT_LAYOUT)).toEqual(["view", "text"]);
    expect(dockWidth(DEFAULT_LAYOUT)).toBe(560);
    expect(dockWidth(togglePart(DEFAULT_LAYOUT, "top"))).toBe(560);
  });

  it("the top area shows one stream at a time; picking one opens the area", () => {
    const sim = showStream(DEFAULT_LAYOUT, "sim");
    expect(openStreams(sim)).toEqual(["sim", "text"]);
    const closed = togglePart(sim, "top");
    expect(openStreams(closed)).toEqual(["text"]);
    expect(openStreams(showStream(closed, "view"))).toEqual(["view", "text"]);
  });

  it("either part closes, but never both", () => {
    const topOnly = togglePart(DEFAULT_LAYOUT, "text");
    expect(openStreams(topOnly)).toEqual(["view"]);
    expect(togglePart(topOnly, "top")).toBe(topOnly);
    expect(togglePart(togglePart(DEFAULT_LAYOUT, "top"), "text").collapsed).toEqual({ top: true, text: false });
  });

  it("the left edge sets the width within its bounds and the window; the divider trades heights", () => {
    expect(resizeWidth(DEFAULT_LAYOUT, -60).width).toBe(620);
    expect(resizeWidth(DEFAULT_LAYOUT, 1000).width).toBe(WIDTH_MIN);
    expect(resizeWidth(DEFAULT_LAYOUT, -5000).width).toBe(WIDTH_MAX);
    expect(resizeWidth(DEFAULT_LAYOUT, -500, 700).width).toBe(700);
    expect(resizeSplit(DEFAULT_LAYOUT, 100, 1000).split).toBeCloseTo(0.6);    // down: a taller top area
    expect(resizeSplit(DEFAULT_LAYOUT, 5000, 1000).split).toBe(SPLIT_MAX);
    expect(resizeSplit(DEFAULT_LAYOUT, -5000, 1000).split).toBe(SPLIT_MIN);
    expect(resizeSplit(DEFAULT_LAYOUT, 10, 0)).toBe(DEFAULT_LAYOUT);
  });

  it("persists the layout and reads a bad or P11 record sensibly", () => {
    const l = resizeSplit(resizeWidth(showStream(DEFAULT_LAYOUT, "sim"), -40), 50, 1000);
    setDockLayout(l);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string)).toEqual(l);
    dockLayout.value = DEFAULT_LAYOUT;
    resetDockLayout();
    expect(dockLayout.value).toEqual(l);
    // a drag in progress is not saved until it ends
    setDockLayout({ ...l, width: 400 }, false);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string).width).toBe(l.width);
    expect(parseLayout({ width: "wide", split: 9, active: "x", collapsed: { top: "yes" } }))
      .toEqual({ ...DEFAULT_LAYOUT, split: SPLIT_MAX });
    expect(parseLayout(null)).toEqual(DEFAULT_LAYOUT);
    expect(parseLayout({ collapsed: { top: true, text: true } }).collapsed).toEqual({ top: true, text: false });
    // P11 F2's {view, sim, text}: Simulations alone open shows Simulations; both closed folds the area
    expect(parseLayout({ collapsed: { view: true, sim: false, text: false } })).toMatchObject({ active: "sim", collapsed: { top: false, text: false } });
    expect(parseLayout({ collapsed: { view: true, sim: true, text: false } }).collapsed.top).toBe(true);
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
