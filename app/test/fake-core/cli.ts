// `npm run fake-core` → port 7400, token dev-token, scenario base.
// Options: --port N --token T --scenario <name|path.json> --origin <extra CORS origin> (repeatable) --glb <path>
import { parseArgs } from "node:util";
import { startFakeCore } from "./server";

const { values } = parseArgs({
  options: {
    port: { type: "string", default: "7400" },
    token: { type: "string", default: "dev-token" },
    scenario: { type: "string", default: "base" },
    origin: { type: "string", multiple: true, default: [] },
    glb: { type: "string" },
  },
});

const core = await startFakeCore({
  port: Number(values.port), token: values.token, scenario: values.scenario, origins: values.origin, glbPath: values.glb,
});
console.log(`fake core on ${core.url} (scenario ${values.scenario})`);

const shutdown = () => { void core.stop().then(() => process.exit(0)); };
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
