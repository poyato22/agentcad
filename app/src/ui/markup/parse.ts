// Mises markup → a small block/inline AST, rendered by render.tsx. The agents emit this (Task C3
// owns the text); it is standard GFM (headings, bold/italic, inline code, links, lists, fenced
// code, tables, strikethrough) plus Mises extensions:
//   - callouts  `> [!pass]` / `> [!warn]` / `> [!fail]` / `> [!note]`, colored by status
//   - part chips `[[part:<part_id>|Label]]`, a click selects the part
//   - values     numbers with units (`3.2 mm`, `48 g`, `SF 2.4`, `120 MPa`), auto-tinted
//   - highlight  `==text==`
// This module is pure (no Preact): it only produces data. Raw HTML is never parsed as markup —
// it stays text and render.tsx escapes it, so nothing here ever emits HTML.
//
// Agent text is untrusted, so the parser is built to stay cheap on hostile input: nesting is
// capped at MAX_DEPTH (deeper quotes / lists / emphasis become plain text), every pattern is
// bounded or sticky, and a delimiter with no closer left is never searched for again — the scan
// is linear in the input (times the depth cap), never quadratic, and never recurses deeply.

/** A callout's kind maps to a status token (note is neutral). */
export type CalloutKind = "pass" | "warn" | "fail" | "note";
export type Align = "left" | "center" | "right" | null;

export type Inline =
  | { type: "text"; text: string }
  | { type: "strong"; children: Inline[] }
  | { type: "em"; children: Inline[] }
  | { type: "del"; children: Inline[] }
  | { type: "highlight"; children: Inline[] }
  | { type: "code"; text: string }
  | { type: "value"; text: string }
  | { type: "part"; id: string; label: string }
  | { type: "link"; href: string; host: string; children: Inline[] };

export type Block =
  | { type: "heading"; level: number; inline: Inline[] }
  | { type: "paragraph"; inline: Inline[] }
  | { type: "callout"; kind: CalloutKind; blocks: Block[] }
  | { type: "list"; ordered: boolean; items: Block[][] }
  | { type: "code"; lang: string | null; text: string }
  | { type: "table"; header: Inline[][]; align: Align[]; rows: Inline[][][] }
  | { type: "hr" };

/** Deepest nesting of quotes / lists (blocks) or emphasis / links (inline) parsed as markup. */
export const MAX_DEPTH = 16;

/** Units a bare number may carry. Achromatic value tint. */
const UNITS = [
  "mm³", "cm³", "mm^3", "cm^3", "mm²", "cm²", "mm^2", "cm^2",
  "µm", "mm", "cm", "km", "mAh", "mA", "kg", "mg", "g",
  "GPa", "MPa", "kPa", "Pa", "kN", "mN", "N·m", "N⋅m", "Nm", "N",
  "kHz", "Hz", "°C", "°F", "°", "deg", "rpm", "RPM",
  "mm/s", "m/s", "km/h", "ms", "min", "Wh", "kW", "W", "V", "A", "%", "s", "m",
];
/** Prefix-form values, e.g. "SF 2.4", "Cd 0.47". */
const VALUE_PREFIX = ["SF", "Cd", "Cl", "Re"];

// Bounded digits, and a number may only start where no digit/point precedes it, so a long digit
// run is tried once (not at every offset): the scan stays linear.
const NUM = "[+-]?\\d{1,12}(?:\\.\\d{1,12})?";
// Longest first: alternation is first-match-wins, so "mm/s" must be tried before "mm".
const UNIT_ALT = [...UNITS].sort((a, b) => b.length - a.length).map((u) => u.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
const PREFIX_ALT = VALUE_PREFIX.join("|");
/** A number + unit, or a prefix + number. The unit needs a non-word boundary after it so that
 *  "3mmap" or "12 games" is left alone. */
const VALUE_RE = new RegExp(
  `(?:\\b(?:${PREFIX_ALT})\\s${NUM}(?![\\d.]))|(?:(?<![\\d.])${NUM}\\s?(?:${UNIT_ALT})(?![\\p{L}\\d]))`,
  "gu",
);

/** Characters never allowed in an href: controls, format chars (bidi overrides, zero-width), and
 *  any whitespace — they let a link read as something it isn't. */
const UNSAFE_HREF_RE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}\s]/u;

