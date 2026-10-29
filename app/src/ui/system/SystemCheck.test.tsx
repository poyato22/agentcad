import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MisesEvent, SystemCheckRow, SystemInstallStart } from "../../api/types";
import { statusLabel, statusTone, SystemCheck } from "./SystemCheck";

const rows: SystemCheckRow[] = [
  { id: "python", name: "Core Python & venv", status: "ok", version: "3.13.1", path: "/opt/.venv", detail: "Ready.", fix: null },
  { id: "prusaslicer", name: "PrusaSlicer", status: "missing", version: null, path: null,
    detail: "Not found. Needed to slice.", fix: { kind: "install", label: "Install PrusaSlicer", dep: "prusaslicer" } },
];

function install(ev: Partial<{ line: string; done: boolean; ok: boolean }>, dep = "prusaslicer"): MisesEvent {
  return { type: "system_install", run_id: null, ts: 0, data: { dep, line: ev.line ?? "", done: ev.done ?? false, ok: ev.ok ?? false } };
}

function mountDeps(systemInstall: () => Promise<SystemInstallStart>) {
  let emit: (e: MisesEvent) => void = () => {};
  const client = { systemCheck: vi.fn().mockResolvedValue(rows), systemInstall: vi.fn(systemInstall) };
  const subscribe = (cb: (e: MisesEvent) => void) => { emit = cb; return () => {}; };
  return { client, subscribe, send: (e: MisesEvent) => emit(e) };
}

afterEach(() => cleanup());

describe("status mapping", () => {
  it("maps each status to a tone and label", () => {
    expect(statusTone("ok")).toBe("green");
    expect(statusTone("error")).toBe("red");
    expect(statusTone("optional_missing")).toBe("faint");
    expect(statusLabel("missing")).toBe("Missing");
    expect(statusLabel("outdated")).toBe("Outdated");
  });
});

describe("SystemCheck", () => {
  it("renders a row per dependency with its status and fix", async () => {
    const { client, subscribe } = mountDeps(async () => ({ ok: true, fix: null }));
    render(<SystemCheck client={client} subscribe={subscribe} />);
    expect(await screen.findByText("PrusaSlicer")).toBeTruthy();
    expect(screen.getByText("Core Python & venv")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Install PrusaSlicer" })).toBeTruthy();
  });

  it("re-checks when Re-check is pressed", async () => {
    const { client, subscribe } = mountDeps(async () => ({ ok: true, fix: null }));
    render(<SystemCheck client={client} subscribe={subscribe} />);
    await screen.findByText("PrusaSlicer");
    fireEvent.click(screen.getByRole("button", { name: "Re-check" }));
    await waitFor(() => expect(client.systemCheck).toHaveBeenCalledTimes(2));
  });

  it("installs a dep, streams output, then re-checks", async () => {
    const { client, subscribe, send } = mountDeps(async () => ({ ok: true, fix: null }));
    render(<SystemCheck client={client} subscribe={subscribe} />);
    await screen.findByText("PrusaSlicer");
    fireEvent.click(screen.getByRole("button", { name: "Install PrusaSlicer" }));
    await waitFor(() => expect(client.systemInstall).toHaveBeenCalledWith("prusaslicer"));

    act(() => { send(install({ line: "Downloading…" })); send(install({ line: "Installing…" })); });
    await waitFor(() => expect(screen.getByLabelText("prusaslicer install log").textContent).toContain("Downloading…"));

    act(() => send(install({ done: true, ok: true })));
    await waitFor(() => expect(client.systemCheck).toHaveBeenCalledTimes(2)); // re-check after finish
    expect(screen.getByText(/Installed/)).toBeTruthy();
  });

  it("shows a fix when the install is refused (no Homebrew)", async () => {
    const brew = { kind: "open_url" as const, label: "Install Homebrew", url: "https://brew.sh" };
    const { client, subscribe } = mountDeps(async () => ({ ok: false, fix: brew }));
    render(<SystemCheck client={client} subscribe={subscribe} />);
    await screen.findByText("PrusaSlicer");
    fireEvent.click(screen.getByRole("button", { name: "Install PrusaSlicer" }));
    const link = await screen.findByRole("link", { name: "Install Homebrew" });
    expect(link.getAttribute("href")).toBe("https://brew.sh");
    expect(screen.queryByLabelText("prusaslicer install log")).toBeNull();
  });

  it("standalone mode shows a Continue button", async () => {
    const onDone = vi.fn();
    const { client, subscribe } = mountDeps(async () => ({ ok: true, fix: null }));
    render(<SystemCheck standalone onDone={onDone} client={client} subscribe={subscribe} />);
    const cont = await screen.findByRole("button", { name: "Continue" });
    fireEvent.click(cont);
    expect(onDone).toHaveBeenCalledOnce();
  });
});
