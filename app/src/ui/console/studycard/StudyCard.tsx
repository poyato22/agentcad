// A study inline in the thread (and on the Studies page): the type and status, a live phase bar
// while it runs, the fidelity note up front ("low fidelity · ±40% (validated)"), key metrics,
// the requirement checks with pass / warn / fail, a small chart, a thumbnail of the colored
// result, and the agent's one-line reason. "Immersive" opens the full-window 3D view; a
// double-click on the chart does too. Every string from the core renders as text.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { StudyCard as Card, StudyChart as Chart, StudyCheck } from "../../../api/studies";
import { approvals, runs } from "../../../state/store";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { cardChart } from "../../pages/studies/charts/chartModel";
import { StudyChart } from "../../pages/studies/charts/StudyChart";
import { studyData } from "../../pages/studies/data";
import { Markup } from "../../markup/render";
import { openImmersive } from "../../pages/studies/immersive/state";
import { liveProgress, progressForCall } from "../../pages/studies/live";
import { fmtMetric, honesty, fmtValue, phaseText, PHASES, progressFraction, typeName, VERDICT_TEXT, verdictTone } from "../../pages/studies/model";
import { showInStudies } from "../../pages/studies/nav";
import { cachedThumbnail, thumbnail } from "../../pages/studies/scene/thumbnails";
import { bareToolName, kindText, type StudyCallView } from "./cardModel";
import "./studycard.css";

/** Fetch once the element is (nearly) on screen; immediately where IntersectionObserver is missing. */
function useVisible(): [preact.RefObject<HTMLElement>, boolean] {
  const ref = useRef<HTMLElement>(null);
  const [seen, setSeen] = useState(typeof IntersectionObserver === "undefined");
  useEffect(() => {
    if (seen || !ref.current) return;
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { setSeen(true); io.disconnect(); } }, { rootMargin: "200px" });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [seen]);
  return [ref, seen];
}

export function PhaseBar({ phase, pct, detail, cancelled }: { phase: string | null; pct: number | null; detail: string | null; cancelled?: boolean }): JSX.Element {
  const frac = progressFraction(phase, pct);
  const at = phase ? (PHASES as readonly string[]).indexOf(phase) : -1;
  return (
    <div class="scard-phase" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(frac * 100)}
      aria-valuetext={`${phaseText(phase)}${detail ? ` · ${detail}` : ""}`} data-testid="phase-bar" data-phase={phase ?? "starting"}>
      <div class="scard-phase-steps" aria-hidden="true">
        {PHASES.slice(1).map((p, i) => (
          <span key={p} class="scard-phase-step" data-state={at > i + 1 || phase === "done" ? "done" : at === i + 1 ? "now" : "todo"}>{phaseText(p)}</span>
        ))}
      </div>
      <div class="scard-bar"><div class="scard-bar-fill" style={{ width: `${Math.round(frac * 100)}%` }} /></div>
      <div class="scard-phase-detail faint text">{cancelled ? "cancelled" : detail ?? phaseText(phase)}</div>
    </div>
  );
}

export function CheckRows({ checks, onOpen }: { checks: readonly StudyCheck[]; onOpen?: (simId: string) => void }): JSX.Element | null {
  if (checks.length === 0) return null;
  return (
    <ul class="scard-checks" aria-label="Requirement checks">
      {checks.map((c, i) => (
        <li key={`${c.requirement_id ?? i}`} data-testid="check" data-status={c.status}>
          <Led tone={verdictTone(c.status)} />
          <span class="scard-check-what text">{c.requirement_id ? `${c.requirement_id} · ` : ""}{kindText(c.kind)} {fmtValue(c.target, c.unit)}</span>
          <span class="scard-check-got">{fmtValue(c.measured, c.unit)}</span>
          <span class={`scard-verdict tone-${verdictTone(c.status)}`}>{VERDICT_TEXT[c.status]}</span>
          {c.note && <span class="scard-check-note faint text">{c.note}</span>}
          {onOpen && c.sim_id && <button type="button" class="scard-link" onClick={() => onOpen(c.sim_id as string)}>view</button>}
        </li>
      ))}
    </ul>
  );
}

