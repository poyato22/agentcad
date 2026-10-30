// Parts list (BOM) page (R5): made + bought sections, totals by vendor, inline edit of qty/cost/
// notes, add a bought part, remove a line, CSV export. Untrusted part fields are shown as text.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Bom } from "../../../api/browser";
import type { CoreClient } from "../../../api/client";
import type { Build } from "../../../api/builds";
import { activeBuildId, builds } from "../../../state/builds";
import { bindClient, resetStore } from "../../../state/store";
import { lineTotal, money, specsLine } from "./model";
import { BomPage } from "./index";

const build = { id: "b1", name: "Robot arm", trashed_at: null, created_at: 1, updated_at: 2 } as unknown as Build;

const bom: Bom = {
  lines: [
    { id: "l1", kind: "made", part_id: "bracket", catalog_id: null, name: "Arm bracket", qty: 2, unit_cost: 0.84, currency: "USD", vendor: null, url: null, specs: [], notes: "", source: "agent", estimated: true },
    { id: "l2", kind: "bought", part_id: null, catalog_id: "cat_x", name: "Falcon 2207", qty: 4, unit_cost: 24.5, currency: "USD", vendor: "ServoCity", url: "https://servocity.com/falcon", specs: [{ key: "KV", value: 1750, unit: null }], notes: "", source: "agent", estimated: false },
  ],
  totals: { amount: 99.68, currency: "USD", by_vendor: [{ vendor: "ServoCity", amount: 98, currency: "USD" }, { vendor: null, amount: 1.68, currency: "USD" }], made: 1.68, bought: 98, estimated: true },
};

function setBuild(b: Build | null) {
  builds.value = b ? [b] : [];
  activeBuildId.value = b ? b.id : null;
}

describe("BOM model", () => {
  it("formats money, line totals and spec lines", () => {
    expect(money(99.68, "USD")).toBe("$99.68");
    expect(money(1250, "SEK")).toBe("1,250.00 SEK");
    expect(lineTotal(bom.lines[1])).toBe(98);
    expect(lineTotal({ ...bom.lines[1], unit_cost: null })).toBeNull();
    expect(specsLine(bom.lines[1])).toBe("KV 1750");
  });
});

describe("BomPage", () => {
  let client: { bom: ReturnType<typeof vi.fn>; bomPatch: ReturnType<typeof vi.fn>; bomDelete: ReturnType<typeof vi.fn>; bomAdd: ReturnType<typeof vi.fn>; bomCsv: ReturnType<typeof vi.fn> };
  beforeEach(() => {
    resetStore();
    client = {
      bom: vi.fn(async () => structuredClone(bom)),
      bomPatch: vi.fn(async () => bom.lines[0]),
      bomDelete: vi.fn(async () => undefined),
      bomAdd: vi.fn(async () => bom.lines[1]),
      bomCsv: vi.fn(async () => new Blob(["csv"], { type: "text/csv" })),
    };
    bindClient(client as unknown as CoreClient);
  });
  afterEach(() => { cleanup(); bindClient(null); setBuild(null); });

  it("prompts to open a build when none is active", () => {
    setBuild(null);
    render(<BomPage />);
    expect(screen.getByText(/Open a build to see its parts list/)).toBeTruthy();
  });

  it("shows made and bought sections with totals and an estimate label", async () => {
    setBuild(build);
    render(<BomPage />);
    await waitFor(() => expect(client.bom).toHaveBeenCalledWith("b1"));
    await screen.findByText("Arm bracket");
    expect(screen.getByRole("heading", { name: "Made in-house" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Bought" })).toBeTruthy();
    // made line is an estimate
    const made = document.querySelector('[data-bom-line="l1"]') as HTMLElement;
    expect(within(made).getByText("estimate")).toBeTruthy();
    // totals
    const totals = document.querySelector("[data-bom-totals]") as HTMLElement;
    expect(within(totals).getByText("$99.68")).toBeTruthy();
    expect(within(totals).getByText("ServoCity")).toBeTruthy();
    expect(within(totals).getByText(/estimated costs/)).toBeTruthy();
  });

  it("edits a quantity on Enter via bomPatch", async () => {
    setBuild(build);
    render(<BomPage />);
    await screen.findByText("Falcon 2207");
    const qty = screen.getByLabelText("Quantity of Falcon 2207") as HTMLInputElement;
    fireEvent.input(qty, { target: { value: "6" } });
    fireEvent.keyDown(qty, { key: "Enter" });
    await waitFor(() => expect(client.bomPatch).toHaveBeenCalledWith("b1", "l2", { qty: 6 }));
  });

  it("adds a bought part and exports CSV", async () => {
    setBuild(build);
    render(<BomPage />);
    await screen.findByText("Arm bracket");
    fireEvent.click(screen.getByRole("button", { name: "Add bought part" }));
    fireEvent.input(screen.getByLabelText("Part name"), { target: { value: "M3 screw" } });
    fireEvent.input(screen.getByLabelText("Unit cost"), { target: { value: "0.1" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(client.bomAdd).toHaveBeenCalledTimes(1));
    expect(client.bomAdd.mock.calls[0][1]).toMatchObject({ kind: "bought", name: "M3 screw", unit_cost: 0.1, source: "user" });

    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(client.bomCsv).toHaveBeenCalledWith("b1"));
  });

  it("removes a line", async () => {
    setBuild(build);
    render(<BomPage />);
    await screen.findByText("Falcon 2207");
    fireEvent.click(screen.getByRole("button", { name: "Remove Falcon 2207" }));
    await waitFor(() => expect(client.bomDelete).toHaveBeenCalledWith("b1", "l2"));
  });
});
