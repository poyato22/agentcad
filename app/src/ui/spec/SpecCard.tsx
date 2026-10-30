// Spec card: the build's goal, its requirements (status, what, target, current value on the
// checked part, load case, priority, who set it) and its load cases (faces, force, "estimate"
// on impact cases). "Edit" turns the rows into inputs; "Save" PUTs the spec, which the core
// validates exactly like the agent's spec_set. Rows the user edits become the user's.
// An edit is saved against the spec version it started from: if the agent's spec_set lands while
// the editor is open, the card says so and offers Reload or "Save mine anyway" (never a silent
// overwrite; the core also refuses a stale base with 409).
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import { REQUIREMENT_KINDS, SPEC_DIRECTIONS, type RequirementKind, type Spec, type SpecCheck } from "../../api/spec";
import { SIM_FACE_PRESETS } from "../../api/sims";
import { partLabel } from "../../lib/names";
import { Button } from "../common/Button";
import { Led } from "../common/Led";
import { Markup } from "../markup/render";
import type { SaveResult } from "./controller";
import {
  draftFromSpec, fmtMeasured, fmtTarget, KIND_LABEL, newReqId, overallTone, priorityDots, reqViews, specFromDraft, STATUS_TONE, STATUS_WORD,
  tally, tallyText, type CaseDraft, type ReqDraft, type SpecDraft,
} from "./model";

export interface SpecCardProps {
  spec: Spec;
  check: SpecCheck | null;
  checkError?: string | null;
  /** The spec's `updated_at` (the version an edit starts from). */
  updatedAt?: number | null;
  /** `base` undefined: save regardless of what changed meanwhile. */
  onSave: (spec: Spec, base?: number | null) => Promise<SaveResult>;
  /** Fewer columns (the console is narrow). */
  compact?: boolean;
}

