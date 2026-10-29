// Typed values while drawing (SketchUp's measurements box): "40" a length, "40,20" a pair
// (width, height or dx, dy), "<30" an angle, "40<30" length at an angle, "Ø4" / "R2" a diameter
// or radius. Units: mm (default), cm, m, in or ". Decimals use a point (the comma separates a pair).

export type Typed =
  | { kind: "length"; mm: number }
  | { kind: "pair"; a: number; b: number }
  | { kind: "angle"; deg: number }
  | { kind: "polar"; mm: number; deg: number }
  | { kind: "diameter"; mm: number }
  | { kind: "radius"; mm: number };

const UNIT_MM: Record<string, number> = { "": 1, mm: 1, cm: 10, m: 1000, in: 25.4, '"': 25.4 };
const LEN = String.raw`(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(mm|cm|m|in|")?`;
const ANG = String.raw`(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:°|deg)?`;

function mm(v: string, u: string | undefined): number {
  return Math.round(Number(v) * UNIT_MM[(u ?? "").toLowerCase()] * 1e6) / 1e6;
}

/** The typed text → a value, or null when it isn't one (the box shows it as invalid). */
export function parseTyped(text: string): Typed | null {
  const t = text.trim().toLowerCase().replace(/\s+/g, " ");
  if (!t) return null;
  let m = new RegExp(String.raw`^(?:ø|⌀|d)\s*${LEN}$`).exec(t);
  if (m) return positive({ kind: "diameter" as const, mm: mm(m[1], m[2]) });
  m = new RegExp(String.raw`^r\s*${LEN}$`).exec(t);
  if (m) return positive({ kind: "radius" as const, mm: mm(m[1], m[2]) });
  m = new RegExp(String.raw`^<\s*${ANG}$`).exec(t);
  if (m) return { kind: "angle", deg: Number(m[1]) };
  m = new RegExp(String.raw`^${LEN}\s*<\s*${ANG}$`).exec(t);
  if (m) return positive({ kind: "polar" as const, mm: mm(m[1], m[2]), deg: Number(m[3]) });
  m = new RegExp(String.raw`^${LEN}\s*(?:,|x|×|\s)\s*${LEN}$`).exec(t);
  if (m) {
    const a = mm(m[1], m[2]);
    const b = mm(m[3], m[4] ?? m[2]);
    return { kind: "pair", a, b };                 // 0,0 is the origin (a first point); as a step it is refused
  }
  m = new RegExp(String.raw`^${LEN}$`).exec(t);
  if (m) return positive({ kind: "length" as const, mm: mm(m[1], m[2]) });
  return null;
}

function positive<T extends { mm: number }>(v: T): T | null {
  return v.mm > 0 && Number.isFinite(v.mm) ? v : null;
}

/** Characters that start or continue typed input while drawing. Letters only continue a number
 *  (units, "x"), except R / D to start a radius or diameter where the step takes one (`radial`). */
export function isTypedChar(key: string, buffer: string, radial = false): boolean {
  if (key.length !== 1) return false;
  if (/[0-9.<,\-Øø⌀"°×]/.test(key)) return true;
  if (/^[rRdD]$/.test(key) && buffer === "") return radial;
  return buffer !== "" && /[a-zA-Z ]/.test(key);
}
