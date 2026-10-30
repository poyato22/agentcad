import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Build } from "../../../api/builds";
import { builds } from "../../../state/builds";
import { atHome, resetHome } from "../../../state/home";
import { bindClient, resetStore } from "../../../state/store";
import type { CoreClient } from "../../../api/client";
import { HomePage } from "./index";

function build(p: Partial<Build>): Build {
  return { id: "b", name: "Build", created_at: 0, updated_at: 0, trashed_at: null,
    part_count: 0, run_count: 0, onshape: null, ...p };
}

const BUILDS = [
  build({ id: "arm", name: "Robot arm", part_count: 3, updated_at: 500 }),
  build({ id: "gear", name: "Gearbox", part_count: 1, updated_at: 200 }),
  build({ id: "old", name: "Scrap", trashed_at: 999 }),
];

const client = {
  projectThumb: vi.fn().mockRejectedValue(new Error("no preview")),   // tiles fall back to a glyph
  createBuild: vi.fn(),
  listBuilds: vi.fn().mockResolvedValue(BUILDS),
  buildTabs: vi.fn().mockResolvedValue({ active_project_id: null, open_project_ids: [] }),
  setBuildTabs: vi.fn().mockResolvedValue({}),
  parts: vi.fn().mockResolvedValue([]),
  restoreBuild: vi.fn().mockResolvedValue(build({ id: "old", name: "Scrap", trashed_at: null })),
};

beforeEach(() => {
  resetStore();
  resetHome();
  builds.value = BUILDS;
  for (const f of Object.values(client)) (f as ReturnType<typeof vi.fn>).mockClear?.();
  bindClient(client as unknown as CoreClient);
});
afterEach(() => { cleanup(); builds.value = []; });

describe("HomePage", () => {
  it("shows a tile per live build with its name and part count", () => {
    render(<HomePage />);
    expect(screen.getByText("Robot arm")).toBeTruthy();
    expect(screen.getByText("Gearbox")).toBeTruthy();
    expect(screen.queryByText("Scrap")).toBeNull();                  // trashed, hidden by default
    expect(screen.getByText(/3 parts/)).toBeTruthy();
    expect(screen.getByText(/1 part\b/)).toBeTruthy();
  });

  it("searches by name", () => {
    render(<HomePage />);
    fireEvent.input(screen.getByLabelText("Search builds"), { target: { value: "gear" } });
    expect(screen.queryByText("Robot arm")).toBeNull();
    expect(screen.getByText("Gearbox")).toBeTruthy();
  });

  it("opens a build when its tile is clicked, leaving Home", async () => {
    render(<HomePage />);
    fireEvent.click(screen.getByTitle("Open Robot arm"));
    await waitFor(() => expect(atHome.value).toBe(false));
    expect(client.setBuildTabs).toHaveBeenCalled();
  });

  it("creates a build from the New tile and opens it", async () => {
    client.createBuild.mockResolvedValue(build({ id: "new1", name: "Chassis" }));
    render(<HomePage />);
    fireEvent.click(screen.getByText("New build"));
    const input = await screen.findByLabelText("New build name");
    fireEvent.input(input, { target: { value: "Chassis" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(client.createBuild).toHaveBeenCalledWith("Chassis"));
    await waitFor(() => expect(atHome.value).toBe(false));
  });

  it("shows trashed builds with Restore behind the Trash filter", () => {
    render(<HomePage />);
    fireEvent.click(screen.getByRole("button", { name: /Trash/ }));
    expect(screen.getByText("Scrap")).toBeTruthy();
    expect(screen.queryByText("Robot arm")).toBeNull();
    fireEvent.click(screen.getByText("Restore"));
    expect(client.restoreBuild).toHaveBeenCalledWith("old");
  });

  it("labels 'last edited' from real activity and keeps tiles announced as buttons", () => {
    const now = Date.now() / 1000;
    builds.value = [build({ id: "busy", name: "Busy", updated_at: now - 30 * 86400, last_edited_at: now - 120 })];
    render(<HomePage />);
    const tile = screen.getByRole("button", { name: /Busy/ });
    expect(tile.textContent).toContain("2 min ago");
    expect(tile.closest("li")).not.toBeNull();
  });

  it("says why a New build failed", async () => {
    client.createBuild.mockRejectedValueOnce(new Error("disk full"));
    render(<HomePage />);
    fireEvent.click(screen.getByText("New build"));
    const input = await screen.findByLabelText("New build name");
    fireEvent.input(input, { target: { value: "X" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect((await screen.findByRole("alert")).textContent).toContain("disk full");
    expect(atHome.value).toBe(true);
  });

  it("falls back to a placeholder when a build has no preview", async () => {
    render(<HomePage />);
    // the thumb fetch rejects -> a role=img placeholder, never a broken <img>
    await waitFor(() => {
      const tile = screen.getByTitle("Open Robot arm");
      expect(within(tile).getByRole("img", { name: /Robot arm preview/ })).toBeTruthy();
    });
  });
});
