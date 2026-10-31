import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";
import { printMode, PrintModeSwitch, setPrintMode } from "./mode";

afterEach(() => { cleanup(); setPrintMode("print"); localStorage.removeItem("mises.print.mode"); });

describe("Print modes (D21: drawings live inside Print)", () => {
  it("the switch offers 3D print and Drawing, one chosen, and remembers the choice", () => {
    render(<PrintModeSwitch />);
    const group = screen.getByRole("radiogroup", { name: "Print mode" });
    expect([...group.querySelectorAll("[role=radio]")].map((b) => b.textContent)).toEqual(["3D print", "Drawing"]);
    expect(screen.getByRole("radio", { name: "3D print" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("radio", { name: "Drawing" }));
    expect(printMode.value).toBe("drawing");
    expect(localStorage.getItem("mises.print.mode")).toBe("drawing");
  });

  it("arrow keys flip the mode", () => {
    render(<PrintModeSwitch />);
    fireEvent.keyDown(screen.getByRole("radio", { name: "3D print" }), { key: "ArrowRight" });
    expect(printMode.value).toBe("drawing");
  });
});
