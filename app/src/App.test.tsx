import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { CoreClient } from "./api/client";
import { actions, bindClient, conn, resetStore } from "./state/store";
import { closeProfile } from "./ui/profile/open";
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
    expect(inRegion("console", "[data-console] [data-run-switcher]")).not.toBeNull();  // conversation view (default)
    expect(inRegion("console", "[data-console] [data-conversation]")).not.toBeNull();
    expect(inRegion("console", "[data-console] [data-command-line] textarea")).not.toBeNull(); // Task 9
    expect(container.querySelector("[data-stub]")).toBeNull(); // no placeholder left anywhere
  });

  it("new_button_opens_the_inline_composer", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "+ New" }));
    expect(await screen.findByRole("form", { name: "New run" })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("reflects_connection_state", async () => {
    const { container } = render(<App />);
    conn.value = "open";
    await Promise.resolve();
    expect(container.querySelector('[data-region="app"]')?.getAttribute("data-conn")).toBe("open");
  });

  it("mounts_the_setup_gate_and_the_profile_button_opens_profile", async () => {
    const setup = { complete: false, has_model: false, has_onshape: false, missing: ["model", "onshape", "profile"] };
    const profile = { display_name: "", units: "mm", default_material: "PLA", default_agent: "designer", default_model: null,
      onshape_calls_year_cap: 2500, onshape_calls_run_cap: 200, max_parallel_agents: 2, local_endpoints: {}, theme: "system", setup_completed_at: null };
    const c = {
      setupState: vi.fn().mockResolvedValue(setup), getProfile: vi.fn().mockResolvedValue(profile), listKeys: vi.fn().mockResolvedValue([]),
      claudeCodeStatus: vi.fn().mockResolvedValue({ installed: false, version: null, signed_in: false, auth_method: null }),
      models: vi.fn().mockResolvedValue({ default: "a/b", vendors: [] }), status: vi.fn().mockRejectedValue(new Error("x")),
      agents: vi.fn().mockResolvedValue([]), listRuns: vi.fn().mockResolvedValue([]), parts: vi.fn().mockResolvedValue([]),
    };
    bindClient(c as unknown as CoreClient);
    render(<App />);
    // first run: the wizard covers the app
    expect(await screen.findByRole("dialog", { name: "Set up Mises" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close — finish later" }));
    fireEvent.click(screen.getByRole("button", { name: "Profile" }));
    expect(await screen.findByRole("dialog", { name: "Profile" })).toBeTruthy();
    closeProfile();
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
