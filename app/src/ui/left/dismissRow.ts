import { useEffect, type MutableRef } from "preact/hooks";

/** A row popover (color picker, Move menu) closes on a press outside its tree row — a press on
 *  the row's own toggle buttons is theirs to handle. */
export function useDismissRow(ref: MutableRef<HTMLElement | null>, onClose: () => void): void {
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const row = ref.current?.closest("li");
      const t = e.target as Node | null;
      if (!t || !row || row.contains(t)) return;
      onClose();
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [onClose]);
}
