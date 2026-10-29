// Renders Mises markup (parse.ts) as Preact nodes. Every string goes through JSX text, so the
// browser escapes it: raw HTML in the source is shown verbatim, never parsed. Part chips select
// their part; http(s) links open in the system browser (the full URL is the hover title, so the
// domain is visible first); values and ==highlights== get an achromatic tint (no status colour,
// no blue — those are reserved). A reply over ~12 lines collapses behind "Show more".
import type { JSX } from "preact";
import { useMemo, useState } from "preact/hooks";
import { actions } from "../../state/store";
import { type Align, type Block, type CalloutKind, type Inline, parseBlocks } from "./parse";
import "./markup.css";

/** Lines of rendered markup past which the message collapses behind "Show more". */
export const COLLAPSE_LINES = 12;

export function Markup({ source, onSelectPart, collapsible = true }: {
  source: string;
  /** Overrides the default part select (the thread uses the store; tests inject a spy). */
  onSelectPart?: (id: string) => void;
  collapsible?: boolean;
}): JSX.Element {
  const blocks = useMemo(() => parseBlocks(source), [source]);
  const select = onSelectPart ?? ((id: string) => actions.selectPart(id));
  const long = collapsible && countLines(source) > COLLAPSE_LINES;
  const [open, setOpen] = useState(false);

  return (
    <div class={`mk${long && !open ? " mk-clamp" : ""}`} data-markup>
      {blocks.map((b, i) => <BlockView key={i} block={b} select={select} />)}
      {long && (
        <button type="button" class="mk-more" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

function countLines(src: string): number {
  return src.replace(/\r\n?/g, "\n").split("\n").length;
}

function BlockView({ block, select }: { block: Block; select: (id: string) => void }): JSX.Element {
  switch (block.type) {
    case "heading": {
      const Tag = `h${Math.min(block.level, 6)}` as keyof JSX.IntrinsicElements;
      return <Tag class="mk-h"><InlineRun nodes={block.inline} select={select} /></Tag>;
    }
    case "paragraph":
      return <p class="mk-p"><InlineRun nodes={block.inline} select={select} /></p>;
    case "callout":
      return (
        <div class="mk-callout" data-callout={block.kind} role={block.kind === "fail" ? "alert" : undefined}>
          <span class="mk-callout-flag" aria-hidden="true">{CALLOUT_FLAG[block.kind]}</span>
          <div class="mk-callout-body">
            {block.blocks.map((b, i) => <BlockView key={i} block={b} select={select} />)}
          </div>
        </div>
      );
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag class="mk-list">
          {block.items.map((item, i) => (
            <li key={i}>{item.map((b, j) => <BlockView key={j} block={b} select={select} />)}</li>
          ))}
        </Tag>
      );
    }
    case "code":
      return <pre class="mk-code"><code>{block.text}</code></pre>;
    case "table":
      return (
        <div class="mk-table-wrap">
          <table class="mk-table">
            <thead>
              <tr>{block.header.map((c, i) => <th key={i} style={alignStyle(block.align[i])}><InlineRun nodes={c} select={select} /></th>)}</tr>
            </thead>
            <tbody>
              {block.rows.map((row, i) => (
                <tr key={i}>{row.map((c, j) => <td key={j} style={alignStyle(block.align[j])}><InlineRun nodes={c} select={select} /></td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "hr":
      return <hr class="mk-hr" />;
  }
}

const CALLOUT_FLAG: Record<CalloutKind, string> = { pass: "✓", warn: "!", fail: "✕", note: "·" };

function alignStyle(a: Align): JSX.CSSProperties | undefined {
  return a ? { textAlign: a } : undefined;
}

function InlineRun({ nodes, select }: { nodes: Inline[]; select: (id: string) => void }): JSX.Element {
  return <>{nodes.map((n, i) => <InlineNode key={i} node={n} select={select} />)}</>;
}

function InlineNode({ node, select }: { node: Inline; select: (id: string) => void }): JSX.Element {
  switch (node.type) {
    case "text":
      return <>{node.text}</>;
    case "strong":
      return <strong><InlineRun nodes={node.children} select={select} /></strong>;
    case "em":
      return <em><InlineRun nodes={node.children} select={select} /></em>;
    case "del":
      return <del><InlineRun nodes={node.children} select={select} /></del>;
    case "highlight":
      return <mark class="mk-mark"><InlineRun nodes={node.children} select={select} /></mark>;
    case "code":
      return <code class="mk-icode">{node.text}</code>;
    case "value":
      return <span class="mk-val" data-value>{node.text}</span>;
    case "part":
      return (
        <button type="button" class="mk-chip" data-part-chip={node.id}
          title={`Select ${node.label}`} onClick={() => select(node.id)}>
          {node.label}
        </button>
      );
    case "link":
      // The full URL is the hover title, so the domain is visible before it opens externally.
      return (
        <a class="mk-link" href={node.href} target="_blank" rel="noopener noreferrer" title={node.href}>
          <InlineRun nodes={node.children} select={select} />
        </a>
      );
  }
}
