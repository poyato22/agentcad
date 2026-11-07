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
import {
  consumeRefocus, decisionClickCounts, focusIsKeyboard, isActivationKey, isTrustedActivation, keyGuardsPass,
  keyToAction, trackFocusModality, windowEpoch, type ApprovalAction,
} from "./approvalKeys";
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

const MAX_FIELDS = 8;
const MAX_VALUE = 60;

/** The agent's own one-line summary: a claim, shown as "agent says", never as what will happen. */
function agentSays(a: Approval): string | null {
  return str(a.deltas.summary) ?? str(a.payload.summary);
}

/** Merge: which branch goes into which workspace (from the core's preview, else the tool args). */
function mergeRoute(a: Approval): { branch: string; target: string } {
  return {
    branch: str(a.deltas.branch_url) ?? str(a.payload.branch_url) ?? "unknown",
    target: str(a.deltas.target_url) ?? str(a.payload.target_url) ?? str(a.payload.url) ?? "unknown",
  };
}

const clip = (t: string) => (t.length > MAX_VALUE ? `${t.slice(0, MAX_VALUE - 1)}…` : t);

/** Payload fields whose text is the thing approved (Plan 9 U-D: the studies agent's message to
 *  the build's lead): shown in full whatever their length, never clipped. */
const TEXT_FIELDS: Readonly<Record<string, readonly string[]>> = { agent_message: ["text"] };
const isProse = (v: string) => v.length > MAX_VALUE && /\s/.test(v);

/** String payload fields shown whole (wrapped, scrolling when long): a text action's text, and
 *  any long prose field of another card. Rendered as text only. */
function proseFields(a: Approval): [string, string][] {
  if (a.action === MERGE_ACTION) return [];
  const always = TEXT_FIELDS[a.action] ?? [];
  return Object.entries(a.payload).filter((e): e is [string, string] =>
    e[0] !== "summary" && typeof e[1] === "string" && (always.includes(e[0]) ? e[1].trim() !== "" : isProse(e[1])));
}

/** Top-level payload fields as short "key: value" strings (the summary is shown as agent says;
 *  prose fields show whole, see proseFields). */
