// One design study: status and live progress, the mass-vs-SF scatter with its Pareto front, the
// previewed variant (its variables, metrics and every requirement checked against it) with
// "Use this variant" (study_apply, after a confirm that shows what changes on the live part),
// and the sortable iterations table. Results are coarse-mesh screening; the winner's fine-mesh
// confirmation is shown when the core ran one. Every stress number is a guide only.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { Requirement, Study, StudyIteration } from "../../api/spec";
import { partLabel } from "../../lib/names";
import { parts } from "../../state/store";
import { Button } from "../common/Button";
import { Led, type LedTone } from "../common/Led";
import type { SpecController } from "./controller";
import { fmtMeasured, fmtNum, iterStatus, KIND_LABEL, nextSort, sortIterations, STATUS_TONE, STATUS_WORD, type SortKey, type SortState } from "./model";
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
  const feasible = study.iterations.filter((i) => i.feasible).length;

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
        <span>{study.geometry_only ? "mass/geometry only (no stress solver)" : `${study.solver ?? "solver"} · coarse mesh in the loop`}</span>
        {study.confirmation && (
          <span class={study.confirmation.confirmed ? "tone-green" : "tone-red"}>
            {`#${study.confirmation.n} fine mesh ${study.confirmation.confirmed ? "held" : "did not hold"}`}
          </span>
        )}
        <span class="sp-guide">guide only</span>
      </div>
      <div class="spv-grid">
        <Scatter iterations={study.iterations} requirements={requirements} bestId={study.best_id} selected={sel} onSelect={(x) => c.selectIteration(x)} />
        {it ? <Variant c={c} study={study} it={it} requirements={requirements} /> : <p class="faint spv-none">Pick a variant on the chart or in the table.</p>}
      </div>
      <IterTable study={study} selected={sel} onSelect={(x) => c.selectIteration(x)} />
    </section>
  );
}

function Variant({ c, study, it, requirements }: { c: SpecController; study: Study; it: StudyIteration; requirements: readonly Requirement[] }): JSX.Element {
  const [confirm, setConfirm] = useState(false);
  const live = parts.value.find((p) => p.part_id === study.part_id) ?? null;
  const busy = c.applying.value;
  const applied = c.applied.value;
  const isApplied = applied?.studyId === study.id && applied.n === it.n;
  const changes = Object.entries(it.vars).map(([k, v]) => ({ k, from: live?.vars[k], to: v }));
  const running = study.status === "running";
  const m = it.metrics;

  const doApply = async () => {
    const ok = await c.apply(study, it.n, { allowInfeasible: !it.feasible });
    if (ok) setConfirm(false);
  };

  return (
    <div class="spv-var" data-variant={it.n}>
      <div class="spv-var-head">
        <span class="spv-title">{`Variant #${it.n}`}</span>
        <span class={it.feasible ? "tone-green" : "tone-red"}>{it.feasible ? "meets the spec" : "fails the spec"}</span>
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
                  <td class="text">{r.load_case_id ? `${KIND_LABEL[r.kind]} · ${r.load_case_id}` : KIND_LABEL[r.kind]}</td>
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
            <Button variant={it.feasible ? "ink" : "danger"} disabled={running || busy !== null} onClick={() => setConfirm(true)}
              title={running ? "Wait for the study to finish" : undefined}>
              {it.feasible ? "Use this variant" : "Use anyway"}
            </Button>
            {isApplied && <span class="tone-green" data-applied>Applied.</span>}
          </div>
        )
        : (
          <div class="spv-confirm" role="group" aria-label="Confirm variant">
            <div class="text">{`Set ${partLabel(study.part_id)}:`}</div>
            <ul class="spv-diff">
              {changes.map((x) => <li key={x.k}><span>{x.k}</span><span class="faint">{x.from !== undefined ? fmtNum(x.from) : "?"}</span><span aria-hidden="true">→</span><span>{fmtNum(x.to)}</span></li>)}
            </ul>
            {!it.feasible && <p class="tone-red text">This variant fails the spec.</p>}
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

function IterTable({ study, selected, onSelect }: { study: Study; selected: number | null; onSelect: (n: number) => void }): JSX.Element {
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
                      <span class={it.feasible ? "tone-green" : "tone-red"}>{it.feasible ? "pass" : "fail"}</span>
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
