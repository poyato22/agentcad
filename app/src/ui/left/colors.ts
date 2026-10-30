// Part colors (PATCH /parts/{id} {color: "#rrggbb" | null}). These are the user's data, not UI
// tokens: muted material-like tones that read on both viewer themes. null = the theme's face grey.

export interface Swatch { hex: string; name: string }

export const PALETTE: readonly Swatch[] = [
  { hex: "#d9822b", name: "Orange" },
  { hex: "#c9a227", name: "Ochre" },
  { hex: "#7d9a3c", name: "Olive" },
  { hex: "#3f9b6e", name: "Green" },
  { hex: "#2f9a9c", name: "Teal" },
  { hex: "#5b7fa6", name: "Steel" },
  { hex: "#8466c4", name: "Violet" },
  { hex: "#c25b8a", name: "Magenta" },
  { hex: "#c2463d", name: "Red" },
  { hex: "#8c6a4f", name: "Bronze" },
  { hex: "#a3a8ad", name: "Aluminium" },
  { hex: "#4a4d52", name: "Graphite" },
];

const HEX_RE = /^#[0-9a-f]{6}$/;

/** "#ABC", "abc", "#aabbcc", " AABBCC " → "#aabbcc"; anything else → null. */
export function normalizeHex(v: string): string | null {
  let s = v.trim().toLowerCase();
  if (!s.startsWith("#")) s = `#${s}`;
  if (/^#[0-9a-f]{3}$/.test(s)) s = `#${s[1]}${s[1]}${s[2]}${s[2]}${s[3]}${s[3]}`;
  return HEX_RE.test(s) ? s : null;
}

export function swatchName(hex: string | null | undefined): string {
  if (!hex) return "Default grey";
  return PALETTE.find((s) => s.hex === hex)?.name ?? hex;
}
