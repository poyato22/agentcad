// Boot: render the shell at once, then resolve core info → client + event stream → sync.
import { render } from "preact";
import { App } from "./App";
import { CoreClient } from "./api/client";
import { onCoreStatus, resolveCoreInfo } from "./api/connection";
import { EventStream } from "./api/events";
import { trackPastes } from "./lib/keys";
import { debug } from "./lib/log";
import { bindClient, shellStatus } from "./state/store";
import { startSync } from "./state/sync";
import "./styles/fonts";
import "./styles/tokens.css";
import "./styles/base.css";

trackPastes(document);
onCoreStatus((s) => { shellStatus.value = s; });

render(<App />, document.getElementById("app") as HTMLElement);

async function connect(): Promise<void> {
  const info = await resolveCoreInfo();
  const client = new CoreClient(info);
  bindClient(client);
  const stream = new EventStream(info);
  startSync(client, stream);
  stream.start();
}

connect().catch((e: unknown) => {
  debug("core connection failed", e);
  shellStatus.value = { state: "crashed", code: null, stderrTail: e instanceof Error ? e.message : String(e), restarts: 0 };
});
