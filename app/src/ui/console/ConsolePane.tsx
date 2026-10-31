// Agent console (right column). Default: the agent dock — View · Simulations · Conversation
// streams, all visible at once, and the composer (Dock.tsx). "Full log" (the Conversation's ⋯
// menu) shows the dense log: roster · filters · step log · command line. An evidence link
// (focusedStepId) opens the full log at that step.
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { focusedStepId } from "../../state/store";
import { Button } from "../common/Button";
import { CommandLine } from "./command/CommandLine";
import { Dock } from "./Dock";
import { Filters } from "./Filters";
import { Roster } from "./Roster";
import { watchQueued } from "./queued";
import { StepLog } from "./StepLog";
import { BrowserPanel } from "./browser/BrowserPanel";
import { consoleView, setConsoleView } from "./view";
import "./console.css";
import "./conversation.css";

export function ConsolePane(): JSX.Element {
  const view = consoleView.value;
  const focus = focusedStepId.value;
  useEffect(() => watchQueued(), []);
  useEffect(() => {
    if (focus !== null && consoleView.peek() === "chat") setConsoleView("details", false);
  }, [focus]);

  if (view === "browser") return <BrowserPanel />;

  if (view === "details") {
    return (
      <div class="con" data-console data-view="details">
        <DetailsBar />
        <Roster />
        <Filters />
        <StepLog />
        <CommandLine />
      </div>
    );
  }
  return (
    <div class="con" data-console data-view="chat">
      <Dock />
    </div>
  );
}

/** Full log header: back to the dock. */
function DetailsBar(): JSX.Element {
  return (
    <div class="cv-detailsbar">
      <span class="cv-detailsbar-t">Full log</span>
      <span class="cv-spacer" />
      <Button variant="ghost" onClick={() => setConsoleView("chat")}>Conversation</Button>
    </div>
  );
}
