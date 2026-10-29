import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExternalLink } from "../ui/common/ExternalLink";
import { externalUrl, openExternal } from "./openExternal";

const invokeMock = vi.hoisted(() => vi.fn(async () => null));
const tauriMock = vi.hoisted(() => ({ on: false }));
vi.mock("@tauri-apps/api/core", async (orig) => ({
  ...(await orig<typeof import("@tauri-apps/api/core")>()),
  isTauri: () => tauriMock.on,
  invoke: invokeMock,
}));

afterEach(() => { cleanup(); invokeMock.mockClear(); tauriMock.on = false; vi.restoreAllMocks(); });

describe("openExternal", () => {
  it("accepts http(s) only, without credentials", () => {
    expect(externalUrl("https://cad.onshape.com/documents/x")).toBe("https://cad.onshape.com/documents/x");
    expect(externalUrl("http://brew.sh")).toBe("http://brew.sh/");
    for (const bad of ["javascript:alert(1)", "file:///etc/passwd", "mises://x", "https://u:p@evil.example.com", "", "not a url"]) {
      expect(externalUrl(bad)).toBeNull();
    }
  });

  it("in the Tauri app: the opener plugin's open_url", async () => {
    const w = vi.fn();
    expect(await openExternal("https://brew.sh", { tauri: () => true, windowOpen: w })).toBe(true);
    expect(invokeMock).toHaveBeenCalledWith("plugin:opener|open_url", { url: "https://brew.sh/" });
    expect(w).not.toHaveBeenCalled();
  });

  it("in a browser: window.open with noopener; refused URLs open nothing", async () => {
    const w = vi.fn();
    expect(await openExternal("https://brew.sh", { tauri: () => false, windowOpen: w })).toBe(true);
    expect(w).toHaveBeenCalledWith("https://brew.sh/");
    expect(await openExternal("javascript:alert(1)", { tauri: () => true, windowOpen: w })).toBe(false);
    expect(invokeMock).not.toHaveBeenCalled();
    expect(w).toHaveBeenCalledTimes(1);
  });

  it("ExternalLink: a click goes through the opener, never the webview's own navigation", () => {
    tauriMock.on = true;
    render(<ExternalLink href="https://www.getfpv.com/learn">Open online (www.getfpv.com)</ExternalLink>);
    const a = screen.getByRole("link", { name: "Open online (www.getfpv.com)" });
    const ev = new MouseEvent("click", { bubbles: true, cancelable: true });
    a.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    expect(invokeMock).toHaveBeenCalledWith("plugin:opener|open_url", { url: "https://www.getfpv.com/learn" });
    fireEvent.click(a);
  });

  it("ExternalLink: a non-http URL renders as text, not a link", () => {
    render(<ExternalLink href="javascript:alert(1)">bad</ExternalLink>);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("bad").tagName).toBe("SPAN");
  });
});
