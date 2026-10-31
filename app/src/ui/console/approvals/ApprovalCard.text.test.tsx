// Plan 9 U-D: the studies agent's hand-off to the build's lead (`agent_message`) waits on the
// user's approval of the exact text. Its card shows that text whole (wrapped, scrolling, as text)
// and why it asks, from the real core's card (core/scripts/dump_studies_context_fixtures.py);
// other cards keep their short "key: value" lines.
import { cleanup, render } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { Approval } from "../../../api/types";
import { actions, resetStore } from "../../../state/store";
import { ApprovalCard } from "./ApprovalCard";
import { resetApprovalUi } from "./decide";

const CARD = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui/studies_agent_message_approval.json"), "utf8")) as Approval;
const live = (a: Approval): Approval => ({ ...a, expires_at: Date.now() / 1000 + 600 });

afterEach(() => { cleanup(); resetStore(); resetApprovalUi(); });

function mount(a: Approval) {
  actions.upsertApproval(a);
  return render(<ApprovalCard approval={a} runLabel="simulation · plan the next studies" />).container;
}

describe("ApprovalCard: agent_message shows the exact text", () => {
  it("the real card's whole message and why it asks", () => {
    const text = CARD.payload.text as string;
    expect(text.length).toBeGreaterThan(300);
    const c = mount(live(CARD));
    const prose = c.querySelector('[data-prose-field="text"] .acard-prose-text');
    expect(prose?.textContent).toBe(text);
    expect(c.querySelector("[data-approval-reason]")?.textContent).toBe(CARD.deltas.approval_reason);
    // the short field list no longer carries a clipped copy of it
    expect([...c.querySelectorAll("[data-field]")].map((e) => e.textContent)).toEqual(["to: lead"]);
  });

  it("a 2000-character message shows in full, as text (never HTML)", () => {
    const text = `${"<b>bold</b> ".repeat(20)}${"word ".repeat(380)}END`;
    const c = mount(live({ ...CARD, payload: { ...CARD.payload, text } }));
    const prose = c.querySelector('[data-prose-field="text"] .acard-prose-text')!;
    expect(prose.textContent).toBe(text);
    expect(prose.querySelector("b")).toBeNull();
  });

  it("other cards keep short fields; only long prose shows whole", () => {
    const short = { ...live(CARD), action: "mirror_part_set_vars", deltas: {}, payload: { part_id: "hip_bracket", vars: { t: 4.5 } } };
    const c = mount(short);
    expect(c.querySelector("[data-prose-field]")).toBeNull();
    expect(c.querySelector("[data-approval-reason]")).toBeNull();
    expect([...c.querySelectorAll("[data-field]")].map((e) => e.textContent)).toEqual(["part_id: hip_bracket", 'vars: {"t":4.5}']);
    cleanup();
    const note = "Rebuild the bracket with the thicker web so the hip study can run again on the new geometry.";
    const c2 = mount({ ...short, id: "ap_x2", payload: { part_id: "hip_bracket", note } });
    expect(c2.querySelector('[data-prose-field="note"] .acard-prose-text')?.textContent).toBe(note);
  });
});
