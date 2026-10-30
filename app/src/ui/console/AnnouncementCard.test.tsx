import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";
import type { Announcement } from "../../api/types";
import { resetStore, selectedPartId, selectedRunId } from "../../state/store";
import { AnnouncementCard } from "./AnnouncementCard";

afterEach(() => { cleanup(); resetStore(); });

const ann = (over: Partial<Announcement>): Announcement =>
  ({ id: "a1", run_id: "r1", kind: "part_created", title: "quad_arm", detail: null, ref: {}, ts: 1, ...over });

describe("AnnouncementCard", () => {
  it("names what happened and renders the title as text", () => {
    render(<AnnouncementCard announcement={ann({ kind: "part_created", title: "<b>quad_arm</b>", detail: "3 mm plate", ref: { part_id: "quad_arm" } })} />);
    const card = document.querySelector("[data-announce]") as HTMLElement;
    expect(card.getAttribute("data-announce")).toBe("part_created");
    expect(card.textContent).toContain("Created a part");
    expect(card.querySelector("b")).toBeNull();
    expect(card.textContent).toContain("<b>quad_arm</b>");
    expect(card.textContent).toContain("3 mm plate");
  });

  it("jumps to the referenced part", () => {
    render(<AnnouncementCard announcement={ann({ kind: "assembly_added", title: "gear", ref: { part_id: "gear" } })} />);
    fireEvent.click(screen.getByRole("button", { name: "Show part" }));
    expect(selectedPartId.value).toBe("gear");
  });

  it("a spawned-agent card opens that agent", () => {
    render(<AnnouncementCard announcement={ann({ kind: "agent_spawned", title: "design the gripper", ref: { run_id: "sub1" } })} />);
    fireEvent.click(screen.getByRole("button", { name: "Open agent" }));
    expect(selectedRunId.value).toBe("sub1");
  });

  it("an unknown kind still shows, with a generic label and no jump", () => {
    render(<AnnouncementCard announcement={ann({ kind: "something_new", title: "hi", ref: {} })} />);
    const card = document.querySelector("[data-announce]") as HTMLElement;
    expect(card.textContent).toContain("Update");
    expect(screen.queryByRole("button")).toBeNull();
  });
});
