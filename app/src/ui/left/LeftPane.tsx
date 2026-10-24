import type { JSX } from "preact";
import { Pane } from "../common/Pane";
import "./left.css";
import { History } from "./History";
import { PartList } from "./PartList";
import { Variables } from "./Variables";

/** Three stacked sections: PARTS · VARIABLES · HISTORY. */
export function LeftPane(): JSX.Element {
  return (
    <div class="left-pane" data-testid="left-pane">
      <Pane title="Parts" class="left-parts"><PartList /></Pane>
      <Pane title="Variables" class="left-vars"><Variables /></Pane>
      <Pane title="History" class="left-history"><History /></Pane>
    </div>
  );
}
