import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { partSourceSchema } from "../../api/builds";
import { CoreError, type CoreClient } from "../../api/client";
import type { PartSummary } from "../../api/types";
import { resetBuilds } from "../../state/builds";
import { onPartChanged, resetPartEdits, userEdits } from "../../state/partEdits";
import { resetSources } from "../../state/partSource";
import { bindClient, parts, resetStore, selectedPartId } from "../../state/store";
import { resetVarEditors, Variables } from "./Variables";
import { VARS_DEBOUNCE_MS } from "./varsEdit";

const FX = resolve(import.meta.dirname, "../../../test/fixtures/core-ui");
const fx = <T,>(n: string): T => JSON.parse(readFileSync(resolve(FX, n), "utf8")) as T;
const SOURCE = partSourceSchema.parse(fx("part_source.json"));     // thickness: 2..12 mm (annotated); length: inferred
const ARM: PartSummary = { part_id: "quad_arm", id: "quad_arm", label: "Quad arm", vars: { thickness: 4, length: 60 }, metrics: null, mtime: 1, version: 1, kind: "part" };

let client: { partSource: ReturnType<typeof vi.fn>; setPartVars: ReturnType<typeof vi.fn> };

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false });
  resetStore(); resetBuilds(); resetPartEdits(); resetSources(); resetVarEditors();
  client = { partSource: vi.fn().mockResolvedValue(SOURCE), setPartVars: vi.fn() };
  bindClient(client as unknown as CoreClient);
  parts.value = [{ ...ARM, metrics: { valid: true, volume_mm3: 1, area_mm2: 1, mass_g: 1, bbox: { min: [0, 0, 0], max: [1, 1, 1], size: [1, 1, 1] }, center_of_mass: [0, 0, 0], faces: 6, edges: 12, holes: [], solids: 1 } }];
  selectedPartId.value = "quad_arm";
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };

describe("variables panel", () => {
  it("shows a scale and field per var with the script's range and unit; a guessed range is marked", async () => {
    render(<Variables />);
    await flush();
    const scale = screen.getByRole("slider", { name: "thickness (mm)" }) as HTMLInputElement;
    expect([scale.min, scale.max, scale.step]).toEqual(["2", "12", "0.5"]);
    expect(document.querySelector('[data-var="thickness"]')?.classList.contains("inferred")).toBe(false);
    expect(document.querySelector('[data-var="length"]')?.classList.contains("inferred")).toBe(true);
    expect(document.querySelector('[data-var="length"] .var-range')?.textContent).toBe("~15 – 240");
    expect((screen.getByTestId("var-thickness") as HTMLInputElement).value).toBe("4");
  });

  it("debounces a drag into one POST /vars on the version you started from, then writes your line", async () => {
    client.setPartVars.mockResolvedValue({ part_id: "quad_arm", version: 2, vars: { thickness: 5, length: 60 } });
    render(<Variables />);
    await flush();
    const scale = screen.getByRole("slider", { name: "thickness (mm)" });
    fireEvent.input(scale, { target: { value: "4.5" } });
    fireEvent.input(scale, { target: { value: "5" } });
    expect(client.setPartVars).not.toHaveBeenCalled();
    expect(screen.getByRole("status").textContent).toContain("Rebuilding when you stop");
    await act(async () => { vi.advanceTimersByTime(VARS_DEBOUNCE_MS + 5); });
    await flush();
    expect(client.setPartVars).toHaveBeenCalledTimes(1);
    expect(client.setPartVars).toHaveBeenCalledWith("quad_arm", { thickness: 5 }, 1);
    expect(userEdits.value.map((l) => l.text)).toEqual(["changed thickness 4 → 5 mm on Quad arm"]);
  });

  it("an agent's edit meanwhile: the 409 asks Reload / Keep mine, and Keep mine re-sends on the new version", async () => {
    const stale = fx<{ body: Record<string, unknown> }>("part_vars_stale.json");
    client.setPartVars
      .mockRejectedValueOnce(new CoreError("stale_version", String(stale.body.message), { status: 409, body: stale.body }))
      .mockResolvedValueOnce({ part_id: "quad_arm", version: 3, vars: { thickness: 7, length: 60 } });
    render(<Variables />);
    await flush();
    act(() => onPartChanged({ type: "part_changed", run_id: null, ts: 1, data: { part_id: "quad_arm", version: 2, by: "agent", run_id: null, summary: "set thickness=5.0" } }));
    fireEvent.input(screen.getByTestId("var-thickness"), { target: { value: "7" } });
    fireEvent.keyDown(screen.getByTestId("var-thickness"), { key: "Enter" });
    await flush();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("An agent changed this part (now thickness 5) while you were editing.");
    fireEvent.click(screen.getByRole("button", { name: "Keep mine" }));
    await flush();
    expect(client.setPartVars.mock.calls.map((c) => c[2])).toEqual([2, 2]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("Reload drops your value", async () => {
    client.setPartVars.mockRejectedValueOnce(new CoreError("stale_version", "conflict", { status: 409, body: { current_version: 2, vars: { thickness: 5 } } }));
    render(<Variables />);
    await flush();
    fireEvent.input(screen.getByTestId("var-thickness"), { target: { value: "9" } });
    fireEvent.keyDown(screen.getByTestId("var-thickness"), { key: "Enter" });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await flush();
    expect((screen.getByTestId("var-thickness") as HTMLInputElement).value).toBe("4");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
