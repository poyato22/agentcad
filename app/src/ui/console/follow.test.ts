import { describe, expect, it, vi } from "vitest";
import { createFollow } from "./follow";

describe("follow", () => {
  it("follow_off_on_scroll_up_and_counts_new", () => {
    const set = vi.fn();
    const f = createFollow(set);
    expect(f.following).toBe(true);
    f.onAppend(3);                       // following: nothing pending
    expect(f.pendingNew).toBe(0);
    f.onScroll(false);                   // user scrolled up
    expect(f.following).toBe(false);
    expect(set).toHaveBeenLastCalledWith(false);
    f.onAppend(2);
    f.onAppend(5);
    f.onAppend(0);
    expect(f.pendingNew).toBe(7);
    f.onScroll(false);                   // more scrolling up does not re-call setFollow
    expect(set).toHaveBeenCalledTimes(1);
    f.onScroll(true);                    // scrolled back to the bottom by hand
    expect(f.following).toBe(true);
    expect(f.pendingNew).toBe(0);
    expect(set).toHaveBeenLastCalledWith(true);
  });

  it("follow_chip_reenables", () => {
    const set = vi.fn();
    const f = createFollow(set);
    f.onScroll(false);
    f.onAppend(4);
    f.resume();                          // chip or ⤓ clicked
    expect(f.following).toBe(true);
    expect(f.pendingNew).toBe(0);
    expect(set).toHaveBeenLastCalledWith(true);
    f.onAppend(4);
    expect(f.pendingNew).toBe(0);
  });

  it("ignores_negative_appends", () => {
    const f = createFollow(() => {});
    f.onScroll(false);
    f.onAppend(-3);
    expect(f.pendingNew).toBe(0);
  });
});
