import { cleanup, fireEvent, render } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { COLLAPSE_LINES, Markup } from "./render";

afterEach(cleanup);

describe("Markup rendering", () => {
  it("renders callouts with their status kind and bold/value/highlight marks", () => {
    const { container } = render(<Markup source={"> [!fail] over by 3.2 mm\n\n**bold** and ==hi=="} collapsible={false} />);
    const fail = container.querySelector('[data-callout="fail"]');
    expect(fail).toBeTruthy();
    expect(fail?.getAttribute("role")).toBe("alert");
    expect(container.querySelector("[data-value]")?.textContent).toBe("3.2 mm");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("mark.mk-mark")?.textContent).toBe("hi");
  });

  it("a part chip selects its part on click", () => {
    const spy = vi.fn();
    const { container } = render(<Markup source="use [[part:mount_plate|the plate]]" onSelectPart={spy} collapsible={false} />);
    const chip = container.querySelector('[data-part-chip="mount_plate"]') as HTMLButtonElement;
    expect(chip.textContent).toBe("the plate");
    fireEvent.click(chip);
    expect(spy).toHaveBeenCalledWith("mount_plate");
  });

  it("renders a GFM table", () => {
    const { container } = render(<Markup source={"| A | B |\n|--|--:|\n| x | 12 g |"} collapsible={false} />);
    expect(container.querySelectorAll("table th")).toHaveLength(2);
    expect(container.querySelector("table td [data-value]")?.textContent).toBe("12 g");
  });

  it("http(s) links open externally with the URL as the title; nothing else is a link", () => {
    const { container } = render(<Markup source="[spec](https://example.test/x) and [bad](javascript:x)" collapsible={false} />);
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("https://example.test/x");
    expect(links[0].getAttribute("rel")).toBe("noopener noreferrer");
    expect(links[0].getAttribute("title")).toBe("https://example.test/x");
    expect(container.textContent).toContain("bad");
  });

  it("escapes raw HTML (no injected elements)", () => {
    const evil = "<script>alert(1)</script><img src=x onerror=alert(1)> [[part:ok|<b>x</b>]]";
    const { container } = render(<Markup source={evil} collapsible={false} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    // the chip label with angle brackets is shown as text, not as a <b> element
    const chip = container.querySelector('[data-part-chip="ok"]') as HTMLElement;
    expect(chip.querySelector("b")).toBeNull();
    expect(chip.textContent).toBe("<b>x</b>");
    expect(container.textContent).toContain("<script>alert(1)</script>");
  });

  it("collapses a long reply behind Show more", () => {
    const long = Array.from({ length: COLLAPSE_LINES + 5 }, (_, i) => `line ${i}`).join("\n");
    const { container, getByText } = render(<Markup source={long} />);
    expect(container.querySelector(".mk-clamp")).toBeTruthy();
    fireEvent.click(getByText("Show more"));
    expect(container.querySelector(".mk-clamp")).toBeNull();
    expect(getByText("Show less")).toBeTruthy();
  });

  it("a short reply is not collapsible", () => {
    const { container, queryByText } = render(<Markup source={"one line\ntwo line"} />);
    expect(container.querySelector(".mk-clamp")).toBeNull();
    expect(queryByText("Show more")).toBeNull();
  });
});
