import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { useRef } from "preact/hooks";
import { afterEach, describe, expect, it } from "vitest";
import { useFocusTrap } from "./focusTrap";

function Panel() {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref);
  return (
    <div ref={ref} role="dialog" aria-label="Panel">
      <button type="button">first</button>
      <button type="button" disabled>off</button>
      <input aria-label="middle" />
      <button type="button">last</button>
    </div>
  );
}

afterEach(cleanup);

describe("useFocusTrap", () => {
  it("focuses_the_first_control_on_open_and_cycles_tab_inside", () => {
    render(<Panel />);
    const first = screen.getByRole("button", { name: "first" });
    const last = screen.getByRole("button", { name: "last" });
    expect(document.activeElement).toBe(first);
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  it("returns_focus_to_where_it_was_on_close", () => {
    const outside = document.createElement("button");
    outside.textContent = "outside";
    document.body.appendChild(outside);
    outside.focus();
    const { unmount } = render(<Panel />);
    expect(document.activeElement).not.toBe(outside);
    unmount();
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });
});
