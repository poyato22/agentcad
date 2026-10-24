// Connection lifecycle: resolve core info → bind a CoreClient → start EventStream + sync.
// Rebuilds everything when the stream closes with 4401 ("unauthorized": the core restarted with a
// new token) or when the shell reports `ready` with different core info. Resolve failures and
// repeated 4401s retry with backoff; a successful open resets it.
import { CoreClient } from "../api/client";
import { onCoreStatus as tauriCoreStatus, resolveCoreInfo as resolveInfo, type ShellCoreStatus } from "../api/connection";
import { EventStream } from "../api/events";
import type { CoreInfo } from "../api/types";
import { debug } from "../lib/log";
import { bindClient, shellStatus } from "./store";
import { startSync as runSync } from "./sync";

type StreamLike = Pick<EventStream, "onEvent" | "onState" | "start" | "stop">;

export interface SessionDeps {
  resolveCoreInfo?: () => Promise<CoreInfo>;
  onCoreStatus?: (cb: (s: ShellCoreStatus) => void) => () => void;
  createClient?: (info: CoreInfo) => CoreClient;
  createStream?: (info: CoreInfo) => StreamLike;
  startSync?: (client: CoreClient, stream: StreamLike) => () => void;
  /** Delays before re-resolving after a failure or a 4401; the last value repeats. */
  retryMs?: number[];
  onError?: (err: unknown) => void;
}

export interface Session {
  readonly info: CoreInfo | null;
  stop(): void;
}

export const DEFAULT_RETRY_MS = [500, 1000, 2000, 5000];

export function startSession(deps: SessionDeps = {}): Session {
  const resolve = deps.resolveCoreInfo ?? resolveInfo;
  const createClient = deps.createClient ?? ((i: CoreInfo) => new CoreClient(i));
  const createStream = deps.createStream ?? ((i: CoreInfo) => new EventStream(i));
  const startSync = deps.startSync ?? ((c: CoreClient, s: StreamLike) => runSync(c, s));
  const retry = deps.retryMs?.length ? deps.retryMs : DEFAULT_RETRY_MS;
  const onError = deps.onError ?? ((e: unknown) => debug("core session error", e));

  let stopped = false;
  let info: CoreInfo | null = null;
  let generation = 0;          // bumps on every (re)connect; stale async work checks it
  let attempt = 0;             // consecutive failures / 4401s
  let timer: ReturnType<typeof setTimeout> | null = null;
  let teardown: (() => void) | null = null;

  const nextDelay = () => retry[Math.min(attempt++, retry.length - 1)];

  function stopCurrent() {
    teardown?.();
    teardown = null;
  }

  function schedule(delay: number) {
    if (stopped) return;
    if (timer !== null) clearTimeout(timer);
    const gen = ++generation;
    timer = setTimeout(() => { timer = null; void connect(gen); }, delay);
  }

  async function connect(gen: number) {
    let next: CoreInfo;
    try {
      next = await resolve();
    } catch (e) {
      if (stopped || gen !== generation) return;
      onError(e);
      schedule(nextDelay());
      return;
    }
    if (stopped || gen !== generation) return;
    stopCurrent();
    info = next;
    const client = createClient(next);
    bindClient(client);
    const stream = createStream(next);
    const stopSync = startSync(client, stream);
    const offState = stream.onState((s) => {
      if (stopped || gen !== generation) return;
      if (s === "open") attempt = 0;
      if (s === "unauthorized") {
        stopCurrent();
        schedule(nextDelay());
      }
    });
    teardown = () => { offState(); stopSync(); stream.stop(); };
    stream.start();
  }

  const offStatus = (deps.onCoreStatus ?? tauriCoreStatus)((s) => {
    if (stopped) return;
    shellStatus.value = s;
    if (s.state === "ready" && info && (s.info.token !== info.token || s.info.baseUrl !== info.baseUrl)) {
      stopCurrent();
      attempt = 0;
      if (timer !== null) { clearTimeout(timer); timer = null; }
      void connect(++generation);
    }
  });

  // first connect runs without a timer so the UI connects as soon as possible
  void connect(++generation);

  return {
    get info() { return info; },
    stop() {
      stopped = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
      offStatus();
      stopCurrent();
    },
  };
}
