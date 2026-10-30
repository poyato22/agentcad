// @vitest-environment node
import { describe, expect, it } from "vitest";
import { toast } from "../topbar/stopAll";
import { routedNotesText, showRoutedNotes } from "./routedNotes";

describe("routed notes (POST /runs → the build's lead)", () => {
  it("are said, as text, in the top bar's toast; none, nothing", () => {
    expect(routedNotesText({ notes: [] })).toBeNull();
    expect(routedNotesText({})).toBeNull();
    const notes = ["model_ref ignored: the build's lead runs on claude-code:opus", "scope ignored: the build's lead keeps the scope it was started with"];
    expect(routedNotesText({ notes })).toBe(`Sent to the build's lead · ${notes[0]} · ${notes[1]}`);
    showRoutedNotes({ notes });
    expect(toast.value).toBe(routedNotesText({ notes }));
  });
});
