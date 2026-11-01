import { afterEach, describe, expect, it, vi } from "vitest";
import { activeBuildId } from "./builds";
import { canRedo, canUndo, clearUndo, inTextField, installUndoKeys, pushUndo, redo, topUndo, undo, undoToast, UNDO_DEPTH } from "./undo";

afterEach(() => { clearUndo(); vi.useRealTimers(); });

function counter() {
  const log: string[] = [];
  return { log, cmd: (n: string) => ({ label: n, undo: () => { log.push(`undo ${n}`); }, redo: () => { log.push(`redo ${n}`); } }) };
}

describe("undo stack (E11)", () => {
  it("undoes newest first, redoes in order, and a new action clears redo", async () => {
    const { log, cmd } = counter();
    pushUndo(cmd("a")); pushUndo(cmd("b"));
    await undo(); await undo();
    expect(log).toEqual(["undo b", "undo a"]);
    expect(undoToast.value?.text).toBe("Undid: a");
    await redo();
    expect(log.at(-1)).toBe("redo a");
    expect(undoToast.value?.text).toBe("Redid: a");
    pushUndo(cmd("c"));
    expect(canRedo()).toBe(false);
    expect(topUndo()?.label).toBe("c");
  });

  it("says so when there is nothing to undo, and a failed undo leaves the stack with its reason", async () => {
    expect(await undo()).toBe(false);
    expect(undoToast.value?.text).toBe("Nothing to undo");
    pushUndo({ label: "Rename Arm", undo: () => { throw new Error("gone"); }, redo: () => {} });
    expect(await undo()).toBe(false);
    expect(undoToast.value).toMatchObject({ text: "Couldn't undo Rename Arm: gone", error: true });
    expect(canUndo()).toBe(false);
  });

  it("keeps a bounded history", () => {
    const { cmd } = counter();
    for (let i = 0; i < UNDO_DEPTH + 5; i++) pushUndo(cmd(`${i}`));
    let n = 0;
    while (canUndo()) { n++; topUndo(); void undo(); break; }
    expect(n).toBe(1);
  });

  it("⌘Z / ⇧⌘Z outside text fields; a text field keeps its own undo; never under a dialog", async () => {
    const { log, cmd } = counter();
    pushUndo(cmd("a"));
    let modal = false;
    const off = installUndoKeys({ trusted: () => true, modal: () => modal });
    const key = (target: EventTarget, shift = false) => {
      const e = new KeyboardEvent("keydown", { key: "z", code: "KeyZ", metaKey: true, shiftKey: shift, bubbles: true, cancelable: true });
      target.dispatchEvent(e);
      return e;
    };
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    expect(key(input).defaultPrevented).toBe(false);
    input.blur();
    modal = true;
    expect(key(document.body).defaultPrevented).toBe(false);
    modal = false;
    expect(key(document.body).defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(log).toEqual(["undo a"]));
    key(document.body, true);
    await vi.waitFor(() => expect(log).toEqual(["undo a", "redo a"]));
    off();
    input.remove();
  });

  it("never runs a command in another build, and a no-op says nothing", async () => {
    const { log, cmd } = counter();
    activeBuildId.value = "b1";
    pushUndo(cmd("a"));
    activeBuildId.value = "b2";
    expect(await undo()).toBe(false);
    expect(log).toEqual([]);
    activeBuildId.value = "b1";
    undoToast.value = null;
    pushUndo({ label: "Hide a part", undo: () => false, redo: () => true });
    expect(await undo()).toBe(false);
    expect(undoToast.value).toBeNull();
    activeBuildId.value = null;
  });

  it("knows a text field from a button", () => {
    const b = document.createElement("button");
    const t = document.createElement("textarea");
    const r = document.createElement("input"); r.type = "range";
    expect(inTextField(b)).toBe(false);
    expect(inTextField(t)).toBe(true);
    expect(inTextField(r)).toBe(false);
  });
});
