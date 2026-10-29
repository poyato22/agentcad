// Opens a web link in the user's system browser. The ONE way the app opens external links (A6 fix
// round 1 ruling): a plain <a target="_blank"> is dropped silently by the macOS webview (wry has no
// new-window handler), so every link goes through here.
//
// In the Tauri app: tauri-plugin-opener's `open_url`, whose capability is scoped to https://* and
// http://* only (src-tauri/capabilities/default.json; checked by a cargo test). In a plain browser
// (dev, e2e): window.open with noopener. Only http(s) URLs without credentials are opened; anything
// else (javascript:, file:, custom schemes) is refused here before it reaches either path.
import { invoke, isTauri } from "@tauri-apps/api/core";
import { debug } from "../lib/log";

/** The URL to open, normalized, or null when it isn't an http(s) link we may open. */
export function externalUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  let u: URL;
  try { u = new URL(raw); } catch { return null; }
  if ((u.protocol !== "https:" && u.protocol !== "http:") || u.username || u.password || !u.hostname) return null;
  return u.href;
}

export interface OpenDeps {
  tauri?: () => boolean;
  invokeOpen?: (url: string) => Promise<unknown>;
  windowOpen?: (url: string) => unknown;
}

/** Opens `raw` in the system browser. Resolves false when it was refused or failed. */
export async function openExternal(raw: string, deps: OpenDeps = {}): Promise<boolean> {
  const url = externalUrl(raw);
  if (!url) return false;
  const inTauri = (deps.tauri ?? isTauri)();
  try {
    if (inTauri) {
      await (deps.invokeOpen ?? ((u: string) => invoke("plugin:opener|open_url", { url: u })))(url);
    } else {
      (deps.windowOpen ?? ((u: string) => window.open(u, "_blank", "noopener,noreferrer")))(url);
    }
    return true;
  } catch (e) {
    debug("opening an external link failed", e);
    return false;
  }
}
