import type { JSX } from "preact";
import { Pane, PaneEmpty } from "../common/Pane";

// STUB (Task 2). Task 5 replaces this file: PARTS · VARIABLES · HISTORY.
export function LeftPane(): JSX.Element {
  return (
    <div data-stub="LeftPane" style={{ height: "100%" }}>
      <Pane title="Parts">
        <PaneEmpty>no parts yet</PaneEmpty>
      </Pane>
    </div>
  );
}
