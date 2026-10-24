import { act, cleanup, render, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { PartSummary } from "../../api/types";
import { bindClient, inflight, parts, resetStore, selectedPartId } from "../../state/store";
import type { ViewerHandle } from "./scene";
import { Viewer, highlighted } from "./Viewer";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// The core's real GLB fixture. Copied into the test realm's ArrayBuffer: jsdom's global differs from Node's
// and GLTFLoader checks `instanceof ArrayBuffer`.
const fixtureBytes = (): ArrayBuffer =>
  new Uint8Array(readFileSync(resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/mount_plate.glb"))).buffer as ArrayBuffer;

const handle = vi.hoisted(() => ({ setPart: vi.fn(), setMode: vi.fn(), setHighlight: vi.fn(), fit: vi.fn(), resize: vi.fn(), dispose: vi.fn() }));
vi.mock("./scene", async (orig) => ({ ...(await orig<typeof import("./scene")>()), createViewer: (): ViewerHandle => handle }));

const mk = (mtime: number): PartSummary => ({ part_id: "mount_plate", vars: {}, metrics: null, mtime });
const bind = (partGlb: unknown) => bindClient({ partGlb } as unknown as CoreClient);

beforeEach(() => { resetStore(); for (const f of Object.values(handle)) f.mockClear(); });
afterEach(() => { cleanup(); resetStore(); });

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
    await waitFor(() => expect(handle.setPart).toHaveBeenCalledTimes(1));
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
});
