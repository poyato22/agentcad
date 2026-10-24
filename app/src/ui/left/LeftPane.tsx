import type { JSX } from "preact";
import { Pane } from "../common/Pane";
import "./left.css";
import { History } from "./History";
import { PartList } from "./PartList";
import { Variables } from "./Variables";

/** Three stacked sections: PARTS · VARIABLES · HISTORY. */
export function LeftPane(): JSX.Element {
  return (
    <div class="left-pane" data-testid="left-pane" data-stub="LeftPane"> {/* data-stub kept: App.test.tsx (Task 2) still looks for it; Task 10 may drop it */}
      <Pane title="Parts" class="left-parts"><PartList /></Pane>
      <Pane title="Variables" class="left-vars"><Variables /></Pane>
      <Pane title="History" class="left-history"><History /></Pane>
    </div>
  );
}
