import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { stepSchema } from "../../api/schemas";
import { ANNOUNCE_KINDS, type Announcement, type Step } from "../../api/types";
import { bindClient, resetStore, selectedPartId, selectedRunId } from "../../state/store";
import { activePage, setPage } from "../pages/router";
import { announcementOf } from "./announceModel";
import { jumpFor, PENDING_JUMPS, registerAnnounceJump } from "./announceJumps";
import { ANNOUNCE_META, AnnouncementCard, PART_RENDER_PX } from "./AnnouncementCard";

afterEach(() => { cleanup(); resetStore(); setPage("design"); });

const ann = (over: Partial<Announcement>): Announcement =>
  ({ id: "a1", run_id: "r1", kind: "part_created", title: "Created part quad_arm", detail: null, ref: {}, ts: 1, ...over });

/** The real U3c lead's stored steps (core scripts/dump_agents_fixtures.py). */
const LEAD: Step[] = (JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/plan7/lead_steps.json"), "utf8")) as unknown[]).map((s) => stepSchema.parse(s));

describe("AnnouncementCard", () => {
  it("rebuilds the real core's stored announcement: Started an agent, opening the sub-agent", () => {
    const s = LEAD.find((x) => "agent_announce" in x.payload) as Step;
    const a = announcementOf(s);
    expect(a?.kind).toBe("agent_spawned");
    expect(a?.actor).toBe(s.run_id);
    render(<AnnouncementCard announcement={a as Announcement} />);
    expect(screen.getByText("Started an agent")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open agent" }));
    expect(selectedRunId.value).toBe(a?.ref.run_id);
  });

  it("names what happened and renders the title as text", () => {
    render(<AnnouncementCard announcement={ann({ title: "<b>quad_arm</b>", detail: "3 mm plate", ref: { part_id: "quad_arm" } })} />);
    const card = document.querySelector("[data-announce]") as HTMLElement;
    expect(card.textContent).toContain("Created a part");
    expect(card.querySelector("b")).toBeNull();
    expect(card.textContent).toContain("<b>quad_arm</b>");
    expect(card.textContent).toContain("3 mm plate");
  });

  it("D10: Show in assembly goes to the Design page with the created part selected", () => {
    setPage("studies");
    render(<AnnouncementCard announcement={ann({ ref: { part_id: "gear" } })} />);
    fireEvent.click(screen.getByRole("button", { name: "Show in assembly" }));
    expect(selectedPartId.value).toBe("gear");
    expect(activePage.value).toBe("design");
  });

  it("D10 / D13: a created part's card shows its render, large, from the part's thumbnail route", async () => {
    const partThumb = vi.fn(async () => new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" }));
    bindClient({ partThumb } as unknown as CoreClient);
    const made = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:gear");
    setPage("studies");
    render(<AnnouncementCard announcement={ann({ title: "Created part gear", ref: { part_id: "gear" } })} />);
    expect(partThumb).toHaveBeenCalledWith("gear", PART_RENDER_PX);
    expect(PART_RENDER_PX).toBeGreaterThanOrEqual(640);   // sharp at the dock's widest
    await waitFor(() => expect(document.querySelector("img.cv-announce-img")?.getAttribute("src")).toBe("blob:gear"));
    expect(screen.getByRole("img", { name: "Render of Created part gear" }).tagName).toBe("IMG");
    // the picture is the jump too
    fireEvent.click(screen.getByRole("button", { name: "Show Created part gear in the assembly" }));
    expect(selectedPartId.value).toBe("gear");
    expect(activePage.value).toBe("design");
    made.mockRestore();
  });

  it("other kinds show no part render", () => {
    render(<AnnouncementCard announcement={ann({ kind: "bom_added", ref: { part_id: "gear" } })} />);
    expect(document.querySelector("[data-part-render]")).toBeNull();
  });

  it("a surface's own jump wins (the assembly view registers assembly_added)", () => {
    let went = "";
    const off = registerAnnounceJump("assembly_added", (a) => ({ label: "Show in assembly", go: () => { went = a.ref.instance_id ?? ""; } }));
    render(<AnnouncementCard announcement={ann({ kind: "assembly_added", ref: { part_id: "gear", instance_id: "i7" } })} />);
    fireEvent.click(screen.getByRole("button", { name: "Show in assembly" }));
    expect(went).toBe("i7");
    off();
  });

  it("an unknown kind still shows, with a generic label and no jump", () => {
    render(<AnnouncementCard announcement={ann({ kind: "something_new", ref: {} })} />);
    expect((document.querySelector("[data-announce]") as HTMLElement).textContent).toContain("Update");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("I5: every kind has a label, and a jump or an explicit pending target (never a silent gap)", () => {
    for (const kind of ANNOUNCE_KINDS) {
      expect(ANNOUNCE_META[kind], kind).toBeTruthy();
      const bare = jumpFor({ kind, ref: {}, projectId: null, jointId: null });
      const viaRef = jumpFor({ kind, ref: { part_id: "p", run_id: "r" }, projectId: null, jointId: null });
      expect(bare !== null || viaRef !== null || PENDING_JUMPS[kind] !== undefined, kind).toBe(true);
      if (kind !== "agent_spawned" && kind !== "part_created" && kind !== "assembly_added") expect(PENDING_JUMPS[kind], kind).toBeTruthy();
    }
  });
});
