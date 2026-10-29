// A study inline in the thread (and on the Studies page): the type and status, a live phase bar
// while it runs, the fidelity note up front ("low fidelity · ±40% (validated)"), key metrics,
// the requirement checks with pass / warn / fail, a small chart, a thumbnail of the colored
// result, and the agent's one-line reason. "Immersive" opens the full-window 3D view; a
// double-click on the chart does too. Every string from the core renders as text.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { StudyCard as Card, StudyChart as Chart, StudyCheck } from "../../../api/studies";
import { runs } from "../../../state/store";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { cardChart } from "../../pages/studies/charts/chartModel";
import { StudyChart } from "../../pages/studies/charts/StudyChart";
import { studyData } from "../../pages/studies/data";
import { openImmersive } from "../../pages/studies/immersive/state";
import { isTerminal, liveProgress, progressForCall } from "../../pages/studies/live";
import { fidelityText, fmtMetric, fmtValue, phaseText, PHASES, progressFraction, typeName, VERDICT_TEXT, verdictTone } from "../../pages/studies/model";
import { showInStudies } from "../../pages/studies/nav";
import { cachedThumbnail, thumbnail } from "../../pages/studies/scene/thumbnails";
import { kindText, type StudyCallView } from "./cardModel";
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
    <div class="sk-phase" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(frac * 100)}
      aria-valuetext={`${phaseText(phase)}${detail ? ` · ${detail}` : ""}`} data-testid="phase-bar" data-phase={phase ?? "starting"}>
      <div class="sk-phase-steps" aria-hidden="true">
        {PHASES.slice(1).map((p, i) => (
          <span key={p} class="sk-phase-step" data-state={at > i + 1 || phase === "done" ? "done" : at === i + 1 ? "now" : "todo"}>{phaseText(p)}</span>
        ))}
      </div>
      <div class="sk-bar"><div class="sk-bar-fill" style={{ width: `${Math.round(frac * 100)}%` }} /></div>
      <div class="sk-phase-detail faint text">{cancelled ? "cancelled" : detail ?? phaseText(phase)}</div>
    </div>
  );
}

