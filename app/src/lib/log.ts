// Debug logging that is silent in tests and production builds. Never log tokens or payloads.
const enabled = import.meta.env.DEV && import.meta.env.MODE !== "test";

export function debug(...args: unknown[]): void {
  if (enabled) console.debug("[mises]", ...args);
}
