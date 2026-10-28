// WebSocket event stream from the core (`WS /events?token=&run_id=`). Reconnects with backoff;
// a 4401 close (bad token, e.g. the core restarted with a new one) stops retrying and reports
// "unauthorized" so the app can re-resolve core info. Frames that aren't a valid Event are ignored.
import { debug } from "../lib/log";
import { eventSchema } from "./schemas";
import type { CoreInfo, MisesEvent } from "./types";

export type StreamState = "connecting" | "open" | "reconnecting" | "unauthorized" | "closed";

export const DEFAULT_BACKOFF_MS = [250, 500, 1000, 2000, 5000] as const;
export const CLOSE_UNAUTHORIZED = 4401;

export interface EventStreamOptions { runId?: string; backoffMs?: number[]; WebSocketImpl?: typeof WebSocket }

const taps = new Set<(e: MisesEvent) => void>();

/**
 * Hears every event of every running stream (after its own subscribers). For UI that the store's
 * reducer doesn't serve (e.g. `local_pull` download progress, `run_queued`). A tap that throws
 * is logged and skipped. Returns an unsubscribe function.
 */
export function onCoreEvent(cb: (e: MisesEvent) => void): () => void {
  taps.add(cb);
  return () => { taps.delete(cb); };
}

function tapAll(ev: MisesEvent): void {
  for (const cb of [...taps]) {
    try { cb(ev); } catch (e) { debug("event tap failed", ev.type, e); }
  }
}

const stateTaps = new Set<(s: StreamState) => void>();

/**
 * Hears the stream state of every running stream (after its own subscribers). For UI that needs
 * to resync on a (re)connect without owning the stream (e.g. the model picker re-reading
 * /local/status for in-flight downloads). A tap that throws is logged and skipped.
 */
export function onCoreState(cb: (s: StreamState) => void): () => void {
  stateTaps.add(cb);
  return () => { stateTaps.delete(cb); };
}

function tapAllState(s: StreamState): void {
  for (const cb of [...stateTaps]) {
    try { cb(s); } catch (e) { debug("state tap failed", s, e); }
  }
}

export class EventStream {
  private readonly url: string;
  private readonly backoff: readonly number[];
  private readonly WS: typeof WebSocket;
  private readonly eventCbs = new Set<(e: MisesEvent) => void>();
  private readonly stateCbs = new Set<(s: StreamState) => void>();
  private ws: WebSocket | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private attempt = 0;
  private running = false;
  private current: StreamState = "closed";

  constructor(info: CoreInfo, opts: EventStreamOptions = {}) {
    this.url = eventsUrl(info, opts.runId);
    this.backoff = opts.backoffMs?.length ? opts.backoffMs : DEFAULT_BACKOFF_MS;
    this.WS = opts.WebSocketImpl ?? globalThis.WebSocket;
  }

  get state(): StreamState { return this.current; }

  onEvent(cb: (e: MisesEvent) => void): () => void {
    this.eventCbs.add(cb);
    return () => { this.eventCbs.delete(cb); };
  }

  onState(cb: (s: StreamState) => void): () => void {
    this.stateCbs.add(cb);
    return () => { this.stateCbs.delete(cb); };
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.attempt = 0;
    this.connect("connecting");
  }

  stop(): void {
    this.running = false;
    if (this.timer !== null) { clearTimeout(this.timer); this.timer = null; }
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onopen = ws.onclose = ws.onmessage = ws.onerror = null;
      try { ws.close(); } catch { /* already closed */ }
    }
    this.setState("closed");
  }

  private connect(state: "connecting" | "reconnecting"): void {
    this.setState(state);
    let ws: WebSocket;
    try {
      ws = new this.WS(this.url);
    } catch {
      this.scheduleRetry();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.attempt = 0;
      this.setState("open");
    };
    ws.onmessage = (m: MessageEvent) => {
      if (this.ws !== ws) return;
      const ev = parseFrame(m.data);
      if (!ev) return;
      for (const cb of [...this.eventCbs]) cb(ev);
      tapAll(ev);
    };
    ws.onerror = () => { /* a close event always follows */ };
    ws.onclose = (c: CloseEvent) => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (!this.running) { this.setState("closed"); return; }
      if (c.code === CLOSE_UNAUTHORIZED) {
        this.running = false;
        this.setState("unauthorized");
        return;
      }
      this.scheduleRetry();
    };
  }

  private scheduleRetry(): void {
    this.setState("reconnecting");
    const delay = this.backoff[Math.min(this.attempt, this.backoff.length - 1)];
    this.attempt++;
    this.timer = setTimeout(() => {
      this.timer = null;
      if (this.running) this.connect("reconnecting");
    }, delay);
  }

  private setState(s: StreamState): void {
    if (s === this.current) return;
    this.current = s;
    for (const cb of [...this.stateCbs]) cb(s);
    tapAllState(s);
  }
}

/** `wsUrl` is the full events endpoint (`ws://127.0.0.1:<port>/events`); a bare origin also works. */
export function eventsUrl(info: CoreInfo, runId?: string): string {
  const base = info.wsUrl.replace(/\/+$/, "");
  const u = new URL(/\/events$/.test(base) ? base : `${base}/events`);
  u.searchParams.set("token", info.token);
  if (runId) u.searchParams.set("run_id", runId);
  return u.toString();
}

function parseFrame(data: unknown): MisesEvent | null {
  if (typeof data !== "string") return null;
  let raw: unknown;
  try { raw = JSON.parse(data); } catch { return null; }
  const r = eventSchema.safeParse(raw);
  if (!r.success) {
    debug("ignoring malformed event frame");
    return null;
  }
  return r.data;
}
