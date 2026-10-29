// Renders Mises markup (parse.ts) as Preact nodes. Every string goes through JSX text, so the
// browser escapes it: raw HTML in the source is shown verbatim, never parsed.
//   - Part chips select their part; their accessible name is "part: <label>".
//   - Links show their real domain next to the label, a label that looks like another domain is
//     replaced by the real one, and a click opens the system browser via openExternal.
//   - Values and ==highlights== get an achromatic tint (no status colour, no blue).
//   - A reply over ~12 lines collapses behind "Show more" (never one with a warn/fail callout).
//   - Past MAX_MARKUP_CHARS the rest is not rendered; "Copy full reply" copies all of it.
//   - An error boundary per message falls back to plain escaped text if rendering ever throws.
import { Component, type ComponentChildren, type JSX } from "preact";
import { useMemo, useState } from "preact/hooks";
import { debug } from "../../lib/log";
import { openExternal } from "../../platform/openExternal";
import { actions } from "../../state/store";
import { type Align, type Block, type CalloutKind, type Inline, linkHost, parseBlocks } from "./parse";
import "./markup.css";

/** Lines of source past which the message collapses behind "Show more". */
export const COLLAPSE_LINES = 12;
/** Characters of one message rendered as markup; the rest is offered as "Copy full reply". */
export const MAX_MARKUP_CHARS = 64 * 1024;

interface MarkupProps {
  source: string;
  /** Overrides the default part select (the thread uses the store; tests inject a spy). */
  onSelectPart?: (id: string) => void;
  collapsible?: boolean;
}

/** Mises markup for one message, inside an error boundary that falls back to plain text. */
export function Markup(props: MarkupProps): JSX.Element {
  return <MarkupBoundary source={props.source}><MarkupBody {...props} /></MarkupBoundary>;
}

class MarkupBoundary extends Component<{ source: string; children: ComponentChildren }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError(): { failed: boolean } { return { failed: true }; }
  componentDidCatch(e: unknown): void { debug("markup render failed; showing plain text", e); }
  componentDidUpdate(prev: { source: string }): void {
    if (prev.source !== this.props.source && this.state.failed) this.setState({ failed: false });
  }
  render(): ComponentChildren {
    if (this.state.failed) return <div class="mk mk-raw text" data-markup-fallback>{this.props.source}</div>;
    return this.props.children;
  }
}

/** Cut at the last line break before the cap (so markup isn't split mid-line when possible). */
function displayPart(src: string): { shown: string; cut: boolean } {
  if (src.length <= MAX_MARKUP_CHARS) return { shown: src, cut: false };
  const nl = src.lastIndexOf("\n", MAX_MARKUP_CHARS);
  return { shown: src.slice(0, nl > MAX_MARKUP_CHARS / 2 ? nl : MAX_MARKUP_CHARS), cut: true };
}

function hasAlert(blocks: Block[]): boolean {
  return blocks.some((b) => (b.type === "callout" && (b.kind === "fail" || b.kind === "warn" || hasAlert(b.blocks)))
    || (b.type === "list" && b.items.some(hasAlert)));
}

function MarkupBody({ source, onSelectPart, collapsible = true }: MarkupProps): JSX.Element {
  const { shown, cut } = useMemo(() => displayPart(source), [source]);
  const blocks = useMemo(() => parseBlocks(shown), [shown]);
  const select = onSelectPart ?? ((id: string) => actions.selectPart(id));
  // a reply carrying a warn/fail callout is never collapsed: the problem must stay in view
  const long = collapsible && countLines(shown) > COLLAPSE_LINES && !hasAlert(blocks);
  const [open, setOpen] = useState(false);

  return (
    <div class="mk" data-markup>
      <div class={`mk-body${long && !open ? " mk-clamp" : ""}`} data-markup-body>
        {blocks.map((b, i) => <BlockView key={i} block={b} select={select} />)}
      </div>
      {/* outside the clamped box, so the clamp can never hide its own control */}
      {long && (
        <button type="button" class="mk-more" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Show less" : "Show more"}
        </button>
      )}
      {cut && <Truncated source={source} />}
    </div>
  );
}

function Truncated({ source }: { source: string }): JSX.Element {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(source).then(() => setCopied(true), (e: unknown) => debug("copy failed", e));
  };
  return (
    <div class="mk-trunc faint" data-markup-truncated>
      <span class="text">{`Reply cut for display at ${Math.round(MAX_MARKUP_CHARS / 1024)} KB of ${Math.ceil(source.length / 1024)} KB.`}</span>
      <button type="button" class="mk-more" onClick={copy}>{copied ? "Copied" : "Copy full reply"}</button>
    </div>
  );
}

function countLines(src: string): number {
  let n = 1;
  for (let i = 0; i < src.length; i++) if (src.charCodeAt(i) === 10) n++;
  return n;
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

/** The plain text of inline nodes (for a link label check). */
export function plainText(nodes: Inline[]): string {
  return nodes.map((n) => ("children" in n ? plainText(n.children) : n.type === "part" ? n.label : n.text)).join("");
}

/** A label that itself reads as a domain or URL ("good.test", "https://good.test/x") whose host
 *  differs from where the link really goes: a spoof, so it is not shown. */
export function labelSpoofs(label: string, host: string): boolean {
  const m = /^\s*(?:https?:\/\/)?((?:[\p{L}\p{N}-]+\.)+[\p{L}]{2,})(?=[\s/:?#]|$)/iu.exec(label);
  if (!m) return false;
  const claimed = linkHost(`https://${m[1]}`).toLowerCase();
  return claimed !== host.toLowerCase();
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
          aria-label={`part: ${node.label}`} title={`Select part ${node.label}`} onClick={() => select(node.id)}>
          {node.label}
        </button>
      );
    case "link": {
      const spoof = labelSpoofs(plainText(node.children), node.host);
      return (
        <span class="mk-linkwrap">
          <a class="mk-link" href={node.href} rel="noopener noreferrer" title={node.href} data-link-host={node.host}
            onClick={(e) => { e.preventDefault(); void openExternal(node.href); }}>
            {spoof ? node.host : <InlineRun nodes={node.children} select={select} />}
          </a>
          {/* the real domain, always visible (not only on hover) */}
          <span class="mk-host" data-host><span aria-hidden="true">{" ↗ "}</span>{node.host}</span>
        </span>
      );
    }
  }
}
