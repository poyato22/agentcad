// Parts list (BOM) page (R5) on the real core's GET /projects/{id}/bom: made + bought sections,
// per-currency totals that never add USD to EUR, the unpriced count, estimate tags with their
// basis, inline edits (whole-number quantities only), add a bought part with an unknown or set
// currency, remove, CSV export, live refresh on bom_changed. Untrusted fields are shown as text.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Bom } from "../../../api/browser";
import type { Build } from "../../../api/builds";
import type { CoreClient } from "../../../api/client";
import { money } from "../../../lib/money";
import { activeBuildId, builds } from "../../../state/builds";
import { bindClient, resetStore } from "../../../state/store";
import { currencies, parseCurrency, perCurrency, validQty } from "./model";
import { BomPage } from "./index";

const taps = vi.hoisted(() => new Set<(e: unknown) => void>());
vi.mock("../../../api/events", async (orig) => ({
  ...(await orig<typeof import("../../../api/events")>()),
  onCoreEvent: (cb: (e: unknown) => void) => { taps.add(cb); return () => { taps.delete(cb); }; },
}));

const FX = resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui/browser");
const BOM = JSON.parse(readFileSync(resolve(FX, "bom.json"), "utf8")) as Bom;
const build = { id: BOM.project_id, name: "Robot arm", trashed_at: null } as unknown as Build;
const SERVO = BOM.items.find((r) => r.name === "Micro servo SG92R")!;
const AUTO = BOM.items.find((r) => r.source === "auto")!;

function setBuild(b: Build | null) { builds.value = b ? [b] : []; activeBuildId.value = b ? b.id : null; }

describe("BOM model", () => {
  it("formats money per currency and never assumes USD", () => {
    expect(money(35.97, "USD")).toBe("$35.97");
    expect(money(2.4, "EUR")).toBe("€2.40");
    expect(money(3, null)).toBe("3.00 (currency unknown)");
    expect(money(1250, "SEK")).toBe("1,250.00 SEK");
    expect(currencies(BOM.totals.by_currency)).toEqual(["EUR", "USD", "unknown"]);
    expect(perCurrency(BOM.totals.bought)).toBe("€2.40 · $35.70 · 3.00 (currency unknown)");
  });
  it("quantities are whole numbers from 1; currencies are 3-letter codes or empty", () => {
    expect(validQty("6")).toBe(6);
    for (const bad of ["0", "1.5", "-2", "", "abc"]) expect(validQty(bad)).toBeNull();
    expect(parseCurrency(" eur ")).toBe("EUR");
    expect(parseCurrency("")).toBeNull();
    expect(parseCurrency("euro")).toBeUndefined();
  });
});

