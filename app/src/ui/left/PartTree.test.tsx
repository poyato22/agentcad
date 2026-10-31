import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { CoreError } from "../../api/client";
import type { PartSummary } from "../../api/types";
import { activeBuildId, resetBuilds } from "../../state/builds";
import { agentFlash, onPartChanged, resetPartEdits } from "../../state/partEdits";
import { bindClient, parts, resetStore, selectedPartId } from "../../state/store";
import { viewScope } from "../viewer/Viewer";
import { measureHook } from "./partMenuHooks";
import { resetSelection, selectedIds } from "./partSelection";
import { PartTree } from "./PartTree";
import { assemblyOf, lastDeleted, onAgentDeleted, treeError, treeNote } from "./treeActions";
import { LeftPane } from "./LeftPane";
import { openPartId } from "../assembly/focus";
import { hiddenParts, resetVisibility } from "./visibility";
import { modifyRequest } from "../viewer/tool";

const row = (id: string, order: number, parent: string | null = null, color: string | null = null): PartSummary =>
  ({ part_id: id, id, label: id, vars: {}, metrics: null, mtime: 1, parent_id: parent, order, kind: "part", version: 1, project_id: "b1", color });

const METRICS: NonNullable<PartSummary["metrics"]> = {
  valid: true, volume_mm3: 4000, area_mm2: 2200, mass_g: 12.5, bbox: { min: [0, 0, 0], max: [40, 20, 5], size: [40, 20, 5] },
  center_of_mass: [20, 10, 2.5], faces: 6, edges: 12, holes: [], solids: 1,
};

// frame ─┬ arm_a
//        └ arm_b
// plate
const LIST = () => [row("frame", 0), row("arm_a", 0, "frame"), row("arm_b", 1, "frame"), row("plate", 1)];

type Fn = ReturnType<typeof vi.fn>;
let client: {
  reorderParts: Fn; patchPart: Fn; parts: Fn; deletePart: Fn; restorePart: Fn; deleteInstance: Fn; removePartsFromAssembly: Fn; restoreAssembly: Fn; duplicatePart: Fn; exportPart: Fn; assembly: Fn; addInstance: Fn; getProfile: Fn; bom: Fn;
};

beforeEach(() => {
  resetStore(); resetBuilds(); resetPartEdits(); resetVisibility(); resetSelection();
  treeError.value = null; treeNote.value = null; viewScope.value = "part";
  activeBuildId.value = "b1";
  parts.value = LIST();
  client = {
    deletePart: vi.fn(async (id: string) => ({ deleted: id, removed_instances: [], undo: null })),
    restorePart: vi.fn(async (id: string) => row(id, 1)),
    deleteInstance: vi.fn(async () => []),
    removePartsFromAssembly: vi.fn(async () => ({ removed_instances: ["i1", "i2"], removed_joints: [], undo: { instances: [{ id: "i1" }], joints: [] } })),
    restoreAssembly: vi.fn(async () => ({ restored_instances: 2, restored_joints: 0 })),
    bom: vi.fn(async () => { throw new Error("no bom here"); }),
    duplicatePart: vi.fn(async (id: string) => row(`${id}_copy`, 9)),
    exportPart: vi.fn(async (id: string, f: string) => ({ path: `/Users/me/Downloads/${id}.${f}`, name: `${id}.${f}`, format: f })),
    assembly: vi.fn(async () => ({ instances: [], joints: [], dof_remaining: 0, overconstrained: [] })),
    addInstance: vi.fn(async () => ({})),
    getProfile: vi.fn(async () => ({ default_material: "PETG" })),
    reorderParts: vi.fn(async (_b: string, parent: string | null, ids: string[]) => parts.peek().map((p) => {
      const i = ids.indexOf(p.part_id);
      return i < 0 ? p : { ...p, parent_id: parent, order: i };
    })),
    patchPart: vi.fn(async (id: string, patch: Record<string, unknown>) => ({ ...parts.peek().find((p) => p.part_id === id), ...patch })),
    parts: vi.fn(async () => LIST()),                       // the core's tree as it is (after a refused move)
  };
  bindClient(client as unknown as CoreClient);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); assemblyOf.value = null; lastDeleted.value = null; openPartId.value = null; });

