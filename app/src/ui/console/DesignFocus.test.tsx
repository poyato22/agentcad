// Plan 9 G-B: the "Design focus" chip in the Conversation header.
import { act, cleanup, fireEvent, render } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { AgentRun } from "../../api/types";
import { bindClient, resetStore } from "../../state/store";
import { chosenFocus, DesignFocus, runDesignClass } from "./DesignFocus";
import { run } from "./testSteps";

const withClass = (cls: string | null, source = "classifier"): AgentRun =>
  ({ ...run("r1", { role: "lead", project_id: "b1" }), design_class: cls ? { cls, source, score: 0.8 } : null }) as AgentRun;

function bind(answer: unknown = { design_class: { cls: "engineering", source: "user" } }) {
  const c = { setDesignClass: vi.fn().mockResolvedValue(answer) };
  bindClient(c as unknown as CoreClient);
  return c;
}

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

afterEach(() => { cleanup(); resetStore(); chosenFocus.value = {}; });

describe("design focus chip", () => {
  it("reads the run's class leniently", () => {
    expect(runDesignClass(withClass("design"))).toEqual({ cls: "design", byUser: false });
    expect(runDesignClass(withClass("mixed", "user"))).toEqual({ cls: "mixed", byUser: true });
    expect(runDesignClass(withClass(null))).toBeNull();
    expect(runDesignClass(withClass("weird"))).toBeNull();
    expect(runDesignClass(null)).toBeNull();
  });

  it("shows nothing until the core has classified the build", () => {
    bind();
    const { container } = render(<DesignFocus run={withClass(null)} buildId="b1" />);
    expect(container.querySelector("[data-design-focus]")).toBeNull();
  });

  it("shows the three classes with the classifier's one pressed", () => {
    bind();
    const { container } = render(<DesignFocus run={withClass("design")} buildId="b1" />);
    const chip = container.querySelector("[data-design-focus]") as HTMLElement;
    expect(chip.getAttribute("aria-label")).toBe("Design focus");
    expect([...chip.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Eng", "Mixed", "Design"]);
    expect([...chip.querySelectorAll("button")].map((b) => b.getAttribute("aria-label"))).toEqual(["Engineering", "Mixed", "Design"]);
    expect(chip.querySelector("[data-focus=design]")?.getAttribute("aria-pressed")).toBe("true");
    expect(chip.querySelector("[data-focus-by-user]")).toBeNull();
  });

  it("sets the user's class through the PATCH and shows it as theirs", async () => {
    const c = bind();
    const { container } = render(<DesignFocus run={withClass("design")} buildId="b1" />);
    fireEvent.click(container.querySelector("[data-focus=engineering]") as HTMLElement);
    await flush();
    expect(c.setDesignClass).toHaveBeenCalledWith("b1", "engineering");
    expect(container.querySelector("[data-design-focus]")?.getAttribute("data-design-focus")).toBe("engineering");
    expect(container.querySelector("[data-focus-by-user]")).toBeTruthy();
  });

  it("clicking the user's own active class hands it back to the classifier", async () => {
    const c = bind({ design_class: { cls: "design", source: "classifier" } });
    const { container } = render(<DesignFocus run={withClass("mixed", "user")} buildId="b1" />);
    fireEvent.click(container.querySelector("[data-focus=mixed]") as HTMLElement);
    await flush();
    expect(c.setDesignClass).toHaveBeenCalledWith("b1", "auto");
    // re-review 2 m7: the classifier's answer shows at once, never the stale user-set class
    // from the run payload (which still says source "user" until the runs refetch)
    expect(container.querySelector("[data-design-focus]")?.getAttribute("data-design-focus")).toBe("design");
    expect(container.querySelector("[data-focus-by-user]")).toBeNull();
  });

  it("drops its local answer once the run payload changes", async () => {
    bind({ design_class: { cls: "engineering", source: "user" } });
    const { container, rerender } = render(<DesignFocus run={withClass("design")} buildId="b1" />);
    fireEvent.click(container.querySelector("[data-focus=engineering]") as HTMLElement);
    await flush();
    expect(container.querySelector("[data-design-focus]")?.getAttribute("data-design-focus")).toBe("engineering");
    // the runs refetch: a later follow-up made it mixed (classifier): the payload wins now
    rerender(<DesignFocus run={withClass("mixed")} buildId="b1" />);
    expect(container.querySelector("[data-design-focus]")?.getAttribute("data-design-focus")).toBe("mixed");
  });

  it("keeps the current class when the core refuses", async () => {
    const c = { setDesignClass: vi.fn().mockRejectedValue(new Error("offline")) };
    bindClient(c as unknown as CoreClient);
    const { container } = render(<DesignFocus run={withClass("design")} buildId="b1" />);
    fireEvent.click(container.querySelector("[data-focus=mixed]") as HTMLElement);
    await flush();
    expect(container.querySelector("[data-design-focus]")?.getAttribute("data-design-focus")).toBe("design");
  });
});
