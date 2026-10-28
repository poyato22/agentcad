import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreClient } from "../../../api/client";
import type { ModelsInfo, Profile } from "../../../api/types";
import { isAutoNumbered } from "../../../lib/names";
import { bindClient, resetStore, selectedRunId } from "../../../state/store";
import { consoleCollapsed } from "../router";
import { BUILD_PROMPT, buildTarget } from "./build";
import { SketchPage, strokeSummary } from "./index";
import { circleStroke, lineStroke, rectStroke } from "./model";
import { bytesToBase64, MAX_PHOTO_BYTES, readPhoto, sniffImage } from "./photo";
import { escapeXml, sketchSvg } from "./render";
import { build, canUndo, doc, photo, photoError, resetSketch, selectedId, sketchActions } from "./state";

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]);
const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 16]);
const HEIC = new Uint8Array([0, 0, 0, 24, ...[..."ftypheic"].map((c) => c.charCodeAt(0)), 0, 0, 0, 0]);

const PROFILE: Profile = {
  display_name: "", units: "mm", default_material: "PLA", default_agent: "designer", default_model: null,
  onshape_calls_year_cap: 2500, onshape_calls_run_cap: 200, max_parallel_agents: 2, local_endpoints: {}, theme: "system", setup_completed_at: null,
};
const MODELS: ModelsInfo = {
  default: "anthropic/claude-sonnet-5-5",
  vendors: [{ vendor: "anthropic", key: "env" }, { vendor: "ollama", key: "not_needed" }, { vendor: "openai", key: "none" }],
};
const AGENTS = [{ name: "designer", description: "", model: "inherit" }];

type Call = { method: string; path: string; body: unknown };

function fakeClient(over: { profile?: Partial<Profile>; createRun?: (b: unknown) => Response } = {}) {
  const calls: Call[] = [];
  const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method: init?.method ?? "GET", path: url.pathname, body });
    switch (url.pathname) {
      case "/profile": return json({ ...PROFILE, ...over.profile });
      case "/agents": return json(AGENTS);
      case "/models": return json(MODELS);
      case "/sketches": return json({ sketch_id: "sk_0123456789ab", strokes: body.sketch.strokes.length, dimensions: body.sketch.dimensions.length, photo: null, render: !!body.render_png_b64 });
      case "/runs": return over.createRun ? over.createRun(body) : json({ run_id: "r42" });
      default: return json({ detail: "Not Found" }, 404);
    }
  });
  bindClient(new CoreClient({ baseUrl: "http://127.0.0.1:9", wsUrl: "ws://127.0.0.1:9/events", token: "t", owned: false }, fetchImpl as unknown as typeof fetch));
  return calls;
}

async function flush() { await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }

// jsdom has no canvas: renderPng returns null (the e2e run checks the real PNG render)
beforeEach(() => { resetSketch(); vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null); });
afterEach(() => { cleanup(); resetStore(); resetSketch(); consoleCollapsed.value = false; });

describe("photo checks", () => {
  it("sniffs by content", () => {
    expect(sniffImage(PNG)).toBe("png");
    expect(sniffImage(JPEG)).toBe("jpeg");
    expect(sniffImage(HEIC)).toBe("heic");
    expect(sniffImage(new TextEncoder().encode("GIF89a......"))).toBeNull();
  });

  it("rejects wrong types and anything over 10 MB, accepts PNG/JPEG/HEIC", async () => {
    const big = new File([new Uint8Array(MAX_PHOTO_BYTES + 1)], "big.png", { type: "image/png" });
    const r1 = await readPhoto(big);
    expect(r1.ok).toBe(false);
    if (!r1.ok) expect(r1.error).toMatch(/10 MB/);
    const gif = await readPhoto(new File([new TextEncoder().encode("GIF89a....")], "a.png", { type: "image/png" }));
    expect(gif.ok).toBe(false);
    for (const [bytes, kind] of [[PNG, "png"], [JPEG, "jpeg"], [HEIC, "heic"]] as const) {
      const r = await readPhoto(new File([bytes], `x.${kind}`));
      expect(r.ok && r.photo.kind).toBe(kind);
      if (r.ok) expect(r.photo.data_b64).toBe(bytesToBase64(bytes));
    }
  });
});

