// Agent console (right column). Default: the conversation view — run switcher · thread (approval
// and question cards, failed calls and errors inline) · command line, or the inline composer
// after "+ New". "Details" shows the dense log: roster · filters · step log · command line.
// An evidence link (focusedStepId) opens Details at that step.
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { focusedStepId } from "../../state/store";
import { CommandLine } from "./command/CommandLine";
import { Composer } from "./Composer";
import { Conversation } from "./Conversation";
import { Filters } from "./Filters";
import { Roster } from "./Roster";
import { DetailsBar, RunSwitcher } from "./RunSwitcher";
import { watchQueued } from "./queued";
import { StepLog } from "./StepLog";
import { composerOpen, consoleView, setConsoleView } from "./view";
import "./console.css";
import "./conversation.css";

export function ConsolePane(): JSX.Element {
  const view = consoleView.value;
  const focus = focusedStepId.value;
  useEffect(() => watchQueued(), []);
  useEffect(() => {
    if (focus !== null && consoleView.peek() === "chat") setConsoleView("details", false);
  }, [focus]);

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
      <RunSwitcher />
      <Conversation />
      {composerOpen.value ? <Composer /> : <CommandLine />}
    </div>
  );
}
