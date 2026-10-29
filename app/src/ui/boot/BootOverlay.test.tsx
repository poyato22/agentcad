import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { conn, resetStore, shellStatus } from "../../state/store";
import { BootOverlay } from "./BootOverlay";
import { replayIntro } from "./boot";

// Stub the heavy children so the overlay's state machine is what's under test.
vi.mock("./Intro", () => ({
  Intro: ({ onDone }: { onDone: () => void }) => <button type="button" data-boot="intro" onClick={onDone}>intro done</button>,
}));
vi.mock("../system/SystemCheck", () => ({
  SystemCheck: ({ onDone }: { onDone?: () => void }) => <button type="button" data-boot="systemcheck" onClick={() => onDone?.()}>continue</button>,
}));

beforeEach(() => {
  try { localStorage.clear(); } catch { /* ignore */ }
  shellStatus.value = { state: "starting" };
  conn.value = "connecting";
});
afterEach(() => { cleanup(); resetStore(); });

describe("BootOverlay", () => {
  it("renders nothing when disabled", () => {
    const { container } = render(<BootOverlay enabled={false} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the loading screen while connecting, hidden on a crash", async () => {
    const { container } = render(<BootOverlay enabled />);
    expect(container.querySelector("[data-boot=loading]")).not.toBeNull();
    shellStatus.value = { state: "crashed", code: 1, stderrTail: "x", restarts: 0 };
    await waitFor(() => expect(container.querySelector("[data-boot=loading]")).toBeNull());
  });

  it("first launch: loading → intro → system check → ready", async () => {
    const { container } = render(<BootOverlay enabled />);
    conn.value = "open";
    fireEvent.click(await screen.findByText("intro done"));
    fireEvent.click(await screen.findByText("continue"));
    await waitFor(() => expect(container.firstChild).toBeNull()); // ready: nothing over the shell
  });

  it("skips straight to ready when the intro and check were already seen", async () => {
    localStorage.setItem("mises.intro.seen", "1");
    localStorage.setItem("mises.systemcheck.seen", "1");
    const { container } = render(<BootOverlay enabled />);
    conn.value = "open";
    await waitFor(() => expect(container.firstChild).toBeNull());
  });

  it("Replay intro plays the intro again without the system check", async () => {
    localStorage.setItem("mises.intro.seen", "1");
    localStorage.setItem("mises.systemcheck.seen", "1");
    const { container } = render(<BootOverlay enabled />);
    conn.value = "open";
    await waitFor(() => expect(container.firstChild).toBeNull());
    replayIntro();
    fireEvent.click(await screen.findByText("intro done"));
    await waitFor(() => expect(container.firstChild).toBeNull()); // back to ready, no system check
  });
});
