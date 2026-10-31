import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Build } from "../../../api/builds";
import { builds } from "../../../state/builds";
import { atHome, homeView, resetHome } from "../../../state/home";
import { bindClient, resetStore } from "../../../state/store";
import type { CoreClient } from "../../../api/client";
import { HomePage, UNDO_MS } from "./index";

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
  trashBuild: vi.fn().mockResolvedValue(build({ id: "gear", name: "Gearbox", part_count: 1, trashed_at: 1000 })),
  purgeBuild: vi.fn().mockResolvedValue({ purged: ["old"], count: 1 }),
  emptyTrash: vi.fn().mockResolvedValue({ purged: ["old"], count: 1 }),
  // the Home sub-views (Onshape, parts library) load their own lists
  catalogCategories: vi.fn().mockResolvedValue([]),
  catalogSearch: vi.fn().mockResolvedValue([]),
  allowedDomains: vi.fn().mockResolvedValue([]),
  buildChoices: vi.fn().mockResolvedValue([]),
  onshapeDocuments: vi.fn().mockResolvedValue({ items: [], next: null }),
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
  it("an empty build asks for no preview (m12: no 404 per Home load)", async () => {
    builds.value = [build({ id: "empty", name: "Empty", part_count: 0 }), build({ id: "arm", name: "Robot arm", part_count: 3 })];
    render(<HomePage />);
    await waitFor(() => expect(client.projectThumb).toHaveBeenCalledWith("arm"));
    expect(client.projectThumb).not.toHaveBeenCalledWith("empty");
  });

  it("shows a tile per live build with its name and part count", () => {
    render(<HomePage />);
    expect(screen.getByText("Robot arm")).toBeTruthy();
    expect(screen.getByText("Gearbox")).toBeTruthy();
    expect(screen.queryByText("Scrap")).toBeNull();                  // trashed, hidden by default
    expect(screen.getByText(/3 parts/)).toBeTruthy();
    expect(screen.getByText(/1 part\b/)).toBeTruthy();
  });

  it("has U5's drawing drop tile next to New build (not in the Trash)", () => {
    render(<HomePage />);
    expect(screen.getByTestId("home").querySelector(".home-cell-drop")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Trash/ }));
    expect(screen.getByTestId("home").querySelector(".home-cell-drop")).toBeNull();
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
    const tile = screen.getByRole("button", { name: "Open Busy" });
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

  it("D30: the tile's ⋯ menu moves a build to the Trash with an Undo that restores it (without opening it)", async () => {
    render(<HomePage />);
    fireEvent.click(screen.getByRole("button", { name: "More for Gearbox" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Move to Trash" }));
    await waitFor(() => expect(client.trashBuild).toHaveBeenCalledWith("gear"));
    const toast = await screen.findByRole("status");
    expect(toast.textContent).toContain("Moved “Gearbox” to the Trash");
    const tabWrites = client.setBuildTabs.mock.calls.length;
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(client.restoreBuild).toHaveBeenCalledWith("gear"));
    expect(client.setBuildTabs.mock.calls.length).toBe(tabWrites);  // Undo never switches builds
    expect(atHome.value).toBe(true);
  });

  it("D30: the Undo offer lasts five seconds", async () => {
    vi.useFakeTimers();
    try {
      render(<HomePage />);
      fireEvent.contextMenu(screen.getByTitle("Open Gearbox"));       // the context menu has it too
      fireEvent.click(screen.getByRole("menuitem", { name: "Move to Trash" }));
      await vi.waitFor(() => expect(screen.getByText(/Moved “Gearbox”/)).toBeTruthy());
      await vi.advanceTimersByTimeAsync(UNDO_MS + 10);
      expect(screen.queryByText(/Moved “Gearbox”/)).toBeNull();
    } finally { vi.useRealTimers(); }
  });

  it("D30: a Trash tile deletes forever only after a confirm; Empty trash asks first", async () => {
    render(<HomePage />);
    fireEvent.click(screen.getByRole("button", { name: /^Trash/ }));
    fireEvent.click(screen.getByRole("button", { name: "Delete forever" }));
    expect(client.purgeBuild).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole("group", { name: "Confirm deleting Scrap" })).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(client.purgeBuild).toHaveBeenCalledWith("old"));
    await waitFor(() => expect(screen.queryByText("Scrap")).toBeNull());
  });

  it("D30: Empty trash confirms before purging", async () => {
    render(<HomePage />);
    fireEvent.click(screen.getByRole("button", { name: /^Trash/ }));
    fireEvent.click(screen.getByRole("button", { name: "Empty trash…" }));
    expect(client.emptyTrash).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Empty trash" }));
    await waitFor(() => expect(client.emptyTrash).toHaveBeenCalled());
  });

  it("D22: Onshape and the parts library open as Home sub-views with a way back", () => {
    render(<HomePage />);
    fireEvent.click(screen.getByRole("button", { name: "Onshape" }));
    expect(homeView.value).toBe("onshape");
    expect(screen.getByTestId("home").getAttribute("data-home-view")).toBe("onshape");
    fireEvent.click(screen.getByRole("button", { name: /Builds/ }));
    expect(homeView.value).toBe("builds");
    fireEvent.click(screen.getByRole("button", { name: "Parts library" }));
    expect(screen.getByTestId("home").getAttribute("data-home-view")).toBe("library");
  });

  it("D24: a tile shows the mass when the core reports it", () => {
    builds.value = [build({ id: "dog", name: "Dog", part_count: 4, mass_g: 3100 })];
    render(<HomePage />);
    expect(screen.getByTitle("Open Dog").textContent).toContain("3.10 kg");
  });

  it("falls back to a placeholder when a build has no preview", async () => {
    render(<HomePage />);
    // the thumb fetch rejects -> a role=img placeholder, never a broken <img>
    await waitFor(() => {
      const tile = screen.getByTitle("Open Robot arm");
      expect(within(tile).getByRole("img", { name: /Robot arm cover/ })).toBeTruthy();
    });
  });
});
