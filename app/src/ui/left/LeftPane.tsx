import type { JSX } from "preact";
import { activeBuild } from "../../state/builds";
import { Pane } from "../common/Pane";
import { setPage } from "../pages/router";
import "./left.css";
import { History } from "./History";
import { PartTree } from "./PartTree";
import { Variables } from "./Variables";

/** Three stacked sections: PARTS (the active build's tree) · VARIABLES · HISTORY. */
export function LeftPane(): JSX.Element {
  const b = activeBuild.value;
  const partsRight = (
    <span class="left-parts-right">
      {b && <span class="left-build" title={`Build: ${b.name}`}>{b.name}</span>}
      <button type="button" class="left-bom-link" title="Parts list (BOM) for this build (⌘8)" onClick={() => setPage("bom")}>Parts list</button>
    </span>
  );
  return (
    <div class="left-pane" data-testid="left-pane">
      <Pane title="Parts" class="left-parts" right={partsRight}><PartTree /></Pane>
      <Pane title="Variables" class="left-vars"><Variables /></Pane>
      <Pane title="History" class="left-history"><History /></Pane>
    </div>
  );
}