export function SpecCard(props: SpecCardProps): JSX.Element {
  const [draft, setDraft] = useState<SpecDraft | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  /** The updated_at the open edit started from. */
  const [base, setBase] = useState<number | null>(null);
  const [refused, setRefused] = useState(false);
  const { spec, check } = props;
  const views = reqViews(spec, check?.requirements ?? null);
  const t = tally(views);
  const overall = check?.overall ?? "not_checked";
  const now = props.updatedAt ?? null;
  const conflict = draft !== null && (refused || base !== now);

  const startEdit = () => { setDraft(draftFromSpec(spec)); setBase(now); setRefused(false); setErrors([]); };
  const save = async (force: boolean) => {
    if (!draft) return;
    const r = specFromDraft(draft);
    if (!r.ok) { setErrors(r.errors); return; }
    setSaving(true);
    const res = await props.onSave(r.spec, force ? undefined : base);
    setSaving(false);
    if (res.ok) { setDraft(null); setErrors([]); setRefused(false); return; }
    if (res.conflict) setRefused(true);
    else setErrors([res.error]);
  };

  return (
    <section class={`sp-card${props.compact ? " sp-compact" : ""}`} data-spec-card aria-label="Spec">
      <header class="sp-head">
        <Led tone={overallTone(overall, t)} title={overall === "pass" && t.not_checked > 0
          ? `pass on ${t.pass + t.warn} of ${views.length} checked; ${t.not_checked} not checked`
          : `overall: ${STATUS_WORD[overall]}`} />
        {!props.compact && <span class="sp-title">Spec</span>}
        {!props.compact && <span class="sp-tally" data-tally>{tallyText(t)}</span>}
        {check && <span class="faint">{`on ${partLabel(check.part_id)}`}</span>}
        <span class="sp-head-r">
          {draft
            ? (
              <>
                <Button variant="ink" disabled={saving || conflict} onClick={() => { void save(false); }}>{saving ? "Saving…" : "Save"}</Button>
                <Button variant="ghost" disabled={saving} onClick={() => { setDraft(null); setErrors([]); }}>Cancel</Button>
              </>
            )
            : <Button variant="ghost" onClick={startEdit}>Edit</Button>}
        </span>
      </header>
      {draft
        ? <input class="sp-goal-in" aria-label="Goal" value={draft.goal} onInput={(e) => setDraft({ ...draft, goal: (e.currentTarget as HTMLInputElement).value })} />
        : spec.goal && <div class="sp-goal text" data-agent-text><Markup source={spec.goal} collapsible={false} /></div>}
      {conflict && (
        <div class="sp-conflict" role="alert" data-spec-conflict>
          <span class="text">The spec changed while you were editing (the agent updated it).</span>
          <Button variant="ghost" disabled={saving} onClick={startEdit}>Reload</Button>
          <Button variant="danger" disabled={saving} onClick={() => { void save(true); }}>Save mine anyway</Button>
        </div>
      )}
      {props.checkError && <p class="tone-red text sp-err">{`Could not check the part: ${props.checkError}`}</p>}
      {errors.length > 0 && (
        <ul class="sp-errors tone-red" role="alert" data-spec-errors>{errors.map((e) => <li key={e} class="text">{e}</li>)}</ul>
      )}
      {draft ? <ReqEditor draft={draft} onChange={setDraft} /> : (
        views.length === 0
          ? <p class="faint">No requirements yet. The engineer agent writes them from the interview, or add them with Edit.</p>
          : (
            <table class="sp-table sp-reqs" aria-label="Requirements">
              <thead>
                <tr>
                  <th>status</th><th>requirement</th><th class="sp-num">target</th><th class="sp-num">current</th>
                  {!props.compact && <th>case</th>}<th>priority</th>{!props.compact && <th>by</th>}
                </tr>
              </thead>
              <tbody>
                {views.map(({ req, row, status }) => (
                  <tr key={req.id} data-req={req.id} data-status={status} title={row?.detail || undefined}>
                    <td class="sp-st"><Led tone={STATUS_TONE[status]} /><span class={`sp-st-w tone-${STATUS_TONE[status]}`}>{STATUS_WORD[status]}</span></td>
                    <td>
                      <span class="text">{KIND_LABEL[req.kind]}</span>
                      {!props.compact && <span class="faint text sp-id">{` ${req.id}`}</span>}
                      {row?.guide && !props.compact && <span class="sp-tag" title="FEM is a guide, not a guarantee">guide</span>}
                      {row?.estimate && <span class="sp-tag sp-tag-est" title="measured against an estimated (impact) load">estimate</span>}
                    </td>
                    <td class="sp-num">{fmtTarget(req.kind, req.target)}</td>
                    <td class="sp-num" data-current>{fmtMeasured(req.kind, row?.measured ?? null)}</td>
                    {!props.compact && <td class="faint text">{req.load_case_id ?? ""}</td>}
                    <td class="sp-prio" aria-label={`priority ${req.priority} of 5`}>{priorityDots(req.priority)}</td>
                    {!props.compact && <td class="faint">{req.source === "user" ? "you" : "agent"}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          )
      )}
      {draft ? <CaseEditor draft={draft} onChange={setDraft} /> : spec.load_cases.length > 0 && (
        <table class="sp-table sp-cases" aria-label="Load cases">
          <thead><tr><th>load case</th><th>fixed → loaded</th><th class="sp-num">force</th><th /></tr></thead>
          <tbody>
            {spec.load_cases.map((lc) => (
              <tr key={lc.id} data-case={lc.id} title={lc.note || undefined}>
                <td><span class="text">{lc.name}</span><span class="faint text sp-id">{` ${lc.id}`}</span></td>
                <td class="text">{`${lc.fix} → ${lc.load}`}</td>
                <td class="sp-num">{`${fmtNumN(lc.newtons)} N ${lc.direction}`}</td>
                <td>{lc.estimate && <span class="sp-tag sp-tag-est" title={lc.note || "equivalent static load, F = m·g·k"}>estimate</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

const fmtNumN = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

function ReqEditor({ draft, onChange }: { draft: SpecDraft; onChange: (d: SpecDraft) => void }): JSX.Element {
  const [kind, setKind] = useState<RequirementKind>("max_mass_g");
  const set = (i: number, patch: Partial<ReqDraft>) => {
    const requirements = draft.requirements.map((r, j) => (j === i ? { ...r, ...patch, touched: true } : r));
    onChange({ ...draft, requirements });
  };
  const add = () => {
    const id = newReqId(kind, new Set(draft.requirements.map((r) => r.id)));
    const lc = kind === "min_safety_factor" || kind === "max_deflection_mm" ? draft.load_cases[0]?.id ?? "" : "";
    onChange({ ...draft, requirements: [...draft.requirements, { id, kind, target: kind === "printable" ? "yes" : "", load_case_id: lc, priority: 3, source: "user", touched: true }] });
  };
  return (
    <div class="sp-edit" data-req-editor>
      <table class="sp-table sp-reqs">
        <thead><tr><th>requirement</th><th>target</th><th>case</th><th>priority</th><th /></tr></thead>
        <tbody>
          {draft.requirements.map((r, i) => (
            <tr key={r.id} data-req={r.id}>
              <td><span class="text">{KIND_LABEL[r.kind]}</span><span class="faint text sp-id">{` ${r.id}`}</span></td>
              <td><input class="sp-in" aria-label={`${r.id} target`} value={r.target} onInput={(e) => set(i, { target: (e.currentTarget as HTMLInputElement).value })} /></td>
              <td>
                {(r.kind === "min_safety_factor" || r.kind === "max_deflection_mm") && (
                  <select aria-label={`${r.id} load case`} value={r.load_case_id} onChange={(e) => set(i, { load_case_id: (e.currentTarget as HTMLSelectElement).value })}>
                    <option value="">—</option>
                    {draft.load_cases.map((c) => <option key={c.id} value={c.id}>{c.id}</option>)}
                  </select>
                )}
              </td>
              <td>
                <select aria-label={`${r.id} priority`} value={String(r.priority)} onChange={(e) => set(i, { priority: Number((e.currentTarget as HTMLSelectElement).value) })}>
                  {[5, 4, 3, 2, 1].map((p) => <option key={p} value={String(p)}>{`${p} ${priorityDots(p)}`}</option>)}
                </select>
              </td>
              <td><Button variant="ghost" aria-label={`Remove ${r.id}`} onClick={() => onChange({ ...draft, requirements: draft.requirements.filter((_, j) => j !== i) })}>Remove</Button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div class="sp-add">
        <select aria-label="New requirement kind" value={kind} onChange={(e) => setKind((e.currentTarget as HTMLSelectElement).value as RequirementKind)}>
          {REQUIREMENT_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
        </select>
        <Button variant="ghost" onClick={add}>Add requirement</Button>
      </div>
    </div>
  );
}

function CaseEditor({ draft, onChange }: { draft: SpecDraft; onChange: (d: SpecDraft) => void }): JSX.Element | null {
  if (draft.load_cases.length === 0) return null;
  const set = (i: number, patch: Partial<CaseDraft>) => {
    onChange({ ...draft, load_cases: draft.load_cases.map((c, j) => (j === i ? { ...c, ...patch, touched: true } : c)) });
  };
  const faces = (cur: string) => (SIM_FACE_PRESETS as readonly string[]).includes(cur) ? SIM_FACE_PRESETS : [cur, ...SIM_FACE_PRESETS];
  return (
    <table class="sp-table sp-cases sp-edit" data-case-editor>
      <thead><tr><th>load case</th><th>fixed</th><th>loaded</th><th>force N</th><th>dir</th><th>estimate</th></tr></thead>
      <tbody>
        {draft.load_cases.map((c, i) => (
          <tr key={c.id} data-case={c.id}>
            <td><input class="sp-in" aria-label={`${c.id} name`} value={c.name} onInput={(e) => set(i, { name: (e.currentTarget as HTMLInputElement).value })} /></td>
            <td>
              <select aria-label={`${c.id} fixed face`} value={c.fix} onChange={(e) => set(i, { fix: (e.currentTarget as HTMLSelectElement).value })}>
                {faces(c.fix).map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </td>
            <td>
              <select aria-label={`${c.id} loaded face`} value={c.load} onChange={(e) => set(i, { load: (e.currentTarget as HTMLSelectElement).value })}>
                {faces(c.load).map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </td>
            <td><input class="sp-in sp-in-n" aria-label={`${c.id} force in newtons`} inputMode="decimal" value={c.newtons} onInput={(e) => set(i, { newtons: (e.currentTarget as HTMLInputElement).value })} /></td>
            <td>
              <select aria-label={`${c.id} direction`} value={c.direction} onChange={(e) => set(i, { direction: (e.currentTarget as HTMLSelectElement).value as CaseDraft["direction"] })}>
                {SPEC_DIRECTIONS.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </td>
            <td><input type="checkbox" aria-label={`${c.id} is an estimate`} checked={c.estimate} onChange={(e) => set(i, { estimate: (e.currentTarget as HTMLInputElement).checked })} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
