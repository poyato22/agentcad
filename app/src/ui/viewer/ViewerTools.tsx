import type { JSX } from "preact";

export type ViewMode = "shaded" | "wire";
/** This part only, or every visible part of the build. */
export type ViewScope = "part" | "build";

export interface ViewerToolsProps {
  mode: ViewMode; onMode(m: ViewMode): void;
  scope?: ViewScope; onScope?(s: ViewScope): void;
  /** "Edit features": the ops form without a pick (choose the face / edge from a list). */
  canEdit?: boolean; onEdit?(): void;
}

/** Tool strip along the top of the sheet: part · build, shaded · wire, features, with section and
 *  measure shown disabled. */
export function ViewerTools({ mode, onMode, scope, onScope, canEdit, onEdit }: ViewerToolsProps): JSX.Element {
  const toggle = (m: ViewMode, text: string) => (
    <button type="button" class="vt-btn" aria-pressed={mode === m} data-active={mode === m} data-selected={mode === m ? "" : undefined} onClick={() => onMode(m)}>{text}</button>
  );
  const scopeBtn = (s: ViewScope, text: string, title: string) => (
    <button type="button" class="vt-btn" aria-pressed={scope === s} data-active={scope === s} data-selected={scope === s ? "" : undefined} title={title} onClick={() => onScope?.(s)}>{text}</button>
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
      {onEdit && (
        <>
          <span class="vt-gap" />
          <button type="button" class="vt-btn" disabled={!canEdit} title="Add a hole, shell, fillet or chamfer (or click the model where you want it)"
            onClick={() => onEdit()}>features</button>
        </>
      )}
      <span class="vt-gap" />
      {later("section")}
      {later("measure")}
    </div>
  );
}
