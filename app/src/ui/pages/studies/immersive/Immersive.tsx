// The immersive view: one study, full window. While it runs you watch it go: the part with the
// phase (mesh → solve → post) and the solver's own detail, then the result fading in. Once done:
// orbit, change the channel, scrub the deformed scale, play mode shapes or the airflow, move the
// section plane and probe. A side strip shows the metrics, the requirement checks, the charts and
// which agent ran it and why. Esc closes; ⌘[ / ⌘] step through the other studies of the same
// review, run or history. The top strip keeps clear of the macOS traffic lights. Text only.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { StudyCharts, StudyRecord } from "../../../../api/studies";
import { isTrustedKey } from "../../../../lib/keys";
import { parts, runs } from "../../../../state/store";
import { Button } from "../../../common/Button";
import { Led } from "../../../common/Led";
import { useFocusTrap } from "../../../common/focusTrap";
import { CheckRows, PhaseBar } from "../../../console/studycard/StudyCard";
import { StudyChart } from "../charts/StudyChart";
import { studyData, type StudyData } from "../data";
import { isTerminal, liveProgress, progressForCall } from "../live";
import { fidelityText, fmtMetric, phaseText, typeName, VERDICT_TEXT, verdictTone } from "../model";
import { showInStudies } from "../nav";
import { solverText } from "../ResultPanel";
import { StudyView, type StudyViewDeps } from "../StudyView";
import { closeImmersive, immersive, resolvePending, stepImmersive, type ImmersiveTarget } from "./state";
import "./immersive.css";

export interface ImmersiveDeps { data?: StudyData; view?: StudyViewDeps }

export function ImmersiveRoot({ deps = {} }: { deps?: ImmersiveDeps }): JSX.Element | null {
  const t = immersive.value;
  if (!t) return null;
  return <Immersive target={t} deps={deps} />;
}

