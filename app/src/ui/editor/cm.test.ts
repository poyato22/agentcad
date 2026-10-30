import { undo } from "@codemirror/commands";
import { describe, expect, it, vi } from "vitest";
import { createEditor, setDoc } from "./cm";

describe("code editor document", () => {
  it("a reload from the core is not an edit and can't be undone back to the dropped text", () => {
    const onChange = vi.fn();
    const view = createEditor(document.createElement("div"), "mine", { onChange, onSave: () => {} });
    view.dispatch({ changes: { from: 4, insert: " edited" } });     // the user's typing
    expect(onChange).toHaveBeenLastCalledWith("mine edited");
    onChange.mockClear();
    setDoc(view, "theirs");                                          // Load theirs
    expect(onChange).not.toHaveBeenCalled();
    undo(view);                                                      // ⌘Z
    expect(view.state.doc.toString()).not.toBe("mine edited");
    view.destroy();
  });
});
