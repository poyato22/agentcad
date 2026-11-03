// Plan 13 H4: a whole-assembly study (walk / launch / drop / run mechanism) in the dock's
// Simulations stream: its name and verdict, its live phase and progress while it runs
// (`assembly_study_progress`), its one-line summary, and Open in Studies. Core text: rendered as text.
import type { JSX } from "preact";
import type { AssemblyStudySummary } from "../../api/assemblyStudies";
import { Led } from "../common/Led";
import { showAssemblyStudy } from "../pages/studies/assembly/instance";
import { verdictTone } from "../pages/studies/model";
import { assemblyProgress } from "./buildStudies";

export function AssemblyStudyCard({ s, running }: { s: AssemblyStudySummary; running: boolean }): JSX.Element {
  const p = assemblyProgress.value.get(s.study_id);
  const phase = running ? p?.phase ?? s.phase ?? "running" : s.status;
  const pct = running ? p?.pct ?? s.pct ?? 0 : 100;
  const tone = running ? "blue" : s.status === "done" ? verdictTone(s.verdict === "not_checked" ? null : s.verdict) : s.status === "failed" ? "red" : "faint";
  return (
    <div class="scard" data-testid="assembly-study-card" data-status={s.status} data-kind={s.kind}>
      <div class="scard-head">
        <Led tone={tone} />
        <b>Whole assembly · {s.name}</b>
        <span class="faint text">{running ? `${phase}${p?.detail ? ` · ${p.detail}` : ""}` : s.status === "done" ? s.verdict ?? "done" : s.status}</span>
      </div>
      {running && <div class="scard-bar"><div class="scard-bar-fill" style={{ width: `${Math.round(pct)}%` }} /></div>}
      {!running && (s.summary || s.error) && <p class="text">{s.error ?? s.summary}</p>}
      <button type="button" class="btn" data-testid="assembly-study-open" onClick={() => showAssemblyStudy(s.study_id)}>Open in Studies</button>
    </div>
  );
}
