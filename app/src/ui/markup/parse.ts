// Mises markup → a small block/inline AST, rendered by render.tsx. The agents emit this (Task C3
// owns the text); it is standard GFM (headings, bold/italic, inline code, links, lists, fenced
// code, tables, strikethrough) plus Mises extensions:
//   - callouts  `> [!pass]` / `> [!warn]` / `> [!fail]` / `> [!note]`, colored by status
//   - part chips `[[part:<part_id>|Label]]`, a click selects the part
//   - values     numbers with units (`3.2 mm`, `48 g`, `SF 2.4`, `120 MPa`), auto-tinted
//   - highlight  `==text==`
// This module is pure (no Preact): it only produces data. Raw HTML is never parsed as markup —
// it stays text and render.tsx escapes it, so nothing here ever emits HTML.

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
  | { type: "link"; href: string; children: Inline[] };

export type Block =
  | { type: "heading"; level: number; inline: Inline[] }
  | { type: "paragraph"; inline: Inline[] }
  | { type: "callout"; kind: CalloutKind; blocks: Block[] }
  | { type: "list"; ordered: boolean; items: Block[][] }
  | { type: "code"; lang: string | null; text: string }
  | { type: "table"; header: Inline[][]; align: Align[]; rows: Inline[][][] }
  | { type: "hr" };

/** Valid part ids (same rule as the core's PART_ID_RE); a chip to a malformed id stays text. */
const PART_ID_RE = /^[a-z][a-z0-9_]{1,40}$/;

/** Units a bare number may carry; longest first so "mm³" wins over "mm". Achromatic value tint. */
const UNITS = [
  "mm³", "cm³", "mm^3", "cm^3", "mm²", "cm²", "mm^2", "cm^2",
  "µm", "mm", "cm", "km", "mAh", "mA", "kg", "mg", "g",
  "GPa", "MPa", "kPa", "Pa", "kN", "mN", "N·m", "N⋅m", "Nm", "N",
  "kHz", "Hz", "°C", "°F", "°", "deg", "rpm", "RPM",
  "mm/s", "m/s", "km/h", "ms", "min", "Wh", "kW", "W", "V", "A", "%", "s", "m",
];
/** Prefix-form values, e.g. "SF 2.4", "Cd 0.47". */
const VALUE_PREFIX = ["SF", "Cd", "Cl", "Re"];

const NUM = "[+-]?\\d+(?:\\.\\d+)?";
// Longest first: alternation is first-match-wins, so "mm/s" must be tried before "mm".
const UNIT_ALT = [...UNITS].sort((a, b) => b.length - a.length).map((u) => u.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
const PREFIX_ALT = VALUE_PREFIX.join("|");
/** A number + unit, or a prefix + number. The unit needs a non-word boundary after it so that
 *  "3mmap" or "12 games" is left alone. */
const VALUE_RE = new RegExp(
  `(?:\\b(?:${PREFIX_ALT})\\s${NUM})|(?:${NUM}\\s?(?:${UNIT_ALT})(?![\\p{L}\\d]))`,
  "u",
);

/** Only http(s) links render as links (opened after the domain is shown); anything else is text. */
export function safeHref(href: string): string | null {
  const h = href.trim();
  if (/^https?:\/\/[^\s]+$/i.test(h)) return h;
  return null;
}

// ---- blocks

const CALLOUT_RE = /^\[!(pass|warn|fail|note)\]\s*/i;
const FENCE_RE = /^(```+|~~~+)\s*([^\s`]*)\s*$/;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const HR_RE = /^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/;
const UL_RE = /^(\s*)[-*+]\s+(.*)$/;
const OL_RE = /^(\s*)\d+[.)]\s+(.*)$/;

/** Split markup into blocks. */
export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n");
  return parseLines(lines);
}

