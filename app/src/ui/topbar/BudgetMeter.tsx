// Top-bar Onshape budget meter (Plan 5 A6): calls used / the yearly limit as a short gauge, plus
// what the selected run (or, with no run selected, the app's own browsing this session) used.
// It moves live: every real Onshape request is followed by an `onshape_budget` event
// (state/onshapeBudget.ts), and the count briefly lights up when it goes up. Amber from the warn
// threshold, red at the limit. Hover or focus shows the details and the reset date; a click opens
// the Onshape page.
import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import { budgetLevel, onshapeBudget, onshapeBudgetTick, onshapeRunUsed } from "../../state/onshapeBudget";
import { coreStatus, selectedRunId } from "../../state/store";
import { setPage } from "../pages/router";

const num = (n: number) => n.toLocaleString("en-US");

export function fmtResetDate(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function BudgetMeter(): JSX.Element | null {
  const btn = useRef<HTMLButtonElement>(null);
  // The card is position: fixed (the top bar's right side clips overflow), placed under the button.
  const [card, setCard] = useState<{ right: number } | null>(null);
  const show = () => {
    const r = btn.current?.getBoundingClientRect();
    setCard({ right: r && r.width > 0 ? Math.max(8, window.innerWidth - r.right) : 8 });
  };
  const hide = () => setCard(null);

  const b = onshapeBudget.value;
  const tick = onshapeBudgetTick.value;
  if (!b) return null;
  // not connected yet: the meter still shows (idle, dimmed) — the Onshape page's connect card
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
  const note = idle ? "Onshape is not connected yet. Add your keys in Profile → Onshape."
    : level === "limit" ? "Limit reached. Onshape requests are refused until the reset."
      : level === "warn" ? `Past the warning line of ${num(b.warn_at)} calls.` : null;
  const label = `Onshape budget${idle ? " (not connected)" : ""}: ${num(b.used_year)} of ${num(b.limit_year)} calls used this year`
    + (delta !== null ? `, ${num(delta)} ${whose}` : "") + `. Resets ${reset}.${note ? ` ${note}` : ""} Opens the Onshape page.`;

  return (
    <div class="budget" data-level={level} data-state={idle ? "idle" : undefined} onMouseEnter={show} onMouseLeave={hide}>
      <button ref={btn} type="button" class="budget-btn" aria-label={label} title="Onshape API calls this year"
        onFocus={show} onBlur={hide} onKeyDown={(e) => { if (e.key === "Escape") hide(); }}
        onClick={() => setPage("onshape")}>
        <span class="budget-name">Onshape</span>
        <span class="budget-val">
          {/* keyed on the tick so the count replays its "just moved" flash on every new call */}
          <span key={tick} class={tick > 0 ? "budget-used budget-moved" : "budget-used"} data-budget-used>{num(b.used_year)}</span>
          <span class="budget-limit">/{num(b.limit_year)}</span>
        </span>
        <span class="budget-bar" aria-hidden="true">
          <span class="budget-fill" style={{ width: `${pct}%` }} />
          <span class="budget-warn" style={{ left: `${warnPct}%` }} />
        </span>
        {delta !== null && <span class="budget-delta" data-budget-delta>+{num(delta)}</span>}
      </button>
      {/* the button's aria-label already reads every figure; the card is for sighted users */}
      <div id="budget-card" class="budget-card" aria-hidden="true" hidden={card === null}
        style={card ? { right: `${card.right}px` } : undefined}>
        <div class="budget-card-row"><span>Used this year</span><span>{num(b.used_year)} of {num(b.limit_year)}</span></div>
        <div class="budget-card-row"><span>Left</span><span>{num(left)}</span></div>
        {delta !== null && <div class="budget-card-row"><span>Used {whose}</span><span>{num(delta)}</span></div>}
        <div class="budget-card-row"><span>Warns at</span><span>{num(b.warn_at)}</span></div>
        <div class="budget-card-row"><span>Resets</span><span data-budget-reset>{reset}</span></div>
        {note && <p class="budget-card-note budget-card-state">{note}</p>}
        <p class="budget-card-note">Updates after every Onshape request, the agents' included.</p>
      </div>
    </div>
  );
}
