// CoreClient: Plan 4 drawing / printer / slice / print routes (core server/drawing_api.py,
// print_api.py). Responses are the real core's answers (core/tests/fixtures/ui).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { CoreClient, CoreError } from "./client";
import type { CoreInfo } from "./types";

const info: CoreInfo = { baseUrl: "http://127.0.0.1:7400", wsUrl: "ws://127.0.0.1:7400/events", token: "tok-abc", owned: false };
const FX = resolve(import.meta.dirname, "../../test/fixtures/core-ui");
const fx = (name: string): unknown => JSON.parse(readFileSync(resolve(FX, name), "utf8"));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function clientWith(impl: (url: string, init: RequestInit) => Promise<Response>) {
  const f = vi.fn(impl);
  return { client: new CoreClient(info, f as unknown as typeof fetch), f };
}

const authOf = (init: RequestInit) => new Headers(init.headers).get("authorization");

describe("CoreClient drawing routes", () => {
  it("drawing_info_parses_the_real_answer_and_passes_material", async () => {
    const { client, f } = clientWith(async () => json(fx("drawing.json")));
    const d = await client.drawingInfo("mount_plate", "PETG");
    expect(d.scale_text).toBe("2:1");
    expect(d.views).toEqual(["front", "top", "right", "iso"]);
    expect(f.mock.calls[0][0]).toBe("http://127.0.0.1:7400/parts/mount_plate/drawing.json?material=PETG");
    expect(authOf(f.mock.calls[0][1])).toBe("Bearer tok-abc");
  });

  it("drawing_svg_and_dxf_come_back_as_typed_blobs", async () => {
    const svg = readFileSync(resolve(FX, "drawing.svg"), "utf8");
    const { client, f } = clientWith(async (url) => (url.includes(".svg")
      ? new Response(svg, { headers: { "content-type": "image/svg+xml" } })
      : new Response("0\nSECTION\n", { headers: { "content-type": "image/vnd.dxf" } })));
    const s = await client.drawingFile("mount_plate", "svg");
    expect(s.type).toBe("image/svg+xml");
    expect(await s.text()).toContain("<svg");
    const d = await client.drawingFile("mount_plate", "dxf", "PLA");
    expect(d.type).toBe("image/vnd.dxf");
    expect(f.mock.calls.map((c) => c[0])).toEqual([
      "http://127.0.0.1:7400/parts/mount_plate/drawing.svg",
      "http://127.0.0.1:7400/parts/mount_plate/drawing.dxf?material=PLA",
    ]);
  });

  it("drawing_svg_is_typed_svg_even_when_the_header_is_missing", async () => {
    const { client } = clientWith(async () => new Response("<svg/>"));
    expect((await client.drawingFile("p_x", "svg")).type).toBe("image/svg+xml");
  });

  it("drawing_not_built_is_a_not_found_CoreError_with_hint", async () => {
    const { client } = clientWith(async () => json({ error: "not_found", message: "no built part 'ghost'", hint: "build it with mirror_part_create", retryable: false }, 404));
    const e = await client.drawingFile("ghost", "svg").catch((x: unknown) => x);
    expect(e).toBeInstanceOf(CoreError);
    expect((e as CoreError).code).toBe("not_found");
    expect((e as CoreError).hint).toBe("build it with mirror_part_create");
  });
});

