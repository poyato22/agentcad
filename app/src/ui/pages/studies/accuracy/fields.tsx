// The Plan 6 settings that get their own controls instead of the generic schema form:
// - print direction: an isometric axis gizmo (six arrow tips) beside the part's layer stack, so
//   "layers along +x" reads as a picture of the print, not a word;
// - contact pairs: face A ⟷ face B with one "Pick pair" that arms the viewer twice in a row,
//   a part resting on a rigid support, or two assembly instances; friction μ on each;
// - fatigue: the load cycle as a trace (fully reversed / zero to max / min–max), the required
//   life with everyday presets, and the mean-stress correction.
// Every other field of these settings (steps, large deflection …) stays generic. Values are
// written back through the form's own `set`, so validation and the core's checks are unchanged.
import type { JSX } from "preact";
import { useRef } from "preact/hooks";
import { orthotropic } from "../../../../api/accuracy";
import type { StudyMaterial } from "../../../../api/studies";
import { Button } from "../../../common/Button";
import type { Path } from "../formSchema";
import { sig3 } from "../model";
import { AXES, cycleRange, cyclesShort, cyclesText, isAxis, LIFE_PRESETS, printDirectionText, type Axis } from "./model";

export interface CustomFieldProps {
  path: Path;
  value: unknown;
  set: (p: Path, v: unknown) => void;
  disabled?: boolean;
  /** Renders the form's own face picker (select + Pick) for a path. */
  face: (p: Path, label: string) => JSX.Element;
  /** Arms the viewer for `p`, then `then` (a pair); null disarms. */
  onPickFace?: (p: Path | null, then?: Path) => void;
  picking?: Path | null;
  /** The settings' chosen material (its orthotropic data), when known. */
  material?: StudyMaterial | null;
  errors: (p: Path) => string[];
}

const key = (p: Path) => p.join(".");
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

function Err({ msgs }: { msgs: string[] }): JSX.Element | null {
  return msgs.length ? <span class="sf-err" role="alert">{msgs.join("; ")}</span> : null;
}

// ---- print direction

/** Isometric projection of part axes onto the gizmo (x right-down, y left-down, z up). */
const ISO: Record<"x" | "y" | "z", [number, number]> = { x: [0.87, 0.5], y: [-0.87, 0.5], z: [0, -1] };
const tip = (a: Axis, r: number): [number, number] => {
  const [dx, dy] = ISO[a[1] as "x" | "y" | "z"];
  const s = a[0] === "-" ? -1 : 1;
  return [dx * r * s, dy * r * s];
};

/** Thin layer lines across a small block, perpendicular to the build axis (drawn in the same projection). */
function layerLines(a: Axis, n = 7): string {
  const ax = a[1] as "x" | "y" | "z";
  const others = (["x", "y", "z"] as const).filter((o) => o !== ax);
  const [u, v] = others.map((o) => ISO[o]);
  const w = ISO[ax];
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    const t = -1 + (2 * (i + 0.5)) / n;
    // a layer is the u-v square at height t along w; draw its two visible edges
    const c = [w[0] * t * 14, w[1] * t * 14];
    const p = (a1: number, b1: number) => `${(c[0] + u[0] * a1 * 14 + v[0] * b1 * 14).toFixed(1)},${(c[1] + u[1] * a1 * 14 + v[1] * b1 * 14).toFixed(1)}`;
    out.push(`M${p(-1, 1)}L${p(1, 1)}L${p(1, -1)}`);
  }
  return out.join("");
}

