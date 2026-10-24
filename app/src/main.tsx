// Boot: render the shell at once, then run the core session (client + event stream + sync,
// rebuilt on token rotation / 4401; see state/session.ts).
import { render } from "preact";
import { App } from "./App";
import { trackPastes } from "./lib/keys";
import { startSession } from "./state/session";
import "./styles/fonts";
import "./styles/tokens.css";
import "./styles/base.css";

trackPastes(document);
render(<App />, document.getElementById("app") as HTMLElement);
startSession();
