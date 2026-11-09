// The concept cards on the real core's answers (core/tests/fixtures/ui/concept): drawing a
// circle, a lasso and a pin produces normalised marks with comments, Send posts them, Approve
// flips the gate; older versions fold to one line; the pitch picks and mixes.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { conceptDecisionSchema, conceptViewSchema, feedbackSentSchema, pitchChosenSchema, type ConceptView } from "../../api/concept";
import { bindClient } from "../../state/store";
import { resetImageCache } from "../console/cards/critique/images";
import { PitchCards } from "./PitchCards";
import { conceptCardFor } from "./register";
import { SketchCard } from "./SketchCard";
import { SketchReviewRoot } from "./SketchReview";
import { concepts, eventProject, resetConceptState } from "./state";

const DIR = resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/concept");
const real = (name: string): unknown => JSON.parse(readFileSync(resolve(DIR, name), "utf8"));
const VIEW = conceptViewSchema.parse(real("concept_view.json"));
const APPROVED = conceptViewSchema.parse(real("concept_view_approved.json"));
const PITCHING = conceptViewSchema.parse(real("concept_view_pitching.json"));
const PID = VIEW.concept.project_id;

function client(views: ConceptView[]) {
  let i = 0;
  const c = {
    concept: vi.fn(async () => views[Math.min(i++, views.length - 1)]),
    renderImage: vi.fn(async () => new Blob(["png"], { type: "image/png" })),
    sketchFeedback: vi.fn(async () => feedbackSentSchema.parse(real("concept_feedback.json"))),
    approveConcept: vi.fn(async () => conceptDecisionSchema.parse(real("concept_approve.json"))),
    skipConcept: vi.fn(async () => conceptDecisionSchema.parse(real("concept_skip.json"))),
    choosePitch: vi.fn(async () => pitchChosenSchema.parse(real("concept_choose.json"))),
  };
  bindClient(c as unknown as CoreClient);
  return c;
}

beforeEach(() => {
  resetConceptState();
  resetImageCache();
  globalThis.URL.createObjectURL ??= (() => "blob:x") as typeof URL.createObjectURL;
});
afterEach(() => { cleanup(); bindClient(null); });

/** The review overlay, laid out as a 500×500 box at the origin (jsdom has no layout). */
function overlay(): SVGSVGElement {
  const svg = screen.getByTestId("sketch-review").querySelector<SVGSVGElement>("[data-testid=sketch-overlay]")!;
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 500, height: 500, right: 500, bottom: 500, x: 0, y: 0, toJSON: () => ({}) });
  return svg;
}
const at = (x: number, y: number) => ({ clientX: x, clientY: y, button: 0, pointerId: 1 });

async function note(text: string) {
  const box = await screen.findByTestId("mark-comment");
  fireEvent.input(box.querySelector("textarea")!, { target: { value: text } });
  fireEvent.click(screen.getByTestId("mark-done"));
}

