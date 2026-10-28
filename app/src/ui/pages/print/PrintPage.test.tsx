import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../../api/client";
import type { PartSummary, PrintCapabilities, Printer, PrinterStatus, Slice } from "../../../api/types";
import { bindClient, parts, resetStore, selectedRunId } from "../../../state/store";
import { consoleCollapsed, setConsoleCollapsed } from "../router";
import { PrintPage } from "./index";
import { POLL, resetPrintSession } from "./usePrint";

const FX = resolve(import.meta.dirname, "../../../../test/fixtures/core-ui");
const fx = <T,>(n: string): T => JSON.parse(readFileSync(resolve(FX, n), "utf8")) as T;
const CAPS = fx<PrintCapabilities>("print_capabilities.json");
const PRINTER: Printer = { ...fx<Printer>("printer_add.json"), host: "192.168.1.20" };
const IDLE = fx<PrinterStatus>("printer_status.json");
const DONE = { ...fx<Slice>("slice.json"), slice_id: "slice_0123456789" };
const SECRET = "pl-key-SECRET-98765";

const part = (id: string): PartSummary => ({ part_id: id, vars: {}, metrics: null, mtime: 1790000000 });

const client = {
  printCapabilities: vi.fn(), printers: vi.fn(), printerStatus: vi.fn(), getProfile: vi.fn(), addPrinter: vi.fn(), deletePrinter: vi.fn(),
  startSlice: vi.fn(), getSlice: vi.fn(), cancelSlice: vi.fn(), slicePreview: vi.fn(), startPrint: vi.fn(), getPrint: vi.fn(),
};
let sliceReads: number;

beforeEach(() => {
  Object.assign(POLL, { statusMs: 20, sliceMs: 10, printMs: 10 });
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:http://localhost/${++n}`);
  URL.revokeObjectURL = vi.fn();
  sliceReads = 0;
  client.printCapabilities.mockReset().mockResolvedValue(CAPS);
  client.printers.mockReset().mockResolvedValue([PRINTER]);
  client.printerStatus.mockReset().mockResolvedValue(IDLE);
  client.getProfile.mockReset().mockResolvedValue({ default_material: "PLA" });
  client.addPrinter.mockReset().mockImplementation(async (b: { name: string; host: string; model?: string }) => ({ id: "desk_mini", kind: "prusalink", host: b.host, name: b.name, model: b.model ?? "MK4", has_key: true }));
  client.deletePrinter.mockReset().mockResolvedValue(undefined);
  client.startSlice.mockReset().mockResolvedValue({ slice_id: DONE.slice_id, status: "running", run_id: "uirun000001", call_id: "ui_slice" });
  client.getSlice.mockReset().mockImplementation(async () => (++sliceReads < 2 ? { ...DONE, status: "running", estimate: null, preview: false } : DONE));
  client.cancelSlice.mockReset().mockResolvedValue(undefined);
  client.slicePreview.mockReset().mockResolvedValue(new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }));
  client.startPrint.mockReset().mockResolvedValue({ call_id: "ui_print", run_id: "uirun000001", status: "running" });
  client.getPrint.mockReset().mockResolvedValue({ call_id: "ui_print", run_id: "uirun000001", tool: "print_send", status: "running", output: null, error: null });
  bindClient(client as unknown as CoreClient);
  parts.value = [part("mount_plate"), part("hinge_arm")];
  setConsoleCollapsed(true);
});
afterEach(() => { cleanup(); resetStore(); resetPrintSession(); setConsoleCollapsed(false); });

const sliceBtn = () => screen.getByRole("button", { name: "Slice" });
const sendBtn = () => screen.getByRole("button", { name: "Send to printer" });

