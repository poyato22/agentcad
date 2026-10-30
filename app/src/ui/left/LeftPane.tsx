import type { JSX } from "preact";
import { activeBuild } from "../../state/builds";
import { Pane } from "../common/Pane";
import "./left.css";
import { History } from "./History";
import { PartTree } from "./PartTree";
import { Variables } from "./Variables";

/** Three stacked sections: PARTS (the active build's tree) · VARIABLES · HISTORY. */
export function LeftPane(): JSX.Element {
  const b = activeBuild.value;
  return (
    <div class="left-pane" data-testid="left-pane">
      <Pane title="Parts" class="left-parts" right={b ? <span class="left-build" title={`Build: ${b.name}`}>{b.name}</span> : undefined}><PartTree /></Pane>
      <Pane title="Variables" class="left-vars"><Variables /></Pane>
      <Pane title="History" class="left-history"><History /></Pane>
    </div>
  );
}
