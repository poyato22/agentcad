// The print_send card (irreversible: starts a real print) shows what will happen from the core's
// preview (deltas): printer, material, time, filament, cost and the G-code it is bound to.
import { cleanup, render } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { Approval } from "../../../api/types";
import { actions, resetStore } from "../../../state/store";
import { ApprovalCard } from "./ApprovalCard";
import { resetApprovalUi } from "./decide";

const CARD = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../../test/fixtures/core-ui/print_approval.json"), "utf8")) as Approval;

afterEach(() => { cleanup(); resetStore(); resetApprovalUi(); });

describe("ApprovalCard: print_send", () => {
  it("shows_printer_material_time_grams_cost_and_gcode_from_the_core_preview", () => {
    const a: Approval = { ...CARD, expires_at: Date.now() / 1000 + 600 };
    actions.upsertApproval(a);
    const { container } = render(<ApprovalCard approval={a} runLabel="you · actions started from the app" />);
    const facts = container.querySelector("[data-print-facts]");
    expect(facts).not.toBeNull();
    const t = facts?.textContent ?? "";
    expect(t).toContain(`Bench MK4 · MK4 · ${String((CARD.deltas.printer as { host: string }).host)} · IDLE`);
    expect(t).toContain("PLA");
    expect(t).toContain("1d 2h 3m 22s");
    expect(t).toContain("7.51 g");
    expect(t).toContain("$0.17");
    expect(t).toContain("mises_mount_plate.gcode");
    expect(t).toContain("eafcd5bc4670");
    expect(container.textContent).toContain("starts printing");
  });

  it("other_cards_have_no_print_facts", () => {
    const a: Approval = { ...CARD, action: "onshape_merge_request", deltas: {}, expires_at: Date.now() / 1000 + 600 };
    actions.upsertApproval(a);
    const { container } = render(<ApprovalCard approval={a} runLabel="designer · x" />);
    expect(container.querySelector("[data-print-facts]")).toBeNull();
  });
});
