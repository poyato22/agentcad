import type { JSX } from "preact";

export type ViewMode = "shaded" | "wire";

export interface ViewerToolsProps { mode: ViewMode; onMode(m: ViewMode): void }

/** Tool strip along the top of the sheet: shaded · wire, with section and measure shown disabled. */
export function ViewerTools({ mode, onMode }: ViewerToolsProps): JSX.Element {
  const toggle = (m: ViewMode, text: string) => (
    <button type="button" class="vt-btn" aria-pressed={mode === m} data-active={mode === m} onClick={() => onMode(m)}>{text}</button>
  );
  const later = (text: string) => (
    <button type="button" class="vt-btn" aria-disabled="true" title="later" onClick={(e) => e.preventDefault()}>{text}</button>
  );
  return (
    <div class="viewer-tools" role="toolbar" aria-label="View tools">
      {toggle("shaded", "shaded")}
      {toggle("wire", "wire")}
      {later("section")}
      {later("measure")}
    </div>
  );
}
