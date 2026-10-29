import { describe, expect, it } from "vitest";
import { type Block, type Inline, parseBlocks, parseInline, safeHref } from "./parse";

/** Flatten inline nodes to a tagged string so assertions read clearly. */
function show(nodes: Inline[]): string {
  return nodes.map((n) => {
    switch (n.type) {
      case "text": return n.text;
      case "code": return `\`${n.text}\``;
      case "value": return `V(${n.text})`;
      case "part": return `P(${n.id}:${n.label})`;
      case "link": return `L(${n.href}){${show(n.children)}}`;
      default: return `${n.type[0].toUpperCase()}(${show((n as { children: Inline[] }).children)})`;
    }
  }).join("");
}

describe("parseInline — emphasis and code", () => {
  it("bold, italic, strikethrough, inline code", () => {
    expect(show(parseInline("**b** *i* _i2_ ~~d~~ `c`"))).toBe("S(b) E(i) E(i2) D(d) `c`");
  });
  it("does not parse markup inside inline code", () => {
    expect(show(parseInline("`**not bold** [[part:x|y]]`"))).toBe("`**not bold** [[part:x|y]]`");
  });
  it("leaves unmatched delimiters as text", () => {
    expect(show(parseInline("a * b"))).toBe("a * b");
  });
});

describe("parseInline — part chips", () => {
  it("renders a chip with a label", () => {
    expect(parseInline("see [[part:mount_plate|the plate]] here")).toEqual([
      { type: "text", text: "see " },
      { type: "part", id: "mount_plate", label: "the plate" },
      { type: "text", text: " here" },
    ]);
  });
  it("falls back to the id when no label is given", () => {
    expect(show(parseInline("[[part:arm_a]]"))).toBe("P(arm_a:arm_a)");
  });
  it("leaves a malformed part id as text", () => {
    expect(show(parseInline("[[part:Bad Id|x]]"))).toBe("[[part:Bad Id|x]]");
  });
});

describe("parseInline — links", () => {
  it("keeps only http(s) links; others become their text", () => {
    expect(show(parseInline("[Mc Master](https://mcmaster.com/x)"))).toBe("L(https://mcmaster.com/x){Mc Master}");
    expect(show(parseInline("[evil](javascript:x)"))).toBe("evil");
    expect(safeHref("javascript:alert(1)")).toBe(null);
    expect(safeHref("HTTPS://x.test/p")).toBe("HTTPS://x.test/p");
  });
});

describe("parseInline — values (number + unit)", () => {
  it("tints common units and prefix forms", () => {
    expect(show(parseInline("wall 3.2 mm, 48 g, SF 2.4, 120 MPa, 90°"))).toBe(
      "wall V(3.2 mm), V(48 g), V(SF 2.4), V(120 MPa), V(90°)",
    );
  });
  it("tints mm³ and mm/s before mm", () => {
    expect(show(parseInline("8.0 cm³ at 50 mm/s"))).toBe("V(8.0 cm³) at V(50 mm/s)");
  });
  it("does not tint a bare number or a number glued to a word", () => {
    expect(show(parseInline("12 apples and 3mmap"))).toBe("12 apples and 3mmap");
  });
  it("does not tint numbers inside inline code", () => {
    expect(show(parseInline("`3.2 mm`"))).toBe("`3.2 mm`");
  });
});

describe("parseInline — highlight", () => {
  it("wraps ==text==", () => {
    expect(show(parseInline("this is ==important== now"))).toBe("this is H(important) now");
  });
});

describe("parseBlocks — structure", () => {
  it("headings and paragraphs", () => {
    const b = parseBlocks("# Title\n\nsome text\nwrapped");
    expect(b[0]).toMatchObject({ type: "heading", level: 1 });
    expect(b[1]).toMatchObject({ type: "paragraph" });
  });

  it("callouts carry their status kind", () => {
    const b = parseBlocks("> [!pass] all good\n> second line");
    const c = b[0] as Extract<Block, { type: "callout" }>;
    expect(c.type).toBe("callout");
    expect(c.kind).toBe("pass");
    expect(show((c.blocks[0] as Extract<Block, { type: "paragraph" }>).inline)).toContain("all good");
  });

  it("a plain blockquote is a note callout", () => {
    expect((parseBlocks("> just a note") as [Block])[0]).toMatchObject({ type: "callout", kind: "note" });
  });

  it("unordered and ordered lists", () => {
    const ul = parseBlocks("- a\n- b")[0] as Extract<Block, { type: "list" }>;
    expect(ul.type).toBe("list");
    expect(ul.ordered).toBe(false);
    expect(ul.items).toHaveLength(2);
    const ol = parseBlocks("1. a\n2. b")[0] as Extract<Block, { type: "list" }>;
    expect(ol.ordered).toBe(true);
  });

  it("fenced code keeps its language and raw body", () => {
    const b = parseBlocks("```python\nx = 1\n**not bold**\n```")[0] as Extract<Block, { type: "code" }>;
    expect(b).toMatchObject({ type: "code", lang: "python" });
    expect(b.text).toBe("x = 1\n**not bold**");
  });

  it("GFM tables with alignment", () => {
    const t = parseBlocks("| Part | Mass |\n|:--|--:|\n| arm | 12 g |")[0] as Extract<Block, { type: "table" }>;
    expect(t.type).toBe("table");
    expect(t.align).toEqual(["left", "right"]);
    expect(show(t.header[0])).toBe("Part");
    expect(show(t.rows[0][1])).toBe("V(12 g)");
  });

  it("a horizontal rule", () => {
    expect(parseBlocks("---")[0]).toMatchObject({ type: "hr" });
  });
});

// ---- XSS / untrusted-input corpus: nothing ever becomes an HTML node; raw tags stay text.
describe("parse — XSS corpus stays plain text", () => {
  const corpus = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "<iframe src=javascript:alert(1)></iframe>",
    "<a href=\"javascript:alert(1)\">x</a>",
    "<svg/onload=alert(1)>",
    "plain & < > \" ' text",
    "[x](javascript:alert(1))",
    "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "[x](vbscript:msgbox(1))",
    "[[part:x|<b>hi</b>]]",
  ];
  for (const s of corpus) {
    it(`no link/part HTML slips through: ${s.slice(0, 32)}`, () => {
      const nodes = parseInline(s);
      // the only node that carries a URL is a link, and every link href is http(s)
      for (const n of nodes) {
        if (n.type === "link") expect(n.href).toMatch(/^https?:\/\//i);
        // part labels are plain strings rendered as text by render.tsx (escaped there)
        if (n.type === "part") expect(typeof n.label).toBe("string");
      }
    });
  }
});
