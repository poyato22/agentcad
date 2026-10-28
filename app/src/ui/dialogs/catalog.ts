// What the model picker shows: GET /models, GET /local/status (Ollama on this Mac) and the
// profile's default model, shared by every picker, plus Ollama actions (start, download) and
// download progress from `local_pull` events. Loaded on demand (a picker mounting or opening);
// concurrent loads share one request set.
import { signal, type Signal } from "@preact/signals";
import { CoreError } from "../../api/client";
import { onCoreEvent } from "../../api/events";
import { localPullSchema } from "../../api/schemas";
import type { LocalPull, LocalStatus, ModelsInfo } from "../../api/types";
import { debug } from "../../lib/log";
import { actions } from "../../state/store";

export interface Catalog {
  models: ModelsInfo | null;
  /** null: no /local/status on this core (older core) or it failed. */
  local: LocalStatus | null;
  profileDefault: string | null;
  loading: boolean;
  error: string | null;
}

const EMPTY: Catalog = { models: null, local: null, profileDefault: null, loading: false, error: null };

export const catalog: Signal<Catalog> = signal<Catalog>(EMPTY);

/** Download progress by model name. `error` set when the core reported a failure. */
export interface PullState extends LocalPull { error: string | null }
export const pulls: Signal<ReadonlyMap<string, PullState>> = signal<ReadonlyMap<string, PullState>>(new Map());

/** Ollama start in flight / its last error. */
export const ollamaStart: Signal<{ busy: boolean; error: string | null }> = signal({ busy: false, error: null });

let inflight: Promise<void> | null = null;

function describe(e: unknown): string {
  if (e instanceof CoreError) return e.hint ? `${e.message} — ${e.hint}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

/** (Re)loads models, local status and the profile default. Never throws. */
export function refreshCatalog(): Promise<void> {
  ensureTap();
  if (inflight) return inflight;
  catalog.value = { ...catalog.peek(), loading: true };
  const run = (async () => {
    let client;
    try { client = actions.client(); } catch (e) { catalog.value = { ...catalog.peek(), loading: false, error: describe(e) }; return; }
    const c = client;
    const [models, local, profile] = await Promise.allSettled([
      Promise.resolve().then(() => c.models()), Promise.resolve().then(() => c.localStatus()), Promise.resolve().then(() => c.getProfile()),
    ]);
    catalog.value = {
      models: models.status === "fulfilled" ? models.value : catalog.peek().models,
      local: local.status === "fulfilled" ? local.value : null,
      profileDefault: profile.status === "fulfilled" ? profile.value.default_model : catalog.peek().profileDefault,
      loading: false,
      error: models.status === "rejected" ? describe(models.reason) : null,
    };
  })();
  inflight = run.finally(() => { inflight = null; });
  return inflight;
}

export const START_POLL_MS = 1000;
export const START_TRIES = 10;

/** POST /local/start, then re-read the local status until Ollama answers (bounded). */
export async function startOllama(wait: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms))): Promise<void> {
  if (ollamaStart.peek().busy) return;
  ollamaStart.value = { busy: true, error: null };
  try {
    await actions.client().localStart();
    for (let i = 0; i < START_TRIES; i++) {
      await refreshCatalog();
      if (catalog.peek().local?.running) break;
      await wait(START_POLL_MS);
    }
    ollamaStart.value = { busy: false, error: catalog.peek().local?.running ? null : "Ollama didn't start — open it from Applications, then try again" };
  } catch (e) {
    ollamaStart.value = { busy: false, error: describe(e) };
  }
}

/** POST /local/pull; progress then comes from `local_pull` events. */
export async function pullModel(name: string): Promise<void> {
  ensureTap();
  const cur = pulls.peek().get(name);
  if (cur && !cur.error && !isDone(cur)) return;   // already downloading
  setPull({ model: name, completed: 0, total: 0, status: "starting", error: null });
  try {
    await actions.client().localPull(name);
  } catch (e) {
    setPull({ model: name, completed: 0, total: 0, status: "failed", error: describe(e) });
  }
}

function isDone(p: LocalPull): boolean { return p.status === "success"; }

function setPull(p: PullState): void {
  const m = new Map(pulls.peek());
  m.set(p.model, p);
  pulls.value = m;
}

let untap: (() => void) | null = null;

function ensureTap(): void {
  if (untap) return;
  untap = onCoreEvent((ev) => {
    if (ev.type !== "local_pull") return;
    const d = localPullSchema.safeParse(ev.data);
    if (!d.success) { debug("bad local_pull event", d.error.issues[0]?.message); return; }
    const failed = /error|fail/i.test(d.data.status);
    setPull({ ...d.data, error: failed ? d.data.status : null });
    if (isDone(d.data)) void refreshCatalog();
  });
}

/** "38% · 7.2 of 19 GB" (or the status word before sizes are known). */
export function pullText(p: PullState): string {
  if (p.error) return `download failed — ${p.error}`;
  if (p.status === "success") return "downloaded";
  if (p.total <= 0) return p.status || "starting";
  const pct = Math.min(100, Math.floor((p.completed / p.total) * 100));
  const g = (b: number) => (b / 1e9).toFixed(1);
  return `${pct}% · ${g(p.completed)} of ${g(p.total)} GB`;
}

/** Tests. */
export function resetCatalog(): void {
  catalog.value = EMPTY;
  pulls.value = new Map();
  ollamaStart.value = { busy: false, error: null };
  inflight = null;
  untap?.();
  untap = null;
}