describe("CoreClient printer routes", () => {
  it("capabilities_printers_status_parse_the_real_answers", async () => {
    const answers: Record<string, unknown> = {
      "/print/capabilities": fx("print_capabilities.json"), "/printers": fx("printers.json"),
      "/printers/bench_mk4/status": fx("printer_status_printing.json"),
    };
    const { client } = clientWith(async (url) => json(answers[new URL(url).pathname]));
    const caps = await client.printCapabilities();
    expect(caps.slicer.install_command).toBe("brew install --cask prusaslicer");
    expect(caps.kinds.find((k) => k.kind === "bambu")?.note).toBe("coming later");
    expect((await client.printers())[0].has_key).toBe(true);
    const st = await client.printerStatus("bench_mk4");
    expect(st.state).toBe("PRINTING");
    expect(st.job?.progress).toBe(42);
  });

  it("unreachable_status_parses", async () => {
    const { client } = clientWith(async () => json(fx("printer_status_unreachable.json")));
    const st = await client.printerStatus("bench_mk4");
    expect(st.reachable).toBe(false);
    expect(st.hint).toMatch(/same network/);
  });

  it("add_printer_sends_the_key_only_in_the_body_and_strips_any_key_from_the_answer", async () => {
    const { client, f } = clientWith(async () => json({ ...(fx("printer_add.json") as object), api_key: "leak" }));
    const p = await client.addPrinter({ name: "Bench MK4", host: "192.168.1.20", api_key: "sekret-123", model: "MK4" });
    expect(p).not.toHaveProperty("api_key");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:7400/printers");
    expect(url).not.toContain("sekret");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ name: "Bench MK4", host: "192.168.1.20", api_key: "sekret-123", model: "MK4" });
  });

  it("delete_printer", async () => {
    const { client, f } = clientWith(async () => json(fx("printer_delete.json")));
    await client.deletePrinter("bench_mk4");
    expect(f.mock.calls[0][0]).toBe("http://127.0.0.1:7400/printers/bench_mk4");
    expect(f.mock.calls[0][1].method).toBe("DELETE");
  });
});

describe("CoreClient slice and print routes", () => {
  it("slice_start_get_cancel", async () => {
    const answers: Record<string, unknown> = {
      "POST /slices": fx("slice_start.json"), "GET /slices/slice_a": fx("slice.json"), "POST /slices/slice_a/cancel": { ok: true },
    };
    const { client, f } = clientWith(async (url, init) => json(answers[`${init.method} ${new URL(url).pathname}`]));
    const s = await client.startSlice({ part_id: "mount_plate", printer_id: "bench_mk4", material: "PETG" });
    expect(s.status).toBe("running");
    expect(JSON.parse(String(f.mock.calls[0][1].body))).toEqual({ part_id: "mount_plate", printer_id: "bench_mk4", material: "PETG" });
    const got = await client.getSlice("slice_a");
    expect(got.estimate?.filament_g).toBe(7.51);
    await client.cancelSlice("slice_a");
    expect(f.mock.calls[2][0]).toBe("http://127.0.0.1:7400/slices/slice_a/cancel");
  });

  it("slicer_missing_is_424_with_the_install_hint", async () => {
    const { client } = clientWith(async () => json({ error: "tool_failed", message: "PrusaSlicer is not installed", hint: "install it with: brew install --cask prusaslicer", retryable: false }, 424));
    const e = (await client.startSlice({ part_id: "p_x", printer_id: "q_x" }).catch((x: unknown) => x)) as CoreError;
    expect(e.status).toBe(424);
    expect(e.hint).toBe("install it with: brew install --cask prusaslicer");
  });

  it("slice_preview_is_a_png_blob", async () => {
    const { client, f } = clientWith(async () => new Response(new Uint8Array([0x89, 0x50, 0x4e, 0x47]), { headers: { "content-type": "image/png" } }));
    const b = await client.slicePreview("slice_a");
    expect(b.type).toBe("image/png");
    expect(b.size).toBe(4);
    expect(f.mock.calls[0][0]).toBe("http://127.0.0.1:7400/slices/slice_a/preview.png");
  });

  it("start_print_sends_exactly_slice_and_printer_and_parses_the_call", async () => {
    const answers: Record<string, unknown> = { "POST /prints": fx("print_start.json"), "GET /prints/ui_1": fx("print_done.json") };
    const { client, f } = clientWith(async (url, init) => json(answers[`${init.method} ${new URL(url).pathname}`]));
    const p = await client.startPrint("slice_a", "bench_mk4");
    expect(p.status).toBe("running");
    expect(JSON.parse(String(f.mock.calls[0][1].body))).toEqual({ slice_id: "slice_a", printer_id: "bench_mk4" });
    const c = await client.getPrint("ui_1");
    expect(c.status).toBe("done");
    expect(c.output?.summary).toMatch(/sent/);
  });

  it("print_refused_for_printer_state_is_409", async () => {
    const { client } = clientWith(async () => json({ error: "denied", message: "printer 'Bench MK4' reports ERROR; not sending a job", hint: null, retryable: false, state: "ERROR" }, 409));
    const e = (await client.startPrint("slice_a", "bench_mk4").catch((x: unknown) => x)) as CoreError;
    expect(e.status).toBe(409);
    expect(e.message).toContain("ERROR");
  });
});
