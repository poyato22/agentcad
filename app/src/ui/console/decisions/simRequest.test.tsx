// Agent-built simulators (plan 2026-11-06 S3, S6, S9): a simulator request shows in "Needs your
// decision" with its cost estimate and basis; Accept / Reject (reason required) / Ask for
// changes send once; a 409 already_decided closes it; nothing decides without a real click; a
// build paused at its budget shows the spend with Continue / Stop.
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../../api/client";
import type { SimRequest } from "../../../api/simRequests";
import { simRequestSchema } from "../../../api/simRequests";
import { bindClient, resetStore } from "../../../state/store";
import * as keys from "../approvals/approvalKeys";
import { DecisionsSection } from "./DecisionsSection";
import { resetOnce } from "./once";
import { pendingDecisions } from "./pending";
import { resetSimRequests, simRequests } from "./simRequests";

vi.mock("../approvals/approvalKeys", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../approvals/approvalKeys")>()),
  isTrustedActivation: vi.fn(() => true),
}));

const band = (low: number, likely: number, high: number) => ({ low, likely, high });

function req(over: Partial<SimRequest> = {}): SimRequest {
  return simRequestSchema.parse({
    id: "sr_000000000001", name: "flapping_wing_aero", status: "pending", project_id: "p1", run_id: "r1",
    request: {
      computes: "mean lift of a flapping wing", why: "lift >= weight is unverified", method: "blade-element model",
      fidelity_class: "reduced_order", complexity: "small",
      inputs: [{ name: "frequency", unit: "Hz" }], outputs: [{ name: "lift", unit: "N" }],
      validation_cases: [{ name: "fruit fly hover", source: "Sane & Dickinson 2002" }, { name: "plate", source: "thin airfoil" }],
    },
    estimate: {
      method: "turn_scaled", basis: "your 12 recorded turns x an assumed 3 / 6 / 10 turns", confidence: "low",
      usd: band(1.5, 4.25, 12), tokens: band(300000, 1200000, 3000000), wall_minutes: band(9, 18, 30),
      machine_load: "one sandboxed worker", dependencies: [{ name: "numpy", manager: "pip", licence: "BSD-3-Clause", size_mb: 18 }],
      download_mb: 18, download_note: null, budget_pause_usd: 18, weekly_share_pct: null, note: "API-price equivalent",
    },
    decision: null, budget: null, created_at: 1, ...over,
  });
}

function client(over: Record<string, unknown> = {}) {
  const c = {
    listSimRequests: vi.fn().mockResolvedValue({ requests: [] }),
    getRun: vi.fn().mockRejectedValue(new Error("none")),
    decideSimRequest: vi.fn(async (_id: string, action: string) => ({ request: req({ status: action === "accept" ? "accepted" : "rejected" }) })),
    simRequestBudget: vi.fn(async () => ({ request: req({ status: "building" }) })),
    ...over,
  };
  bindClient(c as unknown as CoreClient);
  return c;
}

const show = (r: SimRequest) => { simRequests.value = new Map([[r.id, r]]); };

beforeEach(() => { resetOnce(); resetSimRequests(); });
afterEach(() => { cleanup(); resetStore(); vi.mocked(keys.isTrustedActivation).mockReturnValue(true); });

describe("simulator request decision card", () => {
  it("lists a pending request with its estimate, basis, validation count and downloads", async () => {
    client();
    show(req());
    expect(pendingDecisions.value.map((d) => d.kind)).toEqual(["simulator"]);
    const v = render(<DecisionsSection />);
    const card = v.container.querySelector("[data-sim-request]")!;
    expect(v.container.textContent).toContain("New simulator: flapping wing aero");
    expect(card.querySelector("[data-cost-usd]")!.textContent).toBe("$1.50 – $12.00 (likely $4.25)");
    expect(card.querySelector("[data-estimate-basis]")!.textContent).toContain("12 recorded turns");
    expect(card.querySelector("[data-estimate-basis]")!.textContent).toContain("pauses at $18.00");
    expect(card.querySelector("[data-validation-count]")!.getAttribute("data-validation-count")).toBe("2");
    expect(card.textContent).toContain("numpy (pip, BSD-3-Clause, 18 MB)");
  });

  it("accept sends once even on a double click, and the item leaves the list", async () => {
    const c = client();
    show(req());
    const v = render(<DecisionsSection />);
    const btn = v.getByText("Accept and build");
    await act(async () => { fireEvent.click(btn); fireEvent.click(btn); });
    await waitFor(() => expect(pendingDecisions.value).toEqual([]));
    expect(c.decideSimRequest).toHaveBeenCalledTimes(1);
    expect(c.decideSimRequest).toHaveBeenCalledWith("sr_000000000001", "accept", "");
  });

  it("reject needs a reason, and sends it", async () => {
    const c = client();
    show(req());
    const v = render(<DecisionsSection />);
    fireEvent.click(v.getByText("Reject"));
    const send = v.getByText("Send reject") as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.input(v.getByLabelText("Why reject"), { target: { value: "not this week" } });
    await act(async () => { fireEvent.click(v.getByText("Send reject")); });
    expect(c.decideSimRequest).toHaveBeenCalledWith("sr_000000000001", "reject", "not this week");
  });

  it("a 409 already_decided closes the card", async () => {
    const c = client({ decideSimRequest: vi.fn().mockRejectedValue(new CoreError("already_decided", "decided", { status: 409, body: { decision: { status: "rejected" } } })) });
    show(req());
    const v = render(<DecisionsSection />);
    await act(async () => { fireEvent.click(v.getByText("Accept and build")); });
    await waitFor(() => expect(pendingDecisions.value).toEqual([]));
    expect(c.decideSimRequest).toHaveBeenCalledTimes(1);
  });

  it("never decides without a real click", async () => {
    const c = client();
    vi.mocked(keys.isTrustedActivation).mockReturnValue(false);
    show(req());
    const v = render(<DecisionsSection />);
    await act(async () => { fireEvent.click(v.getByText("Accept and build")); });
    expect(c.decideSimRequest).not.toHaveBeenCalled();
    expect(pendingDecisions.value.length).toBe(1);
  });

  it("a build paused at its budget shows the spend and continues once", async () => {
    const c = client();
    show(req({ status: "paused_budget", budget: { limit_usd: 18, paused_spend: { usd: 18.4, tokens: 1 } } }));
    expect(pendingDecisions.value[0].what).toContain("over budget");
    const v = render(<DecisionsSection />);
    expect(v.container.querySelector("[data-budget-paused]")!.textContent).toContain("spent $18.40 of $18.00");
    await act(async () => { fireEvent.click(v.getByText("Continue")); });
    expect(c.simRequestBudget).toHaveBeenCalledWith("sr_000000000001", "continue");
    await waitFor(() => expect(pendingDecisions.value).toEqual([]));
  });
});
