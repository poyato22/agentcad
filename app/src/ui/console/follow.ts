// Follow (auto-scroll) state for the step log. Scrolling up turns follow off and new rows are
// counted for the "N new ↓" chip; scrolling back to the bottom, the chip or ⤓ turn it back on.
// The view is never moved while follow is off.

export interface FollowController {
  /** From the log's scroll handler: is the view at the bottom now? */
  onScroll(atBottom: boolean): void;
  /** n rows were appended (counted only while follow is off). */
  onAppend(n: number): void;
  /** The chip or ⤓ was clicked: follow again (the caller scrolls to the bottom). */
  resume(): void;
  readonly pendingNew: number;
  readonly following: boolean;
}

export function createFollow(setFollow: (on: boolean) => void): FollowController {
  let following = true;
  let pendingNew = 0;
  const set = (on: boolean) => {
    if (on) pendingNew = 0;
    if (following === on) return;
    following = on;
    setFollow(on);
  };
  return {
    onScroll(atBottom) { set(atBottom); },
    onAppend(n) { if (!following && n > 0) pendingNew += n; },
    resume() { set(true); },
    get pendingNew() { return pendingNew; },
    get following() { return following; },
  };
}
