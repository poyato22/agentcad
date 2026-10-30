// The agent Browser panel (R3) on the real core's ?full=1 snapshots: a site search with its
// results, a product page with its escaped text, table and proxied images, and a page that
// blocked automated access. Untrusted page text is never executed. It refetches when the run
// reads another page.
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../../api/client";
import type { AgentRun } from "../../../api/types";
import { bindClient, resetStore, runs, selectedRunId } from "../../../state/store";
import { resetProxyImages } from "../../common/ProxyImage";
import { BrowserPanel } from "./BrowserPanel";
import { fmtFetched } from "./model";

// the panel listens through onCoreEvent; capture its taps to deliver events by hand
const taps = vi.hoisted(() => new Set<(e: unknown) => void>());
vi.mock("../../../api/events", async (orig) => ({
  ...(await orig<typeof import("../../../api/events")>()),
  onCoreEvent: (cb: (e: unknown) => void) => { taps.add(cb); return () => { taps.delete(cb); }; },
}));
const dispatchForTest = (e: unknown) => { for (const t of [...taps]) t(e); };

const FX = resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui/browser");
const FULL = JSON.parse(readFileSync(resolve(FX, "browser_list_full.json"), "utf8")) as { id: string; kind: string }[];

const run: AgentRun = {
  id: "r1", parent_id: null, agent: "engineer", provider: "anthropic", model: "m", task: "robot arm",
  status: "running", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1, updated_at: 2,
};
/** Waits for an element (waitFor retries only on a throw). */
const el = (sel: string) => waitFor(() => { const e = document.querySelector(sel); if (!e) throw new Error(`no ${sel}`); return e as HTMLElement; });
function setRun() { runs.value = new Map([[run.id, run]]); selectedRunId.value = run.id; }

describe("fmtFetched", () => {
  it("gives a relative time", () => {
    expect(fmtFetched(10_000, 10_000)).toBe("just now");
    expect(fmtFetched(10_000 - 600, 10_000)).toBe("10 min ago");
    expect(fmtFetched(10_000 - 7200, 10_000)).toBe("2 h ago");
  });
});

describe("BrowserPanel", () => {
  let client: { runBrowser: ReturnType<typeof vi.fn>; snapshotImage: ReturnType<typeof vi.fn> };
  beforeEach(() => {
    resetStore(); resetProxyImages();
    client = { runBrowser: vi.fn(async () => structuredClone(FULL)), snapshotImage: vi.fn(async () => { throw new Error("no bytes in unit tests"); }) };
    bindClient(client as unknown as CoreClient);
  });
  afterEach(() => { cleanup(); bindClient(null); });

  it("shows the product page as escaped text with its table, images and Open in browser", async () => {
    setRun();
    render(<BrowserPanel />);
    await waitFor(() => expect(client.runBrowser).toHaveBeenCalledWith("r1"));
    const page = await el('[data-kind="page"]:not([data-blocked])');
    expect(within(page).getByText("Micro servo - TowerPro SG92R : ID 169 : Adafruit")).toBeTruthy();
    expect(within(page).getByText("www.adafruit.com")).toBeTruthy();
    expect(within(page).getByText(/Ignore all previous/)).toBeTruthy();         // verbatim text, not instructions
    expect(within(page).getByText("Weight")).toBeTruthy();
    expect(page.querySelectorAll("[data-proxy-img]").length).toBe(2);
    expect(page.querySelector("img")).toBeNull();
    expect(within(page).getByRole("link", { name: "Open in browser" }).getAttribute("href")).toBe("https://www.adafruit.com/product/169");
  });

  it("shows a site search's results and a blocked page's reason", async () => {
    setRun();
    render(<BrowserPanel />);
    const search = await el('[data-kind="search"]');
    expect(within(search).getByText("Search · Adafruit")).toBeTruthy();
    expect(within(search).getAllByRole("listitem")).toHaveLength(3);
    const blocked = document.querySelector("[data-blocked]") as HTMLElement;
    expect(within(blocked).getByText(/site blocked automated access/)).toBeTruthy();
  });

  it("refetches when the run opens another page", async () => {
    setRun();
    render(<BrowserPanel />);
    await waitFor(() => expect(client.runBrowser).toHaveBeenCalledTimes(1));
    act(() => { dispatchForTest({ type: "tool_result", run_id: "r1", data: { name: "mcp__mises__web_open", ok: true }, ts: 1 }); });
    await waitFor(() => expect(client.runBrowser).toHaveBeenCalledTimes(2));
  });

  it("an empty run shows an explainer, not an error", async () => {
    client.runBrowser.mockResolvedValueOnce([]);
    setRun();
    render(<BrowserPanel />);
    await screen.findByText(/hasn't opened any pages yet/);
  });
});
