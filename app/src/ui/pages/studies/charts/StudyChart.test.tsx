import { render } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import type { StudyChart as Chart } from "../../../../api/studies";
import { StudyChart } from "./StudyChart";

const tiny: Chart = {
  id: "von_mises_histogram", kind: "histogram", title: "Stress distribution",
  x: { label: "von mises", unit: "MPa" }, y: { label: "nodes", unit: "" },
  edges: Array.from({ length: 11 }, (_, i) => i * 0.001), counts: [900, 40, 8, 2, 1, 0, 0, 0, 0, 1],
  markers: [{ label: "allowable", x: 35.7 }],
};

describe("StudyChart histogram at tiny stresses", () => {
  it("bars span the plot, the far allowable is named at the right edge, one node is visible", () => {
    const { container } = render(<StudyChart chart={tiny} width={400} height={200} />);
    const bars = [...container.querySelectorAll("path.sc-bar")];
    expect(bars).toHaveLength(10);
    // each of 10 bins gets ~1/10 of the 344px plot, not a sliver at the left edge
    const xs = bars.map((b) => Number(/^M([\d.]+)/.exec(b.getAttribute("d") ?? "")?.[1]));
    expect(xs[9] - xs[0]).toBeGreaterThan(250);
    const off = container.querySelector("[data-offscale=right]");
    expect(off?.textContent).toBe("allowable 35.7 →");
    expect(container.querySelector("line.sc-marker")).toBeNull();
    // the single-node bin (count 1 of 900) is at least 2px tall
    // height = baseline (the M y) − top (the first Q's control y)
    const h = (d: string) => Number(/^M[\d.]+,([\d.]+)/.exec(d)?.[1]) - Number(/Q[\d.]+,([\d.]+) /.exec(d)?.[1]);
    expect(h(bars[9].getAttribute("d") ?? "")).toBeCloseTo(2, 5);
    expect(h(bars[5].getAttribute("d") ?? "")).toBe(0);
  });
});