describe("SketchCard + SketchReview", () => {
  it("draws a circle, a lasso and a pin as normalised marks and sends them with comments", async () => {
    const c = client([VIEW, VIEW]);
    render(<><SketchCard projectId={PID} sketchId={VIEW.sketches[1].sketch_id} /><SketchReviewRoot /></>);
    await waitFor(() => expect(screen.getByTestId("sketch-card").getAttribute("data-version")).toBe("2"));
    fireEvent.click(screen.getByTestId("sketch-markup"));
    const svg = overlay();
    // circle: centre (250, 150) dragged out 50 px → r = 0.1 of the width
    fireEvent.pointerDown(svg, at(250, 150));
    fireEvent.pointerMove(svg, at(300, 150));
    fireEvent.pointerUp(svg, at(300, 150));
    await note("rounder head");
    // lasso
    fireEvent.click(screen.getByRole("button", { name: /Lasso/ }));
    fireEvent.pointerDown(svg, at(100, 200));
    for (const [x, y] of [[200, 190], [260, 260], [150, 300], [110, 240]]) fireEvent.pointerMove(svg, at(x, y));
    fireEvent.pointerUp(svg, at(110, 240));
    await note("shorter body");
    // pin (P)
    fireEvent.keyDown(window, { key: "p" });
    fireEvent.pointerDown(svg, at(200, 400));
    await note("thicker legs");
    fireEvent.input(screen.getByTestId("review-comment"), { target: { value: "love the stance" } });
    expect(screen.getByTestId("mark-list").querySelectorAll("li")).toHaveLength(3);
    fireEvent.click(screen.getByTestId("review-send"));
    await waitFor(() => expect(c.sketchFeedback).toHaveBeenCalledTimes(1));
    const [sid, body] = c.sketchFeedback.mock.calls[0] as unknown as [string, { marks: Record<string, unknown>[]; comment: string }];
    expect(sid).toBe(VIEW.sketches[1].sketch_id);
    expect(body.comment).toBe("love the stance");
    expect(body.marks[0]).toEqual({ kind: "circle", cx: 0.5, cy: 0.3, r: 0.1, image: 0, comment: "rounder head" });
    expect(body.marks[1]).toMatchObject({ kind: "lasso", image: 0, comment: "shorter body" });
    expect((body.marks[1].points as number[][])[0]).toEqual([0.2, 0.4]);
    expect(body.marks[2]).toEqual({ kind: "pin", x: 0.4, y: 0.8, image: 0, comment: "thicker legs" });
    expect(body.marks.every((m) => !("id" in m))).toBe(true);
    await waitFor(() => expect(screen.queryByTestId("sketch-review")).toBeNull());
    expect((await screen.findByRole("status")).textContent).toBe("Sketch feedback v1: 3 marks and a comment… sent to the agent");
  });

  it("a cancelled pointer drops its stroke; a later pointerup commits nothing", async () => {
    client([VIEW]);
    render(<><SketchCard projectId={PID} sketchId={null} /><SketchReviewRoot /></>);
    fireEvent.click(await screen.findByTestId("sketch-markup"));
    const svg = overlay();
    fireEvent.pointerDown(svg, at(250, 150));
    fireEvent.pointerMove(svg, at(320, 150));
    fireEvent.pointerCancel(svg, at(320, 150));
    fireEvent.pointerUp(svg, at(330, 150));
    fireEvent.click(screen.getByRole("button", { name: /Lasso/ }));
    fireEvent.pointerDown(svg, at(100, 200));
    fireEvent.pointerMove(svg, at(200, 190));
    fireEvent(svg, new Event("lostpointercapture"));
    fireEvent.pointerUp(svg, at(260, 260));
    expect(screen.queryByTestId("mark-comment")).toBeNull();
    expect(screen.queryByTestId("mark-list")).toBeNull();
  });

  it("Approve posts the version and the card flips to Approved", async () => {
    const c = client([VIEW, APPROVED]);
    render(<SketchCard projectId={PID} sketchId={null} />);
    fireEvent.click(await screen.findByTestId("sketch-approve"));
    await waitFor(() => expect(c.approveConcept).toHaveBeenCalledWith(PID, VIEW.sketches[1].sketch_id));
    await waitFor(() => expect(screen.getByTestId("sketch-status").textContent).toMatch(/^Approved v\d by you \d\d:\d\d$/));
    expect(screen.queryByTestId("sketch-approve")).toBeNull();
  });

  it("an approved sketch opened again offers no second Approve", async () => {
    const c = client([APPROVED]);
    render(<><SketchCard projectId={PID} sketchId={null} /><SketchReviewRoot /></>);
    expect(screen.queryByTestId("sketch-approve")).toBeNull();
    fireEvent.click(await screen.findByTestId("sketch-open"));
    expect((await screen.findByTestId("review-decided")).textContent).toMatch(/^Approved/);
    expect(screen.queryByTestId("review-approve")).toBeNull();
    expect(screen.queryByTestId("review-send")).toBeNull();
    expect(c.approveConcept).not.toHaveBeenCalled();
  });

  it("an older version's card folds to one line; the newest holds the chips and compare", async () => {
    client([VIEW]);
    render(<>{conceptCardFor({ kind: "concept_sketch", ref: { sketch_id: VIEW.sketches[0].sketch_id }, projectId: PID })}
      {conceptCardFor({ kind: "concept_sketch", ref: { sketch_id: VIEW.sketches[1].sketch_id }, projectId: PID })}</>);
    expect((await screen.findByTestId("sketch-card-old")).textContent).toContain("Concept sketch v1");
    expect(screen.getAllByTestId("sketch-version").map((b) => b.textContent)).toEqual(["v1", "v2"]);
    fireEvent.click(screen.getByTestId("sketch-compare"));
    expect(document.querySelectorAll(".sk-stage.is-compare .sk-fig")).toHaveLength(2);
    expect(conceptCardFor({ kind: "part_created", ref: {}, projectId: PID })).toBeNull();
  });

  it("concept events name their build; others are ignored", () => {
    expect(eventProject({ type: "concept_updated", run_id: null, data: { project_id: PID }, ts: 0 })).toBe(PID);
    expect(eventProject({ type: "part_changed", run_id: null, data: { project_id: PID }, ts: 0 })).toBeNull();
    expect(eventProject({ type: "concept_sketch", run_id: null, data: { project_id: "../x" }, ts: 0 })).toBeNull();
  });
});

describe("PitchCards", () => {
  it("shows every option with its badges, and mixes two", async () => {
    const c = client([PITCHING, PITCHING]);
    render(<PitchCards projectId={PID} pitchId={PITCHING.pitches[0].pitch_id} />);
    const opts = await screen.findAllByTestId("pitch-option");
    expect(opts.map((o) => o.getAttribute("data-option"))).toEqual(PITCHING.pitches[0].options.map((o) => o.name));
    const rec = PITCHING.pitches[0].options.find((o) => o.recommended)!;
    expect(screen.getByText("Recommended").closest("[data-option]")!.getAttribute("data-option")).toBe(rec.name);
    expect(screen.getByText("Baseline")).toBeTruthy();
    fireEvent.click(screen.getByTestId("pitch-mix"));
    const [a, b] = opts;
    fireEvent.click(a.querySelector("input[type=checkbox]")!);
    fireEvent.input(a.querySelector(".sk-take")!, { target: { value: "body" } });
    fireEvent.click(b.querySelector("input[type=checkbox]")!);
    fireEvent.input(b.querySelector(".sk-take")!, { target: { value: "legs" } });
    fireEvent.click(screen.getByTestId("pitch-send-mix"));
    await waitFor(() => expect(c.choosePitch).toHaveBeenCalledTimes(1));
    expect(c.choosePitch.mock.calls[0]).toEqual([PITCHING.pitches[0].pitch_id, {
      mix: [{ option: a.getAttribute("data-option"), take: "body" }, { option: b.getAttribute("data-option"), take: "legs" }], comment: "" }]);
    expect(concepts.value[PID]).toBeTruthy();
  });
});
