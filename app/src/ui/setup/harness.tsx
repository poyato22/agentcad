// Dev/test-only boot for harness.html: binds a core client (VITE_MISES_* in dev/test) and renders
// the setup gate over a stand-in shell. Task 6 mounts <SetupGate/> in App; this page then goes.
import { render } from "preact";
import { CoreClient } from "../../api/client";
import { resolveCoreInfo } from "../../api/connection";
import { bindClient } from "../../state/store";
import { SetupGate } from "./SetupGate";
import "../../styles/fonts";
import "../../styles/tokens.css";
import "../../styles/base.css";

void resolveCoreInfo().then((info) => {
  bindClient(new CoreClient(info));
  render(
    <>
      <main data-testid="app-shell" class="pane-empty">Mises</main>
      <SetupGate />
    </>,
    document.getElementById("app") as HTMLElement,
  );
});
