import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { AgentRun } from "../../api/types";
import { resetStore, runs, selectedRunId } from "../../state/store";
import { BackToLead } from "./BackToLead";

const run = (id: string, role: "lead" | "worker"): AgentRun =>
  ({ id, role, project_id: "b1", created_at: 1, archived: false, status: "running", agent: "x", task: "t" } as unknown as AgentRun);

beforeEach(() => {
  resetStore();
  runs.value = new Map([["lead", run("lead", "lead")], ["w1", run("w1", "worker")]]);
  selectedRunId.value = "w1";
});
afterEach(cleanup);

function Dock(): preact.JSX.Element {
  return <div data-dock><BackToLead buildId="b1" /><textarea aria-label="Message" /></div>;
}

describe("Back to lead (E13)", () => {
  it("the button shows the lead's thread again", () => {
    render(<Dock />);
    fireEvent.click(screen.getByRole("button", { name: /Back to lead/ }));
    expect(selectedRunId.value).toBe("lead");
  });

  it("Esc in the dock goes back, but not while typing in the composer", () => {
    render(<Dock />);
    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Escape" });
    expect(selectedRunId.value).toBe("w1");
    fireEvent.keyDown(screen.getByRole("button", { name: /Back to lead/ }), { key: "Escape" });
    expect(selectedRunId.value).toBe("lead");
  });

  it("Esc outside the dock is someone else's (the viewer's back to the assembly)", () => {
    render(<><Dock /><button type="button">Viewer</button></>);
    fireEvent.keyDown(screen.getByRole("button", { name: "Viewer" }), { key: "Escape" });
    expect(selectedRunId.value).toBe("w1");
  });
});
