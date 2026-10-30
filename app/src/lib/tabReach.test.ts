import { afterEach, describe, expect, it } from "vitest";
import { installTabReach, reachAll } from "./tabReach";

let stop: (() => void) | null = null;
afterEach(() => { stop?.(); stop = null; document.body.innerHTML = ""; });

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("tab reach (I7: WebKit skips controls without a tabindex)", () => {
  it("gives buttons, links, selects, checkboxes and sliders tabindex 0; text fields and roving items are left alone", () => {
    document.body.innerHTML = `
      <button id="b">b</button><a id="a" href="#x">a</a><a id="na">no href</a><select id="s"></select>
      <input id="c" type="checkbox"><input id="r" type="range"><input id="t" type="text">
      <button id="rov" tabindex="-1">roving</button><input id="f" type="file" hidden>`;
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
    const wrap = document.createElement("div");
    wrap.innerHTML = `<span><button id="late">late</button></span>`;
    document.body.append(wrap);
    await flush();
    expect(document.getElementById("late")!.getAttribute("tabindex")).toBe("0");
    // a component dropping its tabindex puts the control back in the order
    const rov = document.createElement("button");
    rov.setAttribute("tabindex", "-1");
    document.body.append(rov);
    await flush();
    expect(rov.getAttribute("tabindex")).toBe("-1");
    rov.removeAttribute("tabindex");
    await flush();
    expect(rov.getAttribute("tabindex")).toBe("0");
  });
});
