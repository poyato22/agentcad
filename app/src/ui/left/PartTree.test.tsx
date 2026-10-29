import { act, cleanup, createEvent, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { CoreError } from "../../api/client";
import type { PartSummary } from "../../api/types";
import { activeBuildId, resetBuilds } from "../../state/builds";
import { agentFlash, onPartChanged, resetPartEdits } from "../../state/partEdits";
import { bindClient, parts, resetStore, selectedPartId } from "../../state/store";
import { PartTree } from "./PartTree";
import { hiddenParts, resetVisibility } from "./visibility";

const row = (id: string, order: number, parent: string | null = null, color: string | null = null): PartSummary =>
  ({ part_id: id, id, label: id, vars: {}, metrics: null, mtime: 1, parent_id: parent, order, kind: "part", version: 1, project_id: "b1", color });

// frame ─┬ arm_a
//        └ arm_b
// plate
const LIST = () => [row("frame", 0), row("arm_a", 0, "frame"), row("arm_b", 1, "frame"), row("plate", 1)];

let client: { reorderParts: ReturnType<typeof vi.fn>; patchPart: ReturnType<typeof vi.fn> };

beforeEach(() => {
  resetStore(); resetBuilds(); resetPartEdits(); resetVisibility();
  activeBuildId.value = "b1";
  parts.value = LIST();
  client = {
    reorderParts: vi.fn(async (_b: string, parent: string | null, ids: string[]) => parts.peek().map((p) => {
      const i = ids.indexOf(p.part_id);
      return i < 0 ? p : { ...p, parent_id: parent, order: i };
    })),
    patchPart: vi.fn(async (id: string, patch: Record<string, unknown>) => ({ ...parts.peek().find((p) => p.part_id === id), ...patch })),
  };
  bindClient(client as unknown as CoreClient);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const rowEl = (id: string) => document.querySelector<HTMLElement>(`[data-part="${id}"] .tree-row`) as HTMLElement;
const names = () => [...document.querySelectorAll("[data-part]")].map((el) => `${el.getAttribute("data-depth")}${el.getAttribute("data-part")}`);

function dt(): DataTransfer {
  const data = new Map<string, string>();
  return {
    types: ["application/x-mises-part"], setData: (k: string, v: string) => data.set(k, v), getData: (k: string) => data.get(k) ?? "",
    dropEffect: "none", effectAllowed: "all",
  } as unknown as DataTransfer;
}

function drag(from: string, to: string, where: "top" | "middle" | "bottom") {
  const transfer = dt();
  const target = rowEl(to);
  vi.spyOn(target, "getBoundingClientRect").mockReturnValue({ top: 100, height: 20, bottom: 120, left: 0, right: 200, width: 200, x: 0, y: 100, toJSON: () => ({}) });
  const y = where === "top" ? 101 : where === "bottom" ? 119 : 110;
  // jsdom has no DragEvent: the pointer position is set on the event by hand
  const at = (ev: Event) => { Object.defineProperty(ev, "clientY", { value: y }); return ev; };
  fireEvent.dragStart(rowEl(from), { dataTransfer: transfer });
  fireEvent(target, at(createEvent.dragOver(target, { dataTransfer: transfer })));
  return { transfer, target, drop: () => fireEvent(target, at(createEvent.drop(target, { dataTransfer: transfer }))) };
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

  it("the Move menu does the same, and disables a move that isn't possible", async () => {
    render(<PartTree />);
    fireEvent.click(screen.getByRole("button", { name: "Move or edit plate" }));
    const menu = screen.getByRole("menu", { name: "plate actions" });
    expect((screen.getByRole("menuitem", { name: /Move down/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(menu.contains(document.activeElement)).toBe(true);
    fireEvent.click(screen.getByRole("menuitem", { name: /Move into the part above/ }));
    await waitFor(() => expect(client.reorderParts).toHaveBeenCalledWith("b1", "frame", ["arm_a", "arm_b", "plate"]));
  });

  it("a refused move is put back with the core's reason", async () => {
    client.reorderParts.mockRejectedValueOnce(new CoreError("invalid_input", "that move would create a cycle in the part tree", { status: 400 }));
    render(<PartTree />);
    fireEvent.keyDown(rowEl("frame").querySelector(".tree-name") as HTMLElement, { key: "ArrowDown", altKey: true });
    await screen.findByText("Couldn't move the part: that move would create a cycle in the part tree");
    expect(names()).toEqual(["0frame", "1arm_a", "1arm_b", "0plate"]);
  });

  it("picks a color from the palette or a custom hex (PATCH color)", async () => {
    render(<PartTree />);
    fireEvent.click(screen.getByRole("button", { name: /Color of plate/ }));
    fireEvent.click(screen.getByRole("radio", { name: "Teal" }));
    await waitFor(() => expect(client.patchPart).toHaveBeenCalledWith("plate", { color: "#2f9a9c" }));
    expect(parts.value.find((p) => p.part_id === "plate")?.color).toBe("#2f9a9c");
    fireEvent.click(screen.getByRole("button", { name: /Color of frame/ }));
    fireEvent.input(screen.getByRole("textbox", { name: "Hex color" }), { target: { value: "#ABC" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(client.patchPart).toHaveBeenCalledWith("frame", { color: "#aabbcc" }));
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