function parseLines(lines: string[]): Block[] {
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
      const close = new RegExp(`^${marker === "`" ? "```+" : "~~~+"}\\s*$`);
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
      while (i < lines.length && /^\s*>/.test(lines[i])) { quoted.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      const head = CALLOUT_RE.exec(quoted[0] ?? "");
      const kind = (head ? head[1].toLowerCase() : "note") as CalloutKind;
      if (head) quoted[0] = quoted[0].replace(CALLOUT_RE, "");
      out.push({ type: "callout", kind, blocks: parseLines(dropLeadingBlank(quoted)) });
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
      const list = parseList(lines, i);
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
  const copy = [...lines];
  while (copy.length && copy[0].trim() === "") copy.shift();
  return copy;
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
  return cells.length > 0 && cells.every((c) => /^:?-{1,}:?$/.test(c.trim()));
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

function parseList(lines: string[], start: number): { block: Block; next: number } {
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
    items.push(parseLines(itemLines));
  }
  return { block: { type: "list", ordered, items }, next: i };
}

// ---- inline

/** Parse one run of inline text into nodes (delimiters first, then number-with-unit values). */
export function parseInline(src: string): Inline[] {
  return splitValues(parseInlineRaw(src));
}

function parseInlineRaw(src: string): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const flush = () => { if (text) { out.push({ type: "text", text }); text = ""; } };
  let i = 0;
  while (i < src.length) {
    const rest = src.slice(i);

    // inline code: `code` (highest precedence, no nested parsing)
    if (src[i] === "`") {
      const m = /^(`+)([\s\S]*?)\1(?!`)/.exec(rest);
      if (m) { flush(); out.push({ type: "code", text: m[2] }); i += m[0].length; continue; }
    }

    // part chip: [[part:id|Label]] or [[part:id]]
    if (rest.startsWith("[[")) {
      const m = /^\[\[part:([^\]|]+)(?:\|([^\]]*))?\]\]/.exec(rest);
      if (m && PART_ID_RE.test(m[1].trim())) {
        const id = m[1].trim();
        flush();
        out.push({ type: "part", id, label: (m[2] ?? "").trim() || id });
        i += m[0].length;
        continue;
      }
    }

    // link: [text](href) — only safe http(s) hrefs render as a link; else kept as text
    if (src[i] === "[") {
      const m = /^\[([^\]]*)\]\(([^)\s]+)\)/.exec(rest);
      if (m) {
        const href = safeHref(m[2]);
        flush();
        if (href) out.push({ type: "link", href, children: parseInlineRaw(m[1]) });
        else out.push(...parseInlineRaw(m[1] || m[2]));
        i += m[0].length;
        continue;
      }
    }

    // highlight ==text==
    if (rest.startsWith("==")) {
      const m = /^==(?!=)([\s\S]+?)==/.exec(rest);
      if (m) { flush(); out.push({ type: "highlight", children: parseInlineRaw(m[1]) }); i += m[0].length; continue; }
    }

    // strong **text** or __text__
    const strong = /^(\*\*|__)(?!\s)([\s\S]+?)(?<!\s)\1/.exec(rest);
    if (strong) { flush(); out.push({ type: "strong", children: parseInlineRaw(strong[2]) }); i += strong[0].length; continue; }

    // strikethrough ~~text~~
    const del = /^~~(?!\s)([\s\S]+?)(?<!\s)~~/.exec(rest);
    if (del) { flush(); out.push({ type: "del", children: parseInlineRaw(del[1]) }); i += del[0].length; continue; }

    // em *text* or _text_
    const em = /^(\*|_)(?!\s)([\s\S]+?)(?<!\s)\1/.exec(rest);
    if (em) { flush(); out.push({ type: "em", children: parseInlineRaw(em[2]) }); i += em[0].length; continue; }

    text += src[i];
    i++;
  }
  flush();
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
  const re = new RegExp(VALUE_RE.source, "gu");
  let m: RegExpExecArray | null;
  let last = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ type: "text", text: text.slice(last, m.index) });
    out.push({ type: "value", text: m[0] });
    last = m.index + m[0].length;
  }
  if (last === 0) return [{ type: "text", text }];
  if (last < text.length) out.push({ type: "text", text: text.slice(last) });
  return out;
}
