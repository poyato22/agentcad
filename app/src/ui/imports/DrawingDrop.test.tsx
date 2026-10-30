import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../api/client";
import type { DrawingImport } from "../../api/imports";
import { bindClient, parts, resetStore, selectedPartId } from "../../state/store";
import { DrawingDrop } from "./DrawingDrop";
import { POLL_MS, resetImports } from "./state";

const rec = (over: Partial<DrawingImport> = {}): DrawingImport => ({
  import_id: "imp_0123456789ab", kind: "dxf", file_name: "plate.dxf", status: "reading", stage: "reading the drawing",
  error: null, project_id: "p1", part_id: null, run_id: null, attempts: 0, max_attempts: 1, table: null, result: null,
  notes: [], pages: 1, original_url: "/imports/imp_0123456789ab/original", drawing_url: null, ...over,
});

const DONE = rec({
  status: "done", stage: "", part_id: "plate", attempts: 1, result: "mismatch",
  drawing_url: "/parts/plate/drawing.svg", notes: ["the DXF gives no thickness; 5 mm was assumed"],
  table: {
    ok: false, matches: 2, mismatches: 1, missing: 0, unchecked: 1, rows: [
      { id: "L", kind: "length", text: "100", expected: 100, measured: 100, tol_plus: 0.05, tol_minus: 0.05, status: "match", source: "drawing" },
      { id: "W", kind: "width", text: "<img src=x onerror=alert(1)>", expected: 60, measured: 59.5, tol_plus: 0.05, tol_minus: 0.05, status: "mismatch", source: "drawing" },
      { id: "A", kind: "angle", text: "", expected: 90, measured: null, tol_plus: null, tol_minus: null, status: "unchecked", source: null },
      { id: "callout:4x Ø6 THRU", kind: "callout", text: "4x Ø6 THRU", expected: null, measured: null, tol_plus: null, tol_minus: null, status: "match", source: "drawing" },
    ],
  },
});

const client = { importDrawing: vi.fn(), drawingImport: vi.fn(), drawingImportOriginal: vi.fn(), drawingFile: vi.fn() };

beforeEach(() => {
  POLL_MS.value = 5;
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:http://localhost/${++n}`);
  URL.revokeObjectURL = vi.fn();
  client.importDrawing.mockReset().mockResolvedValue(rec());
  client.drawingImport.mockReset().mockResolvedValueOnce(rec({ status: "building", stage: "building the part (attempt 1)" }))
    .mockResolvedValue(DONE);
  client.drawingImportOriginal.mockReset().mockResolvedValue(new Blob(["<svg/>"], { type: "image/svg+xml" }));
  client.drawingFile.mockReset().mockResolvedValue(new Blob(["<svg/>"], { type: "image/svg+xml" }));
  bindClient(client as unknown as CoreClient);
  parts.value = [];
});
afterEach(() => { cleanup(); resetImports(); resetStore(); });

const file = (name: string, body = "0\nSECTION\n", type = "") => new File([body], name, { type });

function choose(f: File) {
  const input = document.querySelector("[data-drawing-input]") as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [f], configurable: true });
  fireEvent.change(input);
}

