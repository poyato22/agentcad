// Which parts the viewer hides, per build. A view preference only (the core has no field for it):
// kept in this browser's storage, and nothing breaks when storage is unavailable.
import { computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { activeBuildId } from "../../state/builds";

const KEY = "mises.hiddenParts";

function load(): Record<string, string[]> {
  try {
    const raw = globalThis.localStorage?.getItem(KEY);
    const v: unknown = raw ? JSON.parse(raw) : {};
    return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, string[]>) : {};
  } catch {
    return {};
  }
}

/** build id ("" before builds load) → hidden part ids. */
export const hiddenByBuild: Signal<Readonly<Record<string, readonly string[]>>> = signal(load());

export const hiddenParts: ReadonlySignal<ReadonlySet<string>> = computed(
  () => new Set(hiddenByBuild.value[activeBuildId.value ?? ""] ?? []),
);

function save(ids: Iterable<string>): void {
  const b = activeBuildId.peek() ?? "";
  const next = { ...hiddenByBuild.peek(), [b]: [...ids] };
  hiddenByBuild.value = next;
  try { globalThis.localStorage?.setItem(KEY, JSON.stringify(next)); } catch { /* storage unavailable: in memory only */ }
}

export function setHidden(partId: string | readonly string[], hidden: boolean): void {
  const cur = new Set(hiddenByBuild.peek()[activeBuildId.peek() ?? ""] ?? []);
  for (const id of typeof partId === "string" ? [partId] : partId) { if (hidden) cur.add(id); else cur.delete(id); }
  save(cur);
}

/** Only these parts stay shown (of `all`, the build's part ids). */
export function isolate(keep: readonly string[], all: readonly string[]): void {
  const k = new Set(keep);
  save(all.filter((id) => !k.has(id)));
}

/** Every part of the build shown again. */
export function showAll(): void { save([]); }

export function resetVisibility(): void { hiddenByBuild.value = {}; }
