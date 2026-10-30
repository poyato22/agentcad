// Part cards (R4): the `part_show` tool call renders a card with image (proxied only), name,
// price, vendor, specs and the Add / Insert / Open actions. Untrusted text is shown plainly; a
// raw-URL image is dropped so the card never fetches a page's host.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../../../api/client";
import type { Step } from "../../../../api/types";
import { activeBuildId } from "../../../../state/builds";
import { bindClient, resetStore } from "../../../../state/store";
import { resetProxyImages } from "../../../common/ProxyImage";
import type { LogItem } from "../../logModel";
import { partCardView, priceText } from "./cardModel";
import { PartCardSlot } from "./index";

type Call = Extract<LogItem, { kind: "call" }>;

function call(input: Record<string, unknown>, resultData?: Record<string, unknown>, ok: boolean | null = true): Call {
  const result = resultData === undefined && ok === null ? null
    : { id: 2, run_id: "r1", ts: 1, kind: "tool_result", payload: { output: { ok: ok !== false, data: resultData ?? {} } }, evidence_hash: "", prev_hash: null, collapsed: false } as Step;
  return { kind: "call", callId: "c1", name: "mcp__mises__part_show", input, result, ok, summary: null, startedTs: 1, endedTs: 2, hint: null, attached: [], runId: "r1" } as Call;
}

describe("partCardView", () => {
  it("parses the input and lets the core's result data (proxied image) win", () => {
    const v = partCardView(call(
      { name: "Falcon 2207", price: 24.5, currency: "USD", vendor: "ServoCity", image: "https://cdn.evil/x.png", catalog_id: "cat_x", specs: [{ key: "KV", value: 1750 }] },
      { image: "/browser/snapshots/s1/images/0" },
    ));
    expect(v).not.toBeNull();
    expect(v?.card.name).toBe("Falcon 2207");
    expect(v?.card.image).toBe("/browser/snapshots/s1/images/0");  // the raw input URL was dropped
    expect(v?.card.catalog_id).toBe("cat_x");
  });
  it("ignores non part_show calls", () => {
    expect(partCardView({ ...call({}), name: "mcp__mises__mirror_measure" } as Call)).toBeNull();
  });
});

describe("priceText", () => {
  it("formats with a symbol or falls back to the code", () => {
    expect(priceText(24.5, "USD")).toBe("$24.50");
    expect(priceText(24.5, "SEK")).toBe("24.50 SEK");
    expect(priceText(null, "USD")).toBeNull();
  });
});

describe("PartCardSlot", () => {
  let client: { bomAdd: ReturnType<typeof vi.fn>; catalogInsert: ReturnType<typeof vi.fn>; snapshotImage: ReturnType<typeof vi.fn> };
  beforeEach(() => {
    resetStore(); resetProxyImages();
    client = {
      bomAdd: vi.fn(async () => ({ id: "l1" })),
      catalogInsert: vi.fn(async () => ({ part_id: "p1", existing: false, metrics: null, meta: null })),
      snapshotImage: vi.fn(async () => { throw new Error("no image in test"); }),
    };
    bindClient(client as unknown as CoreClient);
    activeBuildId.value = "b1";
  });
  afterEach(() => { cleanup(); bindClient(null); activeBuildId.value = null; });

  it("renders untrusted fields as text and the proxied-image placeholder", () => {
    render(<PartCardSlot call={call({ name: "Falcon 2207", price: 24.5, currency: "USD", vendor: "ServoCity", image: "https://cdn.evil/x.png", specs: [{ key: "KV", value: 1750 }, { key: "mass", value: 27.5, unit: "g" }] })} />);
    expect(screen.getByText("Falcon 2207")).toBeTruthy();
    expect(screen.getByText("$24.50")).toBeTruthy();
    expect(screen.getByText("ServoCity")).toBeTruthy();
    expect(screen.getByText("mass 27.5 g")).toBeTruthy();
    // raw-URL image never reaches an <img src>: a placeholder stands in
    expect(document.querySelector('[data-proxy-img="none"]')).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
  });

  it("Add to parts list calls bomAdd for the active build", async () => {
    render(<PartCardSlot call={call({ name: "Servo", catalog_id: "cat_x" })} />);
    fireEvent.click(screen.getByRole("button", { name: /add .* to the parts list/i }));
    await waitFor(() => expect(client.bomAdd).toHaveBeenCalledTimes(1));
    expect(client.bomAdd.mock.calls[0][0]).toBe("b1");
    expect(client.bomAdd.mock.calls[0][1]).toMatchObject({ kind: "bought", name: "Servo", catalog_id: "cat_x", source: "user" });
    await screen.findByText("Added to parts list");
  });

  it("Insert into build shows only for catalog-backed parts", async () => {
    const { rerender } = render(<PartCardSlot call={call({ name: "Shown only", snapshot_id: "s1" })} />);
    expect(screen.queryByRole("button", { name: /insert .* into the build/i })).toBeNull();
    rerender(<PartCardSlot call={call({ name: "Servo", catalog_id: "cat_x" })} />);
    fireEvent.click(screen.getByRole("button", { name: /insert .* into the build/i }));
    await waitFor(() => expect(client.catalogInsert).toHaveBeenCalledWith("cat_x", "b1"));
  });

  it("without a build the actions explain what to do", async () => {
    activeBuildId.value = null;
    render(<PartCardSlot call={call({ name: "Servo", catalog_id: "cat_x" })} />);
    fireEvent.click(screen.getByRole("button", { name: /add .* to the parts list/i }));
    await screen.findByText("Open a build first");
    expect(client.bomAdd).not.toHaveBeenCalled();
  });
});
