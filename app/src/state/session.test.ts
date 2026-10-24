import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startSession } from "./session";
import { actions, resetStore, shellStatus } from "./store";
import type { ShellCoreStatus } from "../api/connection";
import type { StreamState } from "../api/events";
import type { CoreInfo, MisesEvent } from "../api/types";

const info = (token: string, port = 7400): CoreInfo =>
  ({ baseUrl: `http://127.0.0.1:${port}`, wsUrl: `ws://127.0.0.1:${port}/events`, token, owned: true });

class FakeStream {
  static all: FakeStream[] = [];
  started = 0;
  stopped = 0;
  st = new Set<(s: StreamState) => void>();
  info: CoreInfo;
  constructor(info: CoreInfo) { this.info = info; FakeStream.all.push(this); }
  onEvent(_cb: (e: MisesEvent) => void) { return () => {}; }
  onState(cb: (s: StreamState) => void) { this.st.add(cb); return () => { this.st.delete(cb); }; }
  start() { this.started++; }
  stop() { this.stopped++; }
  state(s: StreamState) { for (const cb of [...this.st]) cb(s); }
}

function harness(tokens: string[]) {
  FakeStream.all = [];
  const queue = [...tokens];
  let statusCb: ((s: ShellCoreStatus) => void) | null = null;
  const syncStops: ReturnType<typeof vi.fn>[] = [];
  const resolve = vi.fn(async () => info(queue.length > 1 ? (queue.shift() as string) : queue[0]));
  const session = startSession({
    resolveCoreInfo: resolve,
    onCoreStatus: (cb) => { statusCb = cb; return () => { statusCb = null; }; },
    createStream: (i) => new FakeStream(i) as never,
    startSync: () => { const s = vi.fn(); syncStops.push(s); return s; },
    retryMs: [100, 200],
  });
  return { session, resolve, syncStops, status: (s: ShellCoreStatus) => statusCb?.(s), last: () => FakeStream.all[FakeStream.all.length - 1] };
}

const settle = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

describe("session", () => {
  beforeEach(() => { vi.useFakeTimers(); resetStore(); });
  afterEach(() => { vi.useRealTimers(); resetStore(); });

  it("connects_binds_client_and_starts_stream", async () => {
    const h = harness(["t1"]);
    await settle();
    expect(h.resolve).toHaveBeenCalledTimes(1);
    expect(FakeStream.all).toHaveLength(1);
    expect(h.last().started).toBe(1);
    expect(h.syncStops).toHaveLength(1);
    expect(h.session.info?.token).toBe("t1");
    expect(() => actions.client()).not.toThrow();
    h.session.stop();
  });

  it("unauthorized_close_reresolves_and_rebuilds", async () => {
    const h = harness(["t1", "t2"]);
    await settle();
    const first = h.last();
    first.state("unauthorized");
    expect(first.stopped).toBe(1);
    expect(h.syncStops[0]).toHaveBeenCalledTimes(1);
    expect(h.resolve).toHaveBeenCalledTimes(1);   // waits for the backoff
    await vi.advanceTimersByTimeAsync(100);
    await settle();
    expect(h.resolve).toHaveBeenCalledTimes(2);
    expect(FakeStream.all).toHaveLength(2);
    expect(h.last().info.token).toBe("t2");
    expect(h.last().started).toBe(1);
    expect(h.session.info?.token).toBe("t2");
    h.session.stop();
  });

  it("repeated_unauthorized_backs_off_and_resets_on_open", async () => {
    const h = harness(["same"]);
    await settle();
    h.last().state("unauthorized");
    await vi.advanceTimersByTimeAsync(100);
    await settle();
    h.last().state("unauthorized");
    await vi.advanceTimersByTimeAsync(199);
    expect(h.resolve).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    await settle();
    expect(h.resolve).toHaveBeenCalledTimes(3);
    h.last().state("open");
    h.last().state("unauthorized");
    await vi.advanceTimersByTimeAsync(100);
    await settle();
    expect(h.resolve).toHaveBeenCalledTimes(4);
    h.session.stop();
  });

  it("shell_ready_with_new_token_reconnects", async () => {
    const h = harness(["t1", "t2"]);
    await settle();
    h.status({ state: "ready", info: info("t1") });       // same token → nothing
    await settle();
    expect(h.resolve).toHaveBeenCalledTimes(1);
    h.status({ state: "ready", info: info("t2", 7401) });
    expect(shellStatus.value.state).toBe("ready");
    await settle();
    expect(h.resolve).toHaveBeenCalledTimes(2);
    expect(FakeStream.all[0].stopped).toBe(1);
    expect(h.last().info.token).toBe("t2");
    h.session.stop();
  });

  it("resolve_failure_retries", async () => {
    FakeStream.all = [];
    const resolve = vi.fn<() => Promise<CoreInfo>>().mockRejectedValueOnce(new Error("starting")).mockResolvedValue(info("t1"));
    const onError = vi.fn();
    const session = startSession({
      resolveCoreInfo: resolve, onCoreStatus: () => () => {}, createStream: (i) => new FakeStream(i) as never,
      startSync: () => () => {}, retryMs: [100], onError,
    });
    await settle();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(FakeStream.all).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(100);
    await settle();
    expect(FakeStream.all).toHaveLength(1);
    session.stop();
  });

  it("stop_tears_down_and_ignores_late_events", async () => {
    const h = harness(["t1", "t2"]);
    await settle();
    const s = h.last();
    h.session.stop();
    expect(s.stopped).toBe(1);
    expect(h.syncStops[0]).toHaveBeenCalledTimes(1);
    s.state("unauthorized");
    h.status({ state: "ready", info: info("t9") });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(h.resolve).toHaveBeenCalledTimes(1);
  });
});
