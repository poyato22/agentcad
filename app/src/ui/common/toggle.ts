// Plan 10 E7: the button that opened something closes it again. Every opener in the app (the Home
// Settings menu and Agents, the top bar's activity pill and Profile, menus, popovers) is a toggle.
//
// The catch with popovers is that their outside-press dismissal (common/dismiss) runs on the
// opener's own pointerdown, closing the popover before the opener's click, which would then open
// it again. useToggleOpener remembers whether the thing was open when the press began, so that
// press closes it and the click does not reopen it.
import { useRef } from "preact/hooks";

/** Flip an open/closed thing: close it when open, open it when closed. */
export function toggleOpen(isOpen: boolean, open: () => void, close: () => void): void {
  if (isOpen) close(); else open();
}

export interface OpenerProps {
  "aria-expanded": boolean;
  onPointerDown: () => void;
  onClick: () => void;
}

/** Props for an opener button: a click opens the thing when closed and closes it when open, also
 *  when an outside-press dismissal already closed it during the same press. */
export function useToggleOpener(isOpen: boolean, open: () => void, close: () => void): OpenerProps {
  // set by a pointer press, read (once) by the click that follows; keyboard clicks have no press
  const atPress = useRef<boolean | null>(null);
  return {
    "aria-expanded": isOpen,
    onPointerDown: () => { atPress.current = isOpen; },
    onClick: () => {
      const was = atPress.current ?? isOpen;
      atPress.current = null;
      toggleOpen(was, open, close);
    },
  };
}
