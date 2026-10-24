// Where the core is and how to authenticate. Inside the Tauri shell the Rust supervisor owns
// this (`invoke("core_info")`, `core-status` events). In `vite` dev/test mode only, it falls back
// to VITE_MISES_URL / VITE_MISES_TOKEN; that branch is compiled out of production builds.
// The token is never written to storage or logs.
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { z } from "zod";
import type { CoreInfo } from "./types";

export type ShellCoreStatus = { state: "starting" } | { state: "ready"; info: CoreInfo }
  | { state: "crashed"; code: number | null; stderrTail: string; restarts: number } | { state: "stopped" };

const coreInfoSchema = z.object({
  baseUrl: z.string(), wsUrl: z.string(), token: z.string().min(1), owned: z.boolean(), pid: z.number().int().nullish(),
});

const shellStatusSchema = z.union([
  z.object({ state: z.literal("starting") }),
  z.object({ state: z.literal("ready"), info: coreInfoSchema }),
  z.object({
    state: z.literal("crashed"), code: z.number().nullable().default(null),
    // accept snake_case too in case the Rust enum's fields aren't renamed
    stderrTail: z.string().optional(), stderr_tail: z.string().optional(), restarts: z.number().default(0),
  }).transform((c) => ({ state: "crashed" as const, code: c.code, stderrTail: c.stderrTail ?? c.stderr_tail ?? "", restarts: c.restarts })),
  z.object({ state: z.literal("stopped") }),
]);

export function parseShellStatus(raw: unknown): ShellCoreStatus | null {
  const r = shellStatusSchema.safeParse(raw);
  return r.success ? r.data : null;
}

const RETRY_MS = 250;

/** Resolves once the core is reachable info-wise (not health-checked here). */
export async function resolveCoreInfo(): Promise<CoreInfo> {
  if (isTauri()) {
    for (;;) {
      try {
        return coreInfoSchema.parse(await invoke<unknown>("core_info"));
      } catch (e) {
        if (String(e).includes("starting")) {
          await new Promise((r) => setTimeout(r, RETRY_MS));
          continue;
        }
        throw e instanceof Error ? e : new Error(String(e));
      }
    }
  }
  if (import.meta.env.DEV || import.meta.env.MODE === "test") {
    return devCoreInfo(import.meta.env.VITE_MISES_URL, import.meta.env.VITE_MISES_TOKEN);
  }
  throw new Error("Mises core unavailable: not running inside the Mises app");
}

/** Dev/test only: build CoreInfo from VITE_MISES_URL (http://127.0.0.1:<port>) and VITE_MISES_TOKEN. */
export function devCoreInfo(url: string | undefined, token: string | undefined): CoreInfo {
  if (!url || !token) throw new Error("set VITE_MISES_URL and VITE_MISES_TOKEN (see .env.development)");
  const baseUrl = url.replace(/\/+$/, "");
  const wsUrl = baseUrl.replace(/^http/, "ws") + "/events";
  return { baseUrl, wsUrl, token, owned: false };
}

/**
 * The shell's current core status (`invoke("core_status")`), or null outside Tauri / on error.
 * Needed because a status the shell emits during setup ("`uv` not found") fires before the
 * webview is listening for `core-status`.
 */
export async function fetchCoreStatus(): Promise<ShellCoreStatus | null> {
  if (!isTauri()) return null;
  try {
    return parseShellStatus(await invoke<unknown>("core_status"));
  } catch {
    return null;
  }
}

/** Subscribes to the shell's `core-status` events. No-op (never calls back) in a plain browser. */
export function onCoreStatus(cb: (s: ShellCoreStatus) => void): () => void {
  if (!isTauri()) return () => {};
  let unlisten: (() => void) | null = null;
  let cancelled = false;
  void listen<unknown>("core-status", (e) => {
    const s = parseShellStatus(e.payload);
    if (s) cb(s);
  }).then((u) => {
    if (cancelled) u();
    else unlisten = u;
  });
  return () => {
    cancelled = true;
    unlisten?.();
  };
}
