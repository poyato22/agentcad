// The assembly panel beside the sheet: the parts in the assembly (add, ground, name, place), the
// joints (a value scale per moving axis, Animate, limits), the joint being made, and the check
// (interference, degrees of freedom left). Everything the 3D view does can be done from here by
// keyboard: positions and turns are typed or stepped, faces are chosen from a list.
import type { JSX } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import { JOINT_KINDS, type Instance, type Joint, type JointKind, type Mat4 } from "../../api/assembly";
import { Button } from "../common/Button";
import {
  axisRange, defaultInstanceName, jointAxes, valueAt, withComponent, type AssemblyController, type JointAxis,
} from "./controller";
import { matmul, rotAxis, translate } from "./kinematics";
import { faceOptions, draftProblem, type FaceOption } from "./pick";
import { MOVE_STEP_MM, TURN_STEP_DEG } from "./snap";

export const KIND_LABEL: Record<JointKind, string> = {
  fixed: "Fixed", revolute: "Hinge", slider: "Slide", cylindrical: "Pin and slide", ball: "Ball",
};

const fmt = (v: number, unit: string) => `${Number(v.toFixed(unit === "°" ? 0 : 1))}`;

export function fmtVolume(v: number): string {
  return v < 10 ? `${v.toFixed(2)} mm³` : `${Math.round(v).toLocaleString("en-US")} mm³`;
}

/** Turn a part `deg` about a world axis through its own origin. */
export function turnInPlace(t: Mat4, axis: [number, number, number], deg: number): Mat4 {
  const p = [t[0][3], t[1][3], t[2][3]];
  const r = matmul(matmul(translate(p), rotAxis(axis, deg)), translate(p.map((v) => -v)));
  const out = matmul(r, t);
  return out.map((row) => row.map((v) => (Math.abs(v) < 1e-12 ? 0 : Number(v.toFixed(9)))));
}

export function AssemblyPanel({ ctl }: { ctl: AssemblyController }): JSX.Element {
  const data = ctl.data.value;
  const draft = ctl.draft.value;
  return (
    <aside class="asm-panel" aria-label="Assembly" data-testid="assembly-panel">
      <section class="asm-sec" aria-label="Parts in the assembly">
        <header class="pane-head"><span class="pane-title">Parts</span><span class="pane-right">{data?.instances.length ?? 0}</span></header>
        <InstanceList ctl={ctl} />
        {ctl.selected.value !== null && !draft && <InstanceDetails ctl={ctl} />}
        <AddPart ctl={ctl} />
      </section>
      <section class="asm-sec" aria-label="Joints">
        <header class="pane-head"><span class="pane-title">Joints</span><span class="pane-right">{data?.joints.length ?? 0}</span></header>
        {data?.joints.map((j) => <JointCard key={j.id} ctl={ctl} joint={j} />)}
        {draft ? <JointForm ctl={ctl} /> : (
          <div class="asm-row-actions">
            <Button onClick={() => ctl.startJoint("revolute")} disabled={(data?.instances.length ?? 0) < 2}
              title={(data?.instances.length ?? 0) < 2 ? "Add two parts first" : "Pick a face on each part"}>New joint</Button>
          </div>
        )}
      </section>
      <CheckSection ctl={ctl} />
    </aside>
  );
}

function mobilityText(ctl: AssemblyController, inst: Instance): string {
  const m = ctl.mobilityOf(inst.id);
  if (!m || m.kind === "grounded") return "grounded";
  if (m.kind === "free") return "free";
  const j = ctl.joint(m.joint);
  return j ? KIND_LABEL[j.kind].toLowerCase() : "joined";
}

