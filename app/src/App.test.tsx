import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { CoreClient } from "./api/client";
import { atHome, resetHome } from "./state/home";
import { approvals, bindClient, conn, resetStore } from "./state/store";
import { resetDockMemory, setConsoleCollapsed, setLeftCollapsed } from "./ui/pages/router";
import { closeProfile } from "./ui/profile/open";
import { ApprovalCard } from "./ui/console/approvals/ApprovalCard";
import { QuestionCard } from "./ui/console/approvals/QuestionCard";
import type { Approval } from "./api/types";

// jsdom has no WebGL: the real viewer scene is replaced by an inert handle (Task 6 tests cover it).
vi.mock("./ui/viewer/scene", () => ({
  createViewer: () => ({
    setPart: vi.fn(), setParts: vi.fn(), setMode: vi.fn(), setHighlight: vi.fn(), fit: vi.fn(), resize: vi.fn(), dispose: vi.fn(),
    pick: vi.fn(), toScreen: vi.fn(() => [0, 0]), setMarker: vi.fn(),
  }),
}));

afterEach(() => { cleanup(); resetStore(); setConsoleCollapsed(false); setLeftCollapsed(false); resetDockMemory(); resetHome(); });
// Launch lands on the Home grid (R1); the shell tests below open a build first.
beforeEach(() => { atHome.value = false; });

describe("App shell", () => {
  it("shows_the_home_grid_on_launch", () => {
    atHome.value = true;
    const { container } = render(<App />);
    expect(container.querySelector('[data-testid="home"]')).not.toBeNull();
    expect(container.querySelector('[data-region="viewer"]')).toBeNull();
    // D23: Home has no top bar at all — the top bar exists only inside a build
    expect(container.querySelector('[data-region="topbar"]')).toBeNull();
    expect(container.querySelector(".topbar")).toBeNull();
  });

  it("renders_every_real_pane_in_its_region", () => {
    const { container } = render(<App />);
    const inRegion = (region: string, sel: string) => container.querySelector(`[data-region="${region}"] ${sel}`);
    for (const r of ["app", "topbar", "left", "viewer", "console"]) {
      expect(container.querySelector(`[data-region="${r}"]`)).not.toBeNull();
    }
    expect(inRegion("topbar", "button[aria-label=Home]")).not.toBeNull();              // D1: ⌂ + build name
    expect([...container.querySelectorAll('[data-region="topbar"] [role=tab]')].map((t) => t.textContent))
      .toEqual(["Design", "Studies", "Print", "BOM"]);                                  // D9
    expect(container.querySelector('[data-testid="build-tabs"]')).toBeNull();          // D1: no build tabs
    expect(inRegion("left", '[data-testid="left-pane"]')).not.toBeNull();              // Task 5
    expect(container.querySelector('[data-region="left"]')?.textContent).toMatch(/Parts[\s\S]*BOM/);
    expect(inRegion("viewer", '[data-testid="assembly-view"]')).not.toBeNull();        // Plan 9 D7: the build view
    expect(inRegion("console", "[data-console] [data-dock]")).not.toBeNull();  // the agent dock (default)
    for (const s of ["view", "sim", "text"]) expect(inRegion("console", `[data-dock] [data-stream=${s}]`)).not.toBeNull();
    expect(inRegion("console", "[data-console] [data-conversation]")).not.toBeNull();
    expect(inRegion("console", "[data-console] [data-command-line] textarea")).not.toBeNull(); // Task 9
    expect(container.querySelector("[data-stub]")).toBeNull(); // no placeholder left anywhere
  });

  it("one chat per build: no New run anywhere (P10 E24)", async () => {
    render(<App />);
    expect(screen.queryByRole("button", { name: "Conversation options" })).toBeNull();   // E22 took the rest
    expect(screen.queryByRole("menuitem", { name: /New run/ })).toBeNull();
    expect(screen.queryByRole("form", { name: "New run" })).toBeNull();
    expect(screen.queryByText("No runs yet.")).toBeNull();
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
    expect(await screen.findByRole("dialog", { name: "Set up AgentCAD" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Profile" }));
    expect(await screen.findByRole("dialog", { name: "Profile" })).toBeTruthy();
    closeProfile();
  });

  it("collapsed_rail_shows_a_blue_needs_you_count", () => {
    // A3: when collapsed, the rail still signals that a card waits for the human (blue).
    const a: Approval = {
      id: "a1", run_id: "r1", action: "fixture_merge", payload: {}, payload_hash: "h", microversion: null,
      deltas: {}, status: "pending", expires_at: 0, decided_by: null, decided_at: null, edited_payload: null,
    };
    approvals.value = new Map([["a1", a]]);
    setConsoleCollapsed(true);
    const { container } = render(<App />);
    const badge = container.querySelector("[data-region=console] [data-needs-you]");
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toContain("1");
    // blue (needs the human); the rail expands when clicked
    expect(badge?.classList.contains("needs-you")).toBe(true);
    expect(container.querySelector('[data-region="app"]')?.getAttribute("data-console")).toBe("rail");
  });

  it("the_parts_surface_and_the_dock_float_and_fold_to_slim_tabs", () => {
    // FW-layout (D20): open, each surface is a floating card over the full-bleed build view
    const { container } = render(<App />);
    const left = container.querySelector('[data-region="left"]');
    const dock = container.querySelector('[data-region="console"]');
    expect(left?.classList.contains("surface")).toBe(true);
    expect(dock?.classList.contains("surface")).toBe(true);
    expect(container.querySelector('[data-region="viewer"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Hide the parts" }));
    fireEvent.click(screen.getByRole("button", { name: "Collapse the agent console" }));
    // folded: a slim tab each, the build view keeps the whole window
    expect(container.querySelector('[data-region="left"] [data-testid="left-pane"]')).toBeNull();
    expect(container.querySelector('[data-region="console"] [data-dock]')).toBeNull();
    expect(container.querySelector('[data-region="app"]')?.getAttribute("data-left")).toBe("rail");
    fireEvent.click(screen.getByRole("button", { name: "Show the parts" }));
    fireEvent.click(screen.getByRole("button", { name: "Expand the agent console" }));
    expect(container.querySelector('[data-region="left"] [data-testid="left-pane"]')).not.toBeNull();
    expect(container.querySelector('[data-region="console"] [data-dock]')).not.toBeNull();
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
