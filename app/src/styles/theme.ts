// Theme: dark (default) | light | system. Stored in the profile (`theme`), applied via a
// `data-theme` attribute on <html>; tokens.css redefines the palette for light/system. The signal
// is set from the profile on boot and on every save (ui/topbar/ProfileButton, ui/profile).
import { effect, signal, type Signal } from "@preact/signals";

export type Theme = "system" | "light" | "dark";
export const THEMES = ["system", "light", "dark"] as const satisfies readonly Theme[];

export const DEFAULT_THEME: Theme = "system";

/** The current theme. `data-theme` on <html> tracks it (see the effect below). */
export const theme: Signal<Theme> = signal<Theme>(DEFAULT_THEME);

export function isTheme(v: unknown): v is Theme {
  return v === "system" || v === "light" || v === "dark";
}

/** Sets <html data-theme>. Safe to call with anything; non-themes fall back to the default. */
export function applyTheme(t: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", isTheme(t) ? t : DEFAULT_THEME);
}

/** Sets the signal (which applies it). Used by the profile theme toggle. */
export function setTheme(t: Theme): void {
  theme.value = isTheme(t) ? t : DEFAULT_THEME;
}

// Keep the <html> attribute in step with the signal.
effect(() => applyTheme(theme.value));
