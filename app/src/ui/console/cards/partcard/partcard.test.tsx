// Part cards (R4) against the real core's shapes: `part_show`'s result carries only ids, the
// card is fetched from GET /browser/cards/{id}; it shows the proxied image, name, price, vendor,
// specs and "Open · <domain>"; it is display-only (P10 E15: the core inserts and lists it). An unknown currency is
// shown as unknown, never as USD.
import { cleanup, render, screen } from "@testing-library/preact";
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

  it("is display-only (P10 E15): no Add to parts list / Insert, nothing is sent from the card", async () => {
    // an older core's result (no part_id / bom_id): the real dump's result now names both
    render(<PartCardSlot call={call({ ...RESULT, part_id: null, item_id: null, bom_id: null })} />);
    await screen.findByText(CARD.name);
    expect(screen.queryByRole("button", { name: /parts list|^Insert/ })).toBeNull();
    expect(client.bomAdd).not.toHaveBeenCalled();
    expect(client.catalogInsert).not.toHaveBeenCalled();
    // an older core's result names neither: no status claimed
    expect(document.querySelector("[data-in-build], [data-in-list]")).toBeNull();
  });

  it("says where the core put the part when the result names it (core C1 inserts and lists it)", async () => {
    render(<PartCardSlot call={call({ ...RESULT, part_id: "servo_sg92r", bom_id: "bom_7" })} />);
    await screen.findByText(CARD.name);
    expect(document.querySelector("[data-in-build=servo_sg92r]")?.textContent).toBe("In Robot arm");
    expect(document.querySelector("[data-in-list=bom_7]")?.textContent).toBe("In the parts list");
  });

  it("an unknown currency is shown as unknown, not USD", async () => {
    setCard({ ...CARD, currency: null });
    render(<PartCardSlot call={call(RESULT)} />);
    await screen.findByText("5.95 (currency unknown)");
  });

  it("a card that can't be loaded says so", async () => {
    client.partCard.mockRejectedValue(new Error("boom"));
    render(<PartCardSlot call={call(RESULT)} />);
    await screen.findByText(/Couldn't load the part card/);
  });
});
