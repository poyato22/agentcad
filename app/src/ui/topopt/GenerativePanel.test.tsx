// The Generative design panel and result card against the real core's answers (core
// scripts/dump_topopt_fixtures.py): the setup posts the right body, the run follows
// topopt_progress events and the record, the grid shows CalculiX numbers with ✓ / ✗, Accept
// posts, and an agent's topopt_run row carries the card.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { topoptAcceptResultSchema, topoptRecordSchema, type TopoptRecord } from "../../api/topopt";
import type { MisesEvent, PartSummary, Step } from "../../api/types";
import { parts, resetStore } from "../../state/store";
import { GenerativeRoot, resetGenerativeForms } from "./GenerativePanel";
import { TopoptController } from "./state";
import { topoptIdOf, TopoptRunCard } from "./TopoptCard";

const DIR = resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(DIR, n), "utf8"));
const START = topoptRecordSchema.parse(read("topopt_start.json"));
const RUNNING = topoptRecordSchema.parse(read("topopt_running.json"));
const DONE = topoptRecordSchema.parse(read("topopt_record.json"));
const ACCEPT = topoptAcceptResultSchema.parse(read("topopt_accept.json"));
const EVENTS = readFileSync(resolve(DIR, "topopt_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);
const STEPS = read("topopt_agent_steps.json") as Step[];

const BLOCK: PartSummary = { part_id: "block", vars: {}, mtime: 1, metrics: {
  valid: true, volume_mm3: 3072, area_mm2: 1280, mass_g: 3.8, bbox: { min: [0, 0, 0], max: [24, 16, 8], size: [24, 16, 8] },
  center_of_mass: [12, 8, 4], faces: 6, edges: 12, holes: [], solids: 1,
  face_list: [{ id: "f0", type: "plane", center: [0, 8, 4], normal: [-1, 0, 0], area: 128 }],
} };

function client(states: TopoptRecord[] = [RUNNING, DONE]) {
  let i = 0;
  return {
    topoptStart: vi.fn().mockResolvedValue(START),
    topoptGet: vi.fn(async () => states[Math.min(i++, states.length - 1)]),
    topoptList: vi.fn().mockResolvedValue([DONE]),
    topoptCancel: vi.fn().mockResolvedValue({ status: "cancelling" }),
    topoptAccept: vi.fn().mockResolvedValue(ACCEPT),
    topoptGlb: vi.fn().mockRejectedValue(new Error("no GLB in jsdom")),
    topoptEvoGlb: vi.fn().mockRejectedValue(new Error("no GLB in jsdom")),
    topoptRender: vi.fn().mockResolvedValue(new Blob(["png"], { type: "image/png" })),
    partGlb: vi.fn().mockRejectedValue(new Error("no GLB in jsdom")),
  };
}

let c: ReturnType<typeof client>;
let ctl: TopoptController;
const settle = async () => { await act(async () => { await new Promise((r) => setTimeout(r, 20)); }); };

beforeEach(() => {
  resetStore();
  parts.value = [BLOCK];
  c = client();
  ctl = new TopoptController(() => c);
});
afterEach(() => { ctl.reset(); resetGenerativeForms(); cleanup(); });

describe("generative design panel", () => {
  it("posts the setup and follows the run to its variant grid", async () => {
    ctl.open("block");
    render(<GenerativeRoot controller={ctl} />);
    const dlg = screen.getByRole("dialog", { name: "Generative design" });
    expect(within(dlg).getByText(/block/)).toBeTruthy();
    // held face x-min, load on x-max
    fireEvent.change(within(dlg).getByLabelText("Held face 1"), { target: { value: "x-min" } });
    fireEvent.change(within(dlg).getByLabelText("Load 1 face"), { target: { value: "x-max" } });
    fireEvent.input(within(dlg).getByLabelText("Load 1 force (N)"), { target: { value: "40" } });
    fireEvent.input(within(dlg).getByLabelText("Number of variants"), { target: { value: "2" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "Generate" }));
    await settle();
    const body = c.topoptStart.mock.calls[0][0];
    expect(body.fixtures).toEqual([{ kind: "fixed", face: "x-min" }]);
    expect(body.load_cases[0].loads).toEqual([{ face: "x-max", newtons: 40, direction: "-z" }]);
    expect(body.sweep.volume_fraction).toHaveLength(2);
    // the run view: live progress from events, then the done record
    expect(screen.getByTestId("gd-run")).toBeTruthy();
    act(() => { for (const ev of EVENTS.filter((e) => e.type === "topopt_progress").slice(0, 12)) ctl.onEvent({ ...ev, data: { ...ev.data, topopt_id: START.topopt_id } }); });
    expect(screen.getByTestId("gd-status").textContent).toMatch(/Running/);
    await act(async () => { await ctl.refresh(START.topopt_id); await ctl.refresh(START.topopt_id); });
    await waitFor(() => expect(screen.getByTestId("gd-run").getAttribute("data-status")).toBe("done"));
    const cards = screen.getByTestId("gd-variants").querySelectorAll("[data-variant]");
    expect(cards).toHaveLength(DONE.variants.length);
    // ranked: the recommended (rank 1) first, with CalculiX's numbers and ✓
    const first = cards[0] as HTMLElement;
    expect(first.dataset.variant).toBe(DONE.recommended);
    expect(within(first).getByText("Recommended")).toBeTruthy();
    expect(within(first).getByTestId("gd-verdict").textContent).toBe("✓ Verified");
    const best = DONE.variants.find((v) => v.id === DONE.recommended);
    expect(within(first).getByText(`${Number(best?.verify?.max_deflection_mm?.toPrecision(3))} mm`)).toBeTruthy();
    // Accept the recommended variant as a new part
    fireEvent.click(within(first).getByRole("button", { name: /Accept/ }));
    await settle();
    expect(c.topoptAccept).toHaveBeenCalledWith(START.topopt_id, { variant: DONE.recommended, mode: "new" });
    expect(screen.getByRole("status").textContent).toContain(ACCEPT.part_id);
  });

  it("refuses an incomplete setup before posting", async () => {
    ctl.open("block");
    render(<GenerativeRoot controller={ctl} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove held face 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await settle();
    expect(c.topoptStart).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/Hold the part/);
  });

  it("shows the core's refusal", async () => {
    const { CoreError } = await import("../../api/client");
    c.topoptStart.mockRejectedValueOnce(new CoreError("invalid_input", "no face f9 on this part", { hint: "face ids run f0..f5" }));
    ctl.open("block");
    render(<GenerativeRoot controller={ctl} />);
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe("no face f9 on this part (face ids run f0..f5)");
  });

  it("closes on Escape and cancels a running run", async () => {
    c = client([RUNNING]);
    ctl = new TopoptController(() => c);
    ctl.open("block", RUNNING.topopt_id);
    render(<GenerativeRoot controller={ctl} />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await settle();
    expect(c.topoptCancel).toHaveBeenCalledWith(RUNNING.topopt_id);
    fireEvent.keyDown(window, { key: "Escape" });
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("accepting a failing variant", () => {
  it("asks once more and says why", async () => {
    const failing = topoptRecordSchema.parse({ ...DONE, recommended: null,
      variants: DONE.variants.map((v) => ({ ...v, passes: false })) });
    c = client([failing]);
    ctl = new TopoptController(() => c);
    ctl.open("block", failing.topopt_id);
    render(<GenerativeRoot controller={ctl} />);
    await settle();
    const card = screen.getByTestId("gd-variants").querySelector("[data-variant]") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: /^Accept / }));
    expect(c.topoptAccept).not.toHaveBeenCalled();
    expect(within(card).getByRole("alert").textContent).toMatch(/fails the safety factor/);
    fireEvent.click(within(card).getByRole("button", { name: /anyway/ }));
    await settle();
    expect(c.topoptAccept).toHaveBeenCalledTimes(1);
  });
});

describe("face picking", () => {
  it("highlights a planar face's own triangles", async () => {
    const { faceTriangles } = await import("./FacePickView");
    // two triangles on z=0 facing -z, one on x=10 facing +x
    const pos = new Float32Array([0, 0, 0, 0, 10, 0, 10, 0, 0, 10, 0, 0, 0, 10, 0, 10, 10, 0, 10, 0, 0, 10, 10, 0, 10, 0, 5]);
    const bottom = faceTriangles(pos, { id: "f0", type: "plane", center: [5, 5, 0], normal: [0, 0, -1], area: 100 }, 20);
    expect(bottom.length).toBe(18);
    const side = faceTriangles(pos, { id: "f1", type: "plane", center: [10, 5, 2.5], normal: [1, 0, 0], area: 50 }, 20);
    expect(side.length).toBe(9);
  });

  it("each face has a Pick button that arms the 3D view, and Escape disarms before closing", async () => {
    ctl.open("block");
    render(<GenerativeRoot controller={ctl} />);
    const pick = screen.getByRole("button", { name: "Pick held face 1 on the part" });
    fireEvent.click(pick);
    expect(screen.getByRole("button", { name: /Picking held face 1/ }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(window, { key: "Escape" });
    await settle();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Pick held face 1 on the part" }).getAttribute("aria-pressed")).toBe("false");
  });
});

describe("result card", () => {
  it("an agent's topopt_run row names its run", () => {
    const call = STEPS.find((s) => s.kind === "tool_call");
    const result = STEPS.find((s) => s.kind !== "tool_call" && s.payload.name === "topopt_run");
    expect(call && result).toBeTruthy();
    const id = topoptIdOf({ kind: "call", callId: "t1", name: "topopt_run", input: {}, result: result ?? null, ok: true, summary: null, startedTs: 0, endedTs: 1, hint: null, attached: [] });
    expect(id).toMatch(/^to_[0-9a-f]{12}$/);
    expect(topoptIdOf({ kind: "call", callId: "t2", name: "sim_run", input: {}, result: result ?? null, ok: true, summary: null, startedTs: 0, endedTs: 1, hint: null, attached: [] })).toBeNull();
  });

  it("shows the variant grid and opens the panel", async () => {
    c = client([DONE]);
    ctl = new TopoptController(() => c);
    render(<TopoptRunCard id={DONE.topopt_id} partId="block" controller={ctl} />);
    await settle();
    const card = screen.getByTestId("topopt-card");
    expect(card.dataset.status).toBe("done");
    expect(within(card).getAllByTestId("gd-verdict").map((e) => e.textContent)).toEqual(DONE.variants.map(() => "✓ Verified"));
    fireEvent.click(within(card).getByRole("button", { name: "Open the generative design run" }));
    expect(ctl.panel.value).toEqual({ partId: "block", topoptId: DONE.topopt_id });
  });
});

describe("the density's evolution", () => {
  it("replays the kept iterations of the shown variant, then returns to its result", async () => {
    const base = `/topopt/${DONE.topopt_id}/variants/${DONE.recommended}`;
    const evo = [`${base}/evo_005.glb`, `${base}/evo_010.glb`, `${base}/evo_030.glb`];
    const rec = topoptRecordSchema.parse({ ...DONE, variants: DONE.variants.map((v) => (v.id === DONE.recommended ? { ...v, urls: { ...v.urls, evolution: evo } } : v)) });
    c = client([rec]);
    ctl = new TopoptController(() => c);
    ctl.open("block", rec.topopt_id);
    render(<GenerativeRoot controller={ctl} />);
    await act(async () => { await ctl.refresh(rec.topopt_id); });
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Replay the density" }));
    await settle();
    // (jsdom has no WebGL, so the preview itself never loads: follow the frames by the status line)
    const status = () => screen.getByTestId("gd-status").textContent ?? "";
    expect(status()).toMatch(/iteration 5 · frame 1 of 3/);
    await waitFor(() => expect(status()).toMatch(/iteration 10 · frame 2 of 3/), { timeout: 4000 });
    await waitFor(() => expect(status()).toMatch(/iteration 30 · frame 3 of 3/), { timeout: 4000 });
    await waitFor(() => expect(status()).not.toMatch(/iteration/), { timeout: 4000 });  });

  it("offers no replay when the run kept no iterations", async () => {
    c = client([DONE]);
    ctl = new TopoptController(() => c);
    ctl.open("block", DONE.topopt_id);
    render(<GenerativeRoot controller={ctl} />);
    await act(async () => { await ctl.refresh(DONE.topopt_id); });
    await settle();
    expect(screen.getByTestId("gd-run")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Replay the density" })).toBeNull();
  });
});
