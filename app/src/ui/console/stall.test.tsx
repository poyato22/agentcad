// The stall banner never shows while the run waits on you, nor for a stale run just loaded.
import { render } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";
import type { AgentRun, Approval } from "../../api/types";
import { approvals, resetStore } from "../../state/store";
import { resetChat, STALL_S } from "./chat";
import { StallNote } from "./MessageActions";

const run = (over: Partial<AgentRun> = {}): AgentRun => ({ id: "r1", agent: "engineer", status: "running", task: "t", created_at: 1, updated_at: Date.now() / 1000 - 600_000, project_id: "b1", parent_id: null, ...over }) as unknown as AgentRun;

afterEach(() => { resetStore(); resetChat(); });

describe("stall banner", () => {
  it("is not shown for a run with a pending approval, however long it waited", () => {
    approvals.value = new Map([["a1", { id: "a1", run_id: "r1", status: "pending" } as unknown as Approval]]);
    const { container } = render(<StallNote run={run()} lastText="go" />);
    expect(container.querySelector("[data-stalled]")).toBeNull();
  });

  it("is not shown for a run first seen now, whatever its old timestamps say", () => {
    const { container } = render(<StallNote run={run()} lastText="go" />);
    expect(container.querySelector("[data-stalled]")).toBeNull();
    expect(STALL_S).toBeGreaterThan(0);
  });
});
