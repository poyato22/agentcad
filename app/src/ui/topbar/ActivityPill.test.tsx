import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";
import type { MisesEvent } from "../../api/types";
import { recordActivity, resetActivity } from "../../state/activity";
import { consoleCollapsed, setConsoleCollapsed } from "../pages/router";
import { ActivityPill } from "./ActivityPill";

const act = (run: string, state: string): MisesEvent => ({ type: "agent_activity", run_id: run, ts: 0, data: { state, detail: "", since: 1 } });

afterEach(() => { cleanup(); resetActivity(); setConsoleCollapsed(false); });

describe("ActivityPill", () => {
  it("is hidden when nothing is working", () => {
    const { container } = render(<ActivityPill />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the working/thinking count and opens the console on click", () => {
    recordActivity(act("r1", "thinking"));
    recordActivity(act("r2", "writing"));
    setConsoleCollapsed(true);
    render(<ActivityPill />);
    const pill = screen.getByRole("button", { name: /2 working · 1 thinking/i });
    expect(pill.getAttribute("data-mode")).toBe("thinking");
    fireEvent.click(pill);
    expect(consoleCollapsed.value).toBe(false);
  });
});
