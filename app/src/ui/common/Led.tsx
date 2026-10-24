import type { JSX } from "preact";

/**
 * Status tones. blue = being changed by an agent / needs the human; green/amber/red = pass/warn/fail;
 * faint = idle or off. No other colors.
 */
export type LedTone = "blue" | "green" | "amber" | "red" | "faint";

export function Led({ tone, title }: { tone: LedTone; title?: string }): JSX.Element {
  return <i class={`led led-${tone}`} data-tone={tone} title={title} aria-hidden={title ? undefined : true} />;
}
