import { afterEach, describe, expect, it } from "vitest";
import type { MisesEvent } from "../api/types";
import { announcementsForRun, parseAnnouncement, recordAnnouncement, resetAnnouncements } from "./announcements";

afterEach(() => resetAnnouncements());

const ev = (data: Record<string, unknown>, runId: string | null = "r1", ts = 10): MisesEvent =>
  ({ type: "agent_announce", run_id: runId, data, ts });

describe("parseAnnouncement", () => {
  it("parses kind, title, detail and ref", () => {
    const a = parseAnnouncement(ev({ kind: "part_created", title: "quad_arm", detail: "a 3 mm plate", ref: { part_id: "quad_arm" } }));
    expect(a).toMatchObject({ run_id: "r1", kind: "part_created", title: "quad_arm", detail: "a 3 mm plate" });
    expect(a?.ref.part_id).toBe("quad_arm");
  });
  it("needs a run, a kind and a title", () => {
    expect(parseAnnouncement(ev({ kind: "part_created" }))).toBeNull();          // no title
    expect(parseAnnouncement(ev({ title: "x" }))).toBeNull();                     // no kind
    expect(parseAnnouncement(ev({ kind: "x", title: "y" }, null))).toBeNull();    // no run
  });
  it("ignores non-announce events", () => {
    expect(parseAnnouncement({ type: "run_started", run_id: "r1", data: {}, ts: 1 })).toBeNull();
  });
});

describe("recordAnnouncement", () => {
  it("accumulates per run, oldest first, and de-dupes by id", () => {
    recordAnnouncement(ev({ id: "a1", kind: "part_created", title: "p1" }, "r1", 1));
    recordAnnouncement(ev({ id: "a2", kind: "agent_spawned", title: "sub", ref: { run_id: "r2" } }, "r1", 2));
    recordAnnouncement(ev({ id: "a1", kind: "part_created", title: "p1" }, "r1", 1));  // replay
    const list = announcementsForRun("r1");
    expect(list.map((a) => a.id)).toEqual(["a1", "a2"]);
  });
  it("forgets a run's announcements on run_deleted", () => {
    recordAnnouncement(ev({ id: "a1", kind: "part_created", title: "p1" }, "r1", 1));
    recordAnnouncement({ type: "run_deleted", run_id: "r1", data: { run_id: "r1" }, ts: 3 });
    expect(announcementsForRun("r1")).toEqual([]);
  });
});
