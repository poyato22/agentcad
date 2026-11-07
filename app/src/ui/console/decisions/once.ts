// view-tab: a decision is sent once. Every rendering of an item (the View tab's "Needs your
// decision" section and the conversation) shares one in-flight promise per item key, so a double
// click, a second card for the same item or a key repeat never sends a second request; and an
// item the user decided (this session) is remembered as decided, with what and when, so every
// card for it turns read-only at once, before any refetch lands.
import { signal, type Signal } from "@preact/signals";
import { CoreError } from "../../../api/client";

export interface Resolved { text: string; at: number }

/** Items decided by the user, by key ("approval:<id>", "question:<id>", "pitch:<id>", "sketch:<id>", "skip:<project>"). */
export const resolved: Signal<ReadonlyMap<string, Resolved>> = signal<ReadonlyMap<string, Resolved>>(new Map());
/** Keys with a decision in flight. */
export const sending: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(new Set());

const inflight = new Map<string, Promise<unknown>>();

export function isResolved(key: string): boolean { return resolved.peek().has(key); }

export function markResolved(key: string, text: string, at: number = Date.now() / 1000): void {
  if (resolved.peek().get(key)?.text === text) return;
  const m = new Map(resolved.peek());
  m.set(key, { text, at });
  resolved.value = m;
}

/** The core's 409 for an item already decided (its body carries that decision). */
export function alreadyDecided(e: unknown): Record<string, unknown> | null {
  if (!(e instanceof CoreError) || e.code !== "already_decided") return null;
  const d = e.body?.decision;
  return typeof d === "object" && d !== null ? (d as Record<string, unknown>) : {};
}

/** Run `fn` for `key` at most once at a time; an already resolved key never runs again. */
export function once<T>(key: string, fn: () => Promise<T>): Promise<T | null> {
  if (isResolved(key)) return Promise.resolve(null);
  const running = inflight.get(key);
  if (running) return running as Promise<T>;
  setSending(key, true);
  const p = fn().finally(() => { inflight.delete(key); setSending(key, false); });
  inflight.set(key, p);
  return p;
}

function setSending(key: string, on: boolean): void {
  const n = new Set(sending.peek());
  if (on) n.add(key); else n.delete(key);
  sending.value = n;
}

/** Tests. */
export function resetOnce(): void { inflight.clear(); resolved.value = new Map(); sending.value = new Set(); }
