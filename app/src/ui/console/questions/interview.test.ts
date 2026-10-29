import { describe, expect, it } from "vitest";
import type { AskItem } from "../../../api/types";
import { batchAnswer, emptyAnswers, isComplete, itemValue, togglePick } from "./interview";

const item = (over: Partial<AskItem> & { id: string }): AskItem =>
  ({ prompt: over.id, options: [], multi: false, rank: false, allow_text: true, ...over });

describe("interview answer model", () => {
  it("single-select keeps only the last pick", () => {
    const q = item({ id: "size", options: ["3in", "5in"] });
    let picks = togglePick(q, [], "3in");
    picks = togglePick(q, picks, "5in");
    expect(picks).toEqual(["5in"]);
    expect(togglePick(q, picks, "5in")).toEqual([]);     // toggling off
  });

  it("multi-select keeps several", () => {
    const q = item({ id: "mats", options: ["PLA", "PETG", "ABS"], multi: true });
    const picks = togglePick(q, togglePick(q, [], "PLA"), "ABS");
    expect(picks).toEqual(["PLA", "ABS"]);
  });

  it("rank preserves click order and sends an array", () => {
    const q = item({ id: "prio", options: ["weight", "impact", "stiffness"], rank: true });
    const picks = togglePick(q, togglePick(q, [], "impact"), "weight");
    expect(picks).toEqual(["impact", "weight"]);
    expect(itemValue(q, { picks, text: "" })).toEqual(["impact", "weight"]);
  });

  it("free text wins over picks", () => {
    const q = item({ id: "size", options: ["3in", "5in"] });
    expect(itemValue(q, { picks: ["3in"], text: " 6in " })).toBe("6in");
  });

  it("builds the JSON object the core expects and tracks completeness", () => {
    const items = [
      item({ id: "use", options: ["freestyle", "cinematic"] }),
      item({ id: "prio", options: ["weight", "impact"], rank: true }),
    ];
    const answers = emptyAnswers(items);
    expect(isComplete(items, answers)).toBe(false);
    answers.use = { picks: ["cinematic"], text: "" };
    answers.prio = { picks: ["impact", "weight"], text: "" };
    expect(isComplete(items, answers)).toBe(true);
    expect(batchAnswer(items, answers)).toEqual({ use: "cinematic", prio: ["impact", "weight"] });
  });
});
