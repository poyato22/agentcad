import { afterEach, describe, expect, it } from "vitest";
import type { AgentActivity } from "../../api/types";
import { runBusy } from "../../state/activity";
import {
  COL_MIN, DEFAULT_LAYOUT, dockLayout, dockWidth, LAYOUT_KEY, openStreams, parseLayout, resetDockLayout, resizeColumns, resizeWidth,
  setDockLayout, TAB_W, toggleStream, WIDTH_MAX, WIDTH_MIN,
} from "./dockLayout";
import { stopTargets, waitingOnYou } from "./stopTarget";
import { run } from "./testSteps";
import { markSeen, resetUnread, unreadCount, unreadOf } from "./unread";

afterEach(() => { try { localStorage.removeItem(LAYOUT_KEY); } catch { /* none */ } resetDockLayout(); resetUnread(); });

const openAll = toggleStream(toggleStream(DEFAULT_LAYOUT, "view"), "sim");

describe("dock layout: side-by-side columns (P10 E6)", () => {
  it("opens the Conversation alone by default; the dock grows with each open column (440 / 760 / 1080)", () => {
    expect(openStreams(DEFAULT_LAYOUT)).toEqual(["text"]);
    expect(dockWidth(DEFAULT_LAYOUT)).toBe(440 + 2 * TAB_W);
    const two = toggleStream(DEFAULT_LAYOUT, "view");
    expect(openStreams(two)).toEqual(["view", "text"]);
    expect(dockWidth(two)).toBe(760 + TAB_W);
    expect(openStreams(openAll)).toEqual(["view", "sim", "text"]);
    expect(dockWidth(openAll)).toBe(1080);
  });

  it("any combination can be open, but never none", () => {
    const viewOnly = toggleStream(toggleStream(DEFAULT_LAYOUT, "view"), "text");
    expect(openStreams(viewOnly)).toEqual(["view"]);
    expect(toggleStream(viewOnly, "view")).toBe(viewOnly);
    expect(openStreams(toggleStream(openAll, "sim"))).toEqual(["view", "text"]);
  });

  it("the left edge sets the width for the number of open columns, within its bounds and the window", () => {
    expect(resizeWidth(DEFAULT_LAYOUT, -60).widths).toEqual([500, 760, 1080]);
    expect(resizeWidth(DEFAULT_LAYOUT, 1000).widths[0]).toBe(WIDTH_MIN);
    expect(resizeWidth(DEFAULT_LAYOUT, -5000).widths[0]).toBe(WIDTH_MAX);
    expect(resizeWidth(openAll, -500, 1200).widths[2]).toBe(1200);
    // three columns never below 3 × COL_MIN
    expect(resizeWidth(openAll, 5000).widths[2]).toBe(3 * COL_MIN);
  });

  it("a divider moves width from one open column to the next, never below COL_MIN", () => {
    const l = resizeColumns(openAll, "view", "sim", 60, 1080);
    const px = (x: typeof l, s: "view" | "sim" | "text") => (x.share[s] / (x.share.view + x.share.sim + x.share.text)) * 1080;
    expect(Math.round(px(l, "view"))).toBe(420);
    expect(Math.round(px(l, "sim"))).toBe(300);
    expect(Math.round(px(l, "text"))).toBe(360);
    const far = resizeColumns(openAll, "view", "sim", 5000, 1080);
    expect(Math.round(px(far, "sim"))).toBe(COL_MIN);
    // a closed column is not resized
    expect(resizeColumns(DEFAULT_LAYOUT, "view", "text", 40, 440)).toBe(DEFAULT_LAYOUT);
  });

  it("persists the layout and reads a bad or old record as the default", () => {
    const l = resizeColumns(resizeWidth(openAll, -40), "sim", "text", 30, 1120);
    setDockLayout(l);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string)).toEqual(l);
    dockLayout.value = DEFAULT_LAYOUT;
    resetDockLayout();
    expect(dockLayout.value).toEqual(l);
    // a drag in progress is not saved until it ends
    setDockLayout({ ...l, widths: [400, 760, 1080] }, false);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) as string).widths).toEqual(l.widths);
    expect(parseLayout({ widths: ["wide", -5], collapsed: { sim: "yes" }, share: { view: 99 } }))
      .toEqual({ ...DEFAULT_LAYOUT, widths: [440, 2 * COL_MIN, 1080], share: { view: 5, sim: 1, text: 1 } });
    expect(parseLayout(null)).toEqual(DEFAULT_LAYOUT);
    expect(parseLayout({ collapsed: { view: true, sim: true, text: true } }).collapsed.text).toBe(false);
    // the stacked layout of Plan 9 ({width, view, sim}) has no columns: the default
    expect(parseLayout({ width: 600, view: 300, sim: 200 })).toEqual(DEFAULT_LAYOUT);
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
