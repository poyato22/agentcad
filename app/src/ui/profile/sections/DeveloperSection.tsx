// Developer (Plan 10 E22): the full agent log and the agent browser, moved out of the dock's ⋯ menu
// so the everyday dock is the plain-words Conversation. Both open in the build's agent dock, the
// same views as before (the full log's "Conversation" button goes back).
import type { JSX } from "preact";
import { activeBuildId } from "../../../state/builds";
import { atHome } from "../../../state/home";
import { effectiveRun } from "../../../state/selection";
import { Button } from "../../common/Button";
import { setConsoleView, type ConsoleView } from "../../console/view";
import { setConsoleCollapsed } from "../../pages/router";
import { Card, SectionBox } from "../common";

/** Show one of the dock's developer views: in the open build (leaving Home for it), dock unfolded.
 *  Not remembered across launches: the dock always starts on the Conversation. */
export function showDockView(view: Exclude<ConsoleView, "chat">, onClose: () => void): void {
  onClose();
  if (atHome.peek()) atHome.value = false;
  setConsoleCollapsed(false);
  setConsoleView(view, false);
}

export function DeveloperSection({ onClose }: { onClose: () => void }): JSX.Element {
  const build = activeBuildId.value;
  const run = effectiveRun.value;
  return (
    <SectionBox title="Developer">
      <Card title="Full agent log">
        <p class="dim">
          Every step the agents take, as it happened: each tool call with its input and result, approvals, the model
          used, tokens, cost and timing, for every run in the build, with filters. It exists to audit or debug what an
          agent did. The dock's Conversation already shows the same work in plain words, so you rarely need it.
        </p>
        <div class="profile-row">
          <Button disabled={!build} onClick={() => showDockView("details", onClose)}>Show full agent log</Button>
          {!build && <span class="dim">Open a build first: the log opens in its agent dock.</span>}
        </div>
      </Card>
      <Card title="Agent browser">
        <p class="dim">The page the shown agent's browser is on, and the sites it read, for checking what it saw.</p>
        <div class="profile-row">
          <Button disabled={!build || !run} onClick={() => showDockView("browser", onClose)}>Show agent browser</Button>
          {build && !run && <span class="dim">No agent has run in this build yet.</span>}
        </div>
      </Card>
    </SectionBox>
  );
}