const rowEl = (id: string) => document.querySelector<HTMLElement>(`[data-part="${id}"] .tree-row`) as HTMLElement;
const names = () => [...document.querySelectorAll("[data-part]")].map((el) => `${el.getAttribute("data-depth")}${el.getAttribute("data-part")}`);

/** Lays the rows out 20 px apart from y = 100 (jsdom has no layout). */
function layout(): void {
  document.querySelectorAll<HTMLElement>("[data-part] > .tree-row").forEach((el, i) => {
    const top = 100 + i * 20;
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue({ top, height: 20, bottom: top + 20, left: 0, right: 200, width: 200, x: 0, y: top, toJSON: () => ({}) });
  });
}

/** A real pointer drag: press on the row's name, move past the threshold, over the target row's zone. */
function drag(from: string, to: string, where: "top" | "middle" | "bottom") {
  layout();
  const target = rowEl(to).getBoundingClientRect();
  const y = target.top + (where === "top" ? 1 : where === "bottom" ? 19 : 10);
  const name = rowEl(from).querySelector(".tree-name") as HTMLElement;
  const start = rowEl(from).getBoundingClientRect().top + 10;
  fireEvent.pointerDown(name, { button: 0, clientX: 40, clientY: start });
  fireEvent.pointerMove(window, { clientX: 41, clientY: start + 2 });         // under the threshold: still a click
  fireEvent.pointerMove(window, { clientX: 44, clientY: (start + y) / 2 });
  fireEvent.pointerMove(window, { clientX: 44, clientY: y });
  return { drop: () => { fireEvent.pointerUp(window, { clientX: 44, clientY: y }); fireEvent.click(name); } };
}

