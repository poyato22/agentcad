// Dev/test-only boot for harness.html: binds a core client (VITE_MISES_* in dev/test) and renders
// the Profile panel (and the setup gate it re-opens) over a stand-in shell. Task 6 wires the real
// top-bar button; this page then goes.
import { signal } from "@preact/signals";
import { render } from "preact";
import { CoreClient } from "../../api/client";
import { resolveCoreInfo } from "../../api/connection";
import { bindClient } from "../../state/store";
import { Button } from "../common/Button";
import { SetupGate } from "../setup/SetupGate";
import { ProfilePane } from "./ProfilePane";
import "../../styles/fonts";
import "../../styles/tokens.css";
import "../../styles/base.css";

const open = signal(true);

function Shell() {
  return (
    <>
      <main data-testid="app-shell" class="pane-empty">
        Mises
        <Button onClick={() => { open.value = true; }}>Open profile</Button>
      </main>
      <ProfilePane open={open.value} onClose={() => { open.value = false; }} />
      <SetupGate />
    </>
  );
}

void resolveCoreInfo().then((info) => {
  bindClient(new CoreClient(info));
  render(<Shell />, document.getElementById("app") as HTMLElement);
});
