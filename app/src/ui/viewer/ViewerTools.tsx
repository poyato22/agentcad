import type { JSX } from "preact";
import type { ViewerTool } from "./tool";

export type ViewMode = "shaded" | "wire";
/** This part only, or every visible part of the build. */
export type ViewScope = "part" | "build";

export interface ViewerToolsProps {
  mode: ViewMode; onMode(m: ViewMode): void;
  scope?: ViewScope; onScope?(s: ViewScope): void;
  /** The click tool: Measure (M) or Comment, pressed again for Select. */
  tool?: ViewerTool; onTool?(t: Exclude<ViewerTool, "select">): void;
  /** A part is on the sheet to measure or comment on. */
  canPick?: boolean;
}

/** Tool strip along the top of the sheet: part · build, shaded · wire, measure · comment, with
 *  section shown disabled. Feature ops are "Modify…" (the part's menu or the selection label). */
export function ViewerTools({ mode, onMode, scope, onScope, tool, onTool, canPick }: ViewerToolsProps): JSX.Element {
  const toggle = (m: ViewMode, text: string) => (
    <button type="button" class="vt-btn" aria-pressed={mode === m} data-active={mode === m} data-selected={mode === m ? "" : undefined} onClick={() => onMode(m)}>{text}</button>
  );
  const scopeBtn = (s: ViewScope, text: string, title: string) => (
    <button type="button" class="vt-btn" aria-pressed={scope === s} data-active={scope === s} data-selected={scope === s ? "" : undefined} title={title} onClick={() => onScope?.(s)}>{text}</button>
  );
  const toolBtn = (t: Exclude<ViewerTool, "select">, text: string, title: string, keys?: string) => (
    <button type="button" class="vt-btn" aria-pressed={tool === t} data-active={tool === t} data-selected={tool === t ? "" : undefined}
      disabled={!canPick} title={title} aria-keyshortcuts={keys} onClick={() => onTool?.(t)}>{text}</button>
  );
  const later = (text: string) => (
    <button type="button" class="vt-btn" aria-disabled="true" title="Not available yet" onClick={(e) => e.preventDefault()}>{text}</button>
  );
  return (
    <div class="viewer-tools" role="toolbar" aria-label="View tools">
      {scope !== undefined && (
        <>
          {scopeBtn("part", "part", "Only the selected part")}
          {scopeBtn("build", "build", "Every visible part of this build")}
          <span class="vt-gap" />
        </>
      )}
      {toggle("shaded", "shaded")}
      {toggle("wire", "wire")}
      {onTool && (
        <>
          <span class="vt-gap" />
          {toolBtn("measure", "measure", "Measure: click an edge, a face or a corner; Shift-click a second one (M)", "M")}
          {toolBtn("comment", "comment", "Comment: click the model where something should change, and tell the agent")}
        </>
      )}
      <span class="vt-gap" />
      {later("section")}
    </div>
  );
}