export function PrintDirectionField(p: CustomFieldProps): JSX.Element {
  const on = p.value !== null && p.value !== undefined;
  const axis: Axis | null = isAxis(p.value) ? p.value : null;
  const ortho = p.material ? orthotropic(p.material as unknown as Record<string, unknown>) : null;
  const group = useRef<SVGGElement>(null);
  const pick = (a: Axis) => { if (!p.disabled) p.set(p.path, a); };
  const onKey = (e: KeyboardEvent) => {
    const i = axis ? AXES.indexOf(axis) : -1;
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = AXES[(i + step + AXES.length) % AXES.length];
    pick(next);
    requestAnimationFrame(() => group.current?.querySelector<SVGGElement>(`[data-axis="${next}"]`)?.focus());
  };
  const R = 34;
  return (
    <fieldset class="sf-group acc-print" data-field={key(p.path)} data-testid="print-direction">
      <legend>
        <label class="sf-check">
          <input type="checkbox" checked={on} disabled={p.disabled} data-testid="print-direction-on"
            onChange={() => p.set(p.path, on ? null : "+z")} /> Print direction
        </label>
      </legend>
      {!on && <span class="sf-doc faint text">Off: the material is treated as the same in every direction. Turn on for a printed part: tension across the layers uses the weaker Z strength.</span>}
      {on && (
        <div class="acc-print-body">
          <svg class="acc-gizmo" viewBox="-52 -48 104 96" width="116" height="106" aria-hidden="false">
            <g class="acc-layers" transform="translate(0 2)"><path d={layerLines(axis ?? "+z")} /></g>
            <g ref={group} role="radiogroup" aria-label="Build direction: the axis the layers stack along" onKeyDown={onKey}>
              {AXES.map((a) => {
                const [x, y] = tip(a, R);
                const sel = a === axis;
                const neg = a[0] === "-";
                return (
                  <g key={a} data-axis={a} role="radio" aria-checked={sel} aria-label={`layers stack along ${a}`}
                    tabIndex={sel || (!axis && a === "+z") ? 0 : -1} class={`acc-axis${sel ? " is-sel" : ""}${neg ? " is-neg" : ""}`}
                    onClick={() => pick(a)}>
                    <line x1={0} y1={0} x2={x} y2={y} />
                    <circle cx={x} cy={y} r={sel ? 7.5 : 6} />
                    <text x={x} y={y + 3} text-anchor="middle">{a.replace("-", "−")}</text>
                  </g>
                );
              })}
            </g>
          </svg>
          <div class="acc-print-text">
            <span class="text" data-testid="print-direction-text">{printDirectionText(p.value)}</span>
            {Array.isArray(p.value) && (
              <span class="faint">a vector set by an agent · <button type="button" class="scard-link" onClick={() => p.set(p.path, "+z")}>use +z</button></span>
            )}
            {ortho && (
              <span class="faint" data-testid="ortho-note">
                {`E ${sig3(ortho.e_xy_mpa)} in-plane · ${sig3(ortho.e_z_mpa)} across layers MPa`}
                {typeof ortho.strength_z_mpa === "number" ? ` · Z strength ${sig3(ortho.strength_z_mpa)} MPa` : ""}
              </span>
            )}
            {!ortho && p.material && <span class="faint">{p.material.name} has no across-layer data: an estimate is used</span>}
          </div>
        </div>
      )}
      <Err msgs={p.errors(p.path)} />
    </fieldset>
  );
}

// ---- contact pairs

const PAIR_KINDS: { kind: string; text: string; fresh: () => Record<string, unknown> }[] = [
  { kind: "faces", text: "Face pair", fresh: () => ({ kind: "faces", master: "top", slave: "bottom", friction: 0.2 }) },
  { kind: "rigid", text: "On a rigid support", fresh: () => ({ kind: "rigid", face: "bottom", friction: 0.2 }) },
  { kind: "instances", text: "Two assembly parts", fresh: () => ({ kind: "instances", a: "", b: "", friction: 0.2 }) },
];

