// Ports for e2e runs. Several worktrees may run Playwright at once on one machine, so the
// defaults are derived from the checkout path (stable per worktree, different across
// worktrees). Override with MISES_E2E_VITE_PORT / MISES_E2E_CORE_PORT.
import { createHash } from "node:crypto";
import { resolve } from "node:path";

function derived(): number {
  const h = createHash("sha1").update(resolve(import.meta.dirname, "../..")).digest();
  return 20000 + (h.readUInt16BE(0) % 4000) * 2; // even ports in 20000–27998
}

const base = derived();
export const VITE_PORT = Number(process.env.MISES_E2E_VITE_PORT ?? base);
export const CORE_PORT = Number(process.env.MISES_E2E_CORE_PORT ?? base + 1);
export const CORE_TOKEN = "e2e-token";
export const APP_ORIGIN = `http://localhost:${VITE_PORT}`;
export const CORE_URL = `http://127.0.0.1:${CORE_PORT}`;
