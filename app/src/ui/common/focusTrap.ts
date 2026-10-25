// Focus trap for modal panels (setup wizard, Profile): focus moves inside when the panel opens,
// Tab / Shift+Tab cycle within it, and focus returns to where it was when the panel closes.
import type { RefObject } from "preact";
import { useEffect } from "preact/hooks";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => !el.closest("[inert]") && el.getAttribute("aria-hidden") !== "true");
}

/** Traps Tab inside `ref` while mounted. `initial` picks the element focused on open (else the first). */
export function useFocusTrap(ref: RefObject<HTMLElement>, initial?: () => HTMLElement | null): void {
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!root.contains(document.activeElement)) {
      const first = initial?.() ?? focusables(root)[0] ?? root;
      if (first === root && !root.hasAttribute("tabindex")) root.setAttribute("tabindex", "-1");
      first.focus();
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== "Tab") return;
      const list = focusables(root);
      const first = list[0];
      const last = list[list.length - 1];
      if (!first || !last) { e.preventDefault(); return; }
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !root.contains(active))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (active === last || !root.contains(active))) { e.preventDefault(); first.focus(); }
    };
    root.addEventListener("keydown", onKey);
    return () => {
      root.removeEventListener("keydown", onKey);
      if (before && before.isConnected) before.focus();
    };
  }, []);
}
