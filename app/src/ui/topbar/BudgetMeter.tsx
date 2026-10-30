// Onshape budget (Plan 5 A6). Plan 7 U2b split it in two: while Onshape is connected the bar keeps a
// compact live count beside Status (BudgetChip); the details live in the Status card (BudgetMeter).
// Calls used / the yearly limit as a short gauge, plus what the selected run (or, with no run
// selected, the app's own browsing this session) used, the reset date and a way to the Onshape page.
// It moves live: every real Onshape request is followed by an `onshape_budget` event
// (state/onshapeBudget.ts), and the count briefly lights up when it goes up. Amber from the warn
// threshold, red at the limit; the Status button's ONSHAPE dot carries the same tone.
import type { JSX } from "preact";
import { budgetLevel, onshapeBudget, onshapeBudgetTick, onshapeRunUsed } from "../../state/onshapeBudget";
import { coreStatus, selectedRunId } from "../../state/store";
import { setPage } from "../pages/router";

const num = (n: number) => n.toLocaleString("en-US");

export function fmtResetDate(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** The live count in the top bar beside Status (U2b fix round 1): only while Onshape is connected,
 *  so a busy agent's spend is visible without opening anything. Flashes on every call, amber from
 *  warn_at, red at the limit; a click opens the Onshape page. The details stay in the Status card. */
export function BudgetChip(): JSX.Element | null {
  const b = onshapeBudget.value;
  const tick = onshapeBudgetTick.value;
  const s = coreStatus.value;
  if (!b || !s || s.onshape === "not_configured") return null;
  const level = budgetLevel(b);
  const note = level === "limit" ? " Limit reached." : level === "warn" ? ` Past the warning line of ${num(b.warn_at)}.` : "";
  return (
    <button type="button" class="budget-chip" data-level={level} title="Onshape calls this year: opens the Onshape page"
      aria-label={`Onshape: ${num(b.used_year)} of ${num(b.limit_year)} calls used this year.${note} Opens the Onshape page.`}
      onClick={() => setPage("onshape")}>
      <span class="budget-chip-name">Onshape </span>
      <span key={tick} class={tick > 0 ? "budget-chip-used budget-moved" : "budget-chip-used"} data-chip-used>{num(b.used_year)}</span>
      <span class="budget-limit">/{num(b.limit_year)}</span>
    </button>
  );
}

/** `onOpen`: called after "Open the Onshape page" navigates (the Status card closes). */
export function BudgetMeter({ onOpen }: { onOpen?: () => void } = {}): JSX.Element | null {
  const b = onshapeBudget.value;
  const tick = onshapeBudgetTick.value;
  if (!b) return null;
  // not connected yet: the allowance still shows (idle, dimmed) — the Onshape page's connect card
  // points at it (A-7) — with what the year allows, and no per-run count
  const idle = coreStatus.value?.onshape === "not_configured";
  const level = budgetLevel(b);
  const run = selectedRunId.value;
  // no run selected: the app's own browsing ("" entry), never whichever run sent the last event
  const delta = idle ? null : onshapeRunUsed.value.get(run ?? "") ?? (run ? null : 0);
  const whose = run ? "this run" : "this session";
  const pct = b.limit_year > 0 ? Math.min(100, (b.used_year / b.limit_year) * 100) : 100;
  const warnPct = b.limit_year > 0 ? Math.min(100, (b.warn_at / b.limit_year) * 100) : 100;
  const reset = fmtResetDate(b.reset_at);
  const left = Math.max(0, b.limit_year - b.used_year);
  const note = idle ? "Onshape is not connected yet: add your Onshape keys to connect it."
    : level === "limit" ? "Limit reached. Onshape requests are refused until the reset."
      : level === "warn" ? `Past the warning line of ${num(b.warn_at)} calls.` : null;
  const label = `Onshape budget${idle ? " (not connected)" : ""}: ${num(b.used_year)} of ${num(b.limit_year)} calls used this year`
    + (delta !== null ? `, ${num(delta)} ${whose}` : "") + `. Resets ${reset}.${note ? ` ${note}` : ""}`;

  return (
    <div class="budget" role="group" aria-label={label} data-level={level} data-state={idle ? "idle" : undefined}>
      <div class="budget-head">
        <span class="budget-name">Onshape calls this year</span>
        <span class="budget-val">
          {/* keyed on the tick so the count replays its "just moved" flash on every new call */}
          <span key={tick} class={tick > 0 ? "budget-used budget-moved" : "budget-used"} data-budget-used>{num(b.used_year)}</span>
          <span class="budget-limit">/{num(b.limit_year)}</span>
        </span>
      </div>
      <span class="budget-bar" aria-hidden="true">
        <span class="budget-fill" style={{ width: `${pct}%` }} />
        <span class="budget-warn" style={{ left: `${warnPct}%` }} />
      </span>
      <div class="budget-card-row"><span>Left</span><span>{num(left)}</span></div>
      {delta !== null && <div class="budget-card-row"><span>Used {whose}</span><span data-budget-delta>+{num(delta)}</span></div>}
      <div class="budget-card-row"><span>Resets</span><span data-budget-reset>{reset}</span></div>
      {note && <p class="budget-card-note budget-card-state">{note}</p>}
      <button type="button" class="btn budget-open" onClick={() => { setPage("onshape"); onOpen?.(); }}>Open the Onshape page</button>
    </div>
  );
}
