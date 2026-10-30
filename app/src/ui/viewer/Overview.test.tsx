import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { PartMetrics, PartSummary } from "../../api/types";
import { closeOverview, overviewOpen } from "../../state/home";
import { bindClient, parts, resetStore, selectedPartId } from "../../state/store";
import { Overview } from "./Overview";

const metrics = (mass: number): PartMetrics => ({
  valid: true, volume_mm3: 1000, area_mm2: 600, mass_g: mass,
  bbox: { min: [0, 0, 0], max: [10, 10, 10], size: [10, 10, 10] },
  center_of_mass: [5, 5, 5], faces: 6, edges: 12, holes: [], solids: 1,
});
function part(p: Partial<PartSummary>): PartSummary {
  return { part_id: "p", vars: {}, metrics: metrics(10), mtime: 1, kind: "part", version: 1, ...p };
}

const PARTS = [
  part({ part_id: "base", label: "Base plate", metrics: metrics(42.5) }),
  part({ part_id: "arm", label: "Upper arm", metrics: metrics(8.1) }),
  part({ part_id: "grp", kind: "group", metrics: null }),      // no mesh -> excluded
];

const client = {
  partThumb: vi.fn().mockRejectedValue(new Error("no preview")),
  getProfile: vi.fn().mockResolvedValue({ default_material: "Aluminum 6061" }),
};

beforeEach(() => {
  resetStore();
  parts.value = PARTS;
  client.partThumb.mockClear();
  client.getProfile.mockClear();
  bindClient(client as unknown as CoreClient);
});
afterEach(() => { cleanup(); closeOverview(); parts.value = []; });

describe("Overview (all parts grid)", () => {
  it("lists each built part with its name and mass, skipping groups", () => {
    render(<Overview />);
    expect(screen.getByText("Base plate")).toBeTruthy();
    expect(screen.getByText("Upper arm")).toBeTruthy();
    expect(screen.getByText(/42\.50 g/)).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "All parts" }).textContent).toContain("· 2");
  });

  it("shows the build material once the profile loads", async () => {
    render(<Overview />);
    await waitFor(() => expect(screen.getAllByText(/Aluminum 6061/).length).toBeGreaterThan(0));
  });

  it("focuses a part and closes when its card is clicked", () => {
    render(<Overview />);
    fireEvent.click(screen.getByTitle("Focus Base plate"));
    expect(selectedPartId.value).toBe("base");
  });

  it("closes on Escape", () => {
    overviewOpen.value = true;
    render(<Overview />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(overviewOpen.value).toBe(false);   // App unmounts the overlay off this signal
  });

  it("falls back to a placeholder when a part has no preview", async () => {
    render(<Overview />);
    await waitFor(() => {
      const card = screen.getByTitle("Focus Base plate");
      expect(within(card).getByRole("img", { name: /Base plate preview/ })).toBeTruthy();
    });
  });
});
