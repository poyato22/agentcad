import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../../api/client";
import type { DrawingInfo, PartSummary } from "../../../api/types";
import { bindClient, parts, resetStore, selectedPartId } from "../../../state/store";
import { DrawingPage } from "./index";

const FX = resolve(import.meta.dirname, "../../../../test/fixtures/core-ui");
const SVG = readFileSync(resolve(FX, "drawing.svg"), "utf8");
const INFO = JSON.parse(readFileSync(resolve(FX, "drawing.json"), "utf8")) as DrawingInfo;
const CAPS = JSON.parse(readFileSync(resolve(FX, "print_capabilities.json"), "utf8")) as unknown;

const part = (id: string, mtime = 1790000000): PartSummary => ({ part_id: id, vars: {}, metrics: null, mtime });

const client = { drawingFile: vi.fn(), drawingInfo: vi.fn(), printCapabilities: vi.fn() };
let urls: string[];
let revoked: string[];

beforeEach(() => {
  urls = [];
  revoked = [];
  let n = 0;
  URL.createObjectURL = vi.fn(() => { const u = `blob:http://localhost/${++n}`; urls.push(u); return u; });
  URL.revokeObjectURL = vi.fn((u: string) => { revoked.push(u); });
  client.drawingFile.mockReset().mockImplementation(async (_id: string, kind: string) =>
    new Blob([kind === "svg" ? SVG : "0\nEOF\n"], { type: kind === "svg" ? "image/svg+xml" : "image/vnd.dxf" }));
  client.drawingInfo.mockReset().mockImplementation(async (id: string, material?: string | null) => ({ ...INFO, label: id, material: material ?? "PLA" }));
  client.printCapabilities.mockReset().mockResolvedValue(CAPS);
  bindClient(client as unknown as CoreClient);
  parts.value = [part("mount_plate"), part("hinge_arm")];
});
afterEach(() => { cleanup(); resetStore(); });

const sheet = () => screen.findByRole("img", { name: /^Drawing · / });

