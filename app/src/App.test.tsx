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
  it("renders_all_regions_and_stubs", () => {
    const { container } = render(<App />);
    for (const r of ["app", "topbar", "left", "viewer", "console"]) {
      expect(container.querySelector(`[data-region="${r}"]`)).not.toBeNull();
    }
    expect(container.querySelector('[data-testid="viewer"]')).not.toBeNull(); // real viewer (Task 6)
    for (const s of ["LeftPane", "CommandLine"]) { // remaining stub markers; Task 10 replaces this test
      expect(container.querySelector(`[data-stub="${s}"]`)).not.toBeNull();
    }
    expect(container.querySelector('[data-region="console"] [data-console]')).not.toBeNull(); // Task 7
    expect(container.querySelector('[data-stub="NewRunDialog"]')).toBeNull();
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

  it("card_stubs_render_untrusted_text_as_text", () => {
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
