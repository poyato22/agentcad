import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { useRef, useState } from "preact/hooks";
import { afterEach, describe, expect, it } from "vitest";
import { useDismiss } from "./dismiss";
import { useToggleOpener } from "./toggle";

afterEach(cleanup);

/** An opener and a popover that closes on an outside press (no `also` list for the opener). */
function Harness(): preact.JSX.Element {
  const [open, setOpen] = useState(false);
  const opener = useToggleOpener(open, () => setOpen(true), () => setOpen(false));
  return (
    <div>
      <button type="button" {...opener}>Open it</button>
      {open && <Pop onClose={() => setOpen(false)} />}
    </div>
  );
}
function Pop({ onClose }: { onClose(): void }): preact.JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  useDismiss(ref, onClose);
  return <div ref={ref} data-pop>popover</div>;
}

describe("useToggleOpener (E7)", () => {
  it("the opener opens, and the same opener closes again", () => {
    const { container } = render(<Harness />);
    const btn = screen.getByRole("button", { name: "Open it" });
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    fireEvent.pointerDown(btn); fireEvent.click(btn);
    expect(container.querySelector("[data-pop]")).not.toBeNull();
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    // the press closes it (outside-press dismissal); the click must not reopen it
    fireEvent.pointerDown(btn); fireEvent.click(btn);
    expect(container.querySelector("[data-pop]")).toBeNull();
    expect(btn.getAttribute("aria-expanded")).toBe("false");
  });

  it("keyboard clicks (no press) toggle too", () => {
    const { container } = render(<Harness />);
    const btn = screen.getByRole("button", { name: "Open it" });
    fireEvent.click(btn);
    expect(container.querySelector("[data-pop]")).not.toBeNull();
    fireEvent.click(btn);
    expect(container.querySelector("[data-pop]")).toBeNull();
  });
});
