// Plan 7 U2b (R13): the Part | Assembly switch is the first thing in the viewer's top-left corner,
// a segmented control with plain labels, in the same spot in both modes.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStore } from "../../state/store";
import type { DesignViewerHandle } from "../viewer/scene";
import { Viewer } from "../viewer/Viewer";
import { designMode, ModeSwitch, setDesignMode } from "./mode";

const handle = vi.hoisted(() => ({
  setPart: vi.fn(), setParts: vi.fn(), setMode: vi.fn(), setHighlight: vi.fn(), fit: vi.fn(), resize: vi.fn(), dispose: vi.fn(),
  pick: vi.fn(), toScreen: vi.fn((): [number, number] => [0, 0]), setMarker: vi.fn(),
}));
vi.mock("../viewer/scene", async (orig) => ({ ...(await orig<typeof import("../viewer/scene")>()), createViewer: (): DesignViewerHandle => handle }));

beforeEach(() => { resetStore(); setDesignMode("part"); });
afterEach(() => { cleanup(); setDesignMode("part"); });

describe("ModeSwitch", () => {
  it("is a segmented control labelled Part and Assembly", () => {
    render(<ModeSwitch />);
    const group = screen.getByRole("radiogroup", { name: "Part or assembly" });
    expect(group.classList.contains("seg")).toBe(true);
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => r.textContent)).toEqual(["Part", "Assembly"]);
    expect(radios[0].getAttribute("aria-checked")).toBe("true");
    expect(radios[0].getAttribute("title")).toBe("One part at a time (⌥⌘A)");
    expect(radios[1].getAttribute("title")).toBe("The whole build, put together (⌥⌘A)");
  });

  it("click and the arrow keys switch the mode", () => {
    render(<ModeSwitch />);
    fireEvent.click(screen.getByRole("radio", { name: /Assembly/ }));
    expect(designMode.value).toBe("assembly");
    fireEvent.keyDown(screen.getByRole("radio", { name: /Assembly/ }), { key: "ArrowLeft" });
    expect(designMode.value).toBe("part");
  });

  it("is the only control in the Design viewer's corner; the rest is in the View menu", () => {
    const { container } = render(<Viewer />);
    const corner = container.querySelector(".vp-corner");
    expect(corner?.children).toHaveLength(1);
    expect(corner?.firstElementChild?.getAttribute("data-testid")).toBe("mode-switch");
    expect(container.querySelector(".vp-tabs")).toBeNull();
    // the sheet's buttons: Part, Assembly and View — nothing else until a menu opens
    expect(Array.from(container.querySelectorAll("button")).map((b) => b.textContent?.replace("▾", "").trim()))
      .toEqual(["Part", "Assembly", "View"]);
  });
});
