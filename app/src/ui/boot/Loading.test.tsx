import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { conn, resetStore, shellStatus } from "../../state/store";
import { isFatal, Loading, statusLine } from "./Loading";

afterEach(() => { cleanup(); resetStore(); vi.useRealTimers(); });

describe("Loading status line", () => {
  it("advances through the boot phases while connecting", () => {
    const starting = { state: "starting" } as const;
    expect(statusLine(starting, "connecting", 0)).toBe("Starting core");
    expect(statusLine(starting, "connecting", 1500)).toBe("Loading models");
    expect(statusLine(starting, "connecting", 3000)).toBe("Checking tools");
  });
  it("reports Ready once the stream is open", () => {
    expect(statusLine({ state: "ready", info: {} as never }, "open", 9999)).toBe("Ready");
  });
  it("flags a crashed/stopped shell as fatal", () => {
    expect(isFatal({ state: "crashed", code: 1, stderrTail: "boom", restarts: 0 })).toBe(true);
    expect(isFatal({ state: "stopped" })).toBe(true);
    expect(isFatal({ state: "starting" })).toBe(false);
  });
});

describe("Loading screen", () => {
  beforeEach(() => { shellStatus.value = { state: "starting" }; conn.value = "connecting"; });

  it("shows the cube mark and a status line while starting", () => {
    render(<Loading />);
    expect(screen.getByRole("img", { name: "AgentCAD" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Starting core");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("times out into an error with Retry and an expandable log", async () => {
    vi.useFakeTimers();
    const onRetry = vi.fn();
    render(<Loading timeoutMs={1000} onRetry={onRetry} />);
    await vi.advanceTimersByTimeAsync(1300);
    const retry = screen.getByRole("button", { name: "Retry" });
    expect(retry).toBeTruthy();
    expect(screen.getByText(/taking longer than expected/i)).toBeTruthy();
    // the log is behind "Show log"
    expect(screen.queryByLabelText("Core log")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show log" }));
    expect(screen.getByLabelText("Core log")).toBeTruthy();
    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("Retry restarts the timeout window and shows the starting status again", async () => {
    vi.useFakeTimers();
    render(<Loading timeoutMs={1000} onRetry={vi.fn()} />);
    await vi.advanceTimersByTimeAsync(1300);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    // past the old timeout (measured from mount), but within the new window
    await vi.advanceTimersByTimeAsync(600);
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("Starting core");
    // and the new window still times out
    await vi.advanceTimersByTimeAsync(800);
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("shows the crash output in the log when the shell crashed", () => {
    vi.useFakeTimers();
    shellStatus.value = { state: "crashed", code: 2, stderrTail: "could not find `uv`", restarts: 1 };
    render(<Loading timeoutMs={60_000} />);
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show log" }));
    expect(screen.getByLabelText("Core log").textContent).toContain("could not find `uv`");
  });
});
