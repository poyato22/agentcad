import { describe, expect, it } from "vitest";
import type { Announcement } from "../../api/types";
import type { ThreadItem } from "./threadModel";
import { weaveAnnouncements } from "./announceLines";
import type { ThreadRow } from "./userEditLines";

const item = (key: string, ts: number): ThreadRow =>
  ({ kind: "item", item: { kind: "agent", key, text: key, ts, live: false, stepIds: [] } as ThreadItem });
const ann = (id: string, ts: number): Announcement =>
  ({ id, run_id: "r1", kind: "part_created", title: id, detail: null, ref: {}, ts });

describe("weaveAnnouncements", () => {
  it("places each announcement before the first row that came after it", () => {
    const rows = [item("m1", 10), item("m2", 30)];
    const out = weaveAnnouncements(rows, [ann("a1", 20)]);
    expect(out.map((r) => (r.kind === "announce" ? `A:${r.announcement.id}` : `I:${(r as { item: ThreadItem }).item.key}`)))
      .toEqual(["I:m1", "A:a1", "I:m2"]);
  });
  it("appends announcements that come after every row", () => {
    const out = weaveAnnouncements([item("m1", 10)], [ann("a1", 50)]);
    expect(out[out.length - 1]).toMatchObject({ kind: "announce" });
  });
  it("is a no-op with no announcements", () => {
    expect(weaveAnnouncements([item("m1", 10)], [])).toHaveLength(1);
  });
});
