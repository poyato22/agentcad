import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";
import { RoutingChip } from "./RoutingChip";

afterEach(() => cleanup());

describe("RoutingChip", () => {
  it("shows the chosen agent and reason, with confidence in the title", () => {
    render(<RoutingChip routed={{ agent: "engineer", reason: "system build", confidence: 0.8 }} />);
    const chip = screen.getByText("engineer").closest("[data-routed]") as HTMLElement;
    expect(chip.getAttribute("data-routed")).toBe("engineer");
    expect(chip.textContent).toContain("→");
    expect(chip.textContent).toContain("engineer");
    expect(chip.textContent).toContain("system build");
    expect(chip.getAttribute("title")).toContain("80%");
  });

  it("renders agent and reason as text (never HTML) and clamps a bad confidence", () => {
    render(<RoutingChip routed={{ agent: "designer", reason: "<b>x</b>", confidence: 9 }} />);
    const chip = screen.getByText("designer").closest("[data-routed]") as HTMLElement;
    expect(chip.querySelector("b")).toBeNull();
    expect(chip.textContent).toContain("<b>x</b>");
    expect(chip.getAttribute("title")).toContain("100%");
  });
});
