// The per-message error boundary: if parsing or rendering ever throws, the message falls back to
// escaped plain text and the rest of the thread keeps rendering.
import { cleanup, render } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Markup } from "./render";

vi.mock("./parse", async (orig) => {
  const real = await orig<typeof import("./parse")>();
  return { ...real, parseBlocks: (src: string) => { if (src.includes("BOOM")) throw new RangeError("Maximum call stack size exceeded"); return real.parseBlocks(src); } };
});

afterEach(cleanup);

describe("Markup error boundary", () => {
  it("falls back to plain escaped text for the failing message only", () => {
    const { container } = render(
      <div>
        <Markup source="**ok** before" />
        <Markup source="BOOM <b>raw</b> **x**" />
        <Markup source="**ok** after" />
      </div>,
    );
    const fallback = container.querySelector("[data-markup-fallback]") as HTMLElement;
    expect(fallback.textContent).toBe("BOOM <b>raw</b> **x**");
    expect(fallback.querySelector("b")).toBeNull();
    expect(container.querySelectorAll("strong")).toHaveLength(2);     // the neighbours still render
  });
});
