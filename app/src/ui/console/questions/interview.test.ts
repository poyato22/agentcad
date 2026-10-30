import { describe, expect, it } from "vitest";
import type { AskItem } from "../../../api/types";
import { answerOf, buildAnswers, emptyDraft, move, pick, setText, unanswered } from "./interview";

const item = (over: Partial<AskItem> & { id: string }): AskItem =>
  ({ prompt: over.id, options: [], multi: false, rank: false, allow_text: true, ...over });

describe("interview answer model", () => {
  it("single-select keeps only the last pick and toggles off", () => {
    const q = item({ id: "size", options: ["3in", "5in"] });
    let d = pick(pick(emptyDraft([q]), q, "3in"), q, "5in");
    expect(d.size.picks).toEqual(["5in"]);
    d = pick(d, q, "5in");
    expect(d.size.picks).toEqual([]);
  });

  it("single-select: the last interaction wins, chip or text, and the other is cleared", () => {
    const q = item({ id: "use", options: ["racing", "mapping"] });
    let d = setText(emptyDraft([q]), q, "mapping");
    d = pick(d, q, "racing");                     // chip after text: the text is cleared
    expect(d.use.text).toBe("");
    expect(answerOf(q, d.use)).toBe("racing");
    d = setText(d, q, "survey");                  // text after chip: the chip is cleared
    expect(d.use.picks).toEqual([]);
    expect(answerOf(q, d.use)).toBe("survey");
  });

  it("multi-select sends the picks plus the typed text", () => {
    const q = item({ id: "extras", options: ["GPS", "camera"], multi: true });
    let d = pick(emptyDraft([q]), q, "GPS");
    d = setText(d, q, " lidar ");
    expect(d.extras.picks).toEqual(["GPS"]);      // typing keeps the picks
    expect(answerOf(q, d.extras)).toEqual(["GPS", "lidar"]);
  });

  it("rank is pre-filled in the given order and moves up and down", () => {
    const q = item({ id: "prio", options: ["weight", "impact", "stiffness"], rank: true, allow_text: false });
    let d = emptyDraft([q]);
    expect(answerOf(q, d.prio)).toEqual(["weight", "impact", "stiffness"]);
    d = move(d, q, "stiffness", -1);
    d = move(d, q, "stiffness", -1);
    expect(d.prio.order).toEqual(["stiffness", "weight", "impact"]);
    expect(move(d, q, "stiffness", -1)).toBe(d);  // already first
    d = move(d, q, "weight", 1);
    expect(answerOf(q, d.prio)).toEqual(["stiffness", "impact", "weight"]);
  });

  it("rank only sends a complete order", () => {
    const q = item({ id: "prio", options: ["a", "b", "c"], rank: true, allow_text: false });
    expect(answerOf(q, { picks: [], text: "", order: ["a"] })).toBeNull();
  });

  it("builds the JSON object the core expects and lists what is open", () => {
    const items = [
      item({ id: "use", options: ["freestyle", "cinematic"] }),
      item({ id: "size", options: [] }),
      item({ id: "prio", options: ["weight", "impact"], rank: true }),
    ];
    let d = emptyDraft(items);
    expect(unanswered(items, d).map((q) => q.id)).toEqual(["use", "size"]);
    d = pick(d, items[0], "cinematic");
    d = setText(d, items[1], "5 in");
    expect(unanswered(items, d)).toEqual([]);
    expect(buildAnswers(items, d)).toEqual({ use: "cinematic", size: "5 in", prio: ["weight", "impact"] });
  });
});