describe("render", () => {
  it("draws strokes and escapes label text", () => {
    const d = { strokes: [lineStroke("a", [0, 0], [40, 0]), rectStroke("b", [10, 10], [20, 20])], dimensions: [{ stroke_id: "a", text: "<b>&40", value_mm: null, kind: "note" as const }], notes: "" };
    const svg = sketchSvg(d);
    expect(svg).toContain('<polyline points="0,150 40,150"/>');
    expect(svg).toContain("<polygon");
    expect(svg).toContain("&lt;b&gt;&amp;40");
    expect(svg).not.toContain("<b>");
    expect(escapeXml(`"'`)).toBe("&quot;&apos;");
  });
});

describe("build target", () => {
  it("uses the profile's default agent and model; vision follows the vendor", () => {
    expect(buildTarget(PROFILE, AGENTS, MODELS)).toEqual({ agent: "designer", modelRef: "anthropic/claude-sonnet-5-5", vendor: "anthropic", vision: true, keyMissing: false });
    const local = buildTarget({ ...PROFILE, default_model: "ollama/qwen3" }, AGENTS, MODELS);
    expect(local.vision).toBe(false);
    expect(buildTarget({ ...PROFILE, default_model: "openai/gpt-x" }, AGENTS, MODELS)).toMatchObject({ vision: true, keyMissing: true });
    expect(buildTarget({ ...PROFILE, default_model: "claude-code/opus" }, AGENTS, MODELS).vision).toBe(true);
  });
});

describe("state", () => {
  it("adds, dimensions, deletes, clears with undo/redo", () => {
    sketchActions.addStroke(lineStroke(sketchActions.newStrokeId(), [0, 0], [40, 0]));
    const id = doc.value.strokes[0].id;
    expect(selectedId.value).toBe(id);
    sketchActions.setDimension(id, "40");
    expect(doc.value.dimensions).toEqual([{ stroke_id: id, text: "40", value_mm: 40, kind: "length" }]);
    sketchActions.setDimension(id, "40");              // unchanged: no history entry
    sketchActions.clear();
    expect(doc.value.strokes).toEqual([]);
    expect(selectedId.value).toBeNull();
    sketchActions.undo();
    expect(doc.value.dimensions[0].text).toBe("40");
    sketchActions.undo();
    expect(doc.value.dimensions).toEqual([]);
    sketchActions.redo();
    sketchActions.deleteStroke(id);
    expect(doc.value.dimensions).toEqual([]);
    expect(canUndo.value).toBe(true);
  });

  it("stroke rows read Type · size, never auto-numbered", () => {
    for (const s of [lineStroke("s1", [0, 0], [40, 0]), rectStroke("s2", [0, 0], [20, 10]), circleStroke("s3", [0, 0], 5)]) {
      expect(isAutoNumbered(strokeSummary(s))).toBe(false);
    }
    expect(strokeSummary(lineStroke("s1", [0, 0], [40, 0]))).toBe("Line · 40.0 mm drawn");
    expect(strokeSummary(rectStroke("s2", [0, 0], [20, 10]))).toBe("Rectangle · 20.0 × 10.0 mm drawn");
    expect(strokeSummary(circleStroke("s3", [0, 0], 5))).toBe("Circle · Ø10.0 mm drawn");
  });
});

