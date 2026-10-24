import { describe, expect, it } from "vitest";
import { devCoreInfo, onCoreStatus, parseShellStatus, resolveCoreInfo } from "./connection";

describe("connection", () => {
  it("dev_core_info_from_env_values", () => {
    expect(devCoreInfo("http://127.0.0.1:7400/", "dev-token")).toEqual({
      baseUrl: "http://127.0.0.1:7400", wsUrl: "ws://127.0.0.1:7400/events", token: "dev-token", owned: false,
    });
    expect(() => devCoreInfo(undefined, "t")).toThrow(/VITE_MISES_URL/);
    expect(() => devCoreInfo("http://x", "")).toThrow();
  });

  it("resolves_from_vite_env_outside_tauri", async () => {
    // vitest runs in mode "test"; .env.test provides the dev fake-core values
    const info = await resolveCoreInfo();
    expect(info.baseUrl).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
    expect(info.wsUrl).toMatch(/^ws:\/\/127\.0\.0\.1:\d+\/events$/);
    expect(info.token.length).toBeGreaterThan(0);
  });

  it("parses_shell_status_variants", () => {
    expect(parseShellStatus({ state: "starting" })).toEqual({ state: "starting" });
    const info = { baseUrl: "http://127.0.0.1:1", wsUrl: "ws://127.0.0.1:1/events", token: "t", owned: true };
    expect(parseShellStatus({ state: "ready", info })).toEqual({ state: "ready", info });
    expect(parseShellStatus({ state: "crashed", code: 1, stderrTail: "boom", restarts: 2 }))
      .toEqual({ state: "crashed", code: 1, stderrTail: "boom", restarts: 2 });
    expect(parseShellStatus({ state: "crashed", code: null, stderr_tail: "snake", restarts: 3 }))
      .toEqual({ state: "crashed", code: null, stderrTail: "snake", restarts: 3 });
    expect(parseShellStatus({ state: "stopped" })).toEqual({ state: "stopped" });
    expect(parseShellStatus({ state: "weird" })).toBeNull();
    expect(parseShellStatus({ state: "ready", info: { baseUrl: "x" } })).toBeNull();
  });

  it("on_core_status_is_a_noop_in_the_browser", () => {
    const off = onCoreStatus(() => { throw new Error("must not be called"); });
    expect(typeof off).toBe("function");
    off();
  });
});
