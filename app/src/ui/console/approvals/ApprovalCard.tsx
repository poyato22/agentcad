// Approval card: the human-in-the-loop gate. Hash-bound (the decision carries the payload_hash
// the card shows), decided only by a trusted click on its buttons or a key pressed while focus is
// inside the card (see approvalKeys.ts). A 409 stale_version refreshes the card in place.
// All agent / Onshape text (action, summaries, payload, merge preview) renders as text only.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { Approval } from "../../../api/types";
import { fmtElapsed, fmtHM, fmtSigned, shortHash } from "../../../lib/format";
import { actions, approvals, steps } from "../../../state/store";
import { Button } from "../../common/Button";
import { clickArmed, isTrustedActivation, keyToAction, type ApprovalAction } from "./approvalKeys";
import { canDecide, deciding, decide, isExpired, keptBranch, notices, retired, type Decision } from "./decide";
import { evidenceFor } from "./evidence";
import "./approvals.css";

export { decide } from "./decide";
export type { ApprovalAction } from "./approvalKeys";

const MERGE_ACTION = "onshape_merge_request";
const MAX_EVIDENCE = 6;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const rec = (v: unknown): Record<string, unknown> | null =>
  (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

/** One line: what will change if approved. */
function whatChanges(a: Approval): string {
  return str(a.deltas.summary) ?? str(a.payload.summary) ?? a.action;
}

/** "+4.1 g", "+3200 mm³", "+3m 10s print" from deltas (top level or deltas.delta). */
function deltaChips(a: Approval): string[] {
  const d = { ...rec(a.deltas.delta), ...a.deltas };
  const out: string[] = [];
  const mass = num(d.mass_g);
  if (mass !== null) out.push(fmtSigned(mass, 1, " g"));
  const vol = num(d.volume_mm3);
  if (vol !== null) out.push(fmtSigned(vol, 0, " mm³"));
  const print = num(d.print_time_s);
  if (print !== null) out.push(`${print < 0 ? "−" : "+"}${fmtElapsed(Math.abs(print))} print`);
  return out;
}

/** "elements: 1 · conflicts: 0" — counts of the list fields of Onshape's merge preview. */
function mergePreviewSummary(mp: unknown): string | null {
  const o = rec(mp);
  if (!o) return null;
  const parts = Object.entries(o).filter(([, v]) => Array.isArray(v)).map(([k, v]) => `${k}: ${(v as unknown[]).length}`);
  return parts.length ? parts.join(" · ") : null;
}

function decidedText(a: Approval, kept: boolean): string {
  const by = a.decided_by ? ` by ${a.decided_by}` : "";
  const at = a.decided_at !== null ? ` ${fmtHM(a.decided_at)}` : "";
  const what = a.status === "rejected" && kept ? "kept branch · rejected" : a.status;
  return `${what}${by}${at}`;
}

function useNowS(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

export function ApprovalCard(props: { approval: Approval; runLabel: string }): JSX.Element {
  // the store holds the latest copy (decided, or refreshed after a stale 409)
  const a = approvals.value.get(props.approval.id) ?? props.approval;
  const ref = useRef<HTMLDivElement>(null);
  const [showDiff, setShowDiff] = useState(false);
  // armed ARM_MS after mount and again whenever the payload (hash) changes
  const arm = useRef({ hash: a.payload_hash, at: performance.now() });
  if (arm.current.hash !== a.payload_hash) arm.current = { hash: a.payload_hash, at: performance.now() };

  const pending = a.status === "pending";
  const nowS = useNowS(pending);
  const expired = isExpired(a, nowS);
  const busy = deciding.value.has(a.id);
  const isRetired = retired.value.has(a.id);
  const notice = notices.value.get(a.id);
  const isMerge = a.action === MERGE_ACTION;
  const disabled = busy || !canDecide(a, nowS) || isRetired;

  if (!pending && a.status !== "expired") {
    const tone = a.status === "approved" || a.status === "executed" ? "green" : "dim";
    return (
      <div ref={ref} data-card data-approval={a.id} data-status={a.status} class="acard acard-done" tabIndex={0}
        role="group" aria-label={`Approval: ${a.action}`}>
        <span class={`acard-outcome tone-${tone}`}>{decidedText(a, keptBranch.value.has(a.id))}</span>
        <span class="faint text"> · {a.action} · {props.runLabel}</span>
      </div>
    );
  }

  const choose = (action: ApprovalAction) => {
    if (action === "diff") { setShowDiff((v) => !v); return; }
    if (action === "keep_branch" && !isMerge) return;
    if (disabled) return;
    void decide(a, action as Decision);
  };

  const onButton = (action: Decision) => (e: MouseEvent) => {
    if (!isTrustedActivation(e) || !clickArmed(e, arm.current.at, performance.now())) return;
    choose(action);
  };

  const onKeyDown = (e: KeyboardEvent) => {
    // a held key never acts: keyToAction drops repeats, and a held Enter/Space must not
    // auto-click the focused button over and over
    if (e.repeat && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); return; }
    const card = ref.current;
    if (!card) return;
    const action = keyToAction(e, card, arm.current.at, performance.now());
    if (!action) return;
    e.preventDefault();
    e.stopPropagation();
    choose(action);
  };

  const evidence = evidenceFor(a, steps.value.get(a.run_id) ?? []).slice(-MAX_EVIDENCE);
  const chips = deltaChips(a);
  const preview = mergePreviewSummary(a.deltas.merge_preview);

  return (
    <div ref={ref} data-card data-approval={a.id} data-status={expired ? "expired" : a.status}
      class={expired || isRetired ? "acard acard-inert" : "acard acard-live"} tabIndex={0} role="group"
      aria-label={`Needs you: ${a.action}`} onKeyDown={onKeyDown}>
      <div class="acard-head">
        <span class="acard-flag">▲ NEEDS YOU</span>
        <span class="acard-action text">{a.action}</span>
        <span class="faint text acard-run">{props.runLabel}</span>
      </div>
      <div class="acard-what text">{whatChanges(a)}</div>
      {chips.length > 0 && <div class="acard-deltas">{chips.map((c) => <span key={c}>{c}</span>)}</div>}
      <div class="acard-meta faint">
        <span data-hash={shortHash(a.payload_hash)}>hash {shortHash(a.payload_hash)}</span>
        {a.microversion && <span> · mv {shortHash(a.microversion)}</span>}
        <span> · </span>
        {expired
          ? <span data-expiry class="tone-amber">expired</span>
          : <span data-expiry>expires in {fmtElapsed(a.expires_at - nowS)}</span>}
      </div>
      {evidence.length > 0 && (
        <div class="acard-evidence">
          <span class="label">evidence</span>
          {evidence.map((ev) => (
            <button key={ev.stepId} type="button" class="acard-link" data-evidence={String(ev.stepId)}
              onClick={() => actions.focusStep(ev.stepId)}>
              <span class="text">{ev.label}</span> <span class="faint">{ev.hash8}</span>
            </button>
          ))}
        </div>
      )}
      {notice && <div class={`acard-notice tone-${notice.tone}`} role="status">{notice.text}</div>}
      <div class="acard-buttons">
        <Button variant="primary" disabled={disabled} onClick={onButton("approve")}
          aria-keyshortcuts={isMerge ? "m" : "a"}>
          {isMerge ? "Merge" : "Approve"} <kbd>{isMerge ? "m" : "a"}</kbd>
        </Button>
        <Button onClick={() => choose("diff")} aria-pressed={showDiff} aria-keyshortcuts="d">Diff <kbd>d</kbd></Button>
        {isMerge && (
          <Button disabled={disabled} onClick={onButton("keep_branch")} aria-keyshortcuts="k">Keep branch <kbd>k</kbd></Button>
        )}
        <Button disabled={disabled} onClick={onButton("reject")} aria-keyshortcuts="r">Reject <kbd>r</kbd></Button>
      </div>
      {showDiff && (
        <div class="acard-diff">
          {preview && <div class="dim" data-merge-preview>merge preview · {preview}</div>}
          <pre class="text" data-diff>{JSON.stringify({ payload: a.payload, deltas: a.deltas }, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
