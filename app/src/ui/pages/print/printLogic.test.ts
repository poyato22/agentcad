import { describe, expect, it } from "vitest";
import type { Printer, PrinterStatus, Slice } from "../../../api/types";
import { printerLight, printOutcome, sendBlock, validHost } from "./printLogic";

const printer: Printer = { id: "bench_mk4", kind: "prusalink", host: "192.168.1.20", name: "Bench MK4", model: "MK4", has_key: true };
const idle: PrinterStatus = { printer_id: "bench_mk4", reachable: true, state: "IDLE", sendable: true, temp_nozzle: 25, temp_bed: 24, job: null, error: null };
const slice: Slice = {
  slice_id: "slice_0123456789", status: "done", part_id: "mount_plate", printer_id: "bench_mk4", material: "PLA", model: "MK4",
  created_at: 1, estimate: { time_s: 600, time_text: "10m", filament_mm: 100, filament_g: 3.2, cost_usd: 0.07 }, preview: true,
  error: null, hint: null, sent_to: null,
};
const ok = { slice, printer, status: idle, partId: "mount_plate", material: "PLA", busy: false };

describe("printerLight", () => {
  it("maps_states_to_pass_warn_fail", () => {
    expect(printerLight(idle)).toEqual({ tone: "green", text: "idle · nozzle 25 °C · bed 24 °C" });
    expect(printerLight({ ...idle, state: "PRINTING", sendable: false, job: { progress: 42, time_remaining: 900 } }))
      .toEqual({ tone: "amber", text: "printing 42% · 15m 00s left" });
    expect(printerLight({ ...idle, state: "ERROR", sendable: false }).tone).toBe("red");
    expect(printerLight({ ...idle, state: "ATTENTION", sendable: false }).tone).toBe("red");
    expect(printerLight({ ...idle, state: "BUSY", sendable: false }).tone).toBe("amber");
    expect(printerLight({ ...idle, reachable: false, state: null, sendable: false, error: "printer unreachable (ConnectError)" }))
      .toEqual({ tone: "red", text: "unreachable — printer unreachable (ConnectError)" });
    expect(printerLight(null)).toEqual({ tone: "faint", text: "checking…" });
  });
});

describe("sendBlock", () => {
  it("allows_a_finished_matching_slice_on_a_sendable_printer", () => {
    expect(sendBlock(ok)).toBeNull();
  });

  it("never_sends_to_an_errored_busy_or_unreachable_printer", () => {
    expect(sendBlock({ ...ok, status: { ...idle, state: "ERROR", sendable: false } })).toMatch(/reports ERROR/);
    expect(sendBlock({ ...ok, status: { ...idle, state: "PRINTING", sendable: false } })).toMatch(/reports PRINTING/);
    expect(sendBlock({ ...ok, status: { ...idle, reachable: false, state: null, sendable: false } })).toMatch(/not reachable/);
    expect(sendBlock({ ...ok, status: null })).toMatch(/checking/);
    // sendable must be the core's verdict, not just a state name we think is fine
    expect(sendBlock({ ...ok, status: { ...idle, sendable: false } })).not.toBeNull();
  });

  it("needs_a_done_slice_for_the_same_part_material_printer_and_model", () => {
    expect(sendBlock({ ...ok, slice: null })).toMatch(/Slice first/);
    expect(sendBlock({ ...ok, slice: { ...slice, status: "running" } })).toMatch(/Slice first/);
    expect(sendBlock({ ...ok, partId: "hinge_arm" })).toMatch(/slice again/);
    expect(sendBlock({ ...ok, material: "PETG" })).toMatch(/slice again/);
    expect(sendBlock({ ...ok, printer: { ...printer, id: "mini" } })).toMatch(/slice again/);
    expect(sendBlock({ ...ok, printer: { ...printer, model: "MINI" } })).toMatch(/MK4/);
    expect(sendBlock({ ...ok, slice: { ...slice, sent_to: "bench_mk4" } })).toMatch(/already sent/);
    expect(sendBlock({ ...ok, busy: true })).toMatch(/waiting/);
    expect(sendBlock({ ...ok, printer: null })).toMatch(/Add a printer/);
  });
});

describe("printOutcome", () => {
  it("reads_done_failed_and_cancelled_calls", () => {
    expect(printOutcome({ call_id: "c", run_id: "r", tool: "print_send", status: "done", output: { summary: "sent x to Bench MK4" }, error: null }))
      .toEqual({ tone: "green", text: "sent x to Bench MK4" });
    expect(printOutcome({ call_id: "c", run_id: "r", tool: "print_send", status: "failed", output: null, error: { error: "denied", message: "approval rejected for print_send" } }))
      .toEqual({ tone: "dim", text: "Not sent: approval rejected for print_send" });
    expect(printOutcome({ call_id: "c", run_id: "r", tool: "print_send", status: "failed", output: null, error: { error: "tool_failed", message: "unknown outcome", hint: "check the printer" } }))
      .toEqual({ tone: "red", text: "Not sent: unknown outcome — check the printer" });
    expect(printOutcome({ call_id: "c", run_id: "r", tool: "print_send", status: "failed", output: { ok: false, summary: "no" }, error: null })?.tone).toBe("red");
    expect(printOutcome({ call_id: "c", run_id: "r", tool: "print_send", status: "cancelled", output: null, error: null }))
      .toEqual({ tone: "dim", text: "Cancelled; nothing was sent." });
    expect(printOutcome({ call_id: "c", run_id: "r", tool: "print_send", status: "running", output: null, error: null })).toBeNull();
  });
});

describe("validHost", () => {
  it("accepts_hosts_and_ips_with_port_and_refuses_urls", () => {
    expect(validHost("192.168.1.20")).toBe(true);
    expect(validHost("prusa-mk4.local:8080")).toBe(true);
    expect(validHost("http://192.168.1.20")).toBe(false);
    expect(validHost("192.168.1.20/api")).toBe(false);
    expect(validHost("host:70000")).toBe(false);
    expect(validHost("")).toBe(false);
  });
});
