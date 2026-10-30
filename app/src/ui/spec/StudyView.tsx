// One design study: status and live progress, the mass-vs-SF scatter with its Pareto front, the
// previewed variant (its variables, metrics and every requirement checked against it) with
// "Use this variant" (study_apply, after a confirm that shows what changes on the live part),
// and the sortable iterations table. Results are coarse-mesh screening; the winner's fine-mesh
// confirmation is shown when the core ran one. Every stress number is a guide only.
// "Meets the spec" is always judged against the spec as it is NOW (meetsSpec, the core's rule):
// the study's stored flag was against the spec when it ran, so a variant whose verdict changed
// says "spec changed since this study".
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { Requirement, Study, StudyIteration } from "../../api/spec";
import { partLabel } from "../../lib/names";
import { parts } from "../../state/store";
import { Button } from "../common/Button";
import { Led, type LedTone } from "../common/Led";
import { openImmersive } from "../pages/studies/immersive/state";
import type { SpecController } from "./controller";
import { fmtMeasured, fmtNum, iterStatus, meetsSpec, KIND_LABEL, KIND_SHORT, nextSort, sortIterations, STATUS_TONE, STATUS_WORD, type SortKey, type SortState } from "./model";
import { Scatter } from "./Scatter";

const STATUS: Record<Study["status"], { word: string; tone: LedTone }> = {
  running: { word: "running", tone: "blue" }, done: { word: "done", tone: "faint" }, failed: { word: "failed", tone: "red" },
  cancelled: { word: "cancelled", tone: "faint" }, budget_exhausted: { word: "out of budget", tone: "amber" },
  interrupted: { word: "interrupted", tone: "amber" },
};

export function objectiveWords(o: string): string {
  if (o === "min_mass") return "lightest";
  if (o === "max:min_sf") return "strongest";
  if (o === "min:max_disp_mm") return "stiffest";
  if (o.startsWith("min:")) return `lowest ${o.slice(4)}`;
  if (o.startsWith("max:")) return `highest ${o.slice(4)}`;
  return o;
}

export function StudyStatus({ study, total }: { study: Study; total?: number | undefined }): JSX.Element {
  const s = STATUS[study.status];
  const n = study.iterations.length;
  return (
    <span class="spv-status" data-study-status={study.status}>
      <Led tone={s.tone} />
      <span>{s.word}</span>
      <span class="faint">{total ? `${n}/${total}` : `${n} eval${n === 1 ? "" : "s"}`}</span>
    </span>
  );
}