/** Only plain http(s) URLs with a host render as links; anything else is text. */
export function safeHref(href: string): string | null {
  if (href.length > 2048 || UNSAFE_HREF_RE.test(href)) return null;
  if (!/^https?:\/\//i.test(href)) return null;
  try {
    const u = new URL(href);
    if ((u.protocol !== "http:" && u.protocol !== "https:") || !u.hostname) return null;
  } catch {
    return null;
  }
  return href;
}

/** The host a link really opens (punycode for IDNs, so look-alike letters show as xn--…). */
export function linkHost(href: string): string {
  try { return new URL(href).hostname; } catch { return ""; }
}

// ---- blocks

const CALLOUT_RE = /^\[!(pass|warn|fail|note)\]\s*/i;
const FENCE_RE = /^(```+|~~~+)\s*([^\s`]*)\s*$/;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const HR_RE = /^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/;
const UL_RE = /^(\s*)[-*+]\s+(.*)$/;
const OL_RE = /^(\s*)\d{1,9}[.)]\s+(.*)$/;

/** Split markup into blocks. */
export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n");
  return parseLines(lines, 0);
}

/** Past the depth cap, lines are one paragraph of plain text (no further parsing). */
function plainParagraph(lines: string[]): Block {
  return { type: "paragraph", inline: [{ type: "text", text: lines.map((l) => l.trim()).join("\n") }] };
}