function payloadFields(a: Approval): { items: string[]; more: number } {
  const prose = new Set(proseFields(a).map(([k]) => k));
  const entries = Object.entries(a.payload).filter(([k]) => k !== "summary" && !prose.has(k));
  const items = entries.slice(0, MAX_FIELDS).map(([k, v]) => {
    const text = typeof v === "string" ? v : (JSON.stringify(v) ?? String(v));
    return clip(`${k}: ${text}`);
  });
  return { items, more: Math.max(0, entries.length - MAX_FIELDS) };
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

const PRINT_ACTION = "print_send";

/** print_send: what the core's preview says will happen (printer, material, time, grams, cost, G-code). */
function printFacts(a: Approval): string[] | null {
  if (a.action !== PRINT_ACTION) return null;
  const d = a.deltas;
  const p = rec(d.printer);
  const out: string[] = [];
  // slice_model: the model the G-code was sliced for, next to the target printer's model
  const sliced = str(d.slice_model);
  if (p) out.push([str(p.name), str(p.model), sliced ? `sliced for ${sliced}` : null, str(p.host), str(p.state)].filter(Boolean).join(" · "));
  else if (sliced) out.push(`sliced for ${sliced}`);
  const g = num(d.filament_g);
  const usd = num(d.cost_usd);
  out.push([str(d.material), str(d.time), g !== null ? `${g.toFixed(2)} g` : null, usd !== null ? `$${usd.toFixed(2)}` : null].filter(Boolean).join(" · "));
  const file = [str(d.file_name), str(d.gcode_sha256_short) ? `sha256 ${String(d.gcode_sha256_short)}` : null].filter(Boolean).join(" · ");
  if (file) out.push(file);
  return out.filter(Boolean);
}

/** The core's before → after lines (deltas.changes, e.g. a calibration save) and its note. */
function changeFacts(a: Approval): { lines: string[]; note: string | null } | null {
  const raw = a.deltas.changes;
  if (!Array.isArray(raw)) return null;
  const lines = raw.filter((x): x is string => typeof x === "string").slice(0, MAX_FIELDS).map(clip);
  return lines.length ? { lines, note: str(a.deltas.note) } : null;
}

/** "elements: 1 · conflicts: 0" — counts of the list fields of Onshape's merge preview. */
function mergePreviewSummary(mp: unknown): string | null {
  const o = rec(mp);
  if (!o) return null;
  const parts = Object.entries(o).filter(([, v]) => Array.isArray(v)).map(([k, v]) => `${k}: ${(v as unknown[]).length}`);
  return parts.length ? parts.join(" · ") : null;
}

const DONE_WORD: Readonly<Record<string, string>> = { approved: "Approved", rejected: "Rejected", executed: "Approved and done", expired: "Expired" };

/** "Approved by you 14:02" (the app decides as local-user), else who decided it. */
export function decidedText(a: Approval, kept: boolean): string {
  const by = a.decided_by === "local-user" ? " by you" : a.decided_by ? ` by ${a.decided_by}` : "";
  const at = a.decided_at !== null ? ` ${fmtHM(a.decided_at)}` : "";
  const what = a.status === "rejected" && kept ? "Kept branch · rejected" : DONE_WORD[a.status] ?? a.status;
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
  // last trusted pointerdown on a decision button
  const press = useRef<{ action: Decision; at: number } | null>(null);
  // when focus last entered the card by keyboard; null after pointer focus or any pointer press inside
  // (and the window-blur epoch it was taken in: leaving the window ends it)
  const keyFocus = useRef<{ at: number; epoch: number } | null>(null);
  trackFocusModality(document); // idempotent; installed before the first focus can land on the card

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

  // Mouse: a click counts only if its pointerdown landed on the same button after the card armed
  // for the current hash.
  const onPress = (action: Decision) => (e: PointerEvent) => {
    press.current = isTrustedActivation(e) ? { action, at: performance.now() } : null;
  };
  const onButton = (action: Decision) => (e: MouseEvent) => {
    const pd = press.current;
    press.current = null;
    const pressedAt = pd && pd.action === action ? pd.at : null;
    if (!isTrustedActivation(e) || !decisionClickCounts(e, pressedAt, arm.current.at, performance.now())) return;
    choose(action);
  };

  /** The decision button (Merge/Approve, Keep branch, Reject) a key event is aimed at, if any. */
  const decisionTarget = (e: KeyboardEvent): Decision | null => {
    const t = e.target instanceof Element ? e.target.closest("[data-decision]") : null;
    return t && ref.current?.contains(t) ? (t.getAttribute("data-decision") as Decision) : null;
  };

  const onKeyUp = (e: KeyboardEvent) => {
    // Space activates a button on keyup: never let that native activation happen
    if (isActivationKey(e) && decisionTarget(e)) e.preventDefault();
  };

  const onKeyDown = (e: KeyboardEvent) => {
    const card = ref.current;
    if (!card) return;
    const target = decisionTarget(e);
    if (target && isActivationKey(e)) {
      // Enter/Space on a decision button never use the browser's activation (it would skip the
      // paste, modifier and repeat rules, and Space would be judged at release): they go
      // through the same key guards as m/k/r, checked now, at keydown.
      e.preventDefault();
      e.stopPropagation();
      if (keyGuardsPass(e, card, arm.current.at, performance.now())) choose(target);
      return;
    }
    const kf = keyFocus.current;
    const action = keyToAction(e, card, arm.current.at, performance.now(), kf && kf.epoch === windowEpoch() ? kf.at : null);
    if (!action) return;
    e.preventDefault();
    e.stopPropagation();
    choose(action);
  };

  const onFocusIn = (e: FocusEvent) => {
    const restored = consumeRefocus(e.target); // the window came back: not keyboard focus
    keyFocus.current = !restored && e.target instanceof Element && focusIsKeyboard(e.target)
      ? { at: performance.now(), epoch: windowEpoch() } : null;
  };
  const onPointerDownIn = () => { keyFocus.current = null; };

  const evidence = evidenceFor(a, steps.value.get(a.run_id) ?? []).slice(-MAX_EVIDENCE);
  const chips = deltaChips(a);
  const says = agentSays(a);
  const route = mergeRoute(a);
  const fields = payloadFields(a);
  const prose = proseFields(a);
  const reason = str(a.deltas.approval_reason);
  const preview = mergePreviewSummary(a.deltas.merge_preview);
  const print = printFacts(a);
  const changes = changeFacts(a);

  return (
    <div ref={ref} data-card data-approval={a.id} data-status={expired ? "expired" : a.status}
      class={expired || isRetired ? "acard acard-inert" : "acard acard-live"} tabIndex={0} role="group"
      aria-label={`Needs you: ${a.action}`} onKeyDown={onKeyDown} onKeyUp={onKeyUp}
      onFocusIn={onFocusIn} onPointerDownCapture={onPointerDownIn} onMouseDownCapture={onPointerDownIn}>
      <div class="acard-head">
        <span class="acard-flag">Needs you</span>
        <span class="acard-action text">{a.action}</span>
        <span class="faint text acard-run">{props.runLabel}</span>
      </div>
      {isMerge ? (
        <div class="acard-what text" data-route>
          <span class="faint">branch </span>{route.branch}<span class="faint"> → target </span>{route.target}
        </div>
      ) : (
        <div class="acard-what acard-fields text" data-payload-fields>
          {fields.items.map((f, i) => <span key={i} data-field>{f}</span>)}
          {fields.more > 0 && <span class="faint">+{fields.more} more (Diff)</span>}
        </div>
      )}
      {reason && <div class="acard-reason text" data-approval-reason>{reason}</div>}
      {prose.map(([k, v]) => (
        <div key={k} class="acard-prose" data-prose-field={k}>
          <span class="faint">{k}</span>
          <div class="acard-prose-text text" tabIndex={0} aria-label={`${k}: the exact text you approve`}>{v}</div>
        </div>
      ))}
      {print && (
        <div class="acard-what acard-fields text" data-print-facts>
          <span class="tone-amber">Approving starts printing on this printer:</span>
          {print.map((f, i) => <span key={i}>{f}</span>)}
        </div>
      )}
      {changes && (
        <div class="acard-what acard-fields text" data-change-facts>
          {changes.note && <span class="tone-amber">{changes.note}</span>}
          {changes.lines.map((f, i) => <span key={i} data-change>{f}</span>)}
        </div>
      )}
      {says && <div class="acard-says faint text" data-agent-says>agent says: {says}</div>}
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
            <button key={ev.stepId} type="button" tabIndex={0} class="acard-link" data-evidence={String(ev.stepId)}
              onClick={() => actions.focusStep(ev.stepId)}>
              <span class="text">{ev.label}</span> <span class="faint">{ev.hash8}</span>
            </button>
          ))}
        </div>
      )}
      {notice && <div class={`acard-notice tone-${notice.tone}`} role="status">{notice.text}</div>}
      {/* Explicit tabIndex: WebKit (the app's WKWebView) skips <button>s on Tab unless they carry
          one, so without it Tab left the card and the m/a/k/r letters could not be armed there. */}
      <div class="acard-buttons">
        {/* m and a both approve on every card; the merge card lists both */}
        <Button variant="primary" tabIndex={0} disabled={disabled} data-decision="approve" onPointerDown={onPress("approve")}
          onClick={onButton("approve")} aria-keyshortcuts={isMerge ? "m a" : "a m"}>
          {isMerge ? "Merge" : "Approve"} <kbd>{isMerge ? "m" : "a"}</kbd>
        </Button>
        <Button tabIndex={0} onClick={() => choose("diff")} aria-pressed={showDiff} aria-keyshortcuts="d">Diff <kbd>d</kbd></Button>
        {isMerge && (
          <Button tabIndex={0} disabled={disabled} data-decision="keep_branch" onPointerDown={onPress("keep_branch")}
            onClick={onButton("keep_branch")} aria-keyshortcuts="k">Keep branch <kbd>k</kbd></Button>
        )}
        <Button tabIndex={0} disabled={disabled} data-decision="reject" onPointerDown={onPress("reject")}
          onClick={onButton("reject")} aria-keyshortcuts="r">Reject <kbd>r</kbd></Button>
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
