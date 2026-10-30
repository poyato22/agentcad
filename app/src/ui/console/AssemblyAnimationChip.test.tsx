import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { recordAssemblyAnimation, resetAssemblyAnimation, setAnimationPlayer } from "../../state/assemblyAnimation";
import { AssemblyAnimationChip } from "./AssemblyAnimationChip";

const ev = (data: Record<string, unknown>) => recordAssemblyAnimation({ type: "assembly_animate", run_id: "r1", data, ts: 1 });

afterEach(() => { cleanup(); resetAssemblyAnimation(); setAnimationPlayer(null); });

describe("AssemblyAnimationChip", () => {
  it("renders nothing when idle", () => {
    const { container } = render(<AssemblyAnimationChip />);
    expect(container.querySelector("[data-animating]")).toBeNull();
  });

  it("shows while an agent animates and Stop clears it", () => {
    const stop = vi.fn();
    setAnimationPlayer({ play: vi.fn(), stop });
    ev({ joint_id: "j1", from: 0, to: 90 });
    render(<AssemblyAnimationChip />);
    expect(screen.getByText(/Agent is animating a joint/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(stop).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Agent is animating/)).toBeNull();
  });

  it("names a sequence as text", () => {
    ev({ sequence: "walk cycle" });
    render(<AssemblyAnimationChip />);
    expect(screen.getByText(/Agent is animating walk cycle/)).toBeTruthy();
  });
});
