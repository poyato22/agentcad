import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Build } from "../../api/builds";
import type { CoreClient } from "../../api/client";
import { activeBuildId, builds, openBuildIds, resetBuilds } from "../../state/builds";
import { bindClient, resetStore } from "../../state/store";
import { Breadcrumb } from "./Breadcrumb";

const b = (id: string, name: string, trashed: number | null = null): Build =>
  ({ id, name, created_at: 1, updated_at: 1, trashed_at: trashed, part_count: 2, run_count: 0, onshape: null });

let c: Record<string, ReturnType<typeof vi.fn>>;

beforeEach(() => {
  resetStore(); resetBuilds();
  builds.value = [b("a", "Quad frame"), b("b", "Bench rig"), b("c", "Gimbal"), b("t", "Old mount", 9)];
  activeBuildId.value = "a";
  openBuildIds.value = ["a", "b"];
  c = {
    setBuildTabs: vi.fn(async (t: unknown) => t), parts: vi.fn(async () => []),
    createBuild: vi.fn(async (name: string) => b("n", name)),
    renameBuild: vi.fn(async (id: string, name: string) => ({ ...b(id, name) })),
    trashBuild: vi.fn(async (id: string) => b(id, "Quad frame", 5)),
    restoreBuild: vi.fn(async (id: string) => b(id, "Old mount")),
    emptyTrash: vi.fn(async () => ({ purged: ["t"], count: 1 })),
  };
  bindClient(c as unknown as CoreClient);
});
afterEach(() => cleanup());

describe("build tabs", () => {
  it("shows the open builds as tabs between LOCAL and the part", () => {
    const { container } = render(<Breadcrumb />);
    expect(container.textContent).toMatch(/^◇ LOCAL \/ Quad frame×Bench rig×\+▾ \/ no part$/);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false"]);
  });

  it("click switches; ← / → move between tabs; × closes", async () => {
    render(<Breadcrumb />);
    fireEvent.click(screen.getByRole("tab", { name: "Bench rig" }));
    await waitFor(() => expect(activeBuildId.value).toBe("b"));
    expect(c.setBuildTabs).toHaveBeenLastCalledWith({ active_project_id: "b", open_project_ids: ["a", "b"] });
    const first = screen.getByRole("tab", { name: "Quad frame" });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Bench rig" }));
    fireEvent.click(screen.getByRole("button", { name: "Close Quad frame" }));
    await waitFor(() => expect(openBuildIds.value).toEqual(["b"]));
  });

  it("+ makes a new build from an inline name; double-click renames", async () => {
    render(<Breadcrumb />);
    fireEvent.click(screen.getByRole("button", { name: "Add a build" }));
    const name = screen.getByRole("textbox", { name: "New build name" });
    fireEvent.input(name, { target: { value: "Drone arm v2" } });
    fireEvent.keyDown(name, { key: "Enter" });
    await waitFor(() => expect(c.createBuild).toHaveBeenCalledWith("Drone arm v2"));
    await waitFor(() => expect(activeBuildId.value).toBe("n"));
    fireEvent.dblClick(screen.getByRole("tab", { name: "Bench rig" }));
    const r = screen.getByRole("textbox", { name: "Rename Bench rig" });
    fireEvent.input(r, { target: { value: "Test rig" } });
    fireEvent.keyDown(r, { key: "Enter" });
    await waitFor(() => expect(c.renameBuild).toHaveBeenCalledWith("b", "Test rig"));
  });

  it("the ▾ menu opens a closed build, trashes the active one, restores and empties the Trash after asking", async () => {
    render(<Breadcrumb />);
    const open = () => fireEvent.click(screen.getByRole("button", { name: "All builds and the Trash" }));
    open();
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Gimbal/ }));
    await waitFor(() => expect(activeBuildId.value).toBe("c"));
    expect(openBuildIds.value).toEqual(["a", "b", "c"]);

    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Move “Gimbal” to the Trash" }));
    await waitFor(() => expect(c.trashBuild).toHaveBeenCalledWith("c"));

    open();
    fireEvent.click(screen.getByRole("menuitem", { name: /Old mount/ }));
    await waitFor(() => expect(c.restoreBuild).toHaveBeenCalledWith("t"));

    builds.value = [...builds.value.filter((x) => x.id !== "t"), b("t", "Old mount", 9)];
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: "Empty trash…" }));
    expect(c.emptyTrash).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "Confirm emptying the Trash" }).textContent).toContain("Their part files stay on disk.");
    fireEvent.click(screen.getByRole("button", { name: "Empty trash" }));
    await waitFor(() => expect(c.emptyTrash).toHaveBeenCalled());
  });
});