function Immersive({ target, deps }: { target: ImmersiveTarget; deps: ImmersiveDeps }): JSX.Element {
  const data = deps.data ?? studyData;
  const root = useRef<HTMLDivElement>(null);
  useFocusTrap(root, () => root.current?.querySelector<HTMLElement>("[data-close]") ?? null);
  const [rec, setRec] = useState<StudyRecord | null>(null);
  const [charts, setCharts] = useState<StudyCharts | null>(null);
  const [error, setError] = useState<string | null>(null);

  // a study still starting: its sim_id arrives with its first sim_progress
  const pend = target.pending ?? null;
  const pendLive = pend ? progressForCall(liveProgress.value, pend) : null;
  useEffect(() => { if (target.simId === null && pendLive) resolvePending(pendLive.sim_id); }, [pendLive?.sim_id]);

  const simId = target.simId;
  const live = simId ? liveProgress.value.get(simId) ?? null : pendLive;
  const liveDone = live ? isTerminal(live) : false;
  useEffect(() => {
    if (!simId) { setRec(null); return; }
    let on = true;
    setError(null);
    if (liveDone) data.forget(simId);
    void data.record(simId).then((r) => {
      if (!on) return;
      setRec(r);
      if (r.status === "done") void data.charts(simId).then((c) => { if (on) setCharts(c); }, () => { if (on) setCharts(null); });
      else setCharts(null);
    }, (e: unknown) => { if (on) setError(e instanceof Error ? e.message : String(e)); });
    return () => { on = false; };
  }, [simId, liveDone]);

  // keys: Esc closes; ⌘[ / ⌘] step (real key presses only)
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // a face pick or an open select handles its own Escape first
        if (e.defaultPrevented) return;
        e.preventDefault(); closeImmersive(); return;
      }
      if (e.metaKey && (e.key === "[" || e.key === "]") && isTrustedKey(e)) { e.preventDefault(); stepImmersive(e.key === "]" ? 1 : -1); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  const running = rec ? rec.status === "running" : true;
  const shownRec = rec && rec.status === "done" && !(live && !isTerminal(live)) ? rec : null;
  const card = rec?.card ?? null;
  const type = rec?.type ?? pend?.type ?? null;
  const partId = rec?.part_id ?? pend?.partId ?? null;
  const part = parts.value.find((p) => p.part_id === partId) ?? null;
  const runId = rec?.run_id ?? pend?.runId ?? target.origin?.runId ?? null;
  const agent = (runId ? runs.value.get(runId)?.agent : null) ?? target.origin?.agent ?? null;
  const reason = rec?.reason ?? target.origin?.reason ?? null;
  const verdict = card?.verdict ?? null;
  const idx = simId ? target.ids.indexOf(simId) : -1;
  const phase = live && !isTerminal(live) ? live.phase : rec?.phase ?? null;
  const caption = running || (live && !isTerminal(live)) ? `${typeName(type)} · ${phaseText(phase)}${live?.detail ? ` · ${live.detail}` : ""}` : null;

  return (
    <div class="imm" ref={root} role="dialog" aria-modal="true" aria-label={`Immersive view · ${typeName(type)} study${partId ? ` of ${partId}` : ""}`} data-testid="immersive" data-sim={simId ?? ""} data-status={rec?.status ?? "starting"}>
      <header class="imm-top">
        <span class="imm-title">{typeName(type)}</span>
        {partId && <span class="imm-part text">{partId}</span>}
        <span class="imm-verdict">
          {running ? <Led tone="blue" title="running" /> : <Led tone={verdictTone(verdict)} />}
          <span class={`sk-verdict${verdict ? ` tone-${verdictTone(verdict)}` : ""}`}>{running ? "running" : verdict ? VERDICT_TEXT[verdict] : rec?.status ?? ""}</span>
        </span>
        <span class="imm-guide"><span class="sk-guide">GUIDE ONLY</span>{card && <b class="text" data-testid="fidelity-note">{fidelityText(card)}</b>}</span>
        <span class="imm-nav">
          {target.ids.length > 1 && (
            <>
              <Button variant="ghost" onClick={() => stepImmersive(-1)} aria-label="Previous study (⌘[)" data-testid="imm-prev">⌘[</Button>
              <span class="faint" data-testid="imm-pos">{idx + 1} / {target.ids.length}</span>
              <Button variant="ghost" onClick={() => stepImmersive(1)} aria-label="Next study (⌘])" data-testid="imm-next">⌘]</Button>
            </>
          )}
          <Button variant="ghost" onClick={closeImmersive} aria-label="Close the immersive view (Esc)" data-close data-testid="imm-close">Esc ✕</Button>
        </span>
      </header>
      <div class="imm-body">
        <div class="imm-stage">
          {(running || (live && !isTerminal(live))) && (
            <div class="imm-live" data-testid="imm-live"><PhaseBar phase={phase ?? "queued"} pct={live?.pct ?? rec?.pct ?? null} detail={live?.detail ?? null} /></div>
          )}
          <StudyView partId={partId} mtime={part?.mtime} record={shownRec} faces={part?.metrics?.face_list ?? []} immersive liveCaption={caption} deps={deps.view} />
        </div>
        <aside class="imm-side" aria-label="Study details">
          {error && <p class="tone-red text" role="alert">Could not open the study: {error}</p>}
          {(agent || reason) && (
            <section class="imm-sec" data-testid="imm-why">
              <h2 class="label">Why</h2>
              <p class="text">{agent ? <b>{agent}</b> : null}{agent && reason ? ": " : ""}{reason}</p>
            </section>
          )}
          {card?.notes.map((n, i) => <p key={i} class="faint text">{n}</p>)}
          {card && card.metrics.length > 0 && rec?.status === "done" && (
            <dl class="imm-metrics" data-testid="imm-metrics">
              {card.metrics.filter((m) => m.value !== null && m.value !== undefined).map((m) => (
                <div key={m.key} data-key={m.key}><dt class="label">{m.label}</dt><dd>{fmtMetric(m)}</dd></div>
              ))}
            </dl>
          )}
          {card && card.checks.length > 0 && (
            <section class="imm-sec"><h2 class="label">Requirements</h2><CheckRows checks={card.checks} /></section>
          )}
          {rec && rec.status !== "done" && rec.status !== "running" && (
            <p class="tone-red text" role="alert">{rec.status}{rec.error && rec.error !== "cancelled" ? `: ${rec.error}` : ""}{rec.hint ? ` · ${rec.hint}` : ""}</p>
          )}
          {charts?.charts.map((c) => <div key={c.id} class="imm-chart"><StudyChart chart={c} width={296} height={150} /></div>)}
          {rec?.status === "done" && <p class="faint text imm-solver">{solverText(rec)}</p>}
          {rec && rec.warnings.length > 0 && (
            <ul class="st-warnings">{rec.warnings.map((w, i) => <li key={i}><Led tone="amber" /><span class="text">{w}</span></li>)}</ul>
          )}
          {simId && partId && <Button variant="ghost" onClick={() => { closeImmersive(); showInStudies(partId, simId); }}>Open in Studies</Button>}
          {simId && <p class="faint imm-id">{simId}</p>}
        </aside>
      </div>
    </div>
  );
}
