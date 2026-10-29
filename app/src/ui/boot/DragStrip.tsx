// A --chrome-h tall window-drag strip across the top of a full-window boot overlay, so the window
// stays draggable while it is up. It starts at --chrome-inset-x so the traffic lights stay clickable,
// and swallows clicks so dragging the intro doesn't also skip it.
import type { JSX } from "preact";

export function DragStrip(): JSX.Element {
  return <div class="boot-drag" data-tauri-drag-region aria-hidden="true" onClick={(e) => e.stopPropagation()} />;
}
