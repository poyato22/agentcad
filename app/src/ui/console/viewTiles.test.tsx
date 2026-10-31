// FW-app M4: every filmstrip tile in the View stream shows a picture of its card — a part card's
// part render, a concept sketch's first image — and a card with no picture shows its kind's mark,
// never the empty ◫ box. Real core announcements (concept_events.jsonl, the G1 cards).
import { cleanup, render, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { Step } from "../../api/types";
import { bindClient, resetStore } from "../../state/store";
import { announcementOf } from "./announceModel";
import type { ThreadItem } from "./threadModel";
import type { DockEntry } from "./treeStreams";
import { ViewStream } from "./ViewStream";

const FX = resolve(__dirname, "../../../../core/tests/fixtures/ui");
const events = readFileSync(resolve(FX, "concept/concept_events.jsonl"), "utf8").trim().split("\n")
  .map((l) => JSON.parse(l) as { type: string; data: Record<string, unknown> }).filter((e) => e.type === "agent_announce");
const card = (kind: string) => events.find((e) => e.data.kind === kind)?.data as Record<string, unknown>;

function entry(data: Record<string, unknown>, id: number): DockEntry {
  const step = { id, run_id: "r1", kind: "decision", payload: { agent_announce: data }, ts: id } as unknown as Step;
  const a = announcementOf(step);
  if (!a) throw new Error("not an announcement");
  const item: ThreadItem = { kind: "announce", key: `s:${id}`, announcement: a, ts: id, stepIds: [id] };
  return { key: `r1/s:${id}`, item, runId: "r1", agent: "designer", order: id };
}

afterEach(() => { cleanup(); resetStore(); });

describe("View stream filmstrip tiles (FW-app M4)", () => {
  it("a part card's tile is its part render, a sketch's its image, a pitch its mark", async () => {
    const partThumb = vi.fn().mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    const renderImage = vi.fn().mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    bindClient({ partThumb, renderImage, render: renderImage, getRun: vi.fn().mockRejectedValue(new Error("no")) } as unknown as CoreClient);
    const created = { run_id: "r1", kind: "part_created", title: "Created part lid", detail: null, ref: { part_id: "pi_lid" } };
    const list = [entry(created, 1), entry(card("concept_pitch"), 2), entry(card("concept_sketch"), 3), entry({ ...created, title: "Created part fan", ref: { part_id: "pi_fan" } }, 4)];
    const { container } = render(<ViewStream entries={list} />);
    const tiles = Array.from(container.querySelectorAll<HTMLElement>("[data-filmstrip] [data-tile]"));
    expect(tiles.map((t) => t.dataset.tileKind)).toEqual(["part_created", "concept_pitch", "concept_sketch", "part_created"]);
    // none is the empty box
    for (const t of tiles) expect(t.textContent).not.toContain("◫");
    expect(tiles[1].textContent).toContain("◇");                                   // a pitch: its mark
    await waitFor(() => expect(partThumb).toHaveBeenCalledWith("pi_lid", 128));     // its part, small
  });

  it("a sketch announcement carries its first image id", () => {
    const step = { id: 9, run_id: "r1", kind: "decision", payload: { agent_announce: card("concept_sketch") }, ts: 9 } as unknown as Step;
    const ids = (card("concept_sketch").ref as { image_ids: string[] }).image_ids;
    expect(announcementOf(step)?.ref.image_id).toBe(ids[0]);
  });
});
