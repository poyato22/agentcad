import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { PartSummary } from "../../api/types";
import { bindClient, inflight, parts, resetStore, runs, selectedPartId, steps } from "../../state/store";
import type { DesignViewerHandle } from "./scene";
import { noteMyEdit, resetPartEdits } from "../../state/partEdits";
import { Viewer, changingRunLabel, highlighted } from "./Viewer";
import { requestModify, viewerTool } from "./tool";
import { activeBuildId } from "../../state/builds";
import { resetPins } from "./pins";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// The core's real GLB fixture. Copied into the test realm's ArrayBuffer: jsdom's global differs from Node's
// and GLTFLoader checks `instanceof ArrayBuffer`.
const fixtureBytes = (): ArrayBuffer =>
  new Uint8Array(readFileSync(resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/mount_plate.glb"))).buffer as ArrayBuffer;

const handle = vi.hoisted(() => ({
  setPart: vi.fn(), setParts: vi.fn(), setMode: vi.fn(), setHighlight: vi.fn(), fit: vi.fn(), resize: vi.fn(), dispose: vi.fn(),
  pick: vi.fn(), toScreen: vi.fn((_p: [number, number, number]): [number, number] => [0, 0]), setMarker: vi.fn(), setMarkers: vi.fn(),
  onView: vi.fn(() => () => undefined),
}));
vi.mock("./scene", async (orig) => ({ ...(await orig<typeof import("./scene")>()), createViewer: (): DesignViewerHandle => handle }));

const mk = (mtime: number): PartSummary => ({ part_id: "mount_plate", vars: {}, metrics: null, mtime });
const bind = (partGlb: unknown) => bindClient({ partGlb } as unknown as CoreClient);

beforeEach(() => { resetStore(); resetPartEdits(); resetPins(); viewerTool.value = "select"; for (const f of Object.values(handle)) f.mockClear(); });
afterEach(() => { cleanup(); resetStore(); activeBuildId.value = null; });

const treeParts = (): PartSummary[] => JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../test/fixtures/core-ui/parts_tree.json"), "utf8")) as PartSummary[];

describe("Viewer", () => {
  it("empty state shows the figure label and prompt without calling the core", () => {
    const partGlb = vi.fn();
    bind(partGlb);
    const { container } = render(<Viewer />);
    expect(container.textContent).toContain("FIG. 1 — DESIGN");
    expect(container.textContent).toContain("NO PART · ask an agent to create one");
    expect(partGlb).not.toHaveBeenCalled();
  });

  it("hands the loaded scene to the viewer and highlights while an agent changes the part", async () => {
    bind(vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' }));
    parts.value = [mk(1)];
    render(<Viewer />);
    await waitFor(() => expect(handle.setParts.mock.calls.some(([list]) => (list as { id: string }[]).some((i) => i.id === "mount_plate"))).toBe(true));
    expect(handle.setHighlight).toHaveBeenLastCalledWith(false);
    act(() => { inflight.value = new Map([["c1", { run_id: "r", name: "mirror_part_set_vars", input: { part_id: "mount_plate" } }]]); });
    expect(handle.setHighlight).toHaveBeenLastCalledWith(true);
    act(() => { inflight.value = new Map(); });
    expect(handle.setHighlight).toHaveBeenLastCalledWith(false);
  });

  it("shows the part in the title block", async () => {
    const partGlb = vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' });
    bind(partGlb);
    parts.value = [mk(1)];
    const { container } = render(<Viewer />);
    await waitFor(() => expect(partGlb).toHaveBeenCalledTimes(1));
    expect(container.textContent).toContain("Part · mount plate");
  });

  it("reload_only_on_mtime_change", async () => {
    const partGlb = vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' });
    bind(partGlb);
    parts.value = [mk(1)];
    render(<Viewer />);
    await waitFor(() => expect(partGlb).toHaveBeenCalledTimes(1));
    act(() => { parts.value = [mk(1)]; });
    await new Promise((r) => setTimeout(r, 30));
    expect(partGlb).toHaveBeenCalledTimes(1);
    partGlb.mockResolvedValueOnce("not-modified");
    act(() => { parts.value = [mk(2)]; });
    await waitFor(() => expect(partGlb).toHaveBeenCalledTimes(2));
    expect(partGlb).toHaveBeenLastCalledWith("mount_plate", '"x"');
  });

  it("shows GLB failed in the title block", async () => {
    bind(vi.fn().mockRejectedValue(new Error("no model.glb")));
    parts.value = [mk(1)];
    const { findByText } = render(<Viewer />);
    await findByText("GLB failed: no model.glb");
  });

  it("title block credits the run that created the part, not 'you'", () => {
    parts.value = [mk(1)];
    const run = { id: "r1", parent_id: null, agent: "designer", provider: "fake", model: "m", task: "make a mount plate", status: "done" as const,
      branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1, updated_at: 2 };
    runs.value = new Map([["r1", run]]);
    expect(changingRunLabel.value).toBeNull();
    // the real core: mirror_part_create's result step is kind tool_result with output.data.part_id
    steps.value = new Map([["r1", [{ id: 4, run_id: "r1", ts: 2, kind: "tool_result", evidence_hash: "e", prev_hash: null, collapsed: false,
      payload: { call_id: "c1", name: "mirror_part_create", output: { ok: true, summary: "s", data: { part_id: "mount_plate" }, handles: [], warnings: [], step_kind: "tool_result" } } }]]]);
    expect(changingRunLabel.value).toBe("designer · make mount plate");
  });

  it("highlighted is true only while an agent call targets the active part", () => {
    parts.value = [mk(1)];
    selectedPartId.value = "mount_plate";
    expect(highlighted.value).toBe(false);
    inflight.value = new Map([["c1", { run_id: "r", name: "mirror_part_set_vars", input: { part_id: "mount_plate" } }]]);
    expect(highlighted.value).toBe(true);
    inflight.value = new Map([["c1", { run_id: "r", name: "mirror_part_set_vars", input: { part_id: "other" } }]]);
    expect(highlighted.value).toBe(false);
    inflight.value = new Map([["c2", { run_id: "r", name: "mirror_measure", input: { part_id: "mount_plate" } }]]);
    expect(highlighted.value).toBe(false);
  });

  it("a click on a face says what it is; Modify… there opens the ops, Add hole posts /ops with that face id and the version picked on", async () => {
    const tree = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../test/fixtures/core-ui/parts_tree.json"), "utf8")) as PartSummary[];
    const arm = tree.find((p) => p.part_id === "quad_arm") as PartSummary;
    const partOp = vi.fn().mockResolvedValue({ part_id: "quad_arm", version: 6, vars: arm.vars });
    bindClient({ partGlb: vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' }), partOp, partSource: vi.fn().mockRejectedValue(new Error("n/a")) } as unknown as CoreClient);
    parts.value = [arm];
    selectedPartId.value = "quad_arm";
    handle.pick.mockReturnValue({ partId: "quad_arm", point: [3, 2, 2.5], normal: [0, 0, 1] });
    handle.toScreen.mockImplementation((p: [number, number, number]): [number, number] => [p[0] * 10, p[1] * 10]);  // top view, 10 px/mm
    const { getByTestId, findByTestId, getByRole } = render(<Viewer />);
    const canvas = getByTestId("viewer-canvas");
    fireEvent.pointerDown(canvas, { button: 0, clientX: 120, clientY: 80 });
    fireEvent.pointerUp(canvas, { button: 0, clientX: 121, clientY: 80 });
    const label = await findByTestId("pick-label");
    expect(label.textContent).toMatch(/^Face · planar · [\d,]+ mm²/);
    expect(() => getByTestId("ops-menu")).toThrow();           // no ops callout on a plain click
    expect(handle.setMarkers).toHaveBeenLastCalledWith([expect.objectContaining({ kind: "face", normal: [0, 0, 1] })]);
    fireEvent.click(getByRole("button", { name: "Modify…" }));
    const menu = await findByTestId("ops-menu");
    expect(menu.textContent).toContain("f1 · plane facing +Z");
    expect(handle.setMarker).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "face", normal: [0, 0, 1] }));
    fireEvent.input(getByRole("textbox", { name: "Diameter (mm)" }), { target: { value: "3" } });
    fireEvent.click(getByRole("button", { name: "Add hole" }));
    await waitFor(() => expect(partOp).toHaveBeenCalledWith("quad_arm",
      { op: "hole", target: { face_id: "f1" }, params: { diameter: 3, through: true, point: [3, 2, 2.5] } }, 5));
    await waitFor(() => expect(() => getByTestId("ops-menu")).toThrow());
  });

  it("a pick right after an edit (list not refetched yet) sends the picked version or waits — never the newer one", async () => {
    const tree = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../test/fixtures/core-ui/parts_tree.json"), "utf8")) as PartSummary[];
    const arm = tree.find((p) => p.part_id === "quad_arm") as PartSummary;
    const partOp = vi.fn().mockResolvedValue({ part_id: "quad_arm", version: 7, vars: arm.vars });
    bindClient({ partGlb: vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' }), partOp, partSource: vi.fn().mockRejectedValue(new Error("n/a")) } as unknown as CoreClient);
    parts.value = [arm];                                     // version 5 on screen
    selectedPartId.value = "quad_arm";
    noteMyEdit("quad_arm", 6, "added a hole");                // the user's own op just answered v6
    handle.pick.mockReturnValue({ partId: "quad_arm", point: [3, 2, 2.5], normal: [0, 0, 1] });
    handle.toScreen.mockImplementation((p: [number, number, number]): [number, number] => [p[0] * 10, p[1] * 10]);
    const { getByTestId, findByTestId, getByRole } = render(<Viewer />);
    fireEvent.pointerDown(getByTestId("viewer-canvas"), { button: 0, clientX: 120, clientY: 80 });
    fireEvent.pointerUp(getByTestId("viewer-canvas"), { button: 0, clientX: 120, clientY: 80 });
    fireEvent.click(getByRole("button", { name: "Modify…" }));
    const menu = await findByTestId("ops-menu");
    expect(menu.querySelector("[data-behind]")).not.toBeNull();
    expect((getByRole("button", { name: "Add hole" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(menu);
    await new Promise((r) => setTimeout(r, 10));
    expect(partOp).not.toHaveBeenCalled();
    // the list catches up (v6, new face ids): a new pick is based on v6
    act(() => { parts.value = [{ ...arm, version: 6 }]; });
    fireEvent.pointerDown(getByTestId("viewer-canvas"), { button: 0, clientX: 120, clientY: 80 });
    fireEvent.pointerUp(getByTestId("viewer-canvas"), { button: 0, clientX: 120, clientY: 80 });
    fireEvent.click(getByRole("button", { name: "Add hole" }));
    await waitFor(() => expect(partOp).toHaveBeenCalledWith("quad_arm", expect.objectContaining({ target: { face_id: "f1" } }), 6));
  });

  it("a second pick while the callout is open resets it to the new target", async () => {
    const tree = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../test/fixtures/core-ui/parts_tree.json"), "utf8")) as PartSummary[];
    const plate = tree.find((p) => p.part_id === "top_plate") as PartSummary;
    const partOp = vi.fn().mockResolvedValue({ part_id: "top_plate", version: 2, vars: plate.vars });
    bindClient({ partGlb: vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' }), partOp, partSource: vi.fn().mockRejectedValue(new Error("n/a")) } as unknown as CoreClient);
    parts.value = [plate];
    selectedPartId.value = "top_plate";
    handle.toScreen.mockImplementation((p: [number, number, number]): [number, number] => [p[0] * 10, p[1] * 10]);
    const { getByTestId, findByTestId, getByRole } = render(<Viewer />);
    const click = (x: number) => { fireEvent.pointerDown(getByTestId("viewer-canvas"), { button: 0, clientX: x, clientY: 5 }); fireEvent.pointerUp(getByTestId("viewer-canvas"), { button: 0, clientX: x, clientY: 5 }); };
    handle.pick.mockReturnValue({ partId: "top_plate", point: [0, 0, 1.5], normal: [0, 0, 1] });   // the top face's middle
    click(100);
    fireEvent.click(getByRole("button", { name: "Modify…" }));
    expect((await findByTestId("ops-menu")).textContent).toContain("plane facing +Z");
    handle.pick.mockReturnValue({ partId: "top_plate", point: [19.9, 3, 1.5], normal: [0, 0, 1] });  // next to the +X top edge
    click(300);
    await waitFor(() => expect(getByTestId("ops-menu").textContent).toMatch(/e\d+ · line · 40 mm/));
    expect((getByRole("radio", { name: "Fillet" }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(getByRole("button", { name: "Add fillet" }));
    await waitFor(() => expect(partOp).toHaveBeenCalledWith("top_plate", expect.objectContaining({ op: "fillet" }), 1));
  });

  it("an edge click reads as the edge; Modify… in the part's menu opens the ops to choose from a list", async () => {
    const plate = treeParts().find((p) => p.part_id === "top_plate") as PartSummary;
    bindClient({ partGlb: vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' }), partSource: vi.fn().mockRejectedValue(new Error("n/a")) } as unknown as CoreClient);
    parts.value = [plate];
    selectedPartId.value = "top_plate";
    handle.toScreen.mockImplementation((p: [number, number, number]): [number, number] => [p[0] * 10, p[1] * 10]);
    handle.pick.mockReturnValue({ partId: "top_plate", point: [19.9, 3, 1.5], normal: [0, 0, 1] });
    const { getByTestId, findByTestId, queryByTestId } = render(<Viewer />);
    fireEvent.pointerDown(getByTestId("viewer-canvas"), { button: 0, clientX: 300, clientY: 5 });
    fireEvent.pointerUp(getByTestId("viewer-canvas"), { button: 0, clientX: 300, clientY: 5 });
    expect((await findByTestId("pick-label")).textContent).toMatch(/^Edge · line · 40 mm/);
    act(() => { requestModify("top_plate"); });
    const menu = await findByTestId("ops-menu");
    expect(menu.querySelector("select")).not.toBeNull();         // the keyboard form: choose the face / edge
    expect(queryByTestId("pick-label")).toBeNull();
  });

  it("measure: a click shows the metrics value at once, then the core's exact one; Shift adds a second pick", async () => {
    const plate = treeParts().find((p) => p.part_id === "top_plate") as PartSummary;
    const top = plate.metrics!.face_list!.find((f) => f.normal?.[2] === 1)!;
    const bottom = plate.metrics!.face_list!.find((f) => f.normal?.[2] === -1)!;
    let answer!: (v: unknown) => void;
    const measurePart = vi.fn().mockImplementationOnce(() => new Promise((r) => { answer = r; }))
      .mockResolvedValueOnce({ part_id: "top_plate", version: 1, items: [], values: [
        { key: "normal_distance", label: "Normal distance", value: 3, unit: "mm" }, { key: "distance", label: "Distance", value: 3, unit: "mm" },
        { key: "angle", label: "Angle", value: 0, unit: "°" }] });
    const warmMeasure = vi.fn().mockResolvedValue({ part_id: "top_plate", warm: true });
    bindClient({ partGlb: vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' }), measurePart, warmMeasure } as unknown as CoreClient);
    parts.value = [plate];
    selectedPartId.value = "top_plate";
    handle.toScreen.mockImplementation((p: [number, number, number]): [number, number] => [p[0] * 10, p[1] * 10]);
    const { getByTestId, findByTestId, getByRole } = render(<Viewer />);
    act(() => { fireEvent.keyDown(window, { key: "m" }); });
    // U2b's View menu hosts U2a's tools: Measure is checked there
    fireEvent.click(getByRole("button", { name: "View options" }));
    expect(getByRole("menuitemcheckbox", { name: "Measure" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.keyDown(getByRole("menu"), { key: "Escape" });
    const panel = await findByTestId("measure-panel");
    expect(panel.textContent).toContain("Click an edge, a face or a corner");
    await waitFor(() => expect(warmMeasure).toHaveBeenCalledWith("top_plate"));   // the core loads the shape ahead
    handle.pick.mockReturnValue({ partId: "top_plate", point: [0, 0, 1.5], normal: [0, 0, 1] });
    fireEvent.pointerDown(getByTestId("viewer-canvas"), { button: 0, clientX: 100, clientY: 5 });
    fireEvent.pointerUp(getByTestId("viewer-canvas"), { button: 0, clientX: 100, clientY: 5 });
    await waitFor(() => expect(panel.querySelector('[data-value="area"] dd')?.textContent).toBe("1,600 mm²"));
    expect(measurePart).toHaveBeenLastCalledWith("top_plate", [{ face_id: top.id }], plate.version);
    act(() => { answer({ part_id: "top_plate", version: 1, items: [], values: [{ key: "area", label: "Area", value: 1600, unit: "mm²" }] }); });
    await waitFor(() => expect(panel.querySelector("[data-exact]")).not.toBeNull());
    handle.pick.mockReturnValue({ partId: "top_plate", point: [0, 0, -1.5], normal: [0, 0, -1] });
    fireEvent.pointerDown(getByTestId("viewer-canvas"), { button: 0, clientX: 100, clientY: 5 });
    fireEvent.pointerUp(getByTestId("viewer-canvas"), { button: 0, clientX: 100, clientY: 5, shiftKey: true });
    await waitFor(() => expect(measurePart).toHaveBeenLastCalledWith("top_plate", [{ face_id: top.id }, { face_id: bottom.id }], plate.version));
    await waitFor(() => expect(panel.querySelector('[data-value="distance"] dd')?.textContent).toBe("3 mm"));
    expect(panel.querySelector('[data-value="normal_distance"]')).toBeNull();   // the same number twice says nothing new
    expect(panel.querySelector('[data-value="angle"] dd')?.textContent).toBe("0°");
    expect(handle.setMarkers).toHaveBeenLastCalledWith([expect.objectContaining({ kind: "face" }), expect.objectContaining({ kind: "face" })]);
    act(() => { fireEvent.keyDown(window, { key: "Escape" }); });
    expect(viewerTool.value).toBe("select");
  });

  it("M in a text field types, never switches the tool", () => {
    bind(vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' }));
    parts.value = [mk(1)];
    render(<Viewer />);
    const input = document.createElement("input");
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "m" });
    expect(viewerTool.value).toBe("select");
    input.remove();
  });

  it("comment: click a spot, type, send → POST /projects/{id}/feedback; the pin stays on the model", async () => {
    const plate = treeParts().find((p) => p.part_id === "top_plate") as PartSummary;
    const top = plate.metrics!.face_list!.find((f) => f.normal?.[2] === 1)!;
    const sendFeedback = vi.fn().mockResolvedValue({ run_id: "r9", step_id: null, started: true });
    bindClient({ partGlb: vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' }), sendFeedback } as unknown as CoreClient);
    activeBuildId.value = "b1";
    parts.value = [plate];
    selectedPartId.value = "top_plate";
    handle.toScreen.mockImplementation((p: [number, number, number]): [number, number] => [100 + p[0] * 10, 100 + p[1] * 10]);
    const { getByTestId, findByTestId, getByRole, findAllByTestId } = render(<Viewer />);
    fireEvent.click(getByRole("button", { name: "View options" }));
    fireEvent.click(getByRole("menuitemcheckbox", { name: "Comment on the model" }));
    handle.pick.mockReturnValue({ partId: "top_plate", point: [5, -4, 1.5], normal: [0, 0, 1] });
    fireEvent.pointerDown(getByTestId("viewer-canvas"), { button: 0, clientX: 150, clientY: 60 });
    fireEvent.pointerUp(getByTestId("viewer-canvas"), { button: 0, clientX: 150, clientY: 60 });
    const box = await findByTestId("comment-box");
    expect(box.textContent).toContain("Face · planar");
    fireEvent.input(getByRole("textbox", { name: "Your comment" }), { target: { value: "a slot here for the cable" } });
    fireEvent.click(getByRole("button", { name: "Send to agent" }));
    await waitFor(() => expect(sendFeedback).toHaveBeenCalledWith("b1", { part_id: "top_plate", point: [5, -4, 1.5], face_id: top.id, text: "a slot here for the cable" }));
    const pins = await findAllByTestId("pin");
    expect(pins.map((p) => p.textContent)).toEqual(["1"]);
    expect(pins[0].style.left).toBe("150px");
    expect(viewerTool.value).toBe("select");
    fireEvent.click(pins[0]);
    expect((await findByTestId("pin-note")).textContent).toContain("a slot here for the cable");
  });

  it("a drag orbits: no pick, no callout", () => {
    bind(vi.fn().mockResolvedValue({ bytes: fixtureBytes(), etag: '"x"' }));
    parts.value = [mk(1)];
    const { getByTestId, queryByTestId } = render(<Viewer />);
    const canvas = getByTestId("viewer-canvas");
    fireEvent.pointerDown(canvas, { button: 0, clientX: 10, clientY: 10 });
    fireEvent.pointerUp(canvas, { button: 0, clientX: 60, clientY: 40 });
    expect(handle.pick).not.toHaveBeenCalled();
    expect(queryByTestId("ops-menu")).toBeNull();
  });
});