function Thumb({ simId, type }: { simId: string; type: string }): JSX.Element {
  const [url, setUrl] = useState<string | null | undefined>(() => cachedThumbnail(simId));
  const [ref, seen] = useVisible();
  useEffect(() => {
    if (!seen || url !== undefined) return;
    let live = true;
    void thumbnail(simId, (id) => studyData.glb(id).catch(() => null)).then((u) => { if (live) setUrl(u); });
    return () => { live = false; };
  }, [simId, seen]);
  return (
    <span class="scard-thumb" ref={ref as preact.RefObject<HTMLSpanElement>} data-testid="study-thumb" data-state={url ? "loaded" : url === null ? "none" : "loading"}>
      {url ? <img src={url} alt={`${typeName(type)} result, colored`} width={56} height={56} /> : <span class="scard-thumb-empty" aria-hidden="true" />}
    </span>
  );
}

function SmallChart({ simId, type, onOpen }: { simId: string; type: string; onOpen: () => void }): JSX.Element | null {
  const [chart, setChart] = useState<Chart | null | undefined>(undefined);
  const [ref, seen] = useVisible();
  useEffect(() => {
    if (!seen) return;
    let live = true;
    void studyData.charts(simId).then((c) => { if (live) setChart(cardChart(type, c.charts)); }, () => { if (live) setChart(null); });
    return () => { live = false; };
  }, [simId, seen]);
  return (
    <div class="scard-chart" ref={ref as preact.RefObject<HTMLDivElement>} data-testid="card-chart" data-chart={chart?.id ?? ""}>
      {chart && <><div class="scard-chart-title faint">{chart.title}</div><StudyChart chart={chart} compact height={84} onOpen={onOpen} /></>}
    </div>
  );
}

export interface StudyCardProps {
  view: StudyCallView;
  /** Other studies of the same set (⌘[ / ⌘] in the immersive view). */
  siblings?: readonly string[];
}

