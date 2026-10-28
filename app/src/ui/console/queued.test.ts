import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { AgentRun } from "../../api/types";
import { bindClient, resetStore, runs } from "../../state/store";
import { noteRun, pendingRuns, resetQueued, watchQueued } from "./queued";
import { run } from "./testSteps";

afterEach(() => { resetQueued(); resetStore(); });

const flush = () => new Promise((r) => setTimeout(r, 0));

function client(getRun: (id: string) => Promise<AgentRun>) {
  bindClient({ getRun: vi.fn(getRun) } as unknown as CoreClient);
}

describe("queued pending runs", () => {
  it("prunes a pending run once the store has it, and never resurrects a stale copy", async () => {
    watchQueued();
    client(async (id) => run(id, { status: "queued" }));
    noteRun("q1");
    await flush();
    expect(pendingRuns.value.has("q1")).toBe(true);   // shown until the store lists it

    runs.value = new Map([["q1", run("q1", { status: "running" })]]);
    expect(pendingRuns.value.has("q1")).toBe(false);  // store copy wins

    // the store later drops it (e.g. a /runs refetch past the limit): the pruned extra copy
    // must not reappear as a pending run
    runs.value = new Map();
    expect(pendingRuns.value.has("q1")).toBe(false);
  });
});