describe("SketchPage", () => {
  it("disables Build part until there is a stroke or a photo, then builds with the sketch attached", async () => {
    const calls = fakeClient();
    render(<SketchPage />);
    await flush();
    const btn = screen.getByRole("button", { name: "Build part" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByText(/designer · anthropic claude-sonnet-5-5/)).toBeTruthy();

    act(() => sketchActions.addStroke(lineStroke(sketchActions.newStrokeId(), [10, 10], [50, 10])));
    const input = screen.getByLabelText("Dimension label") as HTMLInputElement;
    fireEvent.input(input, { target: { value: "40" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(doc.value.dimensions[0].value_mm).toBe(40);
    fireEvent.input(screen.getByLabelText("Notes for the agent"), { target: { value: "  bracket  " } });

    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    await flush();
    await flush();
    const post = calls.find((c) => c.path === "/sketches");
    expect(post?.body).toMatchObject({
      sketch: { units: "mm", width_mm: 200, height_mm: 150, notes: "bracket", dimensions: [{ text: "40", value_mm: 40, kind: "length" }] },
      photo: null,
    });
    const runs = calls.find((c) => c.path === "/runs");
    expect(runs?.body).toEqual({ agent: "designer", prompt: BUILD_PROMPT, sketch_id: "sk_0123456789ab" });
    expect(build.value).toEqual({ state: "started", run_id: "r42", sketch_id: "sk_0123456789ab" });
    expect(selectedRunId.value).toBe("r42");
    expect(screen.getByRole("status").textContent).toMatch(/Run started/);
  });

  it("opens a collapsed console when the run starts", async () => {
    fakeClient();
    consoleCollapsed.value = true;
    render(<SketchPage />);
    await flush();
    act(() => sketchActions.addStroke(lineStroke(sketchActions.newStrokeId(), [10, 10], [50, 10])));
    fireEvent.click(screen.getByRole("button", { name: "Build part" }));
    await flush();
    await flush();
    expect(consoleCollapsed.value).toBe(false);
  });

  it("shows the core's error with its hint, as text", async () => {
    fakeClient({
      createRun: () => new Response(JSON.stringify({ error: "denied", message: "no API key for anthropic", hint: "<b>set it</b>", retryable: false }), { status: 403, headers: { "Content-Type": "application/json" } }),
    });
    render(<SketchPage />);
    await flush();
    act(() => sketchActions.addStroke(lineStroke(sketchActions.newStrokeId(), [10, 10], [50, 10])));
    fireEvent.click(screen.getByRole("button", { name: "Build part" }));
    await flush();
    await flush();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("no API key for anthropic — <b>set it</b>");
    expect(alert.querySelector("b")).toBeNull();
  });

  it("warns that a non-vision model won't see the photo", async () => {
    fakeClient({ profile: { default_model: "ollama/qwen3" } });
    render(<SketchPage />);
    await flush();
    expect(screen.queryByText(/can't read images/)).toBeNull();
    act(() => sketchActions.setPhoto({ name: "a.png", kind: "png", size: 10, data_b64: "AA==", previewUrl: null }));
    expect(screen.getByText(/can't read images/)).toBeTruthy();
    // a photo alone is enough to build
    expect((screen.getByRole("button", { name: "Build part" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("won't send a sketch over the core's point budget", async () => {
    fakeClient();
    render(<SketchPage />);
    await flush();
    act(() => { for (let i = 0; i < 80; i++) sketchActions.addStroke(circleStroke(sketchActions.newStrokeId(), [50, 50], 1 + i / 10)); });
    expect((screen.getByRole("button", { name: "Build part" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/too much detail/)).toBeTruthy();
  });

  it("shows a photo error from the file input", async () => {
    fakeClient();
    render(<SketchPage />);
    await flush();
    const file = new File([new TextEncoder().encode("not an image")], "notes.txt");
    fireEvent.change(screen.getByLabelText("Choose photo"), { target: { files: [file] } });
    await flush();
    expect(photo.value).toBeNull();
    expect(photoError.value).toMatch(/PNG, JPEG or HEIC/);
    expect(screen.getByRole("alert").textContent).toMatch(/PNG, JPEG or HEIC/);
  });

  it("keyboard: tool keys, undo/redo, delete; ignored while typing", async () => {
    fakeClient();
    render(<SketchPage />);
    await flush();
    fireEvent.keyDown(document, { key: "l" });
    expect(screen.getByRole("button", { name: /^Line/ }).getAttribute("aria-pressed")).toBe("true");
    act(() => sketchActions.addStroke(lineStroke(sketchActions.newStrokeId(), [10, 10], [50, 10])));
    fireEvent.keyDown(document, { key: "z", metaKey: true });
    expect(doc.value.strokes).toHaveLength(0);
    fireEvent.keyDown(document, { key: "z", metaKey: true, shiftKey: true });
    expect(doc.value.strokes).toHaveLength(1);
    const notes = screen.getByLabelText("Notes for the agent");
    fireEvent.keyDown(notes, { key: "Backspace" });
    expect(doc.value.strokes).toHaveLength(1);
    act(() => sketchActions.select(doc.value.strokes[0].id));
    fireEvent.keyDown(document, { key: "Delete" });
    expect(doc.value.strokes).toHaveLength(0);
  });
});
