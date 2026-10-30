import { afterEach, describe, expect, it } from "vitest";
import { installTabReach, reachAll } from "./tabReach";

let stop: (() => void) | null = null;
afterEach(() => { stop?.(); stop = null; document.body.replaceChildren(); });

const flush = () => new Promise((r) => setTimeout(r, 0));

/** An element with attributes, appended to `parent`. */
function el(parent: Element, tag: string, attrs: Record<string, string> = {}): HTMLElement {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  parent.append(e);
  return e;
}

describe("tab reach (I7: WebKit skips controls without a tabindex)", () => {
  it("gives buttons, links, selects, checkboxes and sliders tabindex 0; text fields and roving items are left alone", () => {
    const b = document.body;
    el(b, "button", { id: "b" }); el(b, "a", { id: "a", href: "#x" }); el(b, "a", { id: "na" }); el(b, "select", { id: "s" });
    el(b, "input", { id: "c", type: "checkbox" }); el(b, "input", { id: "r", type: "range" }); el(b, "input", { id: "t", type: "text" });
    el(b, "button", { id: "rov", tabindex: "-1" }); el(b, "input", { id: "f", type: "file", hidden: "" });
    reachAll(document.body);
    const ti = (id: string) => document.getElementById(id)!.getAttribute("tabindex");
    expect(["b", "a", "s", "c", "r"].map(ti)).toEqual(["0", "0", "0", "0", "0"]);
    expect(ti("na")).toBeNull();
    expect(ti("t")).toBeNull();          // text fields are already in WebKit's order
    expect(ti("rov")).toBe("-1");        // a component's own order wins
    expect(ti("f")).toBeNull();          // hidden file pickers stay out
  });

  it("controls rendered later are reached too", async () => {
    stop = installTabReach(document.body);
    const wrap = el(document.body, "div");
    const late = el(el(wrap, "span"), "button");
    await flush();
    expect(late.getAttribute("tabindex")).toBe("0");
    // a component dropping its tabindex puts the control back in the order
    const rov = el(document.body, "button", { tabindex: "-1" });
    await flush();
    expect(rov.getAttribute("tabindex")).toBe("-1");
    rov.removeAttribute("tabindex");
    await flush();
    expect(rov.getAttribute("tabindex")).toBe("0");
  });
});
