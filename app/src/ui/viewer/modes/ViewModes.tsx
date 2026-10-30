// The view-mode strip in the sheet's bottom-left corner: X-ray, section, explode, reference. Each
// mode that is on opens its own row of controls above the strip. The strip drives a
// ModesController on the viewer it is given (the Design sheet or the immersive view).
import type { JSX, RefObject } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import * as THREE from "three";
import { partLabel } from "../../../lib/names";
import type { ModeHost } from "../scene";
import { VIEW_NAMES, type ViewName } from "./camera";
import { ModesController, restBox } from "./controller";
import { clearReference, goToView, patchReference, reference, ReferenceOverlay, setReferenceFile } from "./ReferenceOverlay";
import { sectionAt, sectionRange, unit } from "./section";
import { SectionHandle } from "./SectionHandle";
import { MODES_OFF, XRAY_OPACITY, type ModesState, type Vec3 } from "./state";
import "./modes.css";

/** Owns a ModesController for `host` and applies `modes` to it. `animate` (read once per change)
 *  lets the explode glide. */
export function useModesController(host: ModeHost | null, modes: ModesState, animate: RefObject<boolean>): ModesController | null {
  const [c, setC] = useState<ModesController | null>(null);
  const [, setApplied] = useState(0);
  useEffect(() => {
    if (!host) return;
    const ctl = new ModesController(host);
    setC(ctl);
    return () => { ctl.dispose(); setC(null); };
  }, [host]);
  useEffect(() => {
    if (!c) return;
    const before = c.exploder.count;
    c.set(modes, { animate: animate.current ?? false });
    animate.current = false;
    // what can explode is known once applied
    if (c.exploder.count !== before) setApplied((n) => n + 1);
  }, [c, modes]);
  return c;
}

const AXES: { key: string; normal: Vec3 }[] = [{ key: "X", normal: [1, 0, 0] }, { key: "Y", normal: [0, 1, 0] }, { key: "Z", normal: [0, 0, 1] }];

function axisOf(n: Vec3): string | null {
  const u = unit(n);
  for (const a of AXES) if (Math.abs(Math.abs(u.dot(new THREE.Vector3(...a.normal))) - 1) < 1e-6) return a.key;
  return null;
}

const pct = (v: number) => `${Math.round(v * 100)}%`;

export interface ViewModesProps {
  host: ModeHost | null;
  modes: ModesState;
  onModes(next: ModesState, opts?: { animate?: boolean }): void;
  /** The part X-ray "this part" means (the selected one). */
  focusId: string | null;
  /** The parts shown, for X-ray per part. */
  partIds: readonly string[];
  /** Reference overlay (the Design sheet only). */
  withReference?: boolean;
  /** Set true before changing `modes` from outside to let that change glide (the explode). */
  animateRef?: RefObject<boolean>;
}

