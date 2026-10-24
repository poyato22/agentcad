import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { actions, conn, resetStore } from "./state/store";
import { ApprovalCard } from "./ui/console/approvals/ApprovalCard";
import { QuestionCard } from "./ui/console/approvals/QuestionCard";
import type { Approval } from "./api/types";

// jsdom has no WebGL: the real viewer scene is replaced by an inert handle (Task 6 tests cover it).
vi.mock("./ui/viewer/scene", () => ({
  createViewer: () => ({ setPart: vi.fn(), setMode: vi.fn(), setHighlight: vi.fn(), fit: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}));

afterEach(() => { cleanup(); resetStore(); });

describe("App shell", () => {
  it("renders_every_real_pane_in_its_region", () => {
    const { container } = render(<App />);
    const inRegion = (region: string, sel: string) => container.querySelector(`[data-region="${region}"] ${sel}`);
    for (const r of ["app", "topbar", "left", "viewer", "console"]) {
      expect(container.querySelector(`[data-region="${r}"]`)).not.toBeNull();
    }
    expect(inRegion("topbar", "nav[aria-label=location]")).not.toBeNull();             // Task 4
    expect(screen.getByRole("button", { name: /stop all/i }).closest("[data-region=topbar]")).not.toBeNull();
    expect(inRegion("left", '[data-testid="left-pane"]')).not.toBeNull();              // Task 5
    expect(container.querySelector('[data-region="left"]')?.textContent).toMatch(/Parts[\s\S]*Variables[\s\S]*History/i);
    expect(inRegion("viewer", '[data-testid="viewer"]')).not.toBeNull();               // Task 6
    expect(inRegion("console", "[data-console] [data-roster]")).not.toBeNull();        // Task 7
    expect(inRegion("console", "[data-console] [data-log-scroll]")).not.toBeNull();
    expect(inRegion("console", "[data-console] [data-command-line] textarea")).not.toBeNull(); // Task 9
    expect(container.querySelector("[data-stub]")).toBeNull(); // no placeholder left anywhere
  });

  it("roster_new_run_button_opens_the_dialog", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "+ new run" }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("reflects_connection_state", async () => {
    const { container } = render(<App />);
    conn.value = "open";
    await Promise.resolve();
    expect(container.querySelector('[data-region="app"]')?.getAttribute("data-conn")).toBe("open");
  });

  it("new_run_dialog_opens_from_store", async () => {
    render(<App />);
    actions.openNewRun();
    expect(await screen.findByRole("dialog")).toBeTruthy();
    fireEvent.click(screen.getByText("Close"));
    await Promise.resolve();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("cards_render_untrusted_text_as_text", () => {
    const a: Approval = {
      id: "a", run_id: "r", action: "<img src=x onerror=alert(1)>", payload: {}, payload_hash: "h", microversion: null,
      deltas: {}, status: "pending", expires_at: 0, decided_by: null, decided_at: null, edited_payload: null,
    };
    const { container } = render(<div>
      <ApprovalCard approval={a} runLabel="designer · stiffen upright" />
      <QuestionCard question={{ question_id: "q", run_id: "r", question: "<b>bold?</b>", options: [] }} runLabel="x" />
    </div>);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(container.querySelectorAll("[data-card]")).toHaveLength(2);
  });
});
