import type { JSX } from "preact";
import { Pane, PaneEmpty } from "../common/Pane";
import { CommandLine } from "./command/CommandLine";

// STUB (Task 2). Task 7 replaces this file: roster · filters · step log (with ApprovalCard /
// QuestionCard inline) · CommandLine at the bottom.
export function ConsolePane(): JSX.Element {
  return (
    <div data-stub="ConsolePane" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ flex: "1", minHeight: "0" }}>
        <Pane title="Agents">
          <PaneEmpty>no runs yet</PaneEmpty>
        </Pane>
      </div>
      <CommandLine />
    </div>
  );
}
