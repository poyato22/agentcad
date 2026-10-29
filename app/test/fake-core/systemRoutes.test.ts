// @vitest-environment node
// Plan 5 routes the fake core adds for the shell (Task A1): GET /system/check and
// POST /system/install/{dep} with its streaming `system_install` events and the allowlist.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import WebSocket from "ws";
import { CoreClient, CoreError } from "../../src/api/client";
import type { MisesEvent } from "../../src/api/types";
import { startFakeCore, type FakeCore } from "./server";

let core: FakeCore;
let client: CoreClient;

async function failure(p: Promise<unknown>): Promise<CoreError> {
  const e = await p.then(() => null, (err: unknown) => err);
  if (!(e instanceof CoreError)) throw new Error(`expected a CoreError, got ${String(e)}`);
  return e;
}

async function listen(): Promise<{ events: MisesEvent[]; close: () => void }> {
  const ws = new WebSocket(`${core.url.replace("http", "ws")}/events?token=${core.token}`);
  const events: MisesEvent[] = [];
  ws.on("message", (d) => events.push(JSON.parse(String(d)) as MisesEvent));
  await new Promise((ok, fail) => { ws.on("open", ok); ws.on("error", fail); });
  return { events, close: () => ws.close() };
}

const until = async (ok: () => boolean, ms = 3000) => {
  const end = Date.now() + ms;
  while (!ok()) {
    if (Date.now() > end) throw new Error("timed out");
    await new Promise((r) => setTimeout(r, 10));
  }
};

afterEach(async () => { await core.stop(); });

describe("system check routes", () => {
  beforeEach(async () => {
    core = await startFakeCore({ port: 0, token: "t0k", scenario: "base", origins: [] });
    client = new CoreClient({ baseUrl: core.url, wsUrl: core.url.replace("http", "ws") + "/events", token: core.token, owned: false });
  });

  it("returns the default dependency rows", async () => {
    const rows = await client.systemCheck();
    expect(rows.map((r) => r.id)).toContain("prusaslicer");
    const prusa = rows.find((r) => r.id === "prusaslicer");
    expect(prusa?.status).toBe("missing");
    expect(prusa?.fix).toMatchObject({ kind: "install", dep: "prusaslicer" });
  });

  it("rejects an unknown dep and ignores body arguments", async () => {
    expect((await failure(client.systemInstall("rm-rf-root"))).code).toBe("invalid_input");
  });

  it("streams install output and ends ok", async () => {
    const sock = await listen();
    expect(await client.systemInstall("prusaslicer")).toEqual({ ok: true, fix: null });
    await until(() => sock.events.some((e) => e.type === "system_install" && e.data.done === true));
    sock.close();
    const out = sock.events.filter((e) => e.type === "system_install").map((e) => e.data);
    expect(out.every((o) => o.dep === "prusaslicer")).toBe(true);
    expect(out.at(-1)).toMatchObject({ done: true, ok: true });
  });

  it("refuses with a fix when a scenario says Homebrew is missing", async () => {
    await core.stop();
    core = await startFakeCore({
      port: 0, token: "t0k",
      scenario: { runs: [], steps: [], approvals: [], parts: [], agents: [],
        models: { default: "a/b", vendors: [] },
        status: { core_version: "0", onshape: "not_configured", onshape_calls_year: 0, onshape_cap_year: 0, runs_running: 0, approvals_pending: 0 },
        system_install: { prusaslicer: { fix: { kind: "open_url", label: "Install Homebrew", url: "https://brew.sh" } } } },
    });
    client = new CoreClient({ baseUrl: core.url, wsUrl: core.url.replace("http", "ws") + "/events", token: core.token, owned: false });
    const res = await client.systemInstall("prusaslicer");
    expect(res).toMatchObject({ ok: false, fix: { kind: "open_url", url: "https://brew.sh" } });
  });
});
