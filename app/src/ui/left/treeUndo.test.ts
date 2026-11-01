// Plan 10 E11: the user's tree edits go on the undo stack and come back through the core's routes.
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { PartSummary } from "../../api/types";
import { activeBuildId } from "../../state/builds";
import { bindClient, parts, resetStore } from "../../state/store";
import { clearUndo, redo, undo, undoToast } from "../../state/undo";
import { deleteParts, renamePart, setColor } from "./treeActions";
import { hiddenParts, setHidden } from "./visibility";

const part = (id: string, extra: Partial<PartSummary> = {}): PartSummary =>
  ({ part_id: id, vars: {}, metrics: null, mtime: 1, label: id, color: null, project_id: "b1", ...extra }) as unknown as PartSummary;

afterEach(() => { clearUndo(); resetStore(); activeBuildId.value = null; });

describe("tree edits are undoable (E11)", () => {
  it("rename: ⌘Z names it back, ⇧⌘Z renames again", async () => {
    const patchPart = vi.fn(async (id: string, b: { label?: string }) => part(id, { label: b.label }));
    bindClient({ patchPart } as unknown as CoreClient);
    parts.value = [part("arm", { label: "Arm" })];
    expect(await renamePart("arm", "Upper arm")).toBe(true);
    await undo();
    expect(patchPart).toHaveBeenLastCalledWith("arm", { label: "Arm" });
    expect(parts.value[0].label).toBe("Arm");
    expect(undoToast.value?.text).toBe("Undid: Rename Arm");
    await redo();
    expect(parts.value[0].label).toBe("Upper arm");
  });

  it("color: undo puts the old look back", async () => {
    const patchPart = vi.fn(async (id: string, b: { color?: string | null }) => part(id, { color: b.color ?? null }));
    bindClient({ patchPart } as unknown as CoreClient);
    parts.value = [part("arm", { color: "#111111" })];
    await setColor("arm", "#ff0000");
    await undo();
    expect(patchPart).toHaveBeenLastCalledWith("arm", { color: "#111111", appearance: null });
    expect(parts.value[0].color).toBe("#111111");
  });

  it("delete: undo restores with the core's token; redo deletes again", async () => {
    activeBuildId.value = "b1";
    const deletePart = vi.fn(async () => ({ removed_instances: [], undo: "tok1" }));
    const restorePart = vi.fn(async () => ({}));
    const listParts = vi.fn(async () => [part("arm")]);
    bindClient({ deletePart, restorePart, listParts, parts: listParts } as unknown as CoreClient);
    parts.value = [part("arm")];
    await deleteParts(["arm"]);
    await undo();
    expect(restorePart).toHaveBeenCalledWith("arm", "tok1");
    await redo();
    expect(deletePart).toHaveBeenCalledTimes(2);
  });

  it("hide / show: undo brings back what was shown", async () => {
    activeBuildId.value = "b1";
    setHidden("arm", true);
    expect(hiddenParts.value.has("arm")).toBe(true);
    await undo();
    expect(hiddenParts.value.has("arm")).toBe(false);
  });
});
