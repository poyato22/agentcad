import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UiContext } from "../api/types";
import { startContextSync } from "./context";
import { actions, resetStore } from "./store";

describe("startContextSync", () => {
  let sent: UiContext[];
  const client = { putContext: vi.fn(async (c: UiContext) => { sent.push(c); }) };
  beforeEach(() => { vi.useFakeTimers(); sent = []; client.putContext.mockClear(); });
  afterEach(() => { vi.useRealTimers(); resetStore(); });

  it("sends_the_selection_once_on_start_then_on_every_change_debounced", async () => {
    actions.selectPart("mount_plate");
    const stop = startContextSync(client);
    await vi.advanceTimersByTimeAsync(200);
    expect(sent).toEqual([{ selected_part_id: "mount_plate", selected_run_id: null, picks: [] }]);
    actions.selectRun("r1");
    actions.selectRun("r2");                 // a burst of changes: one PUT with the last state
    await vi.advanceTimersByTimeAsync(200);
    expect(sent).toHaveLength(2);
    expect(sent[1]).toEqual({ selected_part_id: "mount_plate", selected_run_id: "r2", picks: [] });
    actions.selectRun("r2");                 // no change: nothing sent
    await vi.advanceTimersByTimeAsync(200);
    expect(sent).toHaveLength(2);
    stop();
    actions.selectPart(null);
    await vi.advanceTimersByTimeAsync(200);
    expect(sent).toHaveLength(2);            // stopped
  });

  it("a_failed_put_is_retried_with_the_next_change_and_never_throws", async () => {
    client.putContext.mockRejectedValueOnce(new Error("core restarting"));
    const stop = startContextSync(client);
    await vi.advanceTimersByTimeAsync(200);
    expect(client.putContext).toHaveBeenCalledTimes(1);
    actions.selectPart("lid");
    await vi.advanceTimersByTimeAsync(200);
    expect(sent.at(-1)).toEqual({ selected_part_id: "lid", selected_run_id: null, picks: [] });
    stop();
  });
});