async function sliced(): Promise<void> {
  render(<PrintPage />);
  await screen.findByText(/idle/);
  await waitFor(() => expect((sliceBtn() as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(sliceBtn());
  await screen.findByText("1d 2h 3m 22s");
}

describe("PrintPage: printers", () => {
  it("no_printer_shows_the_add_form_with_a_password_key_the_lan_note_and_bambu_later", async () => {
    client.printers.mockResolvedValue([]);
    render(<PrintPage />);
    const form = await screen.findByRole("form", { name: "Add a printer" });
    const key = within(form).getByLabelText("API key") as HTMLInputElement;
    expect(key.type).toBe("password");
    expect(key.autocomplete).toBe("off");
    expect(screen.getByText(/PrusaLink is plain HTTP/)).toBeTruthy();
    const kind = within(form).getByRole("combobox", { name: "Kind" }) as HTMLSelectElement;
    const bambu = [...kind.options].find((o) => o.value === "bambu") as HTMLOptionElement;
    expect(bambu.disabled).toBe(true);
    expect(bambu.textContent).toContain("coming later");
    expect((sliceBtn() as HTMLButtonElement).disabled).toBe(true);
  });

  it("adding_a_printer_sends_the_key_once_clears_it_and_never_shows_it", async () => {
    client.printers.mockResolvedValueOnce([]).mockResolvedValue([PRINTER]);
    render(<PrintPage />);
    const form = await screen.findByRole("form", { name: "Add a printer" });
    fireEvent.input(within(form).getByLabelText("Name"), { target: { value: "Desk MINI" } });
    fireEvent.input(within(form).getByLabelText("Host"), { target: { value: "192.168.1.30" } });
    fireEvent.change(within(form).getByRole("combobox", { name: "Model" }), { target: { value: "MINI" } });
    fireEvent.input(within(form).getByLabelText("API key"), { target: { value: SECRET } });
    fireEvent.click(within(form).getByRole("button", { name: "Add printer" }));
    await waitFor(() => expect(client.addPrinter).toHaveBeenCalledWith({ name: "Desk MINI", host: "192.168.1.30", api_key: SECRET, model: "MINI", kind: "prusalink" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "Add a printer" })).toBeNull());
    expect(new XMLSerializer().serializeToString(document.body)).not.toContain(SECRET);
  });

  it("a_bad_host_is_refused_before_sending", async () => {
    client.printers.mockResolvedValue([]);
    render(<PrintPage />);
    const form = await screen.findByRole("form", { name: "Add a printer" });
    fireEvent.input(within(form).getByLabelText("Name"), { target: { value: "x" } });
    fireEvent.input(within(form).getByLabelText("Host"), { target: { value: "http://192.168.1.30/" } });
    fireEvent.input(within(form).getByLabelText("API key"), { target: { value: SECRET } });
    expect((within(form).getByRole("button", { name: "Add printer" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(form).getByText(/no http:\/\//)).toBeTruthy();
  });

  it("a_failed_add_clears_the_key_and_shows_the_core_message", async () => {
    client.printers.mockResolvedValue([]);
    client.addPrinter.mockRejectedValue(new CoreError("invalid_input", "a printer with id 'desk_mini' already exists", { status: 400 }));
    render(<PrintPage />);
    const form = await screen.findByRole("form", { name: "Add a printer" });
    fireEvent.input(within(form).getByLabelText("Name"), { target: { value: "Desk MINI" } });
    fireEvent.input(within(form).getByLabelText("Host"), { target: { value: "192.168.1.30" } });
    fireEvent.input(within(form).getByLabelText("API key"), { target: { value: SECRET } });
    fireEvent.click(within(form).getByRole("button", { name: "Add printer" }));
    expect((await within(form).findByRole("alert")).textContent).toContain("already exists");
    expect((within(form).getByLabelText("API key") as HTMLInputElement).value).toBe("");
  });

  it("status_light_follows_the_printer", async () => {
    client.printerStatus.mockResolvedValueOnce(IDLE).mockResolvedValue({ ...IDLE, state: "ERROR", sendable: false });
    render(<PrintPage />);
    const light = await screen.findByRole("status", { name: "Printer status" });
    await waitFor(() => expect(light.textContent).toContain("idle"));
    expect(light.querySelector("[data-tone]")?.getAttribute("data-tone")).toBe("green");
    await waitFor(() => expect(light.textContent).toContain("error"));
    expect(light.querySelector("[data-tone]")?.getAttribute("data-tone")).toBe("red");
  });
});

describe("PrintPage: slicing", () => {
  it("slice_shows_preview_time_grams_cost_and_material", async () => {
    await sliced();
    expect(client.startSlice).toHaveBeenCalledWith({ part_id: "mount_plate", printer_id: "bench_mk4", material: "PLA" });
    const result = screen.getByRole("region", { name: "Slice result" });
    expect(result.textContent).toContain("7.51 g");
    expect(result.textContent).toContain("$0.17");
    expect(result.textContent).toContain("PLA");
    expect(result.textContent).toContain("MK4");
    const img = await within(result).findByRole("img", { name: /Preview/ });
    expect(img.getAttribute("src")).toMatch(/^blob:/);
  });

  it("the_slice_survives_leaving_and_coming_back_to_the_page", async () => {
    await sliced();
    cleanup();
    render(<PrintPage />);
    expect((await screen.findByRole("region", { name: "Slice result" })).textContent).toContain("1d 2h 3m 22s");
    expect(client.startSlice).toHaveBeenCalledTimes(1);
  });

  it("material_choice_goes_to_the_slicer", async () => {
    render(<PrintPage />);
    await screen.findByText(/idle/);
    fireEvent.change(screen.getByRole("combobox", { name: "Material" }), { target: { value: "PETG" } });
    fireEvent.click(sliceBtn());
    await waitFor(() => expect(client.startSlice).toHaveBeenCalledWith({ part_id: "mount_plate", printer_id: "bench_mk4", material: "PETG" }));
  });

  it("slicer_missing_shows_the_install_card_and_no_slice", async () => {
    client.printCapabilities.mockResolvedValue({ ...CAPS, slicer: { installed: false, path: null, install_command: "brew install --cask prusaslicer" } });
    render(<PrintPage />);
    const card = await screen.findByRole("region", { name: "Install PrusaSlicer" });
    expect(card.textContent).toContain("brew install --cask prusaslicer");
    expect((sliceBtn() as HTMLButtonElement).disabled).toBe(true);
    client.printCapabilities.mockResolvedValue(CAPS);
    fireEvent.click(within(card).getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Install PrusaSlicer" })).toBeNull());
  });

  it("a_424_from_the_slicer_also_shows_the_install_card", async () => {
    client.startSlice.mockRejectedValue(new CoreError("tool_failed", "PrusaSlicer is not installed", { status: 424, hint: "install it with: brew install --cask prusaslicer" }));
    render(<PrintPage />);
    await screen.findByText(/idle/);
    fireEvent.click(sliceBtn());
    expect((await screen.findByRole("region", { name: "Install PrusaSlicer" })).textContent).toContain("brew install --cask prusaslicer");
  });

  it("a_running_slice_can_be_cancelled", async () => {
    client.getSlice.mockResolvedValue({ ...DONE, status: "running", estimate: null, preview: false });
    render(<PrintPage />);
    await screen.findByText(/idle/);
    fireEvent.click(sliceBtn());
    fireEvent.click(await screen.findByRole("button", { name: "Cancel slice" }));
    await waitFor(() => expect(client.cancelSlice).toHaveBeenCalledWith(DONE.slice_id));
  });

  it("a_failed_slice_shows_its_error", async () => {
    client.getSlice.mockResolvedValue({ ...DONE, status: "failed", estimate: null, preview: false, error: "slicer exited 1", hint: "check the part" });
    render(<PrintPage />);
    await screen.findByText(/idle/);
    fireEvent.click(sliceBtn());
    expect((await screen.findByRole("alert")).textContent).toContain("slicer exited 1");
    expect((sendBtn() as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("PrintPage: sending (irreversible)", () => {
  it("send_is_disabled_before_a_slice", async () => {
    render(<PrintPage />);
    await screen.findByText(/idle/);
    expect((sendBtn() as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Slice first/)).toBeTruthy();
  });

  it("never_sends_to_a_printer_that_reports_an_error", async () => {
    await sliced();
    client.printerStatus.mockResolvedValue({ ...IDLE, state: "ERROR", sendable: false });
    await waitFor(() => expect((sendBtn() as HTMLButtonElement).disabled).toBe(true));
    expect(screen.getByText(/reports ERROR/)).toBeTruthy();
    fireEvent.click(sendBtn());
    expect(client.startPrint).not.toHaveBeenCalled();
  });

  it("changing_the_part_after_slicing_requires_a_new_slice", async () => {
    await sliced();
    fireEvent.change(screen.getByRole("combobox", { name: "Part" }), { target: { value: "hinge_arm" } });
    await waitFor(() => expect((sendBtn() as HTMLButtonElement).disabled).toBe(true));
    expect(screen.getByText(/slice again/)).toBeTruthy();
  });

  it("send_raises_the_card_in_the_console_and_polls_until_decided", async () => {
    await sliced();
    await waitFor(() => expect((sendBtn() as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(sendBtn());
    await waitFor(() => expect(client.startPrint).toHaveBeenCalledWith(DONE.slice_id, "bench_mk4"));
    expect((await screen.findByRole("status", { name: "Print request" })).textContent).toMatch(/approve or reject the card in the console/i);
    expect(consoleCollapsed.value).toBe(false);
    expect(selectedRunId.value).toBe("uirun000001");
    expect((sendBtn() as HTMLButtonElement).disabled).toBe(true); // no second request while one waits
    client.getPrint.mockResolvedValue({ call_id: "ui_print", run_id: "uirun000001", tool: "print_send", status: "done", output: { ok: true, summary: "sent mises_mount_plate.gcode to Bench MK4; the printer is starting the job" }, error: null });
    await waitFor(() => expect(screen.getByRole("status", { name: "Print request" }).textContent).toContain("sent mises_mount_plate.gcode to Bench MK4"));
  });

  it("show_card_reselects_the_run_and_reopens_the_console", async () => {
    // A1: after sending, the waiting line offers "Show card" to return to the stranded card
    // even if the user selected another run and collapsed the console meanwhile.
    await sliced();
    await waitFor(() => expect((sendBtn() as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(sendBtn());
    await screen.findByRole("status", { name: "Print request" });
    selectedRunId.value = "other-run";
    setConsoleCollapsed(true);
    fireEvent.click(screen.getByRole("button", { name: "Show card" }));
    expect(selectedRunId.value).toBe("uirun000001");
    expect(consoleCollapsed.value).toBe(false);
  });

  it("send_checks_the_printer_again_at_click_time", async () => {
    await sliced();
    await waitFor(() => expect((sendBtn() as HTMLButtonElement).disabled).toBe(false));
    POLL.statusMs = 60_000; // the shown status stays "idle"; the fresh read says ERROR
    client.printerStatus.mockResolvedValue({ ...IDLE, state: "ERROR", sendable: false });
    fireEvent.click(sendBtn());
    expect((await screen.findByRole("alert")).textContent).toMatch(/reports ERROR/);
    expect(client.startPrint).not.toHaveBeenCalled();
  });

  it("a_failed_status_poll_marks_the_status_stale_and_disables_send", async () => {
    await sliced();
    await waitFor(() => expect((sendBtn() as HTMLButtonElement).disabled).toBe(false));
    client.printerStatus.mockRejectedValue(new CoreError("network", "fetch failed", { retryable: true }));
    await waitFor(() => expect((sendBtn() as HTMLButtonElement).disabled).toBe(true));
    expect(screen.getByRole("status", { name: "Printer status" }).textContent).toMatch(/status unknown/);
  });

  it("a_refused_send_shows_the_core_reason", async () => {
    await sliced();
    client.startPrint.mockRejectedValue(new CoreError("denied", "printer 'Bench MK4' reports BUSY; not sending a job", { status: 409, hint: "wait until the printer is idle" }));
    await waitFor(() => expect((sendBtn() as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(sendBtn());
    expect((await screen.findByRole("alert")).textContent).toContain("reports BUSY");
  });

  it("a_rejected_card_reads_as_not_sent", async () => {
    await sliced();
    client.getPrint.mockResolvedValue({ call_id: "ui_print", run_id: "uirun000001", tool: "print_send", status: "failed", output: null, error: { error: "denied", message: "approval rejected for print_send", hint: null } });
    await waitFor(() => expect((sendBtn() as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(sendBtn());
    await waitFor(() => expect(screen.getByRole("status", { name: "Print request" }).textContent).toContain("Not sent: approval rejected"));
  });
});
