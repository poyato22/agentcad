// Properties (the part menu): what the part weighs and measures, its material and its look, in
// one small sheet beside the row. Esc or a press outside closes it.
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { PartSummary } from "../../api/types";
import { appearanceName } from "../../lib/appearance";
import { fmtGrams } from "../../lib/format";
import { partName } from "../../lib/names";
import { actions } from "../../state/store";

export interface PartPropertiesProps { part: PartSummary; anchor: { left: number; top: number }; onClose(): void }

const n1 = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));

/** mm³, or cm³ from 10,000 mm³ on. */
export function fmtVolume(mm3: number): string {
  return mm3 >= 10_000 ? `${(mm3 / 1000).toFixed(1)} cm³` : `${n1(mm3)} mm³`;
}
/** mm², or cm² from 10,000 mm² on. */
export function fmtArea(mm2: number): string {
  return mm2 >= 10_000 ? `${(mm2 / 100).toFixed(1)} cm²` : `${n1(mm2)} mm²`;
}

export function PartProperties({ part, anchor, onClose }: PartPropertiesProps): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const [material, setMaterial] = useState<string | null>(null);
  const m = part.metrics;

  useEffect(() => {
    let live = true;
    Promise.resolve().then(() => actions.client().getProfile())
      .then((p) => { if (live) setMaterial(p.default_material); }, () => { /* offline: the row stays "—" */ });
    return () => { live = false; };
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const vh = window.innerHeight; const vw = window.innerWidth;
    el.style.left = `${Math.max(6, Math.min(anchor.left, vw - el.offsetWidth - 6))}px`;
    el.style.top = `${Math.max(6, Math.min(anchor.top, vh - el.offsetHeight - 6))}px`;
    el.focus();
  }, [anchor.left, anchor.top]);

  useEffect(() => {
    const onDown = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [onClose]);

  const size = m?.bbox.size;
  const rows: [string, string][] = [
    ["Mass", m ? fmtGrams(m.mass_g, 1) : "—"],
    ["Material", material === null ? "—" : `${material} (build default)`],
    ["Volume", m ? fmtVolume(m.volume_mm3) : "—"],
    ["Surface", m ? fmtArea(m.area_mm2) : "—"],
    ["Size", size ? `${n1(size[0])} × ${n1(size[1])} × ${n1(size[2])} mm` : "—"],
    ["Faces", m ? `${m.faces}${m.holes.length ? ` · ${m.holes.length} hole${m.holes.length === 1 ? "" : "s"}` : ""}` : "—"],
    ["Look", appearanceName(part.appearance) ?? "Plain"],
    ["Version", String(part.version ?? 1)],
  ];
  return (
    <div ref={ref} class="pprops" role="dialog" aria-label={`Properties of ${partName(part)}`} tabIndex={-1} data-testid="part-properties"
      style={{ left: `${anchor.left}px`, top: `${anchor.top}px` }}
      onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); } }}>
      <div class="pprops-head">
        <span class="pprops-title">{partName(part)}</span>
        <button type="button" class="pprops-x" aria-label="Close properties" onClick={onClose}>×</button>
      </div>
      {!m && <p class="faint pprops-note">Not built yet: no measurements.</p>}
      <dl class="pprops-list">
        {rows.map(([k, v]) => <div key={k} class="pprops-row"><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
    </div>
  );
}