export function CheckRows({ checks, onOpen }: { checks: readonly StudyCheck[]; onOpen?: (simId: string) => void }): JSX.Element | null {
  if (checks.length === 0) return null;
  return (
    <ul class="sk-checks" aria-label="Requirement checks">
      {checks.map((c, i) => (
        <li key={`${c.requirement_id ?? i}`} data-testid="check" data-status={c.status}>
          <Led tone={verdictTone(c.status)} />
          <span class="sk-check-what text">{c.requirement_id ? `${c.requirement_id} · ` : ""}{kindText(c.kind)} {fmtValue(c.target, c.unit)}</span>
          <span class="sk-check-got">{fmtValue(c.measured, c.unit)}</span>
          <span class={`sk-verdict tone-${verdictTone(c.status)}`}>{VERDICT_TEXT[c.status]}</span>
          {c.note && <span class="sk-check-note faint text">{c.note}</span>}
          {onOpen && c.sim_id && <button type="button" class="sk-link" onClick={() => onOpen(c.sim_id as string)}>view</button>}
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
    <span class="sk-thumb" ref={ref as preact.RefObject<HTMLSpanElement>} data-testid="study-thumb" data-state={url ? "loaded" : url === null ? "none" : "loading"}>
      {url ? <img src={url} alt={`${typeName(type)} result, colored`} width={56} height={56} /> : <span class="sk-thumb-empty" aria-hidden="true" />}
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
    <div class="sk-chart" ref={ref as preact.RefObject<HTMLDivElement>} data-testid="card-chart" data-chart={chart?.id ?? ""}>
      {chart && <><div class="sk-chart-title faint">{chart.title}</div><StudyChart chart={chart} compact height={84} onOpen={onOpen} /></>}
    </div>
  );
}

export interface StudyCardProps {
  view: StudyCallView;
  /** Other studies of the same set (⌘[ / ⌘] in the immersive view). */
  siblings?: readonly string[];
}

export function StudyCard({ view, siblings = [] }: StudyCardProps): JSX.Element {
  const card: Card | null = view.card;
  const live = view.running && view.runId
    ? progressForCall(liveProgress.value, { runId: view.runId, type: view.type, partId: view.partId, sinceTs: view.startedTs })
    : view.simId ? liveProgress.value.get(view.simId) ?? null : null;
  const simId = view.simId ?? live?.sim_id ?? null;
  const status = card?.status ?? (view.running ? "running" : view.failed ? "failed" : "done");
  const verdict = card?.verdict ?? (view.failed ? "fail" : null);
  const run = view.runId ? runs.value.get(view.runId) : undefined;
  const agent = run?.agent ?? null;
  const done = status === "done";
  const open = () => openImmersive({
    simId, ids: siblings.length ? siblings : simId ? [simId] : [],
    pending: simId ? null : view.runId ? { runId: view.runId, type: view.type, partId: view.partId, sinceTs: view.startedTs } : null,
    origin: { agent, runId: view.runId, reason: view.reason },
  });
  const fidelity = card ? fidelityText(card) : null;
  const metrics = card?.metrics.filter((m) => m.value !== null && m.value !== undefined && m.key !== "error_band_pct") ?? [];
  const band = card?.metrics.find((m) => m.key === "error_band_pct");
  return (
    <article class="sk" data-testid="study-card" data-type={view.type} data-status={status} data-verdict={verdict ?? "none"} aria-label={`${typeName(view.type)} study${view.partId ? ` of ${view.partId}` : ""}`}>
      <header class="sk-head">
        <span class="sk-type">{typeName(view.type)}</span>
        {view.partId && <span class="sk-part text">{view.partId}</span>}
        <span class="sk-status">
          {view.running ? <Led tone="blue" title="running" /> : <Led tone={verdictTone(verdict)} />}
          <span class={`sk-verdict${verdict ? ` tone-${verdictTone(verdict)}` : ""}`}>{view.running ? "running" : verdict ? VERDICT_TEXT[verdict] : status}</span>
        </span>
      </header>
      {view.reason && <p class="sk-reason text">{agent ? <span class="faint">{agent}: </span> : null}{view.reason}</p>}
      <p class="sk-fidelity" data-testid="fidelity">
        <span class="sk-guide">GUIDE ONLY</span>
        {fidelity && <span class="sk-fidelity-note text" data-testid="fidelity-note">{fidelity}</span>}
        {!card?.fidelity_note && band && typeof band.value === "number" && <span class="faint" data-testid="error-band">error band {fmtMetric(band)}</span>}
      </p>
      {card?.notes.map((n, i) => <p key={i} class="sk-note faint text" data-testid="card-note">{n}</p>)}
      {view.running && <PhaseBar phase={live?.phase ?? "queued"} pct={live?.pct ?? null} detail={live?.detail ?? null} />}
      {!view.running && live && !isTerminal(live) && <PhaseBar phase={live.phase} pct={live.pct} detail={live.detail} />}
      {view.failed && (
        <p class="sk-fail text" role="alert">{view.summary ?? "the study failed"}{view.hint ? <span class="faint"> · {view.hint}</span> : null}</p>
      )}
      {done && (
        <div class="sk-body">
          {simId && <Thumb simId={simId} type={view.type} />}
          <dl class="sk-metrics">
            {metrics.map((m) => (
              <div key={m.key} data-testid="metric" data-key={m.key}><dt class="label">{m.label}</dt><dd>{fmtMetric(m)}</dd></div>
            ))}
          </dl>
        </div>
      )}
      <CheckRows checks={view.checks} />
      {done && simId && <SmallChart simId={simId} type={view.type} onOpen={open} />}
      {card && card.warnings.length > 0 && (
        <details class="sk-warn">
          <summary><Led tone="amber" /> {card.warnings.length} warning{card.warnings.length === 1 ? "" : "s"}</summary>
          <ul>{card.warnings.map((w, i) => <li key={i} class="text dim">{w}</li>)}</ul>
        </details>
      )}
      <footer class="sk-actions">
        <Button onClick={open} aria-label={`Immersive view of the ${typeName(view.type).toLowerCase()} study`} data-testid="open-immersive" disabled={!simId && !view.running}>Immersive</Button>
        {simId && view.partId && <Button variant="ghost" onClick={() => showInStudies(view.partId as string, simId)}>Open in Studies</Button>}
        {simId && <span class="faint sk-id">{simId}</span>}
      </footer>
    </article>
  );
}