describe("BomPage", () => {
  let client: Record<"bom" | "bomPatch" | "bomDelete" | "bomAdd" | "bomCsv", ReturnType<typeof vi.fn>>;
  beforeEach(() => {
    resetStore();
    client = {
      bom: vi.fn(async () => structuredClone(BOM)),
      bomPatch: vi.fn(async () => SERVO),
      bomDelete: vi.fn(async () => undefined),
      bomAdd: vi.fn(async () => SERVO),
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

  it("shows one total per currency (never summed), the unpriced count and the estimate basis", async () => {
    setBuild(build);
    render(<BomPage />);
    await screen.findByText("Arm bracket");
    expect(screen.getByRole("heading", { name: "Made in-house" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Bought" })).toBeTruthy();
    const totals = document.querySelector("[data-bom-totals]") as HTMLElement;
    expect(within(totals.querySelector('[data-currency="USD"]') as HTMLElement).getByText("$35.97")).toBeTruthy();
    expect(within(totals.querySelector('[data-currency="EUR"]') as HTMLElement).getByText("€2.40")).toBeTruthy();
    expect(within(totals.querySelector('[data-currency="unknown"]') as HTMLElement).getByText("3.00 (currency unknown)")).toBeTruthy();
    expect(within(totals).getByText(/listed separately, never added together/)).toBeTruthy();
    expect(within(totals).getByText("1 part has no price yet, so it isn't in the totals.")).toBeTruthy();
    expect(within(totals).getByText("Adafruit")).toBeTruthy();
    const est = within(document.querySelector(`[data-bom-line="${AUTO.id}"]`) as HTMLElement).getByText("estimate");
    expect(est.getAttribute("title")).toBe(AUTO.estimate_note);
    // the user-entered rows carry no estimate tag; the formula-like name is plain text
    expect(within(document.querySelector(`[data-bom-line="${SERVO.id}"]`) as HTMLElement).queryByText("estimate")).toBeNull();
    expect(screen.getByText('=HYPERLINK("https://evil.example")')).toBeTruthy();
    // a link shows its domain
    expect(screen.getByText("· www.adafruit.com")).toBeTruthy();
  });

  it("edits a quantity, and refuses a fraction", async () => {
    setBuild(build);
    render(<BomPage />);
    await screen.findByText("Micro servo SG92R");
    const qty = screen.getByLabelText("Quantity of Micro servo SG92R") as HTMLInputElement;
    fireEvent.input(qty, { target: { value: "1.5" } });
    fireEvent.keyDown(qty, { key: "Enter" });
    expect(client.bomPatch).not.toHaveBeenCalled();
    expect(qty.value).toBe("6");                                  // snapped back
    fireEvent.input(qty, { target: { value: "8" } });
    fireEvent.keyDown(qty, { key: "Enter" });
    await waitFor(() => expect(client.bomPatch).toHaveBeenCalledWith(BOM.project_id, SERVO.id, { qty: 8 }));
  });

  it("sets a currency on a row with none, and won't clear a set one", async () => {
    setBuild(build);
    render(<BomPage />);
    await screen.findByText("M3 screws");
    const screws = BOM.items.find((r) => r.name === "M3 screws")!;
    const cur = screen.getByLabelText("Currency of M3 screws") as HTMLInputElement;
    fireEvent.input(cur, { target: { value: "usd" } });
    fireEvent.keyDown(cur, { key: "Enter" });
    await waitFor(() => expect(client.bomPatch).toHaveBeenCalledWith(BOM.project_id, screws.id, { currency: "USD" }));
    client.bomPatch.mockClear();
    const servoCur = screen.getByLabelText("Currency of Micro servo SG92R") as HTMLInputElement;
    fireEvent.input(servoCur, { target: { value: "" } });
    fireEvent.keyDown(servoCur, { key: "Enter" });
    expect(client.bomPatch).not.toHaveBeenCalled();
    expect(servoCur.value).toBe("USD");
  });

  it("adds a bought part with an unknown currency and exports CSV", async () => {
    setBuild(build);
    render(<BomPage />);
    await screen.findByText("Arm bracket");
    fireEvent.click(screen.getByRole("button", { name: "Add bought part" }));
    fireEvent.input(screen.getByLabelText("Part name"), { target: { value: "M3 nut" } });
    fireEvent.input(screen.getByLabelText("Unit cost"), { target: { value: "0.1" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(client.bomAdd).toHaveBeenCalledTimes(1));
    expect(client.bomAdd.mock.calls[0][1]).toEqual({ kind: "bought", name: "M3 nut", vendor: null, qty: 1, unit_cost: 0.1, currency: null });
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(client.bomCsv).toHaveBeenCalledWith(BOM.project_id));
  });

  it("the add row refuses a fractional quantity before sending", async () => {
    setBuild(build);
    render(<BomPage />);
    await screen.findByText("Arm bracket");
    fireEvent.click(screen.getByRole("button", { name: "Add bought part" }));
    fireEvent.input(screen.getByLabelText("Part name"), { target: { value: "Spacer" } });
    fireEvent.input(screen.getByLabelText("Quantity"), { target: { value: "2.5" } });
    // (the browser's own step check also blocks the button; the form checks again on submit)
    fireEvent.submit(screen.getByRole("form", { name: "Add a bought part" }));
    await screen.findByText(/whole number/);
    expect(client.bomAdd).not.toHaveBeenCalled();
  });

  it("removes a row, and re-reads on bom_changed for this build", async () => {
    setBuild(build);
    render(<BomPage />);
    await screen.findByText("Micro servo SG92R");
    fireEvent.click(screen.getByRole("button", { name: "Remove Micro servo SG92R" }));
    await waitFor(() => expect(client.bomDelete).toHaveBeenCalledWith(BOM.project_id, SERVO.id));
    const n = client.bom.mock.calls.length;
    act(() => { for (const t of [...taps]) t({ type: "bom_changed", run_id: null, data: { project_id: "other", item_id: "x" }, ts: 1 }); });
    expect(client.bom.mock.calls.length).toBe(n);
    act(() => { for (const t of [...taps]) t({ type: "bom_changed", run_id: null, data: { project_id: BOM.project_id, item_id: "x" }, ts: 1 }); });
    await waitFor(() => expect(client.bom.mock.calls.length).toBe(n + 1));
  });
});
