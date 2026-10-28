// Theme: dark | light | system (default). Stored in the profile (`theme`), applied via a
// `data-theme` attribute on <html>; tokens.css redefines the palette (and color-scheme) for
// light/system. The signal is set from the profile on boot and on every save
// (ui/topbar/ProfileButton, ui/profile).
//
// No flash: the last choice is cached in localStorage (a per-device convenience; the profile stays
// the source of truth) and applied synchronously when this module is first imported — main.tsx
// imports it before anything renders.
import { effect, signal, type Signal } from "@preact/signals";

export type Theme = "system" | "light" | "dark";
export const THEMES = ["system", "light", "dark"] as const satisfies readonly Theme[];

export const DEFAULT_THEME: Theme = "system";
export const THEME_STORAGE_KEY = "mises.theme";

export function isTheme(v: unknown): v is Theme {
  return v === "system" || v === "light" || v === "dark";
}

/** The cached choice, or the default when storage is empty, blocked or holds junk. */
export function readCachedTheme(): Theme {
  try {
    const v = globalThis.localStorage?.getItem(THEME_STORAGE_KEY);
    return isTheme(v) ? v : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

function writeCachedTheme(t: Theme): void {
  try { globalThis.localStorage?.setItem(THEME_STORAGE_KEY, t); } catch { /* private mode / blocked: fine */ }
}

/** The current theme. `data-theme` on <html> tracks it (see the effect below). */
export const theme: Signal<Theme> = signal<Theme>(readCachedTheme());

/** Sets <html data-theme>. Safe to call with anything; non-themes fall back to the default. */
export function applyTheme(t: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", isTheme(t) ? t : DEFAULT_THEME);
}

/** Sets the signal (which applies and caches it). Used by the profile theme toggle. */
export function setTheme(t: Theme): void {
  theme.value = isTheme(t) ? t : DEFAULT_THEME;
}

// Keep the <html> attribute and the cache in step with the signal (runs once, synchronously, now).
effect(() => {
  applyTheme(theme.value);
  writeCachedTheme(theme.value);
});
