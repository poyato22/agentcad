// The study viewport on the Studies page and in the immersive view. Before a run: the part with
// its load / support markers (arrows show direction and size, the selected case in blue) and face
// picking for a form field. After: the result with a channel picker, a legend whose range can be
// set, a deformed-scale slider, mode-shape animation (modal / buckling), a section plane, a probe
// on click, and for wind particles moving along the velocity slice (static streamlines with
// reduced motion). Values read from the core render as text.
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { StudyProbe, StudyRecord, StudySection } from "../../../api/studies";
import type { PartFace } from "../../../api/types";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { loadPart } from "../../viewer/loadPart";
import { studyData } from "./data";
import { legendGradient, legendTicks, sig3, unitText, type Marker } from "./model";
import type { Path } from "./formSchema";
import { faceAnchor, type Channel, type V3 } from "./scene/fields";
import { createStudyScene, type ResultInfo, type StudySceneDeps, type StudySceneHandle } from "./scene/studyScene";
import "../../viewer/viewer.css";

export interface StudyViewDeps extends StudySceneDeps {
  partGlb?: (partId: string) => Promise<ArrayBuffer | null>;
  resultGlb?: (simId: string) => Promise<ArrayBuffer | null>;
  section?: (simId: string, q: { axis: "x" | "y" | "z"; offset: number; channel: string; n?: number }) => Promise<StudySection>;
  probe?: (simId: string, p: V3) => Promise<StudyProbe>;
}

const defaults: Required<Pick<StudyViewDeps, "partGlb" | "resultGlb" | "section" | "probe">> = {
  partGlb: async (id) => { const r = await actions.client().partGlb(id); return r === "not-modified" ? null : r.bytes; },
  resultGlb: (id) => studyData.glb(id),
  section: (id, q) => actions.client().studySection(id, q),
  probe: (id, p) => actions.client().studyProbe(id, p),
};

export interface StudyViewProps {
  partId: string | null;
  mtime?: number;
  /** A finished study to show (null: the setup view of the part). */
  record: StudyRecord | null;
  markers?: readonly Marker[];
  faces?: readonly PartFace[];
  selectedCase?: number | null;
  /** A form field waiting for a face pick. */
  picking?: Path | null;
  onPicked?: (face: string) => void;
  onCancelPick?: () => void;
  /** Immersive: a bigger layout and the controls always open. */
  immersive?: boolean;
  /** A running study: a caption over the part (mesh → solve → post). */
  liveCaption?: string | null;
  deps?: StudyViewDeps;
}

const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Channel names as words. */
const CHANNEL_TEXT: Record<string, string> = {
  von_mises: "von Mises", displacement: "displacement", s1: "max principal", s3: "min principal", safety_factor: "safety factor",
  temperature: "temperature", cp: "Cp (pressure)", velocity: "velocity", speed: "speed",
};
export const channelText = (c: string): string => CHANNEL_TEXT[c] ?? c.replace(/^mode_(\d+)$/, "mode $1").replace(/_/g, " ");