function parseLines(lines: string[], depth: number): Block[] {
  if (depth >= MAX_DEPTH) return lines.some((l) => l.trim() !== "") ? [plainParagraph(lines)] : [];
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") { i++; continue; }

    // fenced code
    const fence = FENCE_RE.exec(line);
    if (fence) {
      const marker = fence[1][0];
      const body: string[] = [];
      i++;
      const close = marker === "`" ? /^```+\s*$/ : /^~~~+\s*$/;
      while (i < lines.length && !close.test(lines[i])) { body.push(lines[i]); i++; }
      if (i < lines.length) i++;               // closing fence
      out.push({ type: "code", lang: fence[2] || null, text: body.join("\n") });
      continue;
    }

    if (HR_RE.test(line)) { out.push({ type: "hr" }); i++; continue; }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      out.push({ type: "heading", level: heading[1].length, inline: parseInline(heading[2].trim()) });
      i++;
      continue;
    }

    // blockquote / callout
    if (/^\s*>/.test(line)) {
      const quoted: string[] = [];
      // each `> [!kind]` line starts its own callout, even with no blank line between them
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        const q = lines[i].replace(/^\s*>\s?/, "");
        if (quoted.length > 0 && CALLOUT_RE.test(q)) break;
        quoted.push(q);
        i++;
      }
      const head = CALLOUT_RE.exec(quoted[0] ?? "");
      const kind = (head ? head[1].toLowerCase() : "note") as CalloutKind;
      if (head) quoted[0] = quoted[0].replace(CALLOUT_RE, "");
      out.push({ type: "callout", kind, blocks: parseLines(dropLeadingBlank(quoted), depth + 1) });
      continue;
    }

    // table: a header row of pipes followed by a delimiter row
    if (line.includes("|") && i + 1 < lines.length && isTableDelim(lines[i + 1])) {
      const table = parseTable(lines, i);
      out.push(table.block);
      i = table.next;
      continue;
    }

    // lists
    if (UL_RE.test(line) || OL_RE.test(line)) {
      const list = parseList(lines, i, depth);
      out.push(list.block);
      i = list.next;
      continue;
    }

    // paragraph: consecutive non-blank lines that don't start another block
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" && !startsBlock(lines, i)) { para.push(lines[i].trim()); i++; }
    out.push({ type: "paragraph", inline: parseInline(para.join("\n")) });
  }
  return out;
}

function dropLeadingBlank(lines: string[]): string[] {
  let k = 0;
  while (k < lines.length && lines[k].trim() === "") k++;
  return lines.slice(k);
}

function startsBlock(lines: string[], i: number): boolean {
  const line = lines[i];
  return FENCE_RE.test(line) || HR_RE.test(line) || HEADING_RE.test(line) || /^\s*>/.test(line)
    || UL_RE.test(line) || OL_RE.test(line)
    || (line.includes("|") && i + 1 < lines.length && isTableDelim(lines[i + 1]));
}

function isTableDelim(line: string): boolean {
  const t = line.trim();
  if (!t.includes("|") && !/^:?-+:?$/.test(t)) return false;
  const cells = splitRow(t);
  return cells.length > 0 && cells.every((c) => /^:?-+:?$/.test(c.trim()));
}

/** Split a table row on unescaped pipes, dropping the outer empties from a leading/trailing `|`. */
function splitRow(row: string): string[] {
  const cells: string[] = [];
  let buf = "";
  for (let k = 0; k < row.length; k++) {
    const ch = row[k];
    if (ch === "\\" && row[k + 1] === "|") { buf += "|"; k++; continue; }
    if (ch === "|") { cells.push(buf); buf = ""; continue; }
    buf += ch;
  }
  cells.push(buf);
  if (cells.length && cells[0].trim() === "") cells.shift();
  if (cells.length && cells[cells.length - 1].trim() === "") cells.pop();
  return cells;
}

function parseTable(lines: string[], start: number): { block: Block; next: number } {
  const header = splitRow(lines[start]).map((c) => parseInline(c.trim()));
  const align: Align[] = splitRow(lines[start + 1]).map((c) => {
    const t = c.trim();
    const l = t.startsWith(":");
    const r = t.endsWith(":");
    return l && r ? "center" : r ? "right" : l ? "left" : null;
  });
  const rows: Inline[][][] = [];
  let i = start + 2;
  while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
    rows.push(splitRow(lines[i]).map((c) => parseInline(c.trim())));
    i++;
  }
  return { block: { type: "table", header, align, rows }, next: i };
}

function parseList(lines: string[], start: number, depth: number): { block: Block; next: number } {
  const ordered = OL_RE.test(lines[start]);
  const re = ordered ? OL_RE : UL_RE;
  const other = ordered ? UL_RE : OL_RE;
  const items: Block[][] = [];
  let i = start;
  while (i < lines.length) {
    const m = re.exec(lines[i]);
    if (!m) break;
    const itemLines = [m[2]];
    i++;
    // continuation / nested lines indented under the marker
    while (i < lines.length && lines[i].trim() !== "" && !re.test(lines[i]) && !other.test(lines[i]) && /^\s+\S/.test(lines[i])) {
      itemLines.push(lines[i].replace(/^\s+/, ""));
      i++;
    }
    items.push(parseLines(itemLines, depth + 1));
  }
  return { block: { type: "list", ordered, items }, next: i };
}

// ---- inline

/** Parse one run of inline text into nodes (delimiters first, then number-with-unit values). */
export function parseInline(src: string): Inline[] {
  return splitValues(parseInlineRaw(src, 0));
}

const SPECIAL = new Set(["`", "[", "=", "*", "_", "~"]);
// sticky (anchored at lastIndex) and bounded: each attempt reads at most a few hundred chars
const PART_RE = /\[\[part:([a-z][a-z0-9_]{1,40})(?:\|([^\][\n]{0,200}))?\]\]/y;
const LINK_RE = /\[([^[\]\n]{0,500})\]\(([^)\s]{1,2048})\)/y;
const isSpace = (ch: string | undefined) => ch === undefined || /\s/.test(ch);

function parseInlineRaw(src: string, depth: number): Inline[] {
  if (depth >= MAX_DEPTH) return src ? [{ type: "text", text: src }] : [];
  const out: Inline[] = [];
  /** Delimiters (or backtick-run lengths) with no valid closer anywhere after the current point. */
  const dead = new Set<string>();
  let textStart = 0;
  let i = 0;
  const flushTo = (end: number) => { if (end > textStart) out.push({ type: "text", text: src.slice(textStart, end) }); };

  /** The first valid closer of `d` at or after `from`, or -1 (then `d` is dead for this run). A
   *  closer's validity doesn't depend on the opener, so a miss holds for every later opener. */
  const closer = (d: string, from: number, needNonSpaceBefore: boolean): number => {
    if (dead.has(d)) return -1;
    let j = from;
    for (;;) {
      const k = src.indexOf(d, j);
      if (k < 0) { dead.add(d); return -1; }
      if (!needNonSpaceBefore || !isSpace(src[k - 1])) return k;
      j = k + 1;
    }
  };

  /** A paired delimiter at i (strong, del, em, highlight); returns the next index, or -1. */
  const pair = (d: string, kind: "strong" | "del" | "em" | "highlight"): number => {
    const start = i + d.length;
    if (kind === "highlight" ? src[start] === "=" || start >= src.length : isSpace(src[start])) return -1;
    const k = closer(d, start + 1, kind !== "highlight");
    if (k < 0) return -1;
    flushTo(i);
    out.push({ type: kind, children: parseInlineRaw(src.slice(start, k), depth + 1) });
    return k + d.length;
  };

  while (i < src.length) {
    const c = src[i];
    if (!SPECIAL.has(c)) { i++; continue; }
    let next = -1;

    if (c === "`") {
      // inline code: a run of N backticks up to the next run of exactly N (no nested parsing)
      let e = i;
      while (src[e] === "`") e++;
      const n = e - i;
      const key = `\`${n}`;
      if (!dead.has(key)) {
        let j = e;
        for (;;) {
          const s = src.indexOf("`", j);
          if (s < 0) { dead.add(key); break; }
          let t = s;
          while (src[t] === "`") t++;
          if (t - s === n) { flushTo(i); out.push({ type: "code", text: src.slice(e, s) }); next = t; break; }
          j = t;
        }
      }
      if (next < 0) { i = e; continue; }          // an unmatched run stays text
    } else if (c === "[") {
      PART_RE.lastIndex = i;
      const p = PART_RE.exec(src);
      if (p) {
        flushTo(i);
        out.push({ type: "part", id: p[1], label: (p[2] ?? "").trim() || p[1] });
        next = PART_RE.lastIndex;
      } else {
        LINK_RE.lastIndex = i;
        const m = LINK_RE.exec(src);
        if (m) {
          const href = safeHref(m[2]);
          flushTo(i);
          if (href) out.push({ type: "link", href, host: linkHost(href), children: parseInlineRaw(m[1], depth + 1) });
          else out.push(...parseInlineRaw(m[1] || m[2], depth + 1));
          next = LINK_RE.lastIndex;
        }
      }
    } else if (c === "=" && src[i + 1] === "=") {
      next = pair("==", "highlight");
    } else if ((c === "*" || c === "_") && src[i + 1] === c) {
      next = pair(c + c, "strong");
      if (next < 0) next = pair(c, "em");
    } else if (c === "~" && src[i + 1] === "~") {
      next = pair("~~", "del");
    } else if (c === "*" || c === "_") {
      next = pair(c, "em");
    }

    if (next < 0) { i++; continue; }
    i = next;
    textStart = next;
  }
  flushTo(src.length);
  return out;
}

/** Wrap number-with-unit runs inside plain text nodes as `value` nodes (tinted). */
function splitValues(nodes: Inline[]): Inline[] {
  const out: Inline[] = [];
  for (const n of nodes) {
    if (n.type === "text") { out.push(...valueScan(n.text)); continue; }
    if ("children" in n) { out.push({ ...n, children: splitValues(n.children) }); continue; }
    out.push(n);
  }
  return out;
}

function valueScan(text: string): Inline[] {
  const out: Inline[] = [];
  VALUE_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  let last = 0;
  while ((m = VALUE_RE.exec(text)) !== null) {
    if (m.index > last) out.push({ type: "text", text: text.slice(last, m.index) });
    out.push({ type: "value", text: m[0] });
    last = m.index + m[0].length;
  }
  if (last === 0) return [{ type: "text", text }];
  if (last < text.length) out.push({ type: "text", text: text.slice(last) });
  return out;
}