function InstanceList({ ctl }: { ctl: AssemblyController }): JSX.Element {
  const data = ctl.data.value;
  const hot = ctl.interference.value.ids;
  if (!data || data.instances.length === 0) {
    return <div class="pane-empty">Add a part from this build to start the assembly.</div>;
  }
  return (
    <ul class="left-list" aria-label="Parts in the assembly">
      {data.instances.map((i) => {
        const sel = ctl.selected.value === i.id;
        return (
          <li key={i.id}>
            <button type="button" class={sel ? "left-row sel" : "left-row"} aria-pressed={sel} data-selected={sel ? "" : undefined} data-instance-id={i.id}
              onClick={() => ctl.select(sel ? null : i.id)}>
              <span class="asm-swatch" style={{ background: ctl.part(i.part_id)?.color ?? "var(--mesh-face)" }} aria-hidden="true" />
              <span class="row-name">{i.name}</span>
              {hot.has(i.id) && <span class="asm-hot" title="Interferes with another part">clash</span>}
              <span class="row-right faint">{mobilityText(ctl, i)}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function InstanceDetails({ ctl }: { ctl: AssemblyController }): JSX.Element | null {
  const inst = ctl.instance(ctl.selected.value);
  const [name, setName] = useState(inst?.name ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => { setName(inst?.name ?? ""); setProblem(null); }, [inst?.id, inst?.name]);
  if (!inst) return null;
  const m = ctl.mobilityOf(inst.id);
  const pose = ctl.currentPose()[inst.id] ?? inst.transform;
  const joint = m?.kind === "joint" ? ctl.joint(m.joint) : null;

  const commitName = async () => {
    if (name.trim() === inst.name) return;
    setProblem(await ctl.rename(inst.id, name));
  };
  const place = (t: Mat4) => { void ctl.commitInstance(inst.id, t, false); };
  const setAxis = (k: number, v: number) => {
    if (!Number.isFinite(v)) return;
    const t = pose.map((r) => [...r]);
    t[k][3] = v;
    place(t);
  };

  return (
    <div class="asm-details" aria-label={`${inst.name} details`}>
      <label class="asm-field">
        <span class="label">Name</span>
        <input class="asm-input" value={name} onInput={(e) => setName(e.currentTarget.value)}
          onBlur={() => void commitName()} onKeyDown={(e) => { if (e.key === "Enter") void commitName(); }} />
      </label>
      {problem && <p class="asm-problem" role="alert">{problem}</p>}
      <label class="asm-check">
        <input type="checkbox" checked={inst.fixed} onChange={(e) => void ctl.setFixed(inst.id, e.currentTarget.checked)} />
        Grounded (never moves; joints hang from it)
      </label>
      {m?.kind === "free" && (
        <>
          <div class="asm-xyz" role="group" aria-label="Position (mm)">
            {(["X", "Y", "Z"] as const).map((ax, k) => (
              <label key={ax} class="asm-field asm-field-num">
                <span class="label">{ax}</span>
                <input class="asm-input" type="number" step={MOVE_STEP_MM} value={Number(pose[k][3].toFixed(2))}
                  aria-label={`${ax} position (mm)`}
                  onChange={(e) => setAxis(k, Number(e.currentTarget.value))} />
              </label>
            ))}
          </div>
          <div class="asm-turns" role="group" aria-label={`Turn ${TURN_STEP_DEG}°`}>
            {([[1, 0, 0], [0, 1, 0], [0, 0, 1]] as [number, number, number][]).map((axis, k) => (
              <span key={k} class="asm-turn">
                <span class="faint">{"XYZ"[k]}</span>
                <button type="button" class="vt-btn" aria-label={`Turn −${TURN_STEP_DEG}° about ${"XYZ"[k]}`} onClick={() => place(turnInPlace(pose, axis, -TURN_STEP_DEG))}>−{TURN_STEP_DEG}°</button>
                <button type="button" class="vt-btn" aria-label={`Turn +${TURN_STEP_DEG}° about ${"XYZ"[k]}`} onClick={() => place(turnInPlace(pose, axis, TURN_STEP_DEG))}>+{TURN_STEP_DEG}°</button>
              </span>
            ))}
          </div>
        </>
      )}
      {m?.kind === "joint" && joint && (
        <p class="asm-note dim">Moves on its {KIND_LABEL[joint.kind].toLowerCase()}. Drag it in the view or use the joint’s scale.</p>
      )}
      {m?.kind === "grounded" && <p class="asm-note dim">Grounded. Untick to move it.</p>}
      <div class="asm-row-actions">
        <Button variant="danger" onClick={() => void ctl.removeInstance(inst.id)}>Remove from assembly</Button>
      </div>
    </div>
  );
}

function AddPart({ ctl }: { ctl: AssemblyController }): JSX.Element {
  const parts = ctl.parts.value.filter((p) => p.metrics !== null && (p.kind ?? "part") !== "group");
  const [partId, setPartId] = useState<string>("");
  const [name, setName] = useState("");
  const [touched, setTouched] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const chosen = parts.find((p) => p.part_id === partId) ?? parts[0];
  useEffect(() => { if (!touched && chosen) setName(defaultInstanceName(chosen)); }, [chosen?.part_id, touched]);
  if (parts.length === 0) return <div class="pane-empty">This build has no built parts yet. Ask an agent to make one.</div>;
  const add = async (e: Event) => {
    e.preventDefault();
    if (!chosen) return;
    const err = await ctl.addInstance(chosen.part_id, name);
    setProblem(err);
    if (!err) setTouched(false);
  };
  return (
    <form class="asm-add" onSubmit={(e) => void add(e)} aria-label="Add a part">
      <label class="asm-field">
        <span class="label">Part</span>
        <select class="asm-input" value={chosen?.part_id} onChange={(e) => { setPartId(e.currentTarget.value); setTouched(false); setProblem(null); }}>
          {parts.map((p) => <option key={p.part_id} value={p.part_id}>{defaultInstanceName(p)}</option>)}
        </select>
      </label>
      <label class="asm-field">
        <span class="label">Name in assembly</span>
        <input class="asm-input" value={name} onInput={(e) => { setName(e.currentTarget.value); setTouched(true); setProblem(null); }} />
      </label>
      {problem && <p class="asm-problem" role="alert">{problem}</p>}
      <div class="asm-row-actions"><Button type="submit">Add part</Button></div>
    </form>
  );
}

// ---- joints

function JointCard({ ctl, joint }: { ctl: AssemblyController; joint: Joint }): JSX.Element {
  const names = (id: string) => ctl.instance(id)?.name ?? "missing part";
  const axes = jointAxes(joint.kind);
  const value = ctl.values.value[joint.id] ?? joint.value;
  const animating = ctl.animating.value === joint.id;
  const canAnimate = axes.length > 0 && (joint.limits !== null || axes[0].kind === "angle");
  return (
    <div class="asm-joint" data-joint-id={joint.id} data-animating={animating ? "on" : undefined}>
      <div class="asm-joint-head">
        <span class="asm-joint-kind">{KIND_LABEL[joint.kind]}</span>
        <span class="asm-joint-pair">{names(joint.a.instance_id)} <span class="faint">to</span> {names(joint.b.instance_id)}</span>
        {axes.length > 0 && (
          <button type="button" class="vt-btn" aria-pressed={animating} data-active={animating} data-selected={(animating) ? "" : undefined}
            disabled={!canAnimate} title={canAnimate ? "Sweep through its travel and back" : "Set limits to animate a slide"}
            onClick={() => ctl.animate(joint.id)}>{animating ? "stop" : "animate"}</button>
        )}
        <button type="button" class="vt-btn asm-x" aria-label={`Delete the ${KIND_LABEL[joint.kind].toLowerCase()} between ${names(joint.a.instance_id)} and ${names(joint.b.instance_id)}`}
          onClick={() => void ctl.removeJoint(joint.id)}>×</button>
      </div>
      {axes.map((ax) => <JointScale key={ax.index} ctl={ctl} joint={joint} axis={ax} value={valueAt(value, ax.index)} />)}
    </div>
  );
}

/** The scale: a big live readout, a range slider with drafting ticks, and the limits at its ends. */
function JointScale({ ctl, joint, axis, value }: { ctl: AssemblyController; joint: Joint; axis: JointAxis; value: number }): JSX.Element {
  const [lo, hi] = axisRange(joint, axis);
  const [loText, setLo] = useState(String(lo));
  const [hiText, setHi] = useState(String(hi));
  useEffect(() => { setLo(String(lo)); setHi(String(hi)); }, [lo, hi]);
  const pct = hi > lo ? ((value - lo) / (hi - lo)) * 100 : 0;
  const commitLimits = () => {
    const l = Number(loText); const h = Number(hiText);
    if (!Number.isFinite(l) || !Number.isFinite(h)) return;
    if (joint.limits && l === joint.limits[0] && h === joint.limits[1]) return;
    if (!joint.limits && l === lo && h === hi) return;
    void ctl.setLimits(joint.id, [l, h]);
  };
  const id = `asm-${joint.id}-${axis.index}`;
  const end = () => { ctl.endJointGesture(); window.removeEventListener("pointerup", end); window.removeEventListener("pointercancel", end); };
  return (
    <div class="asm-scale-row">
      <div class="asm-readout">
        <label class="label" for={id}>{axis.label}</label>
        <output class="asm-value" for={id} data-testid="joint-value">{fmt(value, axis.unit)}<span class="asm-unit">{axis.unit}</span></output>
      </div>
      <div class="asm-scale" style={{ "--pct": `${Math.max(0, Math.min(100, pct))}%` }}>
        <input id={id} type="range" min={lo} max={hi} step={axis.kind === "angle" ? 1 : 0.5} value={value}
          aria-valuetext={`${fmt(value, axis.unit)} ${axis.unit === "°" ? "degrees" : "mm"}`}
          onPointerDown={() => { ctl.beginJointGesture(); window.addEventListener("pointerup", end); window.addEventListener("pointercancel", end); }}
          onInput={(e) => ctl.setJointValue(joint.id, withComponent(joint.kind, ctl.values.peek()[joint.id] ?? joint.value, axis.index, Number(e.currentTarget.value)))} />
      </div>
      <div class="asm-limits">
        <input class="asm-input asm-limit" type="number" value={loText} aria-label={`${axis.label} lower limit (${axis.unit})`}
          onInput={(e) => setLo(e.currentTarget.value)} onChange={commitLimits} />
        <span class="faint">{joint.limits ? "limits" : "no limits"}</span>
        <input class="asm-input asm-limit" type="number" value={hiText} aria-label={`${axis.label} upper limit (${axis.unit})`}
          onInput={(e) => setHi(e.currentTarget.value)} onChange={commitLimits} />
      </div>
    </div>
  );
}

function FaceChooser({ ctl, slot, active }: { ctl: AssemblyController; slot: "a" | "b"; active: boolean }): JSX.Element {
  const draft = ctl.draft.value;
  const data = ctl.data.value;
  const pick = draft?.[slot] ?? null;
  const [inst, setInst] = useState<string>(pick?.instanceId ?? "");
  useEffect(() => { if (pick) setInst(pick.instanceId); }, [pick?.instanceId]);
  const instance = data?.instances.find((i) => i.id === inst) ?? null;
  const options: FaceOption[] = useMemo(() => {
    const base = faceOptions(instance ? ctl.part(instance.part_id)?.metrics : null);
    return pick?.option.key === "pick" && pick.instanceId === inst ? [pick.option, ...base] : base;
  }, [instance?.part_id, pick?.option, inst]);
  const title = slot === "a" ? "First face" : "Second face";
  return (
    <fieldset class={active ? "asm-face asm-face-active" : "asm-face"} data-slot={slot} data-selected={active ? "" : undefined}>
      <legend class="label">{title}{active ? " · click it in the view" : ""}</legend>
      <select class="asm-input" aria-label={`${title}: part`} value={inst} onChange={(e) => setInst(e.currentTarget.value)}>
        <option value="">Part…</option>
        {data?.instances.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
      </select>
      <select class="asm-input" aria-label={`${title}: face`} disabled={!instance} value={pick && pick.instanceId === inst ? pick.option.key : ""}
        onChange={(e) => {
          const o = options.find((x) => x.key === e.currentTarget.value);
          if (o && instance) ctl.pickFace(instance.id, o, slot);
        }}>
        <option value="">Face…</option>
        {options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
      </select>
    </fieldset>
  );
}

function JointForm({ ctl }: { ctl: AssemblyController }): JSX.Element | null {
  const d = ctl.draft.value;
  if (!d) return null;
  const unit = d.kind === "slider" ? "mm" : "°";
  const problem = ctl.draftError.value;
  const nextSlot = d.a === null ? "a" : d.b === null ? "b" : null;
  const ready = draftProblem(d) === null;
  return (
    <form class="asm-jointform" aria-label="New joint" data-testid="joint-form"
      onSubmit={(e) => { e.preventDefault(); void ctl.createJoint(); }}
      onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); ctl.cancelJoint(); } }}>
      <div class="asm-kinds" role="radiogroup" aria-label="Joint kind">
        {JOINT_KINDS.map((k) => (
          <button key={k} type="button" role="radio" class="vt-btn" aria-checked={d.kind === k} data-active={d.kind === k} data-selected={(d.kind === k) ? "" : undefined}
            onClick={() => ctl.updateDraft({ kind: k })}>{KIND_LABEL[k].toLowerCase()}</button>
        ))}
      </div>
      <FaceChooser ctl={ctl} slot="a" active={nextSlot === "a"} />
      <FaceChooser ctl={ctl} slot="b" active={nextSlot === "b"} />
      <label class="asm-check">
        <input type="checkbox" checked={d.flip} onChange={(e) => ctl.updateDraft({ flip: e.currentTarget.checked })} />
        Point the faces the same way (instead of face to face)
      </label>
      {d.kind !== "fixed" && (
        <div class="asm-limits-row">
          <label class="asm-check">
            <input type="checkbox" checked={d.limited} onChange={(e) => ctl.updateDraft({ limited: e.currentTarget.checked })} />
            Limit travel
          </label>
          {d.limited && (
            <>
              <input class="asm-input asm-limit" type="number" value={d.lo} aria-label={`Lower limit (${unit})`}
                onChange={(e) => ctl.updateDraft({ lo: Number(e.currentTarget.value) })} />
              <span class="faint">to</span>
              <input class="asm-input asm-limit" type="number" value={d.hi} aria-label={`Upper limit (${unit})`}
                onChange={(e) => ctl.updateDraft({ hi: Number(e.currentTarget.value) })} />
              <span class="faint">{unit}</span>
            </>
          )}
        </div>
      )}
      {problem && <p class="asm-problem" role="alert">{problem}</p>}
      <div class="asm-row-actions">
        <Button type="submit" variant="ink" disabled={!ready}>Create joint</Button>
        <Button onClick={() => ctl.cancelJoint()}>Cancel</Button>
      </div>
    </form>
  );
}

// ---- check

function CheckSection({ ctl }: { ctl: AssemblyController }): JSX.Element {
  const it = ctl.interference.value;
  const data = ctl.data.value;
  const dof = data?.dof_remaining ?? 0;
  const over = data?.overconstrained ?? [];
  const nameOf = (id: string) => ctl.instance(id)?.name ?? id;
  return (
    <section class="asm-sec" aria-label="Check" data-testid="assembly-check">
      <header class="pane-head">
        <span class="pane-title">Check</span>
        <span class="pane-right">
          <button type="button" class="vt-btn" disabled={it.status === "checking"} onClick={() => void ctl.checkInterference()}>check now</button>
        </span>
      </header>
      <div class="asm-check-body" aria-live="polite">
        {it.status === "checking" && <p class="dim">Checking for interference…</p>}
        {it.status === "done" && it.items.length === 0 && (data?.instances.length ?? 0) > 1 && (
          <p class="asm-pass" data-status="pass"><span class="led led-green" aria-hidden="true" /> No parts interfere.</p>
        )}
        {it.status === "done" && it.items.map((x) => (
          <p key={`${x.a}|${x.b}`} class="asm-fail" data-status="fail" data-testid="interference-row">
            <span class="led led-red" aria-hidden="true" /> {x.a} <span class="faint">and</span> {x.b} overlap by <b>{fmtVolume(x.volume_mm3)}</b>
          </p>
        ))}
        {it.status === "error" && <p class="dim">Interference check failed: {it.error}</p>}
        {over.map((g) => (
          <p key={g.join()} class="asm-warn" data-status="warn">
            <span class="led led-amber" aria-hidden="true" /> {g.map(nameOf).join(" and ")} are grounded and joined; one joint can’t apply. Unground one.
          </p>
        ))}
        {data && data.instances.length > 0 && (
          <p class="dim">{dof === 0 ? "Fully constrained." : `${dof} degree${dof === 1 ? "" : "s"} of freedom left.`}</p>
        )}
      </div>
    </section>
  );
}
