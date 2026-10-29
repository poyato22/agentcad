import { cleanup, fireEvent, render } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { COLLAPSE_LINES, labelSpoofs, Markup, MAX_MARKUP_CHARS } from "./render";

const opened: string[] = [];
vi.mock("../../platform/openExternal", () => ({ openExternal: async (url: string) => { opened.push(url); return true; } }));

afterEach(() => { cleanup(); opened.length = 0; });

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

  it("a part chip selects its part on click and is announced as a part", () => {
    const spy = vi.fn();
    const { container } = render(<Markup source="use [[part:approve_it|Approve]]" onSelectPart={spy} collapsible={false} />);
    const chip = container.querySelector('[data-part-chip="approve_it"]') as HTMLButtonElement;
    expect(chip.textContent).toBe("Approve");
    expect(chip.getAttribute("aria-label")).toBe("part: Approve");   // never reads as a bare "Approve" button
    fireEvent.click(chip);
    expect(spy).toHaveBeenCalledWith("approve_it");
  });

  it("renders a GFM table", () => {
    const { container } = render(<Markup source={"| A | B |\n|--|--:|\n| x | 12 g |"} collapsible={false} />);
    expect(container.querySelectorAll("table th")).toHaveLength(2);
    expect(container.querySelector("table td [data-value]")?.textContent).toBe("12 g");
  });

  it("an http(s) link shows its real domain and opens via openExternal; nothing else is a link", () => {
    const { container } = render(<Markup source="[spec](https://example.test/x) and [bad](javascript:x)" collapsible={false} />);
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("https://example.test/x");
    expect(links[0].getAttribute("rel")).toBe("noopener noreferrer");
    expect(links[0].textContent).toBe("spec");
    expect(container.querySelector("[data-host]")?.textContent).toBe(" ↗ example.test");
    fireEvent.click(links[0]);
    expect(opened).toEqual(["https://example.test/x"]);
    expect(container.textContent).toContain("bad");
  });

  it("a label that names another domain is replaced by the real one", () => {
    const { container } = render(<Markup source="[https://good.test/login](https://evil.test/x)" collapsible={false} />);
    const a = container.querySelector("a") as HTMLAnchorElement;
    expect(a.textContent).toBe("evil.test");
    expect(container.textContent).not.toContain("good.test");
    expect(labelSpoofs("good.test", "evil.test")).toBe(true);
    expect(labelSpoofs("www.example.test/spec", "www.example.test")).toBe(false);
    expect(labelSpoofs("the spec sheet", "evil.test")).toBe(false);
  });

  it("escapes raw HTML (no injected elements)", () => {
    const evil = "<script>alert(1)</script><img src=x onerror=alert(1)> [[part:ok|<b>x</b>]]";
    const { container } = render(<Markup source={evil} collapsible={false} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    const chip = container.querySelector('[data-part-chip="ok"]') as HTMLElement;
    expect(chip.querySelector("b")).toBeNull();
    expect(chip.textContent).toBe("<b>x</b>");
    expect(container.textContent).toContain("<script>alert(1)</script>");
  });

  it("collapses a long reply; Show more sits outside the clamped box", () => {
    const long = Array.from({ length: COLLAPSE_LINES + 5 }, (_, i) => `line ${i}`).join("\n\n");
    const { container, getByText } = render(<Markup source={long} />);
    const clamp = container.querySelector(".mk-clamp") as HTMLElement;
    expect(clamp).toBeTruthy();
    const more = getByText("Show more");
    expect(clamp.contains(more)).toBe(false);
    fireEvent.click(more);
    expect(container.querySelector(".mk-clamp")).toBeNull();
    expect(getByText("Show less")).toBeTruthy();
  });

  it("never collapses a reply that carries a warn or fail callout", () => {
    const long = `${Array.from({ length: COLLAPSE_LINES + 5 }, (_, i) => `line ${i}`).join("\n\n")}\n\n> [!fail] over the mass limit`;
    const { container, queryByText } = render(<Markup source={long} />);
    expect(container.querySelector(".mk-clamp")).toBeNull();
    expect(queryByText("Show more")).toBeNull();
  });

  it("a short reply is not collapsible", () => {
    const { container, queryByText } = render(<Markup source={"one line\ntwo line"} />);
    expect(container.querySelector(".mk-clamp")).toBeNull();
    expect(queryByText("Show more")).toBeNull();
  });

  it("past the size cap: the rest isn't rendered, and the full reply can be copied", () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: write } });
    const big = `${"word ".repeat(MAX_MARKUP_CHARS / 5)}\nTAIL-MARKER`;
    const { container, getByText } = render(<Markup source={big} collapsible={false} />);
    expect(container.textContent).not.toContain("TAIL-MARKER");
    expect(container.querySelector("[data-markup-truncated]")?.textContent).toContain("Reply cut for display");
    fireEvent.click(getByText("Copy full reply"));
    expect(write).toHaveBeenCalledWith(big);
  });
});
