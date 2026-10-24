import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EventStream, type StreamState } from "./events";
import type { CoreInfo, MisesEvent } from "./types";

const info: CoreInfo = { baseUrl: "http://127.0.0.1:7400", wsUrl: "ws://127.0.0.1:7400/events", token: "tok", owned: true };

class FakeWS {
  static instances: FakeWS[] = [];
  onopen: ((e: Event) => void) | null = null;
  onclose: ((e: CloseEvent) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  readyState = 0;
  closed = false;
  url: string;
  constructor(url: string) { this.url = url; FakeWS.instances.push(this); }
  open() { this.readyState = 1; this.onopen?.(new Event("open")); }
  serverClose(code: number) { this.readyState = 3; this.onclose?.({ code, reason: "", wasClean: true } as CloseEvent); }
  send(data: unknown) { this.onmessage?.({ data } as MessageEvent); }
  close() { this.closed = true; this.readyState = 3; }
}

function stream(opts: { runId?: string } = {}) {
  return new EventStream(info, { ...opts, WebSocketImpl: FakeWS as unknown as typeof WebSocket });
}

const last = () => FakeWS.instances[FakeWS.instances.length - 1];

describe("EventStream", () => {
  beforeEach(() => { FakeWS.instances = []; vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("builds_url_with_token_and_run_id", () => {
    const s = stream({ runId: "r 1" });
    s.start();
    expect(last().url).toBe("ws://127.0.0.1:7400/events?token=tok&run_id=r+1");
    s.stop();
  });

  it("reconnects_with_backoff", () => {
    const s = stream();
    const states: StreamState[] = [];
    s.onState((x) => states.push(x));
    s.start();
    expect(FakeWS.instances).toHaveLength(1);
    last().serverClose(1006);
    expect(states.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(249);
    expect(FakeWS.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeWS.instances).toHaveLength(2);
    last().serverClose(1006);
    vi.advanceTimersByTime(499);
    expect(FakeWS.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeWS.instances).toHaveLength(3);
    last().serverClose(1006);
    vi.advanceTimersByTime(999);
    expect(FakeWS.instances).toHaveLength(3);
    vi.advanceTimersByTime(1);
    expect(FakeWS.instances).toHaveLength(4);
    last().open();
    expect(states.at(-1)).toBe("open");
    // a successful open resets the backoff
    last().serverClose(1012);
    vi.advanceTimersByTime(250);
    expect(FakeWS.instances).toHaveLength(5);
    s.stop();
  });

  it("backoff_caps_at_last_delay", () => {
    const s = stream();
    s.start();
    for (const d of [250, 500, 1000, 2000, 5000, 5000]) {
      last().serverClose(1006);
      const n = FakeWS.instances.length;
      vi.advanceTimersByTime(d - 1);
      expect(FakeWS.instances).toHaveLength(n);
      vi.advanceTimersByTime(1);
      expect(FakeWS.instances).toHaveLength(n + 1);
    }
    s.stop();
  });

  it("close_4401_sets_unauthorized_and_stops", () => {
    const s = stream();
    const states: StreamState[] = [];
    s.onState((x) => states.push(x));
    s.start();
    last().serverClose(4401);
    expect(states.at(-1)).toBe("unauthorized");
    vi.advanceTimersByTime(60_000);
    expect(FakeWS.instances).toHaveLength(1);
  });

  it("parses_and_emits_events", () => {
    const s = stream();
    const got: MisesEvent[] = [];
    s.onEvent((e) => got.push(e));
    s.start();
    last().open();
    last().send(JSON.stringify({ type: "text_delta", run_id: "r1", data: { text: "hi" }, ts: 12.5 }));
    expect(got).toEqual([{ type: "text_delta", run_id: "r1", data: { text: "hi" }, ts: 12.5 }]);
    s.stop();
  });

  it("ignores_non_json_frames", () => {
    const s = stream();
    const got: MisesEvent[] = [];
    s.onEvent((e) => got.push(e));
    s.start();
    last().open();
    last().send("not json {");
    last().send(JSON.stringify({ nope: true }));
    last().send(JSON.stringify([1, 2]));
    last().send(JSON.stringify({ type: "note", run_id: null, data: { text: "ok" }, ts: 1 }));
    expect(got.map((e) => e.type)).toEqual(["note"]);
    s.stop();
  });

  it("stop_closes_socket_and_never_reconnects", () => {
    const s = stream();
    const states: StreamState[] = [];
    s.onState((x) => states.push(x));
    s.start();
    last().open();
    s.stop();
    expect(last().closed).toBe(true);
    expect(states.at(-1)).toBe("closed");
    last().serverClose(1000);
    vi.advanceTimersByTime(60_000);
    expect(FakeWS.instances).toHaveLength(1);
    expect(states.at(-1)).toBe("closed");
  });

  it("unsubscribe_stops_delivery", () => {
    const s = stream();
    const got: string[] = [];
    const off = s.onEvent((e) => got.push(e.type));
    s.start();
    last().open();
    off();
    last().send(JSON.stringify({ type: "note", run_id: null, data: {}, ts: 1 }));
    expect(got).toEqual([]);
    s.stop();
  });
});
