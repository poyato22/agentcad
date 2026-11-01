// P11 F7: images in the composer — paste (⌘V), drop, 📎 — at most 5, thumbnails with ×, sent to
// the agent as images with the message; a drawing-like one offers the drawing import.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../../api/client";
import type { AgentRun } from "../../../api/types";
import { activeBuildId } from "../../../state/builds";
import { bindClient, resetStore, runs } from "../../../state/store";
import { resetImports } from "../../imports/state";
import { addFiles, attachments, browserEncoder, clearAttachments, imageEncoder, isImage, looksLikeDrawing, MAX_IMAGES, type Encoded } from "./attachments";
import { CommandLine, IMAGE_ONLY_TEXT } from "./CommandLine";

const fakeEncode = vi.fn(async (f: File): Promise<Encoded> => ({
  mediaType: "image/jpeg", dataB64: btoa(f.name), width: 800, height: 600, drawing: /drawing/.test(f.name),
}));
const file = (name: string, type = "image/png") => new File([new Uint8Array([1, 2, 3])], name, { type });

const lead: AgentRun = {
  id: "L", parent_id: null, agent: "engineer", provider: "claude-code", model: "sonnet", task: "t", status: "done",
  branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1, updated_at: 1,
  project_id: "b1", role: "lead",
} as AgentRun;
const client = {
  chat: vi.fn(), message: vi.fn(), cancel: vi.fn(),
  importDrawing: vi.fn(), drawingImport: vi.fn(),
};

beforeEach(() => {
  imageEncoder.current = fakeEncode;
  fakeEncode.mockClear();
  client.chat.mockReset().mockResolvedValue({ run_id: "L", routed: { agent: "engineer", reason: "", confidence: 1 }, new_lead: false, queued: false });
  client.message.mockReset().mockResolvedValue({ ok: true });
  client.importDrawing.mockReset().mockResolvedValue({ import_id: "i1", status: "running" });
  bindClient(client as unknown as CoreClient);
  activeBuildId.value = "b1";
  runs.value = new Map([["L", lead]]);
});
afterEach(() => { cleanup(); clearAttachments(); resetStore(); resetImports(); activeBuildId.value = null; imageEncoder.current = browserEncoder; });

const box = () => screen.getByLabelText("Command line") as HTMLTextAreaElement;

describe("attachments", () => {
  it("takes images only, at most 5", async () => {
    expect(isImage(file("a.png"))).toBe(true);
    expect(isImage(file("IMG_1.HEIC", ""))).toBe(true);
    expect(isImage(file("notes.pdf", "application/pdf"))).toBe(false);
    const why = await addFiles([...Array.from({ length: 6 }, (_, i) => file(`p${i}.png`)), file("x.pdf", "application/pdf")]);
    expect(attachments.value).toHaveLength(MAX_IMAGES);
    expect(why).toContain("at most 5 images");
    expect(why).toContain("only images");
  });

  it("tells a technical drawing from a photo", () => {
    const px = (n: number, rgb: [number, number, number]) => Array.from({ length: n }, () => [...rgb, 255]).flat();
    const drawing = new Uint8ClampedArray([...px(90, [255, 255, 255]), ...px(10, [20, 20, 20])]);
    const photo = new Uint8ClampedArray([...px(50, [200, 120, 60]), ...px(50, [40, 90, 160])]);
    expect(looksLikeDrawing(drawing)).toBe(true);
    expect(looksLikeDrawing(photo)).toBe(false);
    const render = new Uint8ClampedArray([...px(72, [240, 240, 238]), ...px(28, [70, 70, 72])]);   // a shaded block on paper
    expect(looksLikeDrawing(render)).toBe(false);
    expect(looksLikeDrawing(photo, "bracket_drawing.png")).toBe(true);
  });
});

describe("composer images (P11 F7)", () => {
  it("a pasted image shows as a thumbnail and goes to the lead with the message", async () => {
    render(<CommandLine />);
    fireEvent.paste(box(), { clipboardData: { files: [file("shot.png")], types: ["Files"] } });
    await waitFor(() => expect(document.querySelectorAll("[data-attachment]")).toHaveLength(1));
    expect(document.querySelector("[data-attachment] img")?.getAttribute("src")).toBe(`data:image/jpeg;base64,${btoa("shot.png")}`);
    fireEvent.input(box(), { target: { value: "what is wrong here" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(client.chat).toHaveBeenCalledWith("b1", "what is wrong here", [{ media_type: "image/jpeg", data_b64: btoa("shot.png") }]));
    await waitFor(() => expect(attachments.value).toEqual([]));
    expect(document.querySelector("[data-attachment]")).toBeNull();
  });

  it("dropped or picked images attach; × drops one; images alone can be sent", async () => {
    render(<CommandLine />);
    const zone = document.querySelector("[data-command-line]") as HTMLElement;
    fireEvent.drop(zone, { dataTransfer: { files: [file("a.png"), file("b.png")], types: ["Files"] } });
    await waitFor(() => expect(attachments.value).toHaveLength(2));
    fireEvent.change(screen.getByTestId("composer-file"), { target: { files: [file("c.png")] } });
    await waitFor(() => expect(attachments.value).toHaveLength(3));
    fireEvent.click(screen.getByRole("button", { name: "Remove a.png" }));
    expect(attachments.value.map((a) => a.name)).toEqual(["b.png", "c.png"]);
    expect((screen.getByTestId("composer-send") as HTMLButtonElement).disabled).toBe(false);   // no text needed
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(client.chat).toHaveBeenCalledWith("b1", IMAGE_ONLY_TEXT, expect.arrayContaining([expect.objectContaining({ media_type: "image/jpeg" })])));
  });

  it("a dropped technical drawing offers the drawing import on its thumbnail", async () => {
    render(<CommandLine />);
    await addFiles([file("bracket_drawing.png")]);
    const make = await screen.findByTestId("make-part-from-drawing");
    expect(make.textContent).toBe("Make a part from this drawing");
    fireEvent.click(make);
    await waitFor(() => expect(client.importDrawing).toHaveBeenCalledWith(expect.any(File), "bracket_drawing.png", { projectId: "b1" }));
    expect(attachments.value).toEqual([]);                       // it went to the import instead
  });
});