export function StudyView(props: StudyViewProps): JSX.Element {
  const deps = { ...defaults, ...props.deps };
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const sc = useRef<StudySceneHandle | null>(null);
  const [state, setState] = useState<"loading" | "loaded" | "none" | string>("none");
  const [info, setInfo] = useState<ResultInfo | null>(null);
  const [channel, setChannel] = useState<string | null>(null);
  const [full, setFull] = useState<[number, number] | null>(null);
  const [range, setRange] = useState<[number, number] | null>(null);
  const [deform, setDeform] = useState(0);
  const [animate, setAnimate] = useState(false);
  const [section, setSection] = useState<{ axis: "x" | "y" | "z"; offset: number } | null>(null);
  const [probeOn, setProbeOn] = useState(false);
  const [probe, setProbe] = useState<{ point: V3; data: StudyProbe | null; error: string | null } | null>(null);
  const [flowOn, setFlowOn] = useState(true);
  const [labels, setLabels] = useState<{ key: string; x: number; y: number; text: string }[]>([]);
  /** Re-render after the scene changed on its own (async loads), so its data-* state is current. */
  const [, bump] = useState(0);
  const touch = () => bump((n) => n + 1);
  const rec = props.record;
  const simId = rec?.status === "done" ? rec.sim_id : null;
  const type = rec?.type ?? null;
  const reduced = reducedMotion();

  // the scene lives as long as the component
  useEffect(() => {
    const c = canvas.current; const h = host.current;
    if (!c || !h) return;
    let s: StudySceneHandle | null = null;
    try { s = createStudyScene(c, props.deps); } catch { s = null; } // no WebGL: the numbers still show
    sc.current = s;
    const measure = () => { const r = h.getBoundingClientRect(); if (r.width > 0 && r.height > 0) s?.resize(r.width, r.height); };
    measure();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(h);
    return () => { ro?.disconnect(); s?.dispose(); sc.current = null; };
  }, []);

  // the design model (setup), reloaded when the part changes
  useEffect(() => {
    if (!props.partId || simId) return;
    let live = true;
    setState("loading");
    void deps.partGlb(props.partId).then(async (bytes) => {
      if (!live || !bytes) { if (live) setState("none"); return; }
      const p = await loadPart(bytes);
      if (!live || !sc.current) { p.dispose(); return; }
      sc.current.clearResult();
      sc.current.setPart(p);
      setState("loaded");
    }).catch((e: unknown) => { if (live) setState(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [props.partId, props.mtime, simId]);

  // the result
  useEffect(() => {
    setInfo(null); setChannel(null); setRange(null); setSection(null); setProbe(null); setAnimate(false);
    if (!simId) { sc.current?.clearResult(); sc.current?.setFlow(null); return; }
    let live = true;
    setState("loading");
    void deps.resultGlb(simId).then(async (bytes) => {
      if (!live) return;
      if (!bytes || !sc.current) { setState(bytes ? "loaded" : "this study has no result model"); return; }
      const inf = await sc.current.loadResult(bytes);
      if (!live) return;
      setInfo(inf);
      const first = inf.channels.find((c) => c.name === inf.field) ?? inf.channels[0];
      setChannel(first?.name ?? null);
      const r = (rec?.results ?? {}) as Record<string, unknown>;
      setDeform(type === "modal" || type === "buckling" ? 1 : typeof r.deformed_scale === "number" ? r.deformed_scale : 0);
      setState("loaded");
    }).catch((e: unknown) => { if (live) setState(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [simId]);

  useEffect(() => {
    if (!channel || !sc.current) return;
    const f = sc.current.setChannel(channel, range);
    if (f && !range) setFull(f);
  }, [channel, range, info]);

  useEffect(() => { sc.current?.setDeform(deform); }, [deform, info, channel]);
  useEffect(() => { sc.current?.setAnimate(animate); touch(); }, [animate, info, channel]);

  // markers (setup view only)
  useEffect(() => {
    if (simId || !sc.current || state !== "loaded") { if (!simId) sc.current?.setMarkers([], [], null); setLabels([]); return; }
    sc.current.setMarkers(props.markers ?? [], props.faces ?? [], props.selectedCase ?? null);
    placeLabels();
    touch();
  }, [props.markers, props.faces, props.selectedCase, state, simId]);

  function placeLabels(): void {
    const s = sc.current; const b = s?.bounds();
    if (!s || !b) return;
    const out: { key: string; x: number; y: number; text: string }[] = [];
    (props.markers ?? []).forEach((m, i) => {
      if (m.role !== "load" || m.magnitude === null) return;
      const at = faceAnchor(m.face, b, props.faces ?? []);
      const p = at ? s.project(at.center) : null;
      if (p) out.push({ key: `${i}`, x: p[0], y: p[1], text: `${sig3(m.magnitude)} ${m.unit}` });
    });
    setLabels(out);
  }

  // wind: particles on the velocity slice through the middle of the part
  const frame = (rec?.mesh as { frame?: number[][] } | undefined)?.frame ?? null;
  useEffect(() => {
    const s = sc.current;
    if (!s || !simId || type !== "wind" || !info || !flowOn) { s?.setFlow(null); return; }
    let live = true;
    const b = info.bounds;
    void deps.section(simId, { axis: "y", offset: (b.min[1] + b.max[1]) / 2, channel: "velocity" }).then((sec) => {
      if (live) { sc.current?.setFlow(sec, frame); touch(); }
    }).catch(() => { if (live) sc.current?.setFlow(null); });
    return () => { live = false; };
  }, [simId, type, info, flowOn]);

  // section plane: clip + the core's values on it (scalar channels; wind shows speed)
  useEffect(() => {
    const s = sc.current;
    if (!s || !simId || !info) return;
    if (!section) { s.setSection(null); return; }
    let live = true;
    const ch = info.channels.find((c) => c.name === channel);
    const secChannel = type === "wind" ? "speed" : ch && ch.kind === "scalar" ? ch.name : type === "thermal" ? "temperature" : "von_mises";
    const r = range ?? full ?? [0, 1];
    s.setSection({ ...section, values: null, range: r });
    touch();
    const t = setTimeout(() => {
      void deps.section(simId, { ...section, channel: secChannel, n: 48 }).then((v) => {
        if (!live) return;
        const vals = v.values.flat(2).filter((x): x is number => typeof x === "number");
        const rr: [number, number] = secChannel === channel ? r : vals.length ? [Math.min(...vals), Math.max(...vals)] : r;
        sc.current?.setSection({ ...section, values: v, range: rr });
        touch();
      }).catch(() => { /* the plane still clips */ });
    }, 120);
    return () => { live = false; clearTimeout(t); };
  }, [section?.axis, section?.offset, simId, info, channel, range]);

  useEffect(() => { sc.current?.setProbe(probe?.point ?? null); }, [probe?.point]);

  // picking and probing
  const onClick = (e: MouseEvent) => {
    const s = sc.current;
    if (!s) return;
    if (props.picking && !simId) {
      const hit = s.pick(e.clientX, e.clientY, props.faces ?? []);
      if (hit?.face) props.onPicked?.(hit.face);
      return;
    }
    if (probeOn && simId) {
      const hit = s.pick(e.clientX, e.clientY);
      if (!hit) return;
      setProbe({ point: hit.point, data: null, error: null });
      void deps.probe(simId, hit.point).then((d) => setProbe({ point: hit.point, data: d, error: null }),
        (err: unknown) => setProbe({ point: hit.point, data: null, error: err instanceof Error ? err.message : String(err) }));
    }
  };
  useEffect(() => {
    if (!props.picking) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); props.onCancelPick?.(); } };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [props.picking]);

  const channels = info?.channels ?? [];
  const ch: Channel | undefined = channels.find((c) => c.name === channel);
  const modes = channels.filter((c) => /^mode_/.test(c.name));
  const isMode = !!ch && /^mode_/.test(ch.name);
  const canDeform = channels.some((c) => c.name === "displacement") || modes.length > 0;
  const shown = range ?? full;
  const bounds = info?.bounds;
  const axisIdx = section ? "xyz".indexOf(section.axis) : 1;
  const st = sc.current?.state();
  const caption = props.liveCaption;

  return (
    <div class={`sv${props.immersive ? " sv-imm" : ""}`} data-testid="study-view" data-channel={channel ?? ""} data-state={state}
      data-mode={simId ? "result" : "setup"} data-picking={props.picking ? "true" : undefined}>
      <div class="vp sv-vp" ref={host}>
        <canvas ref={canvas} class="vp-canvas" data-testid="study-canvas" onClick={onClick}
          style={{ cursor: props.picking || probeOn ? "crosshair" : undefined }}
          data-markers={st?.markers ?? 0} data-particles={st?.particles ?? 0} data-streamlines={st?.streamlines ?? 0}
          data-animating={st?.animating ? "true" : "false"} data-section={st?.section ? "true" : "false"} />
        <span class="tick tick-tl" /><span class="tick tick-tr" /><span class="tick tick-bl" /><span class="tick tick-br" />
        <span class="label vp-fig">{simId ? `FIG. — ${channelText(channel ?? "result").toUpperCase()}` : "FIG. — SETUP"}</span>
        {labels.map((l) => <span key={l.key} class="sv-mlabel" style={{ left: `${l.x}px`, top: `${l.y}px` }}>{l.text}</span>)}
        {props.picking && <div class="sv-hint" role="status">Click a face of the part · Esc to stop</div>}
        {caption && <div class="sv-caption" role="status" data-testid="live-caption">{caption}</div>}
        {state !== "loaded" && state !== "loading" && state !== "none" && <div class="st-sheet-msg text">{state}</div>}
        {probe && (
          <div class="sv-probe" data-testid="probe" role="status">
            <div class="faint">at {probe.point.map((x) => sig3(x)).join(", ")} mm</div>
            {probe.error && <div class="tone-red text">{probe.error}</div>}
            {probe.data && !probe.data.inside && <div class="faint">{probe.data.solid ? "inside the part" : "outside the result"}</div>}
            {probe.data?.inside && Object.entries(probe.data.values).filter(([, v]) => typeof v === "number").map(([k, v]) => (
              <div key={k}><b>{sig3(v as number)}</b> <span class="faint">{unitText(probe.data?.units[k] ?? "")} {channelText(k)}</span></div>
            ))}
            <button type="button" class="sv-x" aria-label="Close the probe" onClick={() => setProbe(null)}>✕</button>
          </div>
        )}
      </div>
      {simId && info && (
        <div class="sv-controls" data-testid="result-controls">
          {channels.length > 0 && (
            <label class="sv-ctl">
              <span class="label">Channel</span>
              <select value={channel ?? ""} data-testid="channel" onChange={(e) => { setRange(null); setChannel((e.target as HTMLSelectElement).value); }}>
                {channels.map((c) => <option key={c.name} value={c.name}>{channelText(c.name)}{c.freq_hz ? ` · ${sig3(c.freq_hz)} Hz` : c.factor ? ` · λ ${sig3(c.factor)}` : ""}</option>)}
              </select>
            </label>
          )}
          {canDeform && (
            <label class="sv-ctl">
              <span class="label">{isMode ? "Amplitude" : "Deformed ×"}</span>
              <input type="range" min={0} max={isMode ? 3 : Math.max(1, deformMax(rec))} step="any" value={deform} data-testid="deform"
                onInput={(e) => setDeform(Number((e.target as HTMLInputElement).value))} aria-valuetext={`${sig3(deform)} times`} />
              <span class="sv-num">{sig3(deform)}×</span>
            </label>
          )}
          {modes.length > 0 && (
            <Button onClick={() => { if (!isMode) setChannel(modes[0].name); setAnimate(!animate); }} aria-pressed={animate} data-testid="animate-modes"
              disabled={reduced} title={reduced ? "Reduced motion: the mode shape shows as a static frame" : undefined}>
              {animate ? "Pause mode" : "Play mode"}
            </Button>
          )}
          {type === "wind" && (
            <Button onClick={() => setFlowOn(!flowOn)} aria-pressed={flowOn} data-testid="toggle-flow">
              {flowOn ? (reduced ? "Hide streamlines" : "Hide airflow") : (reduced ? "Show streamlines" : "Show airflow")}
            </Button>
          )}
          <label class="sv-ctl">
            <span class="label">Section</span>
            <select value={section?.axis ?? ""} data-testid="section-axis" onChange={(e) => {
              const a = (e.target as HTMLSelectElement).value as "" | "x" | "y" | "z";
              if (!a || !bounds) { setSection(null); return; }
              const i = "xyz".indexOf(a);
              setSection({ axis: a, offset: (bounds.min[i] + bounds.max[i]) / 2 });
            }}>
              <option value="">off</option><option value="x">x plane</option><option value="y">y plane</option><option value="z">z plane</option>
            </select>
            {section && bounds && (
              <input type="range" min={bounds.min[axisIdx]} max={bounds.max[axisIdx]} step="any" value={section.offset} data-testid="section-offset"
                aria-label="Section plane position" onInput={(e) => setSection({ ...section, offset: Number((e.target as HTMLInputElement).value) })} />
            )}
            {section && <span class="sv-num">{sig3(section.offset)} mm</span>}
          </label>
          <Button onClick={() => { setProbeOn(!probeOn); if (probeOn) setProbe(null); }} aria-pressed={probeOn} data-testid="probe-toggle">{probeOn ? "Probing: click the part" : "Probe"}</Button>
        </div>
      )}
      {simId && ch && shown && (
        <Legend channel={ch} range={shown} full={full} onRange={(r) => setRange(r)} />
      )}
    </div>
  );
}

function deformMax(rec: StudyRecord | null): number {
  const s = (rec?.results as { deformed_scale?: unknown } | undefined)?.deformed_scale;
  return typeof s === "number" && s > 0 ? s * 4 : 10;
}

function Legend({ channel, range, full, onRange }: { channel: Channel; range: [number, number]; full: [number, number] | null; onRange: (r: [number, number] | null) => void }): JSX.Element {
  const units = /^mode_/.test(channel.name) ? "normalized" : unitText(channel.units);
  const ticks = useMemo(() => legendTicks(range[0], range[1]).reverse(), [range[0], range[1]]);
  const [lo, setLo] = useState(String(sig3(range[0])));
  const [hi, setHi] = useState(String(sig3(range[1])));
  useEffect(() => { setLo(String(sig3(range[0]))); setHi(String(sig3(range[1]))); }, [range[0], range[1]]);
  const apply = () => {
    const a = Number.parseFloat(lo); const b = Number.parseFloat(hi);
    if (Number.isFinite(a) && Number.isFinite(b) && b > a) onRange([a, b]);
  };
  return (
    <div class="sv-legend" data-testid="legend" aria-label={`${channelText(channel.name)} legend, ${sig3(range[0])} to ${sig3(range[1])} ${units}`}>
      <span class="label">{channelText(channel.name)}{units ? ` · ${units}` : ""}</span>
      <div class="sv-legend-body">
        <div class="st-legend-bar" style={{ background: legendGradient() }} />
        <div class="st-legend-ticks">{ticks.map((t, i) => <span key={i}>{sig3(t)}</span>)}</div>
      </div>
      <form class="sv-legend-range" onSubmit={(e) => { e.preventDefault(); apply(); }}>
        <label><span class="faint">max</span><input type="number" step="any" value={hi} data-testid="range-hi" onInput={(e) => setHi((e.target as HTMLInputElement).value)} onBlur={apply} /></label>
        <label><span class="faint">min</span><input type="number" step="any" value={lo} data-testid="range-lo" onInput={(e) => setLo((e.target as HTMLInputElement).value)} onBlur={apply} /></label>
        {full && (range[0] !== full[0] || range[1] !== full[1]) && <button type="button" class="scard-link" onClick={() => onRange(null)}>full range</button>}
        {/* two fields and no submit button: without this, Enter would not submit the form (B1-8) */}
        {/* rendered but unseen (a display:none default button is skipped by some engines) */}
        <button type="submit" aria-hidden="true" tabIndex={-1} style={{ position: "absolute", width: 0, height: 0, padding: 0, border: 0, opacity: 0, overflow: "hidden" }} />
      </form>
    </div>
  );
}
