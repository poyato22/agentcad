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

export function setHidden(partId: string, hidden: boolean): void {
  const b = activeBuildId.peek() ?? "";
  const cur = new Set(hiddenByBuild.peek()[b] ?? []);
  if (hidden) cur.add(partId); else cur.delete(partId);
  const next = { ...hiddenByBuild.peek(), [b]: [...cur] };
  hiddenByBuild.value = next;
  try { globalThis.localStorage?.setItem(KEY, JSON.stringify(next)); } catch { /* storage unavailable: in memory only */ }
}

export function resetVisibility(): void { hiddenByBuild.value = {}; }
