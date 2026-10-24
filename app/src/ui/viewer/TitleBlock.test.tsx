import { render } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import type { PartSummary } from "../../api/types";
import { TitleBlock } from "./TitleBlock";

const part: PartSummary = {
  part_id: "mount_plate", vars: {}, mtime: 1,
  metrics: {
    valid: true, volume_mm3: 1, area_mm2: 1, mass_g: 12.4,
    bbox: { min: [0, 0, 0], max: [40, 20, 6], size: [40, 20, 6] },
    center_of_mass: [0, 0, 0], faces: 1, edges: 1, holes: [], solids: 1,
  },
};

describe("TitleBlock", () => {
  it("title_block_formats_mm_and_grams", () => {
    const { container } = render(<TitleBlock part={part} runLabel={null} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Part · mount plate");
    expect(text).toContain("40.00 × 20.00 × 6.00");
    expect(text).toContain("12.40 g");
    expect(text).toContain("you");
    expect(container.querySelectorAll("[data-cell]").length).toBe(4);
  });
  it("shows the run label", () => {
    const { container } = render(<TitleBlock part={part} runLabel="designer · stiffen upright" />);
    expect(container.textContent).toContain("designer · stiffen upright");
  });
  it("shows dashes without metrics and a red error", () => {
    const { container, getByText } = render(<TitleBlock part={{ ...part, metrics: null }} runLabel={null} error="bad header" />);
    expect(container.textContent).toContain("—");
    expect(getByText("GLB failed: bad header").className).toContain("tb-error");
  });
  it("empty state has no auto-numbered names", () => {
    const { container } = render(<TitleBlock part={null} runLabel={null} />);
    expect(container.textContent).not.toMatch(/\d/);
  });
});