describe("part tree", () => {
  it("renders nested rows in order", () => {
    render(<PartTree />);
    expect(names()).toEqual(["0frame", "1arm_a", "1arm_b", "0plate"]);
    expect(screen.getByRole("tree").querySelectorAll("[role=treeitem][aria-level='2']").length).toBe(2);
  });

  it("drag reorders: drop below a row calls /parts/reorder with the new sibling order", async () => {
    render(<PartTree />);
    const d = drag("frame", "plate", "bottom");
    await waitFor(() => expect(rowEl("plate").getAttribute("data-drop")).toBe("after"));
    d.drop();
    await waitFor(() => expect(client.reorderParts).toHaveBeenCalledWith("b1", null, ["plate", "frame"]));
    await waitFor(() => expect(names()).toEqual(["0plate", "0frame", "1arm_a", "1arm_b"]));
  });

  it("drag into the middle of a row re-parents under it", async () => {
    render(<PartTree />);
    const d = drag("plate", "arm_b", "middle");
    await waitFor(() => expect(rowEl("arm_b").getAttribute("data-drop")).toBe("inside"));
    d.drop();
    await waitFor(() => expect(client.reorderParts).toHaveBeenCalledWith("b1", "arm_b", ["plate"]));
  });

  it("a short press is a click (selects); a drag ending on a button doesn't select it; Esc cancels a drag", async () => {
    render(<PartTree />);
    layout();
    const name = rowEl("plate").querySelector(".tree-name") as HTMLElement;
    fireEvent.pointerDown(name, { button: 0, clientX: 40, clientY: 170 });
    fireEvent.pointerMove(window, { clientX: 42, clientY: 171 });
    fireEvent.pointerUp(window, { clientX: 42, clientY: 171 });
    fireEvent.click(name);
    expect(selectedPartId.value).toBe("plate");
    expect(client.reorderParts).not.toHaveBeenCalled();

    selectedPartId.value = null;
    const d = drag("plate", "frame", "top");
    await waitFor(() => expect(rowEl("frame").getAttribute("data-drop")).toBe("before"));
    expect(document.querySelector(".tree-ghost")?.textContent).toBe("plate");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(rowEl("frame").getAttribute("data-drop")).toBeNull();
    d.drop();
    await new Promise((r) => setTimeout(r, 10));
    expect(client.reorderParts).not.toHaveBeenCalled();
    expect(selectedPartId.value).toBeNull();
  });

  it("never offers dropping a part into its own child (cycle prevention)", async () => {
    render(<PartTree />);
    const d = drag("frame", "arm_a", "middle");
    expect(rowEl("arm_a").getAttribute("data-drop")).toBeNull();
    d.drop();
    await new Promise((r) => setTimeout(r, 10));
    expect(client.reorderParts).not.toHaveBeenCalled();
  });

  it("Alt+↓ / Alt+↑ reorder and Alt+→ / Alt+← nest by keyboard", async () => {
    render(<PartTree />);
    const name = (id: string) => rowEl(id).querySelector(".tree-name") as HTMLElement;
    fireEvent.keyDown(name("frame"), { key: "ArrowDown", altKey: true });
    await waitFor(() => expect(client.reorderParts).toHaveBeenLastCalledWith("b1", null, ["plate", "frame"]));
    fireEvent.keyDown(name("arm_b"), { key: "ArrowUp", altKey: true });
    await waitFor(() => expect(client.reorderParts).toHaveBeenLastCalledWith("b1", "frame", ["arm_b", "arm_a"]));
    fireEvent.keyDown(name("arm_a"), { key: "ArrowLeft", altKey: true });
    await waitFor(() => expect(client.reorderParts).toHaveBeenLastCalledWith("b1", null, expect.arrayContaining(["arm_a"])));
  });

  it("the ⋯ button and a right-click open the same useful menu, focused, closed by Esc or a press outside", () => {
    render(<PartTree />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for plate" }));
    const menu = screen.getByRole("menu", { name: "plate actions" });
    expect(menu.contains(document.activeElement)).toBe(true);
    const labels = screen.getAllByRole("menuitem").map((b) => b.textContent);
    expect(labels).toEqual(["RenameF2", "Appearance…", "HideH", "Isolate", "Measure", "Add to assembly", "Duplicate⌘D",
      "Export STEP", "Export STL", "Properties⌘I", "Modify…", "Delete…⌫"]);   // Modify…: U2a's item (the viewer registers it)
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.contextMenu(rowEl("frame"), { clientX: 30, clientY: 40 });
    expect(screen.getByRole("menu", { name: "frame actions" })).toBeTruthy();
    expect(selectedPartId.value).toBe("frame");                      // a right-click selects the row first
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("a G3 layout skeleton row is tagged and offers no Modify…", () => {
    parts.value = [...LIST(), { ...row("skeleton_main", 9), kind: "skeleton", metrics: null }];
    render(<PartTree />);
    expect(rowEl("skeleton_main").textContent).toContain("skeleton");
    fireEvent.click(screen.getByRole("button", { name: "Actions for skeleton main" }));
    const modify = screen.queryByRole("menuitem", { name: "Modify…" }) as HTMLButtonElement | null;
    expect(modify === null || modify.disabled).toBe(true);
  });

  it("Modify… (the feature ops) is in the part's menu: it selects the part and asks the viewer", () => {
    parts.value = [...LIST().slice(0, 3), { ...row("plate", 1), metrics: { mass_g: 1 } as PartSummary["metrics"] }];
    render(<PartTree />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for arm a" }));
    expect((screen.getByRole("menuitem", { name: "Modify…" }) as HTMLButtonElement).disabled).toBe(true);   // not built: nothing to modify
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Actions for plate" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Modify…" }));
    expect(selectedPartId.value).toBe("plate");
    expect(modifyRequest.value?.partId).toBe("plate");
  });

  it("a refused move is put back with the core's reason", async () => {
    client.reorderParts.mockRejectedValueOnce(new CoreError("invalid_input", "that move would create a cycle in the part tree", { status: 400 }));
    render(<PartTree />);
    fireEvent.keyDown(rowEl("frame").querySelector(".tree-name") as HTMLElement, { key: "ArrowDown", altKey: true });
    await screen.findByText("Couldn't move the part: that move would create a cycle in the part tree");
    expect(names()).toEqual(["0frame", "1arm_a", "1arm_b", "0plate"]);
  });

  it("quick moves go out one at a time, and the latest answer is the one kept", async () => {
    const answers: (() => void)[] = [];
    client.reorderParts.mockImplementation((_b: string, parent: string | null, ids: string[]) => new Promise((res) => {
      answers.push(() => res(parts.peek().map((p) => { const i = ids.indexOf(p.part_id); return i < 0 ? p : { ...p, parent_id: parent, order: i }; })));
    }));
    render(<PartTree />);
    const name = rowEl("plate").querySelector(".tree-name") as HTMLElement;
    fireEvent.keyDown(name, { key: "ArrowUp", altKey: true });
    fireEvent.keyDown(rowEl("plate").querySelector(".tree-name") as HTMLElement, { key: "ArrowDown", altKey: true });
    await new Promise((r) => setTimeout(r, 0));
    expect(client.reorderParts).toHaveBeenCalledTimes(1);          // the second waits for the first
    answers[0]();
    await waitFor(() => expect(client.reorderParts).toHaveBeenCalledTimes(2));
    answers[1]();
    await waitFor(() => expect(names()).toEqual(["0frame", "1arm_a", "1arm_b", "0plate"]));
  });

  it("one click on the swatch opens the appearance popover; an outside press or Esc closes it", () => {
    render(<PartTree />);
    fireEvent.click(screen.getByRole("button", { name: /Appearance of plate/ }));
    const pop = screen.getByTestId("appearance-popover");
    expect(pop.contains(document.activeElement)).toBe(true);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByTestId("appearance-popover")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Appearance of plate/ }));
    fireEvent.keyDown(screen.getByTestId("appearance-popover"), { key: "Escape" });
    expect(screen.queryByTestId("appearance-popover")).toBeNull();
  });

  it("picks a color (2 clicks) or a custom hex, and the popover stays open to try more (PATCH color)", async () => {
    render(<PartTree />);
    fireEvent.click(screen.getByRole("button", { name: /Appearance of plate/ }));
    fireEvent.click(screen.getByRole("radio", { name: "Teal" }));
    await waitFor(() => expect(client.patchPart).toHaveBeenCalledWith("plate", { color: "#2f9a9c" }));
    expect(parts.value.find((p) => p.part_id === "plate")?.color).toBe("#2f9a9c");
    expect(screen.getByTestId("appearance-popover")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Appearance of frame/ }));
    fireEvent.input(screen.getByRole("textbox", { name: "Hex color" }), { target: { value: "#ABC" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(client.patchPart).toHaveBeenCalledWith("frame", { color: "#aabbcc" }));
  });

  it("a finish preset sets the appearance (an untinted one keeps the color); Plain clears it", async () => {
    parts.value = LIST().map((p) => (p.part_id === "plate" ? { ...p, color: "#c2463d" } : p));
    render(<PartTree />);
    fireEvent.click(screen.getByRole("button", { name: /Appearance of plate/ }));
    fireEvent.click(screen.getByRole("radio", { name: "Glossy plastic" }));
    await waitFor(() => expect(client.patchPart).toHaveBeenCalledWith("plate", {
      appearance: { preset: "glossy_plastic", color: "#c2463d", metalness: 0, roughness: 0.22, opacity: 1 },
    }));
    expect(screen.getByRole("button", { name: "Appearance of plate: Glossy plastic" })).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Polished metal" }));
    await waitFor(() => expect(client.patchPart).toHaveBeenLastCalledWith("plate", { appearance: expect.objectContaining({ preset: "polished_metal", metalness: 1 }) }));
    fireEvent.click(screen.getByRole("radio", { name: "Plain" }));
    await waitFor(() => expect(client.patchPart).toHaveBeenLastCalledWith("plate", { appearance: null }));
  });

  it("⌘-click and Shift-click select several parts; the menu then acts on all of them", async () => {
    render(<PartTree />);
    const name = (id: string) => rowEl(id).querySelector(".tree-name") as HTMLElement;
    fireEvent.click(name("frame"));
    fireEvent.click(name("arm_b"), { shiftKey: true });
    expect(selectedIds.value).toEqual(new Set(["frame", "arm_a", "arm_b"]));
    fireEvent.click(name("arm_a"), { metaKey: true });
    expect(selectedIds.value).toEqual(new Set(["frame", "arm_b"]));
    expect(rowEl("arm_a").classList.contains("sel")).toBe(false);
    fireEvent.contextMenu(rowEl("arm_b"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: /Hide 2 parts/ }));
    expect(hiddenParts.value).toEqual(new Set(["frame", "arm_b"]));
    // a palette pick recolors the whole selection
    fireEvent.click(screen.getByRole("button", { name: /Appearance of frame/ }));
    fireEvent.click(screen.getByRole("radio", { name: "Red" }));
    await waitFor(() => expect(client.patchPart).toHaveBeenCalledTimes(2));
    expect(client.patchPart.mock.calls.map((c) => c[0]).sort()).toEqual(["arm_b", "frame"]);
  });

  it("a recolor the core refuses for one of several parts puts back only that one, and names it", async () => {
    client.patchPart.mockImplementation(async (id: string, patch: Record<string, unknown>) => {
      if (id === "arm_b") throw new CoreError("invalid_input", "nope", { status: 400 });
      return { ...parts.peek().find((p) => p.part_id === id), ...patch };
    });
    render(<PartTree />);
    const name = (id: string) => rowEl(id).querySelector(".tree-name") as HTMLElement;
    fireEvent.click(name("frame"));
    fireEvent.click(name("arm_b"), { metaKey: true });
    fireEvent.click(screen.getByRole("button", { name: /Appearance of frame/ }));
    fireEvent.click(screen.getByRole("radio", { name: "Red" }));
    await screen.findByText("Couldn't set the color for arm b: nope");
    expect(parts.value.find((p) => p.part_id === "frame")?.color).toBe("#c2463d");
    expect(parts.value.find((p) => p.part_id === "arm_b")?.color).toBeNull();
  });

  it("the eye acts on its own row, even in a selection", () => {
    render(<PartTree />);
    const name = (id: string) => rowEl(id).querySelector(".tree-name") as HTMLElement;
    fireEvent.click(name("frame"));
    fireEvent.click(name("plate"), { metaKey: true });
    fireEvent.click(screen.getByRole("button", { name: "Hide plate in the viewer" }));
    expect(hiddenParts.value).toEqual(new Set(["plate"]));
  });

  it("a selection with an unbuilt part can't be duplicated or added to the assembly, and says why", () => {
    parts.value = LIST().map((p) => (p.part_id === "plate" ? { ...p, metrics: METRICS } : p));
    render(<PartTree />);
    const name = (id: string) => rowEl(id).querySelector(".tree-name") as HTMLElement;
    fireEvent.click(name("plate"));
    fireEvent.click(name("frame"), { metaKey: true });
    fireEvent.contextMenu(rowEl("plate"), { clientX: 10, clientY: 10 });
    const dup = screen.getByRole("menuitem", { name: /^Duplicate 2 parts/ }) as HTMLButtonElement;
    expect(dup.disabled).toBe(true);
    expect(dup.title).toBe("frame isn't built yet");
    expect((screen.getByRole("menuitem", { name: "Add to assembly 2 parts" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("the menu works from the keyboard: Shift+F10 opens it on the row, arrows move, Enter picks, Esc returns to the row", async () => {
    parts.value = LIST().map((p) => ({ ...p, metrics: METRICS }));
    render(<PartTree />);
    const name = rowEl("plate").querySelector(".tree-name") as HTMLElement;
    name.focus();
    fireEvent.keyDown(name, { key: "F10", shiftKey: true });
    const menu = screen.getByRole("menu", { name: "plate actions" });
    expect(document.activeElement?.textContent).toBe("RenameF2");
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toBe("HideH");
    fireEvent.keyDown(menu, { key: "End" });
    expect(document.activeElement?.textContent).toBe("Delete…⌫");
    fireEvent.keyDown(menu, { key: "Home" });
    fireEvent.keyDown(menu, { key: "ArrowUp" });               // wraps to the last
    expect(document.activeElement?.textContent).toBe("Delete…⌫");
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(rowEl("plate").querySelector(".tree-name")));
    // the ContextMenu key too, then Enter on an item runs it
    fireEvent.keyDown(rowEl("plate").querySelector(".tree-name") as HTMLElement, { key: "ContextMenu" });
    fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("menu"), { key: "ArrowDown" });
    fireEvent.click(document.activeElement as HTMLElement);     // Enter on a button is its click
    expect(hiddenParts.value.has("plate")).toBe(true);
  });

  it("Isolate shows only the part in the build view; Show all brings the rest back", () => {
    render(<PartTree />);
    fireEvent.contextMenu(rowEl("plate"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Isolate" }));
    expect(hiddenParts.value).toEqual(new Set(["frame", "arm_a", "arm_b"]));
    expect(viewScope.value).toBe("build");
    fireEvent.contextMenu(rowEl("plate"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Show all parts" }));
    expect(hiddenParts.value.size).toBe(0);
  });

  it("Delete asks once, naming the assembly instances, then deletes with them and reloads; Duplicate selects the copy", async () => {
    parts.value = LIST().map((p) => ({ ...p, metrics: METRICS }));
    client.parts.mockImplementation(async () => parts.peek().filter((p) => p.part_id !== "plate"));
    assemblyOf.value = { instances: [{ id: "i1", part_id: "plate" }, { id: "i2", part_id: "plate" }, { id: "i3", part_id: "frame" }] };
    render(<PartTree />);
    fireEvent.contextMenu(rowEl("plate"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: /^Delete…/ }));
    const ask = screen.getByTestId("confirm-delete");
    expect(ask.textContent).toContain("Delete plate?");
    expect(ask.textContent).toContain("Also removes 2 instances from the assembly.");
    expect(client.deletePart).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(client.deletePart).toHaveBeenCalledWith("plate", { cascade: true }));
    await screen.findByText("Deleted plate");
    expect(names()).not.toContain("0plate");

    client.parts.mockImplementation(async () => [...LIST().map((p) => ({ ...p, metrics: METRICS })), row("frame_copy", 2)]);
    fireEvent.contextMenu(rowEl("frame"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: /^Duplicate/ }));
    await waitFor(() => expect(selectedPartId.value).toBe("frame_copy"));
  });

  it("⌫ on a row asks the same; Esc cancels; the Undo toast restores the part and its instances", async () => {
    parts.value = LIST().map((p) => ({ ...p, metrics: METRICS }));
    client.deletePart.mockImplementation(async (id: string) => ({ deleted: id, removed_instances: ["i1"], undo: `${id}.42` }));
    client.parts.mockImplementation(async () => parts.peek().filter((p) => p.part_id !== "plate"));
    assemblyOf.value = { instances: [{ id: "i1", part_id: "plate" }] };
    render(<LeftPane />);
    const name = rowEl("plate").querySelector(".tree-name") as HTMLElement;
    fireEvent.keyDown(name, { key: "Backspace" });
    expect(screen.getByTestId("confirm-delete").textContent).toContain("Also removes 1 instance from the assembly.");
    fireEvent.keyDown(screen.getByTestId("confirm-delete"), { key: "Escape" });
    expect(screen.queryByTestId("confirm-delete")).toBeNull();
    fireEvent.keyDown(name, { key: "Backspace" });
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const toast = await screen.findByTestId("undo-toast");
    expect(toast.textContent).toContain("Deleted plate and its instance");
    client.parts.mockImplementation(async () => LIST().map((p) => ({ ...p, metrics: METRICS })));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(client.restorePart).toHaveBeenCalledWith("plate", "plate.42"));
    await waitFor(() => expect(names()).toContain("0plate"));
    expect(screen.queryByTestId("undo-toast")).toBeNull();
  });

  it("Remove from assembly takes the instances out, keeps the part, and Undo puts them back", async () => {
    parts.value = LIST().map((p) => ({ ...p, metrics: METRICS }));
    assemblyOf.value = { instances: [{ id: "i1", part_id: "plate" }, { id: "i2", part_id: "plate" }], joints: [] };
    render(<LeftPane />);
    fireEvent.contextMenu(rowEl("frame"), { clientX: 10, clientY: 10 });
    expect(screen.queryByRole("menuitem", { name: /Remove from assembly/ })).toBeNull();      // not in the assembly
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    fireEvent.contextMenu(rowEl("plate"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove from assembly" }));       // no joints: no question
    await waitFor(() => expect(client.removePartsFromAssembly).toHaveBeenCalledWith("b1", ["plate"]));
    expect(client.deletePart).not.toHaveBeenCalled();
    expect((await screen.findByTestId("undo-toast")).textContent).toContain("Removed 2 instances from the assembly");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(client.restoreAssembly).toHaveBeenCalledWith("b1", { instances: [{ id: "i1" }], joints: [] }));
  });

  it("Remove from assembly asks first when joints come loose, naming the parts on their other end", async () => {
    parts.value = LIST().map((p) => ({ ...p, metrics: METRICS }));
    assemblyOf.value = {
      instances: [{ id: "i0", part_id: "frame", name: "frame" }, { id: "i1", part_id: "plate", name: "plate" }, { id: "i9", part_id: "arm_a", name: "left arm" }],
      joints: [{ a: { instance_id: "i0" }, b: { instance_id: "i1" } }, { a: { instance_id: "i1" }, b: { instance_id: "i9" } }],
    };
    render(<PartTree />);
    fireEvent.contextMenu(rowEl("plate"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove from assembly" }));
    const ask = screen.getByTestId("confirm-delete");
    expect(ask.textContent).toContain("Remove 1 instance from the assembly?");
    expect(ask.textContent).toContain("2 joints come loose (frame, left arm)");
    expect(client.removePartsFromAssembly).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(client.removePartsFromAssembly).toHaveBeenCalledWith("b1", ["plate"]));
  });

  it("an agent's approved delete gets the same Undo toast", async () => {
    parts.value = LIST();
    client.parts.mockImplementation(async () => LIST().filter((p) => p.part_id !== "plate"));
    render(<LeftPane />);
    act(() => onAgentDeleted({ type: "part_deleted", run_id: "r1", ts: 1,
      data: { part_id: "plate", project_id: "b1", label: "Plate", undo: "plate.7", removed_instances: ["i1"], by: "agent" } }));
    expect((await screen.findByTestId("undo-toast")).textContent).toContain("An agent deleted Plate and its instance");
    // another build's delete keeps its Undo too, naming the build
    lastDeleted.value = null;
    act(() => onAgentDeleted({ type: "part_deleted", run_id: "r1", ts: 1, data: { part_id: "x", project_id: "b9", label: "X", undo: "x.1" } }));
    expect(lastDeleted.peek()?.text).toBe("An agent deleted X in another build");
    expect(client.parts).toHaveBeenCalledTimes(1);                 // only this build's tree re-read
  });

  it("Export saves STEP / STL and says where; Properties shows mass, volume, size and material", async () => {
    parts.value = LIST().map((p) => ({ ...p, metrics: METRICS }));
    render(<PartTree />);
    fireEvent.contextMenu(rowEl("plate"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Export STEP" }));
    await screen.findByText("Exported plate.step to Downloads");
    expect(client.exportPart).toHaveBeenCalledWith("plate", "step");
    fireEvent.contextMenu(rowEl("plate"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: /Properties/ }));
    const props = screen.getByRole("dialog", { name: "Properties of plate" });
    expect(props.textContent).toContain("12.5 g");
    expect(props.textContent).toContain("40.0 × 20.0 × 5.0 mm");
    expect(props.textContent).toContain("4000 mm³");
    await waitFor(() => expect(props.textContent).toContain("PETG (build default)"));
  });

  it("Add to assembly grounds the first instance of an empty assembly", async () => {
    parts.value = LIST().map((p) => ({ ...p, metrics: METRICS }));
    render(<PartTree />);
    fireEvent.contextMenu(rowEl("plate"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Add to assembly" }));
    await waitFor(() => expect(client.addInstance).toHaveBeenCalledWith("b1", { part_id: "plate", name: "plate", fixed: true }));
    await screen.findByText("Added plate to the assembly");
  });

  it("Measure hands the parts to the measure tool when one is registered, else shows Properties", () => {
    parts.value = LIST().map((p) => ({ ...p, metrics: METRICS }));
    const viewerHook = measureHook.peek();   // the viewer's (U2a) when it is loaded
    measureHook.value = null;
    render(<PartTree />);
    fireEvent.contextMenu(rowEl("plate"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Measure" }));
    expect(screen.getByRole("dialog", { name: "Properties of plate" })).toBeTruthy();
    const seen: string[][] = [];
    measureHook.value = (ids) => seen.push(ids);
    fireEvent.contextMenu(rowEl("frame"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Measure" }));
    expect(seen).toEqual([["frame"]]);
    measureHook.value = viewerHook;
  });

  it("a click re-renders only the rows it changed, and a refetch with the same rows re-renders none", () => {
    const many = Array.from({ length: 40 }, (_, i) => row(`p${String(i).padStart(2, "0")}`, i));
    parts.value = many;
    render(<PartTree />);
    const before = new Map([...document.querySelectorAll("[data-part]")].map((el) => [el.getAttribute("data-part"), el.firstElementChild]));
    const spy = vi.spyOn(Element.prototype, "setAttribute");
    fireEvent.click(rowEl("p10").querySelector(".tree-name") as HTMLElement);
    const touched = new Set(spy.mock.contexts.map((el) => (el as Element).closest("[data-part]")?.getAttribute("data-part")).filter(Boolean));
    expect([...touched].sort()).toEqual(["p10"]);
    spy.mockClear();
    act(() => { parts.value = many.map((p) => ({ ...p })); });       // a part_changed refetch: new objects, same values
    expect(spy.mock.calls.length).toBe(0);
    for (const [id, el] of before) expect(document.querySelector(`[data-part="${id}"]`)?.firstElementChild).toBe(el);
  });

  it("a long list renders only the rows in view (virtualized)", () => {
    parts.value = Array.from({ length: 600 }, (_, i) => row(`p${String(i).padStart(3, "0")}`, i));
    render(<div class="pane-body" style="height: 300px; overflow: auto"><PartTree /></div>);
    const shown = document.querySelectorAll("[data-part]").length;
    expect(shown).toBeGreaterThan(10);
    expect(shown).toBeLessThan(100);
    expect(screen.getByTestId("part-tree").hasAttribute("data-virtual")).toBe(true);
  });

  it("renames with F2 (PATCH label) and toggles visibility", async () => {
    render(<PartTree />);
    fireEvent.keyDown(rowEl("plate").querySelector(".tree-name") as HTMLElement, { key: "F2" });
    const input = screen.getByRole("textbox", { name: "Part name" });
    fireEvent.input(input, { target: { value: "Top plate" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(client.patchPart).toHaveBeenCalledWith("plate", { label: "Top plate" }));
    fireEvent.click(screen.getByRole("button", { name: "Hide arm a in the viewer" }));
    expect(hiddenParts.value.has("arm_a")).toBe(true);
  });

  it("selecting a row selects the part; an agent's edit flashes it", () => {
    render(<PartTree />);
    fireEvent.click(screen.getByRole("button", { name: "plate" }));
    expect(selectedPartId.value).toBe("plate");
    act(() => onPartChanged({ type: "part_changed", run_id: "r1", ts: 1, data: { part_id: "arm_b", version: 2, by: "agent", run_id: "r1", summary: "set wall=4" } }));
    expect(agentFlash.value.has("arm_b")).toBe(true);
    expect(rowEl("arm_b").getAttribute("data-flash")).toBe("");
  });
});
