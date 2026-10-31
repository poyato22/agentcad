// Plan 9 D29: "Delete Arm? Also removes 2 instances from the assembly." — asked once, under the
// row, before the part (and its instances) go. Enter or Delete confirms, Esc or a click outside
// cancels. The delete itself can be undone from the toast that follows (LeftPane).
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef } from "preact/hooks";

export interface ConfirmDeleteProps {
  names: readonly string[];
  /** Assembly instances that go with them. */
  instances: number;
  anchor: { left: number; top: number };
  onConfirm(): void;
  onClose(): void;
  /** Another question in the same box ("Remove from assembly"): its own words and button. */
  text?: { title: string; detail: string; action: string };
}

export function confirmText(names: readonly string[], instances: number): { title: string; detail: string } {
  const title = names.length === 1 ? `Delete ${names[0]}?` : `Delete ${names.length} parts?`;
  const detail = instances === 0 ? "Its files go to the trash; you can undo this."
    : `Also removes ${instances === 1 ? "1 instance" : `${instances} instances`} from the assembly.`;
  return { title, detail };
}

export function ConfirmDelete({ names, instances, anchor, onConfirm, onClose, text }: ConfirmDeleteProps): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const yes = useRef<HTMLButtonElement>(null);
  const { title, detail } = text ?? confirmText(names, instances);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.left = `${Math.max(6, Math.min(anchor.left, window.innerWidth - el.offsetWidth - 6))}px`;
    el.style.top = `${Math.max(6, Math.min(anchor.top, window.innerHeight - el.offsetHeight - 6))}px`;
    yes.current?.focus();
  }, [anchor.left, anchor.top]);

  useEffect(() => {
    const onDown = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [onClose]);

  return (
    <div ref={ref} class="pconfirm" role="alertdialog" aria-label={title} aria-describedby="pconfirm-detail" data-testid="confirm-delete"
      style={{ left: `${anchor.left}px`, top: `${anchor.top}px` }}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
        else if (e.key === "Backspace" || e.key === "Delete") { e.preventDefault(); e.stopPropagation(); onConfirm(); }
      }}>
      <p class="pconfirm-title">{title}</p>
      <p class="pconfirm-detail" id="pconfirm-detail">{detail}</p>
      <div class="pconfirm-actions">
        <button type="button" class="pconfirm-btn" onClick={onClose}>Cancel</button>
        <button ref={yes} type="button" class="pconfirm-btn pconfirm-yes" onClick={onConfirm}>{text?.action ?? "Delete"}</button>
      </div>
    </div>
  );
}