export function ViewModes({ host, modes, onModes, focusId, partIds, withReference = true, animateRef }: ViewModesProps): JSX.Element {
  const ownAnimate = useRef(false);
  const animate = animateRef ?? ownAnimate;
  const ctl = useModesController(host, modes, animate);
  const [, setTick] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const [refError, setRefError] = useState<string | null>(null);
  useEffect(() => host?.onRestyle(() => setTick((t) => t + 1)), [host]);

  const set = (patch: Partial<ModesState>, glide = false) => { animate.current = glide; onModes({ ...modes, ...patch }, { animate: glide }); };
  const xrayOn = modes.xray.opacity < 0.999;
  const sec = modes.section;
  const exploding = modes.explode > 0;
  const canExplode = (ctl?.exploder.count ?? 0) >= 2;
  const ref = withReference ? reference.value : null;
  const box = host ? restBox(host.parts()) : null;
  const n = unit(sec.normal);
  const range = box ? sectionRange(box, n) : [0, 0];
  const at = sectionAt(sec, box);
  const step = Math.max((range[1] - range[0]) / 200, 0.01);

  const togglePart = (id: string) => {
    const now = modes.xray.parts ?? [];
    const next = now.includes(id) ? now.filter((p) => p !== id) : [...now, id];
    set({ xray: { ...modes.xray, parts: next.length ? next : null } });
  };

  return (
    <>
      {sec.on && host && <SectionHandle host={host} section={sec} onAt={(v) => set({ section: { ...sec, at: v } })} />}
      {withReference && <ReferenceOverlay host={host} />}
      <div class="vm" data-testid="view-modes">
        {(xrayOn || sec.on || exploding || ref) && (
          <div class="vm-rows">
            {xrayOn && (
              <div class="vm-row" data-mode="xray">
                <span class="vm-name">X-ray</span>
                <input type="range" min={0.05} max={0.95} step={0.05} value={modes.xray.opacity} aria-label="X-ray opacity"
                  onInput={(e) => set({ xray: { ...modes.xray, opacity: Number(e.currentTarget.value) } })} />
                <span class="vm-val">{pct(modes.xray.opacity)}</span>
                <span class="vm-chips" role="group" aria-label="X-ray which parts">
                  <button type="button" class="vm-chip" aria-pressed={modes.xray.parts === null} onClick={() => set({ xray: { ...modes.xray, parts: null } })}>all</button>
                  {partIds.length > 1
                    ? partIds.map((id) => (
                      <button key={id} type="button" class="vm-chip" aria-pressed={modes.xray.parts?.includes(id) ?? false} onClick={() => togglePart(id)} title={id}>{partLabel(id)}</button>
                    ))
                    : focusId && <button type="button" class="vm-chip" aria-pressed={modes.xray.parts?.includes(focusId) ?? false} onClick={() => set({ xray: { ...modes.xray, parts: [focusId] } })}>this part</button>}
                </span>
              </div>
            )}
            {sec.on && (
              <div class="vm-row" data-mode="section">
                <span class="vm-name">Section</span>
                <span class="vm-chips" role="group" aria-label="Section plane normal">
                  {AXES.map((a) => (
                    <button key={a.key} type="button" class="vm-chip" aria-pressed={axisOf(sec.normal) === a.key}
                      onClick={() => set({ section: { on: true, normal: a.normal, at: null } })}>{a.key}</button>
                  ))}
                  <button type="button" class="vm-chip" title="Look at the cut from the other side"
                    onClick={() => set({ section: { on: true, normal: [-sec.normal[0], -sec.normal[1], -sec.normal[2]], at: -at } })}>flip</button>
                </span>
                <input type="range" min={range[0]} max={range[1]} step={step} value={at} aria-label="Section plane position" data-testid="section-slider"
                  onInput={(e) => set({ section: { ...sec, at: Number(e.currentTarget.value) } })} />
                <span class="vm-val">{`${at.toFixed(1)} mm`}</span>
              </div>
            )}
            {exploding && (
              <div class="vm-row" data-mode="explode">
                <span class="vm-name">Explode</span>
                <input type="range" min={0} max={1} step={0.01} value={modes.explode} aria-label="Explode amount" data-testid="explode-slider"
                  onInput={(e) => set({ explode: Number(e.currentTarget.value) })} />
                <span class="vm-val">{pct(modes.explode)}</span>
              </div>
            )}
            {ref && (
              <div class="vm-row" data-mode="reference">
                <span class="vm-name">Reference</span>
                <span class="vm-chips" role="group" aria-label="Pin the reference to a view">
                  {VIEW_NAMES.filter((v) => v !== "iso").map((v: ViewName) => (
                    <button key={v} type="button" class="vm-chip" aria-pressed={ref.view === v} onClick={() => { patchReference({ view: v }); if (host) goToView(host, v); }}>{v}</button>
                  ))}
                </span>
                <input type="range" min={0.1} max={1} step={0.05} value={ref.opacity} aria-label="Reference opacity" onInput={(e) => patchReference({ opacity: Number(e.currentTarget.value) })} />
                <span class="vm-val">{pct(ref.opacity)}</span>
                <input type="range" min={0.25} max={3} step={0.05} value={ref.scale} aria-label="Reference size" onInput={(e) => patchReference({ scale: Number(e.currentTarget.value) })} />
                <button type="button" class="vm-chip" onClick={() => fileRef.current?.click()}>replace</button>
                <button type="button" class="vm-chip" onClick={() => { clearReference(); setRefError(null); }}>remove</button>
              </div>
            )}
          </div>
        )}
        {refError && <div class="vm-error text" role="alert">{refError}</div>}
        <div class="vm-strip" role="toolbar" aria-label="View modes">
          <button type="button" class="vt-btn" aria-pressed={xrayOn} data-active={xrayOn} title="See through the parts to what is inside"
            onClick={() => set({ xray: { ...modes.xray, opacity: xrayOn ? 1 : XRAY_OPACITY } })}>x-ray</button>
          <button type="button" class="vt-btn" aria-pressed={sec.on} data-active={sec.on} title="Cut the model with a plane"
            onClick={() => set({ section: { ...sec, on: !sec.on } })}>section</button>
          <button type="button" class="vt-btn" aria-pressed={exploding} data-active={exploding} disabled={!canExplode && !exploding}
            title={canExplode ? "Move the parts apart" : "Explode needs two or more parts or bodies"}
            onClick={() => set({ explode: exploding ? 0 : 1 }, true)}>explode</button>
          {withReference && (
            <button type="button" class="vt-btn" aria-pressed={ref !== null} data-active={ref !== null} title="Lay a reference image over a standard view"
              onClick={() => { if (ref) clearReference(); else fileRef.current?.click(); }}>reference</button>
          )}
          {(xrayOn || sec.on || exploding) && (
            <button type="button" class="vt-btn" title="Turn every view mode off" onClick={() => set(MODES_OFF, modes.explode > 0)}>clear</button>
          )}
        </div>
        {withReference && (
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden data-testid="reference-file"
            onChange={(e) => {
              const f = e.currentTarget.files?.[0];
              e.currentTarget.value = "";
              if (!f) return;
              if (!setReferenceFile(f)) { setRefError("Choose a PNG, JPEG, WebP or GIF image."); return; }
              setRefError(null);
              if (host) goToView(host, reference.peek()?.view ?? "front");
            }} />
        )}
      </div>
    </>
  );
}
