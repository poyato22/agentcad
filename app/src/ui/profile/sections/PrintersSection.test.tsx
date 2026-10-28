import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../../api/client";
import type { Printer, Profile } from "../../../api/types";
import { bindClient, resetStore } from "../../../state/store";
import { ProfilePane } from "../ProfilePane";

const SECRET = "pl-key-SECRET-555";
const profile: Profile = {
  display_name: "Hartigan", units: "mm", default_material: "PLA", default_agent: "designer", default_model: null,
  onshape_calls_year_cap: 2500, onshape_calls_run_cap: 200, max_parallel_agents: 2, local_endpoints: {}, theme: "system", setup_completed_at: 1790000000,
};
const MK4: Printer = { id: "bench_mk4", kind: "prusalink", host: "192.168.1.20", name: "Bench MK4", model: "MK4", has_key: true };

const client = {
  getProfile: vi.fn(), listKeys: vi.fn(), claudeCodeStatus: vi.fn(), status: vi.fn(), models: vi.fn(), agents: vi.fn(),
  printers: vi.fn(), printerStatus: vi.fn(), printCapabilities: vi.fn(), addPrinter: vi.fn(), deletePrinter: vi.fn(),
};

beforeEach(() => {
  client.getProfile.mockReset().mockResolvedValue(profile);
  client.listKeys.mockReset().mockResolvedValue([]);
  client.claudeCodeStatus.mockReset().mockResolvedValue({ installed: true, version: "2.1.4", signed_in: true, auth_method: "claude.ai" });
  client.status.mockReset().mockResolvedValue(null);
  client.models.mockReset().mockResolvedValue(null);
  client.agents.mockReset().mockResolvedValue([]);
  client.printers.mockReset().mockResolvedValue([MK4]);
  client.printerStatus.mockReset().mockResolvedValue({ printer_id: "bench_mk4", reachable: true, state: "IDLE", sendable: true, temp_nozzle: 25, temp_bed: 24, job: null, error: null });
  client.printCapabilities.mockReset().mockRejectedValue(new Error("offline"));
  client.addPrinter.mockReset().mockImplementation(async (b: { name: string; host: string; model?: string }) => ({ id: "desk_mini", kind: "prusalink", host: b.host, name: b.name, model: b.model ?? "MK4", has_key: true }));
  client.deletePrinter.mockReset().mockResolvedValue(undefined);
  bindClient(client as unknown as CoreClient);
});
afterEach(() => { cleanup(); resetStore(); });

async function open(): Promise<HTMLElement> {
  render(<ProfilePane open onClose={() => {}} />);
  await screen.findByRole("dialog", { name: "Profile" });
  fireEvent.click(await screen.findByRole("button", { name: "Printers" }));
  return screen.findByRole("region", { name: "Printers" });
}

describe("Profile → Printers", () => {
  it("lists_printers_with_host_model_key_and_status", async () => {
    const sec = await open();
    const row = await within(sec).findByRole("row", { name: /Bench MK4/ });
    expect(row.textContent).toContain("192.168.1.20");
    expect(row.textContent).toContain("MK4");
    expect(row.textContent).toContain("●●●●");
    await waitFor(() => expect(row.textContent).toContain("idle"));
    expect(within(sec).getByText(/PrusaLink is plain HTTP/)).toBeTruthy();
    expect(within(sec).getByText(/Bambu.*coming later/)).toBeTruthy();
  });

  it("adds_a_printer_with_a_password_key_that_is_never_shown", async () => {
    const sec = await open();
    fireEvent.click(await within(sec).findByRole("button", { name: "Add printer" }));
    const form = within(sec).getByRole("form", { name: "Add a printer" });
    expect((within(form).getByLabelText("API key") as HTMLInputElement).type).toBe("password");
    fireEvent.input(within(form).getByLabelText("Name"), { target: { value: "Desk MINI" } });
    fireEvent.input(within(form).getByLabelText("Host"), { target: { value: "192.168.1.30" } });
    fireEvent.input(within(form).getByLabelText("API key"), { target: { value: SECRET } });
    fireEvent.click(within(form).getByRole("button", { name: "Add printer" }));
    await within(sec).findByRole("row", { name: /Desk MINI/ });
    expect(client.addPrinter).toHaveBeenCalledWith(expect.objectContaining({ api_key: SECRET }));
    expect(new XMLSerializer().serializeToString(document.body)).not.toContain(SECRET);
  });

  it("removing_asks_inside_the_panel_first", async () => {
    const sec = await open();
    const row = await within(sec).findByRole("row", { name: /Bench MK4/ });
    fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
    expect(client.deletePrinter).not.toHaveBeenCalled();
    fireEvent.click(within(row).getByRole("button", { name: "Yes, remove" }));
    await waitFor(() => expect(client.deletePrinter).toHaveBeenCalledWith("bench_mk4"));
    await waitFor(() => expect(within(sec).queryByRole("row", { name: /Bench MK4/ })).toBeNull());
  });
});
