import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MODES_OFF } from "../viewer/modes/state";
import { closePart, openPart, openPartId } from "./focus";
import { latestAnimation, recordAnimation, resetAssemblyState } from "./state";
import { checkIssues, ViewerPill } from "./ViewerPill";

afterEach(() => { cleanup(); resetAssemblyState(); closePart(); });

describe("the viewer pill (Plan 9 D12 / D17)", () => {
  it("names clashes first, then joints that can't apply", () => {
    const got = checkIssues([{ a: "base", b: "lid", volume_mm3: 180 }], [["base", "lid"]]);
    expect(got).toEqual([
      { tone: "fail", text: "base and lid overlap by 180 mm³" },
      { tone: "warn", text: "base and lid are grounded and joined: one joint can't apply" },
    ]);
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

  it("opening a part shows it alone; closing goes back to the assembly", () => {
    openPart("arm");
    expect(openPartId.value).toBe("arm");
    closePart();
    expect(openPartId.value).toBeNull();
  });
});