export function ContactPairsField(p: CustomFieldProps): JSX.Element {
  const list = Array.isArray(p.value) ? (p.value as unknown[]) : [];
  const armedAt = (q: Path) => !!p.picking && key(p.picking) === key(q);
  return (
    <fieldset class="sf-group acc-contact" data-field={key(p.path)} data-testid="contact-pairs">
      <legend>Contact<span class="faint"> · {list.length ? `${list.length} pair${list.length === 1 ? "" : "s"}` : "none"}</span></legend>
      {list.length === 0 && <span class="sf-doc faint text">Without contact, touching faces pass through each other. Add a pair where parts press together.</span>}
      {list.map((raw, i) => {
        const x = isObj(raw) ? raw : {};
        const ip = [...p.path, i];
        const kind = typeof x.kind === "string" ? x.kind : "faces";
        const a = [...ip, "master"]; const b = [...ip, "slave"];
        const armedA = armedAt(a); const armedB = armedAt(b);
        return (
          <div key={i} class="acc-pair" data-testid="contact-pair" data-kind={kind} data-armed={armedA ? "a" : armedB ? "b" : undefined}>
            <div class="sf-item-head">
              <select aria-label={`Contact ${i + 1} kind`} value={kind} disabled={p.disabled}
                onChange={(e) => { const k = PAIR_KINDS.find((o) => o.kind === (e.target as HTMLSelectElement).value); if (k) p.set(ip, { ...k.fresh(), friction: x.friction ?? 0.2 }); }}>
                {PAIR_KINDS.map((o) => <option key={o.kind} value={o.kind}>{o.text}</option>)}
              </select>
              <span class="sf-item-tools">
                <button type="button" class="sf-tool" disabled={p.disabled} aria-label={`Remove contact ${i + 1}`}
                  onClick={() => { p.onPickFace?.(null); p.set(p.path, list.filter((_, j) => j !== i)); }}>✕</button>
              </span>
            </div>
            {kind === "faces" && (
              <>
                <div class="acc-pair-faces">
                  <span class="acc-pair-tag" data-on={armedA ? "" : undefined}>A</span>{p.face(a, `Contact ${i + 1} face A`)}
                  <span class="acc-pair-link" aria-hidden="true">⟷</span>
                  <span class="acc-pair-tag" data-on={armedB ? "" : undefined}>B</span>{p.face(b, `Contact ${i + 1} face B`)}
                </div>
                {p.onPickFace && (
                  <Button variant={armedA || armedB ? "primary" : "default"} disabled={p.disabled} aria-pressed={armedA || armedB} data-testid="pick-pair"
                    onClick={() => (armedA || armedB ? p.onPickFace?.(null) : p.onPickFace?.(a, b))}>
                    {armedA ? "Click face A…" : armedB ? "Now click face B…" : "Pick pair in the viewer"}
                  </Button>
                )}
                <Err msgs={[...p.errors(a), ...p.errors(b)]} />
              </>
            )}
            {kind === "rigid" && (
              <div class="acc-pair-faces">
                <span class="acc-pair-tag">on</span>{p.face([...ip, "face"], `Contact ${i + 1} face on the support`)}
                <span class="faint">rests on a rigid, fixed surface</span>
                <Err msgs={p.errors([...ip, "face"])} />
              </div>
            )}
            {kind === "instances" && (
              <div class="acc-pair-faces">
                {(["a", "b"] as const).map((k) => (
                  <input key={k} aria-label={`Contact ${i + 1} instance ${k.toUpperCase()}`} placeholder={`instance ${k.toUpperCase()}`} disabled={p.disabled}
                    value={typeof x[k] === "string" ? x[k] as string : ""} onInput={(e) => p.set([...ip, k], (e.target as HTMLInputElement).value)} />
                ))}
                <span class="faint">assembly studies only (the build's instance ids)</span>
                <Err msgs={[...p.errors([...ip, "a"]), ...p.errors([...ip, "b"])]} />
              </div>
            )}
            <label class="acc-friction">
              <span class="sf-label">Friction μ</span>
              <input type="number" step="0.05" min={0} max={2} disabled={p.disabled} aria-label={`Contact ${i + 1} friction`}
                value={typeof x.friction === "number" ? String(x.friction) : ""}
                onInput={(e) => p.set([...ip, "friction"], Number.parseFloat((e.target as HTMLInputElement).value))} />
              <span class="faint">{typeof x.friction === "number" && x.friction === 0 ? "frictionless" : "PLA on PLA ≈ 0.3"}</span>
            </label>
            <Err msgs={p.errors([...ip, "friction"])} />
          </div>
        );
      })}
      <div class="acc-pair-add">
        <Button disabled={p.disabled || list.length >= 12} data-testid="add-contact-pair" onClick={() => p.set(p.path, [...list, PAIR_KINDS[0].fresh()])}>Add face pair</Button>
        <Button variant="ghost" disabled={p.disabled || list.length >= 12} onClick={() => p.set(p.path, [...list, PAIR_KINDS[1].fresh()])}>Add rigid support</Button>
      </div>
      <Err msgs={p.errors(p.path)} />
    </fieldset>
  );
}

// ---- fatigue

const CYCLES: { mode: string; text: string }[] = [
  { mode: "fully_reversed", text: "Fully reversed" }, { mode: "zero_to_max", text: "Zero to max" }, { mode: "min_max", text: "Min to max" },
];

/** Two load cycles as a trace between the cycle's min and max (fractions of the case's load). */
export function CycleTrace({ mode, minRatio }: { mode: string; minRatio: number | null }): JSX.Element {
  const [lo, hi] = cycleRange(mode, minRatio);
  const W = 200; const H = 64; const pad = 6;
  const y = (f: number) => pad + ((1 - f) / 2) * (H - 2 * pad); // +1 top, −1 bottom
  const pts: string[] = [];
  for (let i = 0; i <= 80; i++) {
    const t = i / 80;
    const f = lo + (hi - lo) * (0.5 - 0.5 * Math.cos(t * 4 * Math.PI));
    pts.push(`${(t * W).toFixed(1)},${y(f).toFixed(1)}`);
  }
  return (
    <svg class="acc-trace" viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img"
      aria-label={`Load cycle from ${sig3(lo)} to ${sig3(hi)} times the case's load`} data-testid="cycle-trace">
      <line class="acc-trace-zero" x1={0} x2={W} y1={y(0)} y2={y(0)} />
      <line class="acc-trace-ref" x1={0} x2={W} y1={y(hi)} y2={y(hi)} />
      <line class="acc-trace-ref" x1={0} x2={W} y1={y(lo)} y2={y(lo)} />
      <polyline class="acc-trace-line" points={pts.join(" ")} />
      <text x={W - 2} y={y(hi) - 2} text-anchor="end">{hi === 1 ? "+ case load" : sig3(hi)}</text>
      <text x={W - 2} y={Math.min(H - 1, y(lo) + 9)} text-anchor="end">{lo === -1 ? "− case load" : lo === 0 ? "0" : `${sig3(lo)} × load`}</text>
    </svg>
  );
}

export function FatigueField(p: CustomFieldProps): JSX.Element {
  const on = isObj(p.value);
  const v = on ? (p.value as Record<string, unknown>) : {};
  const mode = typeof v.cycle === "string" ? v.cycle : "fully_reversed";
  const cycles = typeof v.cycles === "number" ? v.cycles : null;
  const minRatio = typeof v.min_ratio === "number" ? v.min_ratio : null;
  const at = (k: string) => [...p.path, k];
  return (
    <fieldset class="sf-group acc-fatigue" data-field={key(p.path)} data-testid="fatigue">
      <legend>
        <label class="sf-check">
          <input type="checkbox" checked={on} disabled={p.disabled} data-testid="fatigue-on"
            onChange={() => p.set(p.path, on ? null : { cycles: 1e6, cycle: "fully_reversed", min_ratio: 0, mean_correction: "goodman" })} /> Cyclic loading (fatigue)
        </label>
      </legend>
      {!on && <span class="sf-doc faint text">For parts that vibrate, spin or flex again and again (drone arms, motor mounts, clips). Reports the life at the hotspot.</span>}
      {on && (
        <>
          <div class="acc-seg" role="radiogroup" aria-label="Load cycle">
            {CYCLES.map((c) => (
              <button key={c.mode} type="button" role="radio" aria-checked={mode === c.mode} disabled={p.disabled} data-cycle={c.mode}
                onClick={() => p.set(at("cycle"), c.mode)}>{c.text}</button>
            ))}
          </div>
          <CycleTrace mode={mode} minRatio={minRatio} />
          {mode === "min_max" && (
            <div class="sf-row">
              <label class="sf-label" for="acc-fat-r">Min ÷ max (R)</label>
              <span class="sf-control">
                <input id="acc-fat-r" type="number" step="0.1" min={-1} max={1} disabled={p.disabled} value={minRatio === null ? "" : String(minRatio)}
                  onInput={(e) => p.set(at("min_ratio"), Number.parseFloat((e.target as HTMLInputElement).value))} />
                <span class="sf-range faint">−1 to 1</span>
              </span>
              <Err msgs={p.errors(at("min_ratio"))} />
            </div>
          )}
          <div class="sf-row">
            <label class="sf-label" for="acc-fat-n">Required life</label>
            <span class="sf-control">
              <input id="acc-fat-n" type="number" step="any" min={1} disabled={p.disabled} data-testid="fatigue-cycles"
                value={cycles === null ? "" : String(cycles)} onInput={(e) => p.set(at("cycles"), Number.parseFloat((e.target as HTMLInputElement).value))} />
              <span class="faint" data-testid="fatigue-cycles-text">{cycles !== null && cycles > 0 ? `${cyclesText(cycles)} cycles` : ""}</span>
            </span>
            <Err msgs={p.errors(at("cycles"))} />
          </div>
          <div class="acc-presets" aria-label="Typical lives">
            {LIFE_PRESETS.map((l) => (
              <button key={l.cycles} type="button" class="acc-chip" aria-pressed={cycles === l.cycles} disabled={p.disabled}
                title={`${cyclesText(l.cycles)} cycles`} onClick={() => p.set(at("cycles"), l.cycles)}>{l.text}</button>
            ))}
          </div>
          <div class="sf-row">
            <label class="sf-label" for="acc-fat-mc">Mean stress</label>
            <select id="acc-fat-mc" disabled={p.disabled} value={typeof v.mean_correction === "string" ? v.mean_correction : "goodman"}
              onChange={(e) => p.set(at("mean_correction"), (e.target as HTMLSelectElement).value)}>
              <option value="goodman">Goodman correction</option>
              <option value="none">no correction</option>
            </select>
          </div>
          <span class="sf-doc faint text">Fatigue estimate from the material's S-N curve (datasheet or estimate). Cycles: {cycles ? cyclesShort(cycles) : "?"}.</span>
        </>
      )}
      <Err msgs={p.errors(p.path)} />
    </fieldset>
  );
}
