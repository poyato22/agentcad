// Plan 7 U2b fix round 1 (I-2): the viewer's display controls live in one keyboard-reachable
// "View" menu at the sheet's top right; only Part | Assembly stays out on the sheet.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ViewerTools, type ViewerToolsProps } from "./ViewerTools";

afterEach(() => cleanup());

function setup(over: Partial<ViewerToolsProps> = {}) {
  const props: ViewerToolsProps = {
    mode: "shaded", onMode: vi.fn(), scope: "part", onScope: vi.fn(), canEdit: true, onEdit: vi.fn(), code: false, onCode: vi.fn(), ...over,
  };
  render(<ViewerTools {...props} />);
  return props;
}
const viewBtn = () => screen.getByRole("button", { name: "View options" });
const open = () => { fireEvent.click(viewBtn()); return screen.getByRole("menu", { name: "View" }); };

describe("ViewerTools (View menu)", () => {
  it("is one closed button until opened; no stray buttons on the sheet", () => {
    setup();
    expect(viewBtn().textContent).toContain("View");
    expect(viewBtn().getAttribute("aria-haspopup")).toBe("menu");
    expect(viewBtn().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByText(/measure|section/i)).toBeNull();
  });

  it("lists display, scope, features and code, checked by state", () => {
    setup({ mode: "wire", scope: "build" });
    const menu = open();
    const radios = Array.from(menu.querySelectorAll("[role=menuitemradio]")).map((e) => [e.querySelector(".view-label")?.textContent, e.getAttribute("aria-checked")]);
    expect(radios).toEqual([["Shaded", "false"], ["Wireframe", "true"], ["This part", "false"], ["Whole build", "true"]]);
    expect(screen.getByRole("menuitem", { name: /Add a feature/ })).toBeTruthy();
    expect(screen.getByRole("menuitemcheckbox", { name: /Part script/ }).getAttribute("aria-checked")).toBe("false");
  });

  it("choosing an item calls back and closes, focus back on View", () => {
    const p = setup();
    open();
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Wireframe" }));
    expect(p.onMode).toHaveBeenCalledWith("wire");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(viewBtn());
    open();
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Whole build" }));
    expect(p.onScope).toHaveBeenCalledWith("build");
    open();
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /Part script/ }));
    expect(p.onCode).toHaveBeenCalledWith(true);
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: /Add a feature/ }));
    expect(p.onEdit).toHaveBeenCalledTimes(1);
  });

  it("keyboard: arrows move, Enter picks, Escape closes and returns focus", () => {
    const p = setup();
    fireEvent.keyDown(viewBtn(), { key: "ArrowDown" });
    const items = Array.from(screen.getByRole("menu").querySelectorAll<HTMLElement>("[role^=menuitem]"));
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(items[1], { key: "ArrowUp" });
    fireEvent.keyDown(items[0], { key: "ArrowUp" });          // wraps to the last
    expect(document.activeElement).toBe(items[items.length - 1]);
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Home" });
    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    fireEvent.keyDown(items[1], { key: "Enter" });
    expect(p.onMode).toHaveBeenCalledWith("wire");
    fireEvent.keyDown(viewBtn(), { key: "ArrowDown" });
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(viewBtn());
  });

  it("features is disabled when the part can't be edited", () => {
    const p = setup({ canEdit: false });
    open();
    const f = screen.getByRole("menuitem", { name: /Add a feature/ });
    expect(f.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(f);
    expect(p.onEdit).not.toHaveBeenCalled();
  });

  it("has a slot for tools other tasks add (U2a measure, G4 x-ray / section)", () => {
    const measure = vi.fn();
    setup({ extra: [{ id: "measure", label: "Measure", hint: "M", checked: false, onSelect: measure }] });
    open();
    const item = screen.getByRole("menuitemcheckbox", { name: /Measure/ });
    fireEvent.click(item);
    expect(measure).toHaveBeenCalledTimes(1);
  });
});
