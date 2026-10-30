// Per-part appearance (Plan 7 U8, R22): a material preset with physically based metalness and
// roughness, a color and an opacity (PATCH /parts/{id} {appearance}). The same table as the core's
// mises/appearance.py PRESETS. The viewer turns it into a MeshStandardMaterial lit by a small
// built-in environment map; the part list draws it as a tiny lit sphere.

export const PRESET_IDS = [
  "matte_plastic", "glossy_plastic", "brushed_aluminum", "polished_metal", "anodized", "rubber", "clear",
] as const;
export type PresetId = (typeof PRESET_IDS)[number];

export interface Appearance {
  preset: PresetId | null; color: string | null; metalness: number; roughness: number; opacity: number;
}

export interface Preset { id: PresetId; name: string; metalness: number; roughness: number; opacity: number; color: string | null }

export const PRESETS: readonly Preset[] = [
  { id: "matte_plastic", name: "Matte plastic", metalness: 0, roughness: 0.85, opacity: 1, color: null },
  { id: "glossy_plastic", name: "Glossy plastic", metalness: 0, roughness: 0.22, opacity: 1, color: null },
  { id: "brushed_aluminum", name: "Brushed aluminum", metalness: 1, roughness: 0.42, opacity: 1, color: "#c4c8cc" },
  { id: "polished_metal", name: "Polished metal", metalness: 1, roughness: 0.06, opacity: 1, color: "#e6e8eb" },
  { id: "anodized", name: "Anodized", metalness: 0.85, roughness: 0.32, opacity: 1, color: "#3d6fb0" },
  { id: "rubber", name: "Rubber", metalness: 0, roughness: 0.97, opacity: 1, color: "#2e2f31" },
  { id: "clear", name: "Clear", metalness: 0, roughness: 0.05, opacity: 0.35, color: "#dde8f0" },
];

/** A part with no appearance: the drafting look (matte, opaque). */
export const DEFAULT_MATERIAL = { metalness: 0, roughness: 0.9, opacity: 1 } as const;

export function presetOf(id: string | null | undefined): Preset | undefined {
  return PRESETS.find((p) => p.id === id);
}

/** What a renderer needs: the color (null = the theme's face grey), metalness, roughness and
 *  opacity, and whether the part has a real material (only those get environment reflections, so
 *  plain parts keep the drafting look). */
export interface MaterialParams { color: string | null; metalness: number; roughness: number; opacity: number; shiny: boolean }

export function materialOf(p: { color?: string | null; appearance?: Appearance | null }): MaterialParams {
  const a = p.appearance;
  if (!a) return { color: p.color ?? null, ...DEFAULT_MATERIAL, shiny: false };
  return { color: a.color ?? p.color ?? null, metalness: a.metalness, roughness: a.roughness, opacity: a.opacity, shiny: true };
}

/** The appearance a preset gives a part that is `color` now (an untinted preset keeps it). */
export function fromPreset(id: PresetId, color: string | null): Appearance {
  const p = presetOf(id) as Preset;
  return { preset: id, color: p.color ?? color, metalness: p.metalness, roughness: p.roughness, opacity: p.opacity };
}

/** The same material in another color (a palette pick keeps the finish). */
export function withColor(a: Appearance | null | undefined, color: string | null): Appearance | null {
  return a ? { ...a, color } : null;
}

/** The look's name for labels: "Polished metal", "Custom", or null when it has none. */
export function appearanceName(a: Appearance | null | undefined): string | null {
  if (!a) return null;
  return presetOf(a.preset)?.name ?? "Custom";
}

/** CSS for a tiny lit sphere of this material: a highlight whose size and strength follow the
 *  roughness, a darker rim for metals. Used by the row swatch and the preset tiles. */
export function swatchStyle(m: MaterialParams, fallback = "var(--mesh-face)"): Record<string, string> {
  const base = m.color ?? fallback;
  if (!m.shiny) return { background: base };
  const gloss = 1 - m.roughness;                        // 0 matte … 1 mirror
  const spot = Math.round(10 + m.roughness * 40);       // highlight radius, %
  const hi = Math.round(25 + gloss * 70);               // highlight strength, %
  const rim = Math.round(m.metalness * 45 + 10);        // rim darkening, %
  const layers = [
    `radial-gradient(circle at 32% 28%, rgb(255 255 255 / ${hi}%) 0, rgb(255 255 255 / 0) ${spot}%)`,
    `radial-gradient(circle at 50% 50%, ${base} 45%, color-mix(in srgb, ${base}, #000 ${rim}%) 100%)`,
  ];
  return { background: layers.join(", "), opacity: String(Math.max(0.45, m.opacity)) };
}
