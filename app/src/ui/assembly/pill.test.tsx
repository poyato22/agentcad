import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MODES_OFF } from "../viewer/modes/state";
import { closePart, openPart, openPartId } from "./focus";
import { latestAnimation, recordAnimation, resetAssemblyState } from "./state";
import { checkIssues, FOLD_ORDER, pillFold, ViewerPill } from "./ViewerPill";

afterEach(() => { cleanup(); resetAssemblyState(); closePart(); });

describe("the viewer pill (Plan 9 D12 / D17)", () => {
  it("names clashes first, then joints that can't apply", () => {
    const got = checkIssues([{ a: "base", b: "lid", volume_mm3: 180 }], [["base", "lid"]]);
    expect(got).toEqual([
      { tone: "fail", text: "base and lid overlap by 180 mm³" },
      { tone: "warn", text: "base and lid are grounded and joined: one joint can't apply" },
    ]);
  });

  it("a declared contact (press fit, bearing seat) is the design, not a clash (G2)", () => {
    const got = checkIssues([{ a: "base", b: "pin", volume_mm3: 4, intended: "press fit" }, { a: "base", b: "lid", volume_mm3: 9, intended: null }], []);
    expect(got).toEqual([{ tone: "fail", text: "base and lid overlap by 9 mm³" }]);
  });

  it("has Play, Section, X-ray, Explode and a chip only when the check found something", () => {
    const onPlay = vi.fn();
    const onModes = vi.fn();
    const { rerender } = render(<ViewerPill host={null} modesCtl={null} modes={MODES_OFF} onModes={onModes} animate={{ current: false }}
      issues={[]} onPlay={onPlay} canPlay={true} />);
    for (const id of ["pill-play", "pill-section", "pill-xray", "pill-explode"]) expect(screen.getByTestId(id)).toBeTruthy();
    expect(screen.queryByTestId("assembly-check-chip")).toBeNull();
    fireEvent.click(screen.getByTestId("pill-play"));
    expect(onPlay).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByTestId("pill-xray"));
    expect(onModes.mock.calls[0][0].xray.opacity).toBeLessThan(1);
    rerender(<ViewerPill host={null} modesCtl={null} modes={MODES_OFF} onModes={onModes} animate={{ current: false }}
      issues={checkIssues([{ a: "a", b: "b", volume_mm3: 2 }, { a: "a", b: "c", volume_mm3: 3 }], [])} onPlay={onPlay} canPlay={false} />);
    expect(screen.getByTestId("assembly-check-chip").textContent).toContain("2 clashes");
    expect((screen.getByTestId("pill-play") as HTMLButtonElement).disabled).toBe(true);
    // nothing to cut without parts on screen
    expect((screen.getByTestId("pill-section") as HTMLButtonElement).disabled).toBe(true);
  });

  it("Play plays the build's own latest animation, never another build's", () => {
    const spec = (pid: string | null) => ({ project_id: pid, steps: [{ joint_id: "j", from: 0, to: 1, duration_s: 1 }], loops: 1 });
    recordAnimation(spec("b1"));
    recordAnimation(spec("b2"));
    expect(latestAnimation("b1")?.project_id).toBe("b1");
    expect(latestAnimation("b3")).toBeNull();
  });

  it("folds Comment, Measure, Laid out, X-ray into More (in that order) only as far as the free area needs (P10 FWA)", () => {
    const items = FOLD_ORDER.map((key) => ({ key, w: 80 }));
    const fixed = 300;
    const all = fixed + 4 * 82 + 11;                    // every button inline, the tools' separator too
    expect(pillFold(all, fixed, items, 9)).toBe(0);
    expect(pillFold(all - 1, fixed, items, 9)).toBe(1); // Comment goes first (More takes its place)
    expect(pillFold(fixed + 2 * 82 + 66, fixed, items, 9, 64)).toBe(2);   // Measure too: no separator left
    expect(pillFold(fixed + 66, fixed, items, 9, 64)).toBe(4);
    expect(pillFold(100, fixed, items, 9)).toBe(4);     // nothing fits: all of them (then it wraps)
    expect(pillFold(fixed, fixed, [], 9)).toBe(0);
  });

  it("a narrow free area puts the folded buttons in More, whose button toggles its menu and runs them", () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    Object.defineProperty(host, "clientWidth", { value: 560, configurable: true });
    const rect = vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(() => ({ width: 80, height: 28, x: 0, y: 0, top: 0, left: 0, right: 80, bottom: 28, toJSON: () => ({}) }) as DOMRect);
    try {
      const onTool = vi.fn();
      render(<ViewerPill host={null} modesCtl={null} modes={MODES_OFF} onModes={vi.fn()} animate={{ current: false }}
        issues={[]} onPlay={vi.fn()} canPlay={true} tool="measure" onTool={onTool} />, { container: host });
      // 560 - 32 free: Play, Section, Explode stay; Comment and Measure (and maybe more) fold
      expect(screen.queryByTestId("pill-comment")).toBeNull();
      expect(screen.queryByTestId("pill-measure")).toBeNull();
      const more = screen.getByTestId("pill-overflow");
      expect(more.getAttribute("aria-pressed")).toBe("true");          // Measure is on, inside More
      fireEvent.click(more);
      expect(screen.getByTestId("pill-overflow-menu")).toBeTruthy();
      expect(screen.getByTestId("pill-measure-more").getAttribute("aria-checked")).toBe("true");
      fireEvent.mouseDown(more);                                        // E7: its own button closes it
      fireEvent.click(more);
      expect(screen.queryByTestId("pill-overflow-menu")).toBeNull();
      fireEvent.click(more);
      fireEvent.click(screen.getByTestId("pill-comment-more"));
      expect(onTool).toHaveBeenCalledWith("comment");
      expect(screen.queryByTestId("pill-overflow-menu")).toBeNull();
      for (const id of ["pill-play", "pill-section", "pill-explode"]) expect(screen.getByTestId(id)).toBeTruthy();
    } finally {
      rect.mockRestore();
      host.remove();
    }
  });

  it("opening a part shows it alone; closing goes back to the assembly", () => {
    openPart("arm");
    expect(openPartId.value).toBe("arm");
    closePart();
    expect(openPartId.value).toBeNull();
  });
});
