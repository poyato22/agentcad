import { useEffect, type MutableRef } from "preact/hooks";

/** Calls `onClose` on a pointer press outside `ref` (menus and popovers). */
export function useDismiss(ref: MutableRef<HTMLElement | null>, onClose: () => void, also: MutableRef<HTMLElement | null>[] = []): void {
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (!t || ref.current?.contains(t) || also.some((a) => a.current?.contains(t))) return;
      onClose();
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [onClose]);
}