export function StudyView({ c, study, requirements, compact = false }: { c: SpecController; study: Study; requirements: readonly Requirement[]; compact?: boolean }): JSX.Element {
  const live = c.progress.value.get(study.id);
  const total = live?.total;
  const n = study.iterations.length;
  const pct = total ? Math.min(100, Math.round((Math.max(live?.n ?? 0, n) / total) * 100)) : 0;
  const sel = c.selectedN.value ?? study.best_id;
  const it = study.iterations.find((i) => i.n === sel) ?? null;
  const meets = new Map(study.iterations.map((i) => [i.n, meetsSpec(requirements, i, study.geometry_only)]));
  const meetsOf = (i: StudyIteration) => meets.get(i.n) ?? false;
  const feasible = study.iterations.filter(meetsOf).length;
  const changed = study.status !== "running" && study.iterations.some((i) => meetsOf(i) !== i.feasible);

  return (
    <section class={`spv${compact ? " spv-compact" : ""}`} data-study={study.id} aria-label={`Design study of ${partLabel(study.part_id)}`}>
      <header class="spv-head">
        <span class="spv-title">{partLabel(study.part_id)}</span>
        <span class="faint">{`${objectiveWords(study.objective)} · ${study.variables.map((v) => `${v.name} ${fmtNum(v.lo)}–${fmtNum(v.hi)}`).join(", ")}`}</span>
        <StudyStatus study={study} total={total} />
      </header>
      {study.status === "running" && total !== undefined && (
        <div class="spv-bar" role="progressbar" aria-label="Evaluations" aria-valuemin={0} aria-valuemax={total} aria-valuenow={Math.max(live?.n ?? 0, n)}>
          <div class="spv-bar-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
      <div class="spv-facts faint">
        <span>{`${feasible}/${n} meet the spec`}</span>
        {changed && <span class="sp-tag sp-tag-est" data-spec-changed title="the spec was edited after this study ran; verdicts are against the current spec">spec changed since this study</span>}
        <span>{study.geometry_only ? "mass/geometry only (no stress solver)" : `${study.solver ?? "solver"} · coarse mesh in the loop`}</span>
        {study.confirmation && (
          <span class={study.confirmation.confirmed ? "tone-green" : "tone-red"}>
            {`#${study.confirmation.n} fine mesh ${study.confirmation.confirmed ? "held" : "did not hold"}`}
          </span>
        )}
        <span class="sp-guide">guide only</span>
      </div>
      <div class="spv-grid">
        <Scatter iterations={study.iterations} requirements={requirements} bestId={study.best_id} selected={sel} onSelect={(x) => c.selectIteration(x)} meets={meetsOf}
          onOpen={(x) => { const v = study.iterations.find((i) => i.n === x); if (v) openVariant(study, v); }} />
        {it ? <Variant key={`${study.id}:${it.n}`} c={c} study={study} it={it} requirements={requirements} meets={meetsOf(it)} /> : <p class="faint spv-none">Pick a variant on the chart or in the table.</p>}
      </div>
      <IterTable study={study} selected={sel} onSelect={(x) => c.selectIteration(x)} meets={meetsOf} />
    </section>
  );
}

function Variant({ c, study, it, requirements, meets }: { c: SpecController; study: Study; it: StudyIteration; requirements: readonly Requirement[]; meets: boolean }): JSX.Element {
  const [confirm, setConfirm] = useState(false);
  const live = parts.value.find((p) => p.part_id === study.part_id) ?? null;
  const busy = c.applying.value;
  const applied = c.applied.value;
  const isApplied = applied?.studyId === study.id && applied.n === it.n;
  const changes = Object.entries(it.vars).map(([k, v]) => ({ k, from: live?.vars[k], to: v }));
  const running = study.status === "running";
  const m = it.metrics;

  const doApply = async () => {
    const ok = await c.apply(study, it.n, { allowInfeasible: !meets });
    if (ok) setConfirm(false);
  };

  return (
    <div class="spv-var" data-variant={it.n}>
      <div class="spv-var-head">
        <span class="spv-title">{`Variant #${it.n}`}</span>
        <span class={meets ? "tone-green" : "tone-red"} data-meets={meets ? "" : undefined}>{meets ? "meets the spec" : "fails the spec"}</span>
        {meets !== it.feasible && <span class="sp-tag sp-tag-est" data-spec-changed title={`when the study ran it ${it.feasible ? "met" : "failed"} the spec`}>spec changed since this study</span>}
        {it.pareto && <span class="sp-tag">Pareto</span>}
        {it.n === study.best_id && <span class="sp-tag">best</span>}
      </div>
      <dl class="spv-kv">
        {Object.entries(it.vars).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{fmtNum(v)}</dd></div>)}
        <div><dt>mass</dt><dd>{m.mass_g !== null ? `${fmtNum(m.mass_g)} g` : "—"}</dd></div>
        <div><dt>min SF</dt><dd>{m.min_sf !== null ? m.min_sf.toFixed(2) : "—"}</dd></div>
        <div><dt>max disp</dt><dd>{m.max_disp_mm !== null ? `${fmtNum(m.max_disp_mm)} mm` : "—"}</dd></div>
      </dl>
      {m.build_error && <p class="tone-red text spv-err">{`Build failed: ${m.build_error}`}</p>}
      {requirements.length > 0 && (
        <table class="sp-table spv-checks" aria-label={`Requirements on variant #${it.n}`}>
          <tbody>
            {requirements.map((r) => {
              const s = iterStatus(r, it);
              return (
                <tr key={r.id} data-req={r.id} data-status={s.status}>
                  <td class="sp-st"><Led tone={STATUS_TONE[s.status]} /><span class={`sp-st-w tone-${STATUS_TONE[s.status]}`}>{STATUS_WORD[s.status]}</span></td>
                  <td class="spv-req" title={`${KIND_LABEL[r.kind]} ${r.id}`}>{r.load_case_id ? `${KIND_SHORT[r.kind]} · ${r.load_case_id}` : KIND_SHORT[r.kind]}</td>
                  <td class="sp-num">{fmtMeasured(r.kind, s.measured)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {!confirm
        ? (
          <div class="spv-actions">
            <Button variant={meets ? "ink" : "danger"} disabled={running || busy !== null} onClick={() => setConfirm(true)}
              title={running ? "Wait for the study to finish" : undefined}>
              {meets ? "Use this variant" : "Use anyway"}
            </Button>
            {variantSims(it).length > 0 && (
              <Button onClick={() => openVariant(study, it)} title="Open this variant's stress result (immersive view)">View result</Button>
            )}
            {isApplied && <span class="tone-green" data-applied>Applied.</span>}
          </div>
        )
        : (
          <div class="spv-confirm" role="group" aria-label="Confirm variant">
            <div class="text">{`Set ${partLabel(study.part_id)}:`}</div>
            <ul class="spv-diff">
              {changes.map((x) => <li key={x.k}><span>{x.k}</span><span class="faint">{x.from !== undefined ? fmtNum(x.from) : "?"}</span><span aria-hidden="true">→</span><span>{fmtNum(x.to)}</span></li>)}
            </ul>
            {!meets && <p class="tone-red text">This variant fails the spec.</p>}
            <div class="spv-actions">
              <Button variant="primary" disabled={busy !== null} onClick={() => { void doApply(); }}>{busy ? "Applying…" : "Apply and rebuild"}</Button>
              <Button variant="ghost" disabled={busy !== null} onClick={() => setConfirm(false)}>Cancel</Button>
            </div>
          </div>
        )}
      {c.applyError.value && <p class="tone-red text" role="alert" data-apply-error>{c.applyError.value}</p>}
    </div>
  );
}

function IterTable({ study, selected, onSelect, meets }: { study: Study; selected: number | null; onSelect: (n: number) => void; meets: (it: StudyIteration) => boolean }): JSX.Element {
  const [sort, setSort] = useState<SortState>({ key: "n", dir: "asc" });
  const rows = sortIterations(study.iterations, sort);
  const cols: { key: SortKey; label: string; num: boolean }[] = [
    { key: "n", label: "#", num: true },
    ...study.variables.map((v) => ({ key: `var:${v.name}` as SortKey, label: v.name, num: true })),
    { key: "mass_g", label: "mass g", num: true }, { key: "min_sf", label: "min SF", num: true },
    { key: "max_disp_mm", label: "disp mm", num: true }, { key: "feasible", label: "spec", num: false },
  ];
  const cell = (it: StudyIteration, key: SortKey): string => {
    if (key === "n") return String(it.n);
    if (key.startsWith("var:")) { const v = it.vars[key.slice(4)]; return v === undefined ? "—" : fmtNum(v); }
    if (key === "feasible") return "";
    const v = it.metrics[key as "mass_g" | "min_sf" | "max_disp_mm"];
    return v === null ? "—" : key === "min_sf" ? v.toFixed(2) : fmtNum(v);
  };
  return (
    <div class="spv-tablewrap">
      <table class="sp-table spv-iters" aria-label="Iterations">
        <thead>
          <tr>
            {cols.map((col) => (
              <th key={col.key} class={col.num ? "sp-num" : undefined} aria-sort={sort.key === col.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                <button type="button" class="sp-sort" onClick={() => setSort(nextSort(sort, col.key))}>
                  {col.label}{sort.key === col.key ? (sort.dir === "asc" ? " ↑" : " ↓") : ""}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((it) => (
            <tr key={it.n} data-iter={it.n} aria-selected={selected === it.n} class={selected === it.n ? "sp-sel" : undefined}
              tabIndex={0} onClick={() => onSelect(it.n)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(it.n); } }}>
              {cols.map((col) => (
                col.key === "feasible"
                  ? (
                    <td key={col.key}>
                      <span class={meets(it) ? "tone-green" : "tone-red"}>{meets(it) ? "pass" : "fail"}</span>
                      {meets(it) !== it.feasible && <span class="sp-tag" title="the verdict changed with the spec since this study">Δ</span>}
                      {it.pareto && <span class="sp-tag">P</span>}
                      {it.n === study.best_id && <span class="sp-tag">best</span>}
                    </td>
                  )
                  : <td key={col.key} class="sp-num">{cell(it, col.key)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The stress studies a variant ran (one per load case); kept for the best and Pareto variants. */
export function variantSims(it: StudyIteration): string[] {
  return Object.values(it.metrics.sim_ids_by_case ?? {}).filter((x): x is string => typeof x === "string" && x !== "");
}

/** A design-study point → A8's immersive view on its stress result(s), stepping across its load cases. */
export function openVariant(study: Study, it: StudyIteration): void {
  const ids = variantSims(it);
  if (ids.length === 0) return;
  openImmersive({ simId: ids[0], ids, origin: { agent: "design study", runId: study.run_id, reason: `${partLabel(study.part_id)} · variant #${it.n}` } });
}
