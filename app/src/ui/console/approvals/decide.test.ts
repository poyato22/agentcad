import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../../api/client";
import type { Approval } from "../../../api/types";
import { actions, approvals, bindClient, resetStore } from "../../../state/store";
import { decide, notices, resetApprovalUi, retired } from "./decide";

const card = (over: Partial<Approval> = {}): Approval => ({
  id: "ap1", run_id: "r1", action: "fixture_merge", payload: {}, payload_hash: "a".repeat(64), microversion: null,
  deltas: {}, status: "pending", expires_at: Date.now() / 1000 + 600, decided_by: null, decided_at: null, edited_payload: null, ...over,
});

const client = { decide: vi.fn(), pendingApprovals: vi.fn() };

beforeEach(() => {
  client.decide.mockReset();
  client.pendingApprovals.mockReset();
  bindClient(client as unknown as CoreClient);
});
afterEach(() => { resetStore(); resetApprovalUi(); });

describe("decide", () => {
  it("checks_the_latest_copy_in_the_store_not_the_callers", async () => {
    const shown = card();
    // the store already knows it was decided elsewhere / expired / changed
    for (const latest of [card({ status: "approved" }), card({ expires_at: 1 }), card({ payload_hash: "b".repeat(64) })]) {
      actions.upsertApproval(latest);
      await decide(shown, "approve");
    }
    expect(client.decide).not.toHaveBeenCalled();
    // and with the store agreeing, it is sent
    actions.upsertApproval(shown);
    client.decide.mockResolvedValue(card({ status: "approved" }));
    await decide(shown, "approve");
    expect(client.decide).toHaveBeenCalledTimes(1);
  });

  it("network_error_rechecks_the_core_and_still_pending_says_not_sent_without_retrying", async () => {
    const a = card();
    actions.upsertApproval(a);
    client.decide.mockRejectedValue(new CoreError("network", "Failed to fetch", { retryable: true }));
    client.pendingApprovals.mockResolvedValue([a]);
    await decide(a, "approve");
    expect(client.pendingApprovals).toHaveBeenCalledWith("r1");
    expect(client.decide).toHaveBeenCalledTimes(1);
    expect(notices.value.get("ap1")?.text).toBe("decision not sent: Failed to fetch · the core still shows it pending");
    expect(approvals.value.get("ap1")?.status).toBe("pending");
  });

  it("network_error_when_the_core_no_longer_lists_it_shows_that_instead_of_not_sent", async () => {
    const a = card();
    actions.upsertApproval(a);
    client.decide.mockRejectedValue(new CoreError("network", "Failed to fetch", { retryable: true }));
    client.pendingApprovals.mockResolvedValue([]);
    await decide(a, "approve");
    const text = notices.value.get("ap1")?.text ?? "";
    expect(text).not.toMatch(/not sent/);
    expect(text).toBe("no answer from the core, and it no longer lists this as pending — it was decided or expired; see the log");
    expect(retired.value.has("ap1")).toBe(true);
    // never decided twice
    await decide(a, "approve");
    expect(client.decide).toHaveBeenCalledTimes(1);
  });

  it("network_error_with_a_failed_recheck_says_so", async () => {
    const a = card();
    actions.upsertApproval(a);
    client.decide.mockRejectedValue(new CoreError("network", "Failed to fetch", { retryable: true }));
    client.pendingApprovals.mockRejectedValue(new CoreError("network", "Failed to fetch", { retryable: true }));
    await decide(a, "approve");
    expect(notices.value.get("ap1")?.text).toBe("decision may not have been sent: Failed to fetch · could not check the core");
  });
});
