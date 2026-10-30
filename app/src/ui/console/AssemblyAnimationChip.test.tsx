import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { recordAssemblyAnimation, resetAssemblyAnimation, setAnimationPlayer } from "../../state/assemblyAnimation";
import { AssemblyAnimationChip } from "./AssemblyAnimationChip";

const ev = (data: Record<string, unknown>) => recordAssemblyAnimation({ type: "assembly_animate", run_id: "r1", data, ts: 1 });
const player = (playing: boolean) => ({ play: vi.fn(() => playing), watch: vi.fn(async () => true), stop: vi.fn() });

afterEach(() => { cleanup(); resetAssemblyAnimation(); setAnimationPlayer(null); });

describe("AssemblyAnimationChip", () => {
  it("renders nothing when idle", () => {
    const { container } = render(<AssemblyAnimationChip />);
    expect(container.querySelector("[data-animating]")).toBeNull();
  });

  it("shows while an agent animates; Stop clears it and puts the joints back", () => {
    const p = player(true);
    setAnimationPlayer(p);
    ev({ joint_id: "j1", from: 0, to: 90, duration_s: 3, loop: true, loops: 3 });
    render(<AssemblyAnimationChip />);
    expect(screen.getByText("Agent is animating a joint · 3×…")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(p.stop).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Agent is animating/)).toBeNull();
  });

  it("names a sequence by its steps", () => {
    setAnimationPlayer(player(true));
    ev({ sequence: [{ joint_id: "a", from: 0, to: 4, duration_s: 1 }, { joint_id: "b", from: 0, to: 9, duration_s: 1 }] });
    render(<AssemblyAnimationChip />);
    expect(screen.getByText("Agent is animating 2 joints in sequence…")).toBeTruthy();
  });

  it("never claims a motion nobody sees: off screen it offers Watch", async () => {
    const p = player(false);
    setAnimationPlayer(p);
    ev({ joint_id: "j1", from: 0, to: 90 });
    render(<AssemblyAnimationChip />);
    expect(screen.getByText("The agent wants to show you a joint moving.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Watch" }));
    expect(p.watch).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.getByText(/Agent is animating a joint/)).toBeTruthy());
  });
});
