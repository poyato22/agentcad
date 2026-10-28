// Boot: render the shell at once, then run the core session (client + event stream + sync,
// rebuilt on token rotation / 4401; see state/session.ts).
// First: apply the cached theme to <html> before anything renders (no light/dark flash).
import "./styles/theme";
import { render } from "preact";
import { App } from "./App";
import { trackPastes } from "./lib/keys";
import { startSession } from "./state/session";
import { trackFocusModality } from "./ui/console/approvals/approvalKeys";
import "./styles/fonts";
import "./styles/tokens.css";
import "./styles/base.css";

trackPastes(document);
trackFocusModality(document);
render(<App />, document.getElementById("app") as HTMLElement);
startSession();