describe("DrawingDrop", () => {
  it("tile_uploads_a_chosen_file_then_shows_progress_and_the_result", async () => {
    render(<DrawingDrop variant="tile" projectId="p1" />);
    expect(screen.getByText("Drop a drawing to make a part")).toBeTruthy();
    expect(screen.getByText("PDF, PNG, JPG or DXF · up to 20 MB")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Choose a file…" })).toBeTruthy();
    choose(file("plate.dxf"));
    expect(client.importDrawing).toHaveBeenCalledWith(expect.any(File), "plate.dxf", { projectId: "p1" });
    await screen.findByRole("progressbar");
    const result = await screen.findByText(/Made/);
    expect(result.textContent).toBe("Made plate · 2 of 3 dimensions match");
    const table = document.querySelector("[data-match-table]") as HTMLTableElement;
    expect(table.querySelectorAll("tbody tr")).toHaveLength(4);
    expect(table.textContent).toContain("✗ off by -0.5");
    expect(table.textContent).toContain("not checked");
    // untrusted dimension text stays text
    expect(table.querySelector("img")).toBeNull();
    expect(table.textContent).toContain("<img src=x onerror=alert(1)>");
    // side by side: both drawings as <img> from blob: URLs
    await waitFor(() => expect(document.querySelectorAll("[data-side-by-side] img")).toHaveLength(2));
    for (const img of document.querySelectorAll("[data-side-by-side] img")) expect(img.getAttribute("src")).toMatch(/^blob:/);
    expect(client.drawingImportOriginal).toHaveBeenCalledWith("imp_0123456789ab", "image/svg+xml");
    expect(client.drawingFile).toHaveBeenCalledWith("plate", "svg");
    expect(screen.getByText("the DXF gives no thickness; 5 mm was assumed")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open part" }));
    expect(selectedPartId.value).toBe("plate");
  });

  it("a_refused_upload_shows_the_cores_message_and_hint", async () => {
    client.importDrawing.mockReset().mockRejectedValue(new CoreError("invalid_input",
      "the model in use (ollama/llama) cannot read images, so it cannot read this drawing",
      { status: 400, hint: "Choose a model that can read images in Profile → Model" }));
    render(<DrawingDrop variant="tile" />);
    choose(file("plate.png", "x", "image/png"));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("cannot read images");
    expect(alert.textContent).toContain("Profile → Model");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss plate.png" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("wrong_file_types_are_refused_before_upload", async () => {
    render(<DrawingDrop variant="tile" />);
    choose(file("model.step"));
    expect((await screen.findByRole("alert")).textContent).toContain("Use a PDF, PNG, JPG or DXF file.");
    expect(client.importDrawing).not.toHaveBeenCalled();
  });

  it("overlay_appears_only_for_file_drags_and_takes_the_drop", async () => {
    const { container } = render(<div class="host"><DrawingDrop variant="overlay" projectId="p9" /></div>);
    const host = container.querySelector(".host") as HTMLElement;
    const drag = (type: string, types: string[], files: File[] = []) => {
      const ev = new Event(type, { bubbles: true, cancelable: true }) as DragEvent;
      Object.defineProperty(ev, "dataTransfer", { value: { types, files, dropEffect: "none" } });
      act(() => { host.dispatchEvent(ev); });
      return ev;
    };
    drag("dragenter", ["text/plain"]);
    expect(document.querySelector("[data-drawing-drop='overlay']")).toBeNull();
    drag("dragenter", ["Files"]);
    expect(document.querySelector("[data-drawing-drop='overlay']")).not.toBeNull();
    expect(screen.getByText("Drop a drawing to make a part")).toBeTruthy();
    expect(drag("dragover", ["Files"]).defaultPrevented).toBe(true);
    const drop = drag("drop", ["Files"], [file("bracket.pdf", "%PDF-1.7")]);
    expect(drop.defaultPrevented).toBe(true);
    expect(document.querySelector("[data-drawing-drop='overlay']")).toBeNull();
    expect(client.importDrawing).toHaveBeenCalledWith(expect.any(File), "bracket.pdf", { projectId: "p9" });
    await screen.findByRole("progressbar");
  });

  it("an_empty_build_offers_a_keyboard_path_and_a_full_one_does_not", () => {
    const { unmount } = render(<div><DrawingDrop variant="overlay" /></div>);
    expect(screen.getByRole("button", { name: "Choose a drawing…" })).toBeTruthy();
    unmount();
    parts.value = [{ part_id: "lid", vars: {}, metrics: null, mtime: 1 }];
    render(<div><DrawingDrop variant="overlay" /></div>);
    expect(screen.queryByRole("button", { name: "Choose a drawing…" })).toBeNull();
  });
});
