import type { JSX } from "preact";
import { Pane } from "../common/Pane";
import { setPage } from "../pages/router";
import "./left.css";
import { History } from "./History";
import { PartTree } from "./PartTree";

/** Two stacked sections: PARTS (the active build's tree, most of the height) · HISTORY.
 *  Plan 7 U2b: the Variables panel left the main UI (parameters live in the part's Code view), and
 *  the build name no longer repeats in the Parts header (the build tab in the top bar names it). */
export function LeftPane(): JSX.Element {
  const partsRight = (
    <button type="button" class="left-bom-link" title="Parts list (BOM) for this build (⌘7)" onClick={() => setPage("bom")}>Parts list</button>
  );
  return (
    <div class="left-pane" data-testid="left-pane">
      <Pane title="Parts" class="left-parts" right={partsRight}><PartTree /></Pane>
      <Pane title="History" class="left-history"><History /></Pane>
    </div>
  );
}