export function StudyCard({ view, siblings = [] }: StudyCardProps): JSX.Element {
  // R18: the card is compact by default (type, status, honesty, key numbers, thumbnail); the chart
  // and the full requirement checks open inline on click so the thread isn't a wall of detail.
  const [expanded, setExpanded] = useState(false);
  const card: Card | null = view.card;
  const live = view.running && view.runId
    ? progressForCall(liveProgress.value, { runId: view.runId, callId: view.callId, type: view.type, partId: view.partId, sinceTs: view.startedTs })
    : view.simId ? liveProgress.value.get(view.simId) ?? null : null;
  const simId = view.simId ?? live?.sim_id ?? null;
  const status = card?.status ?? (view.running ? "running" : view.failed ? "failed" : "done");
  const verdict = card?.verdict ?? (view.failed ? "fail" : null);
  const run = view.runId ? runs.value.get(view.runId) : undefined;
  const agent = run?.agent ?? null;
  const done = status === "done";
  const open = () => openImmersive({
    simId, ids: siblings.length ? siblings : simId ? [simId] : [],
    pending: simId ? null : view.runId ? { runId: view.runId, callId: view.callId, type: view.type, partId: view.partId, sinceTs: view.startedTs } : null,
    origin: { agent, runId: view.runId, reason: view.reason },
  });
  // fidelity and error band in every state: from the card when done, else from the type / settings
  const hon = honesty(view.type, card, { settings: view.settings });
  const metrics = card?.metrics.filter((m) => m.value !== null && m.value !== undefined && m.key !== "error_band_pct") ?? [];
  // a call the Gate holds for the human: say so (blue = needs you), not "waiting for the solver"
  const awaiting = view.running && view.runId !== null && [...approvals.value.values()].some((a) =>
    a.run_id === view.runId && a.status === "pending" && bareToolName(a.action) === bareToolName(view.tool));
  return (
    <article class="scard" data-testid="study-card" data-type={view.type} data-status={status} data-verdict={verdict ?? "none"} data-expanded={expanded ? "" : undefined} aria-label={`${typeName(view.type)} study${view.partId ? ` of ${view.partId}` : ""}`}>
      <button type="button" class="scard-head scard-head-btn" data-testid="card-expand" aria-expanded={expanded}
        aria-label={expanded ? "Hide study details" : "Show study details"} onClick={() => setExpanded((v) => !v)}>
        <span class="scard-caret" aria-hidden="true">{expanded ? "▾" : "▸"}</span>
        <span class="scard-type">{typeName(view.type)}</span>
        {view.partId && <span class="scard-part text">{view.partId}</span>}
        <span class="scard-status">
          {view.running ? <Led tone="blue" title="running" /> : <Led tone={verdictTone(verdict)} />}
          <span class={`scard-verdict${verdict ? ` tone-${verdictTone(verdict)}` : ""}`}>{view.running ? "running" : verdict ? VERDICT_TEXT[verdict] : status}</span>
        </span>
      </button>
      {/* the agent's own words (its sim_run `reason`) render as Mises markup, like its replies (A2) */}
      {view.reason && (
        <div class="scard-reason text" data-agent-text>
          {agent ? <span class="faint scard-reason-who">{agent}: </span> : null}
          <Markup source={view.reason} collapsible={false} />
        </div>
      )}
      <p class="scard-fidelity" data-testid="fidelity">
        <span class="scard-guide">GUIDE ONLY</span>
        <span class="scard-fidelity-note text" data-testid="fidelity-note">{hon.fidelity}</span>
        {hon.band && <span class="scard-band text" data-testid="error-band">{hon.band}</span>}
      </p>
      {card?.notes.map((n, i) => <p key={i} class="scard-note faint text" data-testid="card-note">{n}</p>)}
      {awaiting && <p class="scard-awaiting" data-testid="awaiting-approval" data-changing="">Waiting for your approval in the console</p>}
      {view.running && !awaiting && <PhaseBar phase={live?.phase ?? "queued"} pct={live?.pct ?? null} detail={live?.detail ?? (live ? null : "starting")} />}
      {view.failed && (
        <p class="scard-fail text" role="alert">{view.summary ?? "the study failed"}{view.hint ? <span class="faint"> · {view.hint}</span> : null}</p>
      )}
      {done && (
        <div class="scard-body">
          {simId && <Thumb simId={simId} type={view.type} />}
          <dl class="scard-metrics">
            {metrics.map((m) => (
              <div key={m.key} data-testid="metric" data-key={m.key}><dt class="label">{m.label}</dt><dd>{fmtMetric(m)}</dd></div>
            ))}
          </dl>
        </div>
      )}
      {expanded && <CheckRows checks={view.checks} />}
      {expanded && done && simId && <SmallChart simId={simId} type={view.type} onOpen={open} />}
      {!expanded && (view.checks.length > 0 || (done && simId)) && (
        <button type="button" class="scard-more faint" aria-expanded={false} onClick={() => setExpanded(true)}>
          {view.checks.length > 0 ? `Show ${view.checks.length} check${view.checks.length === 1 ? "" : "s"} and the chart` : "Show the chart"}
        </button>
      )}
      {card && card.warnings.length > 0 && (
        <ul class="scard-warn" data-testid="card-warnings" aria-label="Warnings">
          {card.warnings.map((w, i) => <li key={i}><Led tone="amber" /><span class="text dim">{w}</span></li>)}
        </ul>
      )}
      <footer class="scard-actions">
        <Button onClick={open} aria-label={`Immersive view of the ${typeName(view.type).toLowerCase()} study`} data-testid="open-immersive" disabled={!simId && !view.running}>Immersive</Button>
        {simId && view.partId && <Button variant="ghost" onClick={() => showInStudies(view.partId as string, simId)}>Open in Studies</Button>}
        {simId && <span class="faint scard-id">{simId}</span>}
      </footer>
    </article>
  );
}
