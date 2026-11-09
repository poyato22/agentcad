// Agent-built simulators (plan 2026-11-06 S3, S6): the decision card for a simulator request.
// It shows what the agent asked for and why, the method, validation cases, dependencies to
// download, and the cost estimate (low / likely / high with its basis), then Accept / Reject
// (a reason is required) / Ask for changes. A request paused at its budget shows the spend and
// Continue / Stop. Decided once; only a trusted (real) click decides. All text is agent-written:
// rendered as text.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import { tokens, usd, type SimDecision, type SimRequest } from "../../../api/simRequests";
import { isTrustedActivation } from "../approvals/approvalKeys";
import { resolved, sending } from "./once";
import { decideBudget, decideSim, simErrors, simKey } from "./simRequests";

const band = (b: { low: number; likely: number; high: number }, f: (v: number) => string): string =>
  `${f(b.low)} – ${f(b.high)} (likely ${f(b.likely)})`;

export function SimRequestCard({ request: r }: { request: SimRequest }): JSX.Element {
  const key = simKey(r);
  const done = resolved.value.get(key);
  const busy = sending.value.has(key);
  const err = simErrors.value.get(r.id);
  const [mode, setMode] = useState<null | "reject" | "changes">(null);
  const [reason, setReason] = useState("");
  const e = r.estimate;
  const q = r.request;
  const go = (ev: Event, action: SimDecision) => {
    if (!isTrustedActivation(ev) || busy || done) return;
    void decideSim(r, action, action === "accept" ? "" : reason);
  };
  const paused = r.status === "paused_budget";
  return (
    <div class="simreq" data-sim-request={r.id} data-status={r.status}>
      <p class="simreq-what text">{q.computes}</p>
      <p class="simreq-why text"><b>Why:</b> {q.why}</p>
      <p class="simreq-method text"><b>Method:</b> {q.method} · {q.fidelity_class.replace(/_/g, " ")}</p>
      <p class="simreq-io faint text">
        In: {q.inputs.map((i) => `${i.name} (${i.unit})`).join(", ")} · Out: {q.outputs.map((o) => `${o.name} (${o.unit})`).join(", ")}
      </p>
      <p class="simreq-val text" data-validation-count={q.validation_cases.length}>
        Validated against {q.validation_cases.length} known case{q.validation_cases.length === 1 ? "" : "s"} before use: {q.validation_cases.map((c) => `${c.name} (${c.source})`).join("; ")}
      </p>
      <dl class="simreq-cost" data-estimate-method={e.method}>
        <dt>Cost</dt><dd class="num" data-cost-usd>{band(e.usd, usd)}</dd>
        <dt>Tokens</dt><dd class="num">{band(e.tokens, tokens)}</dd>
        <dt>Time</dt><dd class="num">{band(e.wall_minutes, (v) => `${Math.round(v)} min`)}</dd>
        {e.weekly_share_pct && <><dt>Weekly usage</dt><dd class="num">{band(e.weekly_share_pct, (v) => `${v}%`)}</dd></>}
        <dt>Downloads</dt>
        <dd>{e.dependencies.length === 0 ? "none" : e.dependencies.map((d) => `${d.name} (${d.manager}, ${d.licence}${d.size_mb != null ? `, ${d.size_mb} MB` : ""})`).join(", ")}{e.download_note ? ` · ${e.download_note}` : ""}</dd>
      </dl>
      <p class="simreq-basis faint text" data-estimate-basis>Estimate ({e.confidence} confidence): {e.basis}. {e.note}. The build pauses at {usd(e.budget_pause_usd)}.</p>
      {paused && r.budget && (
        <p class="simreq-paused text" data-budget-paused>
          Paused at its budget: spent {usd(r.budget.paused_spend?.usd ?? 0)} of {usd(r.budget.limit_usd)}.
        </p>
      )}
      {done ? <p class="simreq-done" data-decided>{done.text}</p> : paused ? (
        <div class="simreq-actions">
          <button type="button" disabled={busy} onClick={(ev) => { if (isTrustedActivation(ev) && !busy) void decideBudget(r, "continue"); }}>Continue</button>
          <button type="button" disabled={busy} onClick={(ev) => { if (isTrustedActivation(ev) && !busy) void decideBudget(r, "stop"); }}>Stop</button>
        </div>
      ) : (
        <div class="simreq-actions">
          {mode === null ? (
            <>
              <button type="button" class="primary" disabled={busy} onClick={(ev) => go(ev, "accept")}>Accept and build</button>
              <button type="button" disabled={busy} onClick={() => setMode("reject")}>Reject</button>
              <button type="button" disabled={busy} onClick={() => setMode("changes")}>Ask for changes</button>
            </>
          ) : (
            <>
              <textarea aria-label={mode === "reject" ? "Why reject" : "What to change"} value={reason}
                placeholder={mode === "reject" ? "Why not (the agent states the gap)" : "What to change"}
                onInput={(ev) => setReason((ev.target as HTMLTextAreaElement).value)} />
              <button type="button" disabled={busy || reason.trim().length < 3} onClick={(ev) => go(ev, mode === "reject" ? "reject" : "changes")}>
                {mode === "reject" ? "Send reject" : "Send changes"}
              </button>
              <button type="button" disabled={busy} onClick={() => { setMode(null); setReason(""); }}>Back</button>
            </>
          )}
        </div>
      )}
      {err && <p class="simreq-err" role="alert">{err}</p>}
    </div>
  );
}
