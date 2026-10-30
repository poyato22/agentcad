// The viewer's background (Plan 7 U8, R21): the theme's drafting sheet (default), a solid color,
// or an Onshape-style soft gradient. Stored in the profile (`viewer_background`); the last choice
// is cached in this browser so the sheet doesn't flash the default before the profile loads.
import { signal, type Signal } from "@preact/signals";
import type { ViewerBackground } from "../../api/types";
import { actions } from "../../state/store";

export const BG_STORAGE_KEY = "mises.viewerBackground";
export const THEME_BACKGROUND: ViewerBackground = { mode: "theme", colors: [] };

export interface BackgroundChoice { id: string; name: string; bg: ViewerBackground }

/** The choices the corner menu and the profile offer. */
export const BACKGROUNDS: readonly BackgroundChoice[] = [
  { id: "theme", name: "Drafting sheet", bg: THEME_BACKGROUND },
  { id: "soft", name: "Soft gradient", bg: { mode: "gradient", colors: ["#fdfdfe", "#c9d3df"] } },
  { id: "studio", name: "Studio dark", bg: { mode: "gradient", colors: ["#4b5059", "#15171a"] } },
  { id: "white", name: "White", bg: { mode: "solid", colors: ["#ffffff"] } },
  { id: "grey", name: "Light grey", bg: { mode: "solid", colors: ["#e4e6e9"] } },
  { id: "slate", name: "Slate", bg: { mode: "solid", colors: ["#33373d"] } },
  { id: "black", name: "Black", bg: { mode: "solid", colors: ["#000000"] } },
];

const HEX = /^#[0-9a-f]{6}$/i;

export function isBackground(v: unknown): v is ViewerBackground {
  if (typeof v !== "object" || v === null) return false;
  const { mode, colors } = v as { mode?: unknown; colors?: unknown };
  if (!Array.isArray(colors) || !colors.every((c) => typeof c === "string" && HEX.test(c))) return false;
  return mode === "theme" || (mode === "solid" && colors.length === 1) || (mode === "gradient" && colors.length === 2);
}

function readCached(): ViewerBackground {
  try {
    const raw = globalThis.localStorage?.getItem(BG_STORAGE_KEY);
    const v: unknown = raw ? JSON.parse(raw) : null;
    return isBackground(v) ? v : THEME_BACKGROUND;
  } catch {
    return THEME_BACKGROUND;
  }
}

export const viewerBackground: Signal<ViewerBackground> = signal<ViewerBackground>(readCached());

/** Sets the signal and the cache (no save): the profile's value on boot, a live preview. */
export function applyBackground(bg: ViewerBackground | null | undefined): void {
  const v = isBackground(bg) ? { mode: bg.mode, colors: bg.colors.map((c) => c.toLowerCase()) } : THEME_BACKGROUND;
  viewerBackground.value = v;
  try { globalThis.localStorage?.setItem(BG_STORAGE_KEY, JSON.stringify(v)); } catch { /* storage unavailable */ }
}

/** Shows it without caching it: a preview the Profile puts back unless it is saved. */
export function previewBackground(bg: ViewerBackground): void {
  if (isBackground(bg)) viewerBackground.value = bg;
}

/** Shows it at once and saves it to the profile; resolves false (and puts the old one back) when
 *  the core refused it. */
export async function saveBackground(bg: ViewerBackground): Promise<boolean> {
  const before = viewerBackground.peek();
  applyBackground(bg);
  try {
    await actions.client().updateProfile({ viewer_background: viewerBackground.peek() });
    return true;
  } catch {
    applyBackground(before);
    return false;
  }
}

/** Inline style for the sheet (.vp): nothing for the theme's sheet (its CSS draws the grid). */
export function backgroundStyle(bg: ViewerBackground): Record<string, string> | undefined {
  if (bg.mode === "solid") return { backgroundColor: bg.colors[0], backgroundImage: "none" };
  // the sheet's CSS tiles its grid (background-size): one gradient spans the whole sheet
  if (bg.mode === "gradient") return { backgroundColor: bg.colors[1], backgroundImage: `linear-gradient(180deg, ${bg.colors[0]}, ${bg.colors[1]})`, backgroundSize: "100% 100%" };
  return undefined;
}

/** The choice matching a background, if it is one of BACKGROUNDS. */
export function choiceOf(bg: ViewerBackground): BackgroundChoice | undefined {
  return BACKGROUNDS.find((c) => c.bg.mode === bg.mode && c.bg.colors.join() === bg.colors.map((x) => x.toLowerCase()).join());
}

function luminance(c: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Is it light (edges and overlays switch to dark ink on it)? Not the theme's sheet: null. A
 *  gradient is judged by its average, the solid by its color. */
export function isLight(bg: ViewerBackground): boolean | null {
  if (bg.mode === "theme") return null;
  const l = bg.colors.reduce((a, c) => a + luminance(c), 0) / bg.colors.length;
  return l > 0.5;
}

/** The model's edge color on this background (null: the theme's --mesh-edge). */
export const EDGE_ON_LIGHT = "#33373d";
export const EDGE_ON_DARK = "#e2e5e9";
export function edgeInk(bg: ViewerBackground): string | null {
  const light = isLight(bg);
  return light === null ? null : light ? EDGE_ON_LIGHT : EDGE_ON_DARK;
}