describe("DrawingPage", () => {
  it("renders_the_core_svg_as_an_img_from_a_blob_url_never_as_markup", async () => {
    render(<DrawingPage />);
    const img = (await sheet()) as HTMLImageElement;
    expect(img.tagName).toBe("IMG");
    expect(img.getAttribute("src")).toMatch(/^blob:/);
    expect(img.getAttribute("alt")).toBe("Drawing · Part · mount plate");
    expect(client.drawingFile).toHaveBeenCalledWith("mount_plate", "svg", null);
    // the SVG's own content never lands in the DOM
    expect(document.querySelector("svg #sheet, #view-front")).toBeNull();
    expect(new XMLSerializer().serializeToString(document.body)).not.toContain("MATERIAL: PLA");
  });

  it("shows_the_drawing_facts_from_drawing_json", async () => {
    render(<DrawingPage />);
    await sheet();
    const facts = await screen.findByRole("list", { name: "Drawing facts" });
    expect(facts.textContent).toContain("scale 2:1");
    expect(facts.textContent).toContain("A4 297 × 210");
    expect(facts.textContent).toContain("40 × 20 × 6 mm");
    expect(facts.textContent).toContain("2x Ø4.2 THRU");
    expect(facts.textContent).toContain("PLA");
  });

  it("part_picker_lists_parts_by_label_and_switching_regenerates", async () => {
    render(<DrawingPage />);
    await sheet();
    const pick = screen.getByRole("combobox", { name: "Part" }) as HTMLSelectElement;
    expect([...pick.options].map((o) => o.textContent)).toEqual(["Part · hinge arm", "Part · mount plate"]);
    fireEvent.change(pick, { target: { value: "hinge_arm" } });
    expect(selectedPartId.value).toBe("hinge_arm");
    await waitFor(() => expect(client.drawingFile).toHaveBeenLastCalledWith("hinge_arm", "svg", null));
    expect(((await sheet()) as HTMLImageElement).alt).toBe("Drawing · Part · hinge arm");
    await waitFor(() => expect(revoked).toContain(urls[0])); // the old blob URL is released
  });

  it("regenerates_when_the_part_is_rebuilt", async () => {
    render(<DrawingPage />);
    await sheet();
    expect(client.drawingFile).toHaveBeenCalledTimes(1);
    act(() => { parts.value = [part("mount_plate", 1790000100), part("hinge_arm")]; });
    await waitFor(() => expect(client.drawingFile).toHaveBeenCalledTimes(2));
    // an unrelated part change does not
    act(() => { parts.value = [part("mount_plate", 1790000100), part("hinge_arm", 1790000200)]; });
    await new Promise((r) => setTimeout(r, 20));
    expect(client.drawingFile).toHaveBeenCalledTimes(2);
  });

  it("material_choice_is_passed_to_the_core", async () => {
    render(<DrawingPage />);
    await sheet();
    const mat = await screen.findByRole("combobox", { name: "Material" }) as HTMLSelectElement;
    await waitFor(() => expect(mat.options.length).toBeGreaterThan(1));
    fireEvent.change(mat, { target: { value: "PETG" } });
    await waitFor(() => expect(client.drawingFile).toHaveBeenLastCalledWith("mount_plate", "svg", "PETG"));
    expect(client.drawingInfo).toHaveBeenLastCalledWith("mount_plate", "PETG");
  });

  it("an_unbuilt_part_says_so_with_the_hint", async () => {
    client.drawingFile.mockRejectedValue(new CoreError("not_found", "no built part 'mount_plate'", { status: 404, hint: "build it with mirror_part_create" }));
    client.drawingInfo.mockRejectedValue(new CoreError("not_found", "no built part 'mount_plate'", { status: 404 }));
    render(<DrawingPage />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Part · mount plate has no built model yet");
    expect(alert.textContent).toContain("build it with mirror_part_create");
    expect(screen.queryByRole("img", { name: /^Drawing · / })).toBeNull();
  });

  it("a_failed_drawing_shows_the_error_and_try_again_refetches", async () => {
    client.drawingFile.mockRejectedValueOnce(new CoreError("tool_failed", "drawing timed out after 60 s", { status: 500 }));
    render(<DrawingPage />);
    expect((await screen.findByRole("alert")).textContent).toContain("drawing timed out after 60 s");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await sheet();
  });

  it("no_parts_shows_an_empty_state", () => {
    parts.value = [];
    render(<DrawingPage />);
    expect(screen.getByText(/No parts yet/)).toBeTruthy();
    expect(client.drawingFile).not.toHaveBeenCalled();
  });

  it("zoom_buttons_and_fit_change_the_view", async () => {
    render(<DrawingPage />);
    await sheet();
    const label = () => screen.getByTestId("zoom").textContent;
    expect(label()).toBe("100%");
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(label()).not.toBe("100%");
    fireEvent.click(screen.getByRole("button", { name: "Fit" }));
    expect(label()).toBe("100%");
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(Number.parseInt(label() ?? "", 10)).toBeLessThan(100);
  });

  it("export_svg_and_dxf_download_files_named_after_the_part", async () => {
    const clicks: { href: string; download: string }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push({ href: this.href, download: this.download });
    });
    render(<DrawingPage />);
    await sheet();
    fireEvent.click(screen.getByRole("button", { name: "Export SVG" }));
    await waitFor(() => expect(clicks).toHaveLength(1));
    expect(clicks[0].download).toBe("mount_plate.svg");
    expect(clicks[0].href).toMatch(/^blob:/);
    fireEvent.click(screen.getByRole("button", { name: "Export DXF" }));
    await waitFor(() => expect(clicks).toHaveLength(2));
    expect(clicks[1].download).toBe("mount_plate.dxf");
    expect(client.drawingFile).toHaveBeenCalledWith("mount_plate", "dxf", null);
  });

  it("export_pdf_prints_the_sheet_only", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    render(<DrawingPage />);
    await sheet();
    fireEvent.click(screen.getByRole("button", { name: "Export PDF" }));
    expect(print).toHaveBeenCalledTimes(1);
    // the print copy is the same blob, untransformed
    const copy = document.querySelector(".drawing-print img") as HTMLImageElement;
    expect(copy.getAttribute("src")).toMatch(/^blob:/);
  });
});
