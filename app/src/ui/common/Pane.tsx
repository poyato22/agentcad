import type { ComponentChildren, JSX } from "preact";

export interface PaneProps {
  /** Pane title, shown upper-case in Iosevka at the heading weight. */
  title: string;
  /** Faint text or controls at the right end of the header. */
  right?: ComponentChildren;
  children?: ComponentChildren;
  class?: string;
}

/** A panel with the 24px header strip from the mockups. */
export function Pane({ title, right, children, class: cls }: PaneProps): JSX.Element {
  return (
    <section class={cls ? `pane ${cls}` : "pane"} aria-label={title}>
      <header class="pane-head">
        <span class="pane-title">{title}</span>
        {right !== undefined && <span class="pane-right">{right}</span>}
      </header>
      <div class="pane-body">{children}</div>
    </section>
  );
}

/** Placeholder content for an empty pane. */
export function PaneEmpty({ children }: { children: ComponentChildren }): JSX.Element {
  return <div class="pane-empty">{children}</div>;
}
