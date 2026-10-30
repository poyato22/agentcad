// Part cards (R4) against the real core's shapes: `part_show`'s result carries only ids, the
// card is fetched from GET /browser/cards/{id}; it shows the proxied image, name, price, vendor,
// specs, "Open · <domain>", and its actions target the card's own build. An unknown currency is
// shown and sent as unknown, never as USD.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PartCard } from "../../../../api/browser";
import type { Build } from "../../../../api/builds";
import type { CoreClient } from "../../../../api/client";
import type { Step } from "../../../../api/types";
import { activeBuildId, builds } from "../../../../state/builds";
import { bindClient, resetStore } from "../../../../state/store";
import { resetProxyImages } from "../../../common/ProxyImage";
import type { LogItem } from "../../logModel";
import { partCardView } from "./cardModel";
import { PartCardSlot } from "./index";
import { resetPartCards } from "./PartCard";

type Call = Extract<LogItem, { kind: "call" }>;
const FX = resolve(import.meta.dirname, "../../../../../../core/tests/fixtures/ui/browser");
const real = <T,>(name: string): T => JSON.parse(readFileSync(resolve(FX, name), "utf8")) as T;
const RESULT = real<Record<string, unknown>>("part_show_result.json");
const CARD = real<PartCard>("browser_card.json");

function call(result: Record<string, unknown> | null, ok: boolean | null = true): Call {
  const step = result === null ? null
    : { id: 2, run_id: "r1", ts: 1, kind: "tool_result", payload: { output: { ok, data: result } }, evidence_hash: "", prev_hash: null, collapsed: false } as Step;
  return { kind: "call", callId: "c1", name: "mcp__mises__part_show", input: { snapshot_id: CARD.snapshot_id }, result: step, ok, summary: null,
    startedTs: 1, endedTs: 2, hint: null, attached: [], runId: "r1" } as Call;
}

describe("partCardView", () => {
  it("reads the card id from the real ids-only result", () => {
    expect(partCardView(call(RESULT))?.cardId).toBe(CARD.id);
  });
  it("has no card while running, and ignores other tools", () => {
    expect(partCardView(call(null, null))?.cardId).toBeNull();
    expect(partCardView({ ...call(RESULT), name: "mirror_measure" } as Call)).toBeNull();
  });
});

describe("PartCardSlot", () => {
  let client: { partCard: ReturnType<typeof vi.fn>; bomAdd: ReturnType<typeof vi.fn>; catalogInsert: ReturnType<typeof vi.fn>; snapshotImage: ReturnType<typeof vi.fn> };
  const setCard = (c: PartCard) => client.partCard.mockResolvedValue(c);
  beforeEach(() => {
    resetStore(); resetProxyImages(); resetPartCards();
    client = {
      partCard: vi.fn(async () => CARD),
      bomAdd: vi.fn(async () => ({})),
      catalogInsert: vi.fn(async () => ({ part_id: "p1", existing: false, metrics: null, meta: null })),
      snapshotImage: vi.fn(async () => { throw new Error("no image bytes in unit tests"); }),
    };
    bindClient(client as unknown as CoreClient);
    builds.value = [{ id: CARD.project_id, name: "Robot arm", trashed_at: null } as unknown as Build, { id: "other", name: "Other build", trashed_at: null } as unknown as Build];
    activeBuildId.value = "other";            // a different tab is active: the card must still target its own build
  });
  afterEach(() => { cleanup(); bindClient(null); activeBuildId.value = null; builds.value = []; });

  it("fetches the card and renders its untrusted fields as text, with the domain on Open", async () => {
    render(<PartCardSlot call={call(RESULT)} />);
    await screen.findByText("Micro servo - TowerPro SG92R");
    expect(client.partCard).toHaveBeenCalledWith(CARD.id);
    expect(screen.getByText("$5.95")).toBeTruthy();
    expect(screen.getByText("TowerPro")).toBeTruthy();
    expect(screen.getByText("weight 9 g")).toBeTruthy();
    const open = screen.getByRole("link", { name: "Open · www.adafruit.com" });
    expect(open.getAttribute("href")).toBe("https://www.adafruit.com/product/169");
    expect(document.querySelector('[data-proxy-img]')).toBeTruthy();       // proxied image slot, never a raw <img src>
  });

  it("Add to parts list targets the card's build, sends only core fields, then shows In parts list", async () => {
    render(<PartCardSlot call={call(RESULT)} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Add .* to the parts list$/ }));
    await waitFor(() => expect(client.bomAdd).toHaveBeenCalledTimes(1));
    const [pid, row] = client.bomAdd.mock.calls[0];
    expect(pid).toBe(CARD.project_id);
    expect(Object.keys(row).sort()).toEqual(["catalog_id", "currency", "kind", "name", "qty", "specs", "unit_cost", "url", "vendor"]);
    expect(row).toMatchObject({ kind: "bought", name: CARD.name, qty: 1, unit_cost: 5.95, currency: "USD" });
    await screen.findByText("Added to the parts list of Robot arm");
    expect(screen.getByRole("button", { name: /is in the parts list/ }).hasAttribute("disabled")).toBe(true);
  });

  it("Insert names the card's build and inserts there", async () => {
    render(<PartCardSlot call={call(RESULT)} />);
    fireEvent.click(await screen.findByRole("button", { name: "Insert Micro servo - TowerPro SG92R into Robot arm" }));
    await waitFor(() => expect(client.catalogInsert).toHaveBeenCalledWith(CARD.catalog_id, CARD.project_id));
  });

  it("an unknown currency is shown and sent as unknown, not USD", async () => {
    setCard({ ...CARD, currency: null });
    render(<PartCardSlot call={call(RESULT)} />);
    await screen.findByText("5.95 (currency unknown)");
    fireEvent.click(screen.getByRole("button", { name: /^Add .* to the parts list$/ }));
    await waitFor(() => expect(client.bomAdd).toHaveBeenCalled());
    expect(client.bomAdd.mock.calls[0][1].currency).toBeNull();
  });

  it("a card that isn't catalog-backed has no Insert", async () => {
    setCard({ ...CARD, catalog_backed: false, catalog_id: null });
    render(<PartCardSlot call={call(RESULT)} />);
    await screen.findByText(CARD.name);
    expect(screen.queryByRole("button", { name: /^Insert/ })).toBeNull();
  });

  it("a card that can't be loaded says so", async () => {
    client.partCard.mockRejectedValue(new Error("boom"));
    render(<PartCardSlot call={call(RESULT)} />);
    await screen.findByText(/Couldn't load the part card/);
  });
});
