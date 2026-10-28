// Drawing page: the current part's A4 engineering drawing (four views, dimensions, title block)
// as the core renders it (GET /parts/{id}/drawing.svg|.json), with pan/zoom and export.
//
// Safety: the SVG comes from the core and is shown ONLY as an <img> from a blob: URL. An SVG in
// an <img> never runs scripts or loads anything, and its markup never enters the DOM (no
// innerHTML anywhere). The drawing regenerates when the part is rebuilt (its mtime changes) or
// the material changes. Export: SVG (the same bytes), DXF (from the core), PDF (the webview's
// print dialog, which prints just the sheet; "Save as PDF" there).
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { CoreError } from "../../../api/client";
import type { DrawingInfo } from "../../../api/types";
import { partLabel } from "../../../lib/names";
import { activePart } from "../../../state/selection";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { PageShell } from "../PageShell";
import { fitView, panBy, SHEET_PX_PER_MM, zoomAt, zoomPercent, type Size, type View } from "./panzoom";
import { PartPicker } from "./PartPicker";
import { saveBlob } from "./save";
import "./drawing.css";

const ZOOM_STEP = 1.25;
const A4: [number, number] = [297, 210];

type Loaded = { partId: string; blob: Blob; url: string; info: DrawingInfo | null };
type Failure = { partId: string; message: string; hint: string | null; notBuilt: boolean };

function failureOf(partId: string, e: unknown): Failure {
  if (e instanceof CoreError) return { partId, message: e.message, hint: e.hint, notBuilt: e.code === "not_found" };
  return { partId, message: e instanceof Error ? e.message : String(e), hint: null, notBuilt: false };
}

const fmtMm = (v: number) => String(Math.round(v * 100) / 100);

export function DrawingPage(): JSX.Element {
  const part = activePart.value;
  const [material, setMaterial] = useState<string | null>(null);
  const [materials, setMaterials] = useState<string[]>([]);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [exportError, setExportError] = useState<string | null>(null);

  // materials for the title block (the print catalogue); without it, only the profile default
  useEffect(() => {
    let live = true;
    actions.client().printCapabilities().then(
      (c) => { if (live) setMaterials(c.materials.map((m) => m.name)); },
      () => { /* optional: the drawing still uses the profile's default material */ },
    );
    return () => { live = false; };
  }, []);

  const partId = part?.part_id ?? null;
  const mtime = part?.mtime ?? 0;
  useEffect(() => {
    if (!partId) return;
    let live = true;
    setBusy(true);
    setFailure(null);
    const c = actions.client();
    Promise.all([c.drawingFile(partId, "svg", material), c.drawingInfo(partId, material).catch(() => null)]).then(
      ([blob, info]) => {
        if (!live) return;
        setLoaded({ partId, blob, url: URL.createObjectURL(blob), info });
        setBusy(false);
      },
      (e: unknown) => {
        if (!live) return;
        setLoaded(null);
        setFailure(failureOf(partId, e));
        setBusy(false);
      },
    );
    return () => { live = false; };
  }, [partId, mtime, material, attempt]);

  // a blob URL is released only once the next drawing (or an error) has replaced it, or on unmount,
  // so the old sheet stays visible while a new one is made
  const loadedUrl = loaded?.url ?? null;
  useEffect(() => () => { if (loadedUrl) URL.revokeObjectURL(loadedUrl); }, [loadedUrl]);

  async function exportDxf(): Promise<void> {
    if (!partId) return;
    setExportError(null);
    try {
      saveBlob(await actions.client().drawingFile(partId, "dxf", material), `${partId}.dxf`);
    } catch (e) {
      setExportError(failureOf(partId, e).message);
    }
  }

  const shown = loaded && loaded.partId === partId ? loaded : null;
  const label = partId ? partLabel(partId) : "";

  return (
    <PageShell title="Drawing">
      <div class="drawing">
        <div class="pg-toolbar" role="toolbar" aria-label="Drawing">
          <PartPicker />
          <label class="pg-field">
            <span class="label">Material</span>
            <select aria-label="Material" value={material ?? ""} disabled={!partId}
              onChange={(e) => setMaterial(e.currentTarget.value || null)}>
              <option value="">profile default</option>
              {materials.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <span class="pg-spacer" />
          <Button disabled={!shown} onClick={() => shown && saveBlob(shown.blob, `${shown.partId}.svg`)}>Export SVG</Button>
          <Button disabled={!shown} onClick={() => { void exportDxf(); }}>Export DXF</Button>
          <Button disabled={!shown} title="Opens the print dialog; choose Save as PDF" onClick={() => window.print()}>Export PDF</Button>
        </div>
        {exportError && <div class="pg-error text" role="alert">{exportError}</div>}
        {!part && <p class="faint pg-empty">No parts. Build one on the Design page.</p>}
        {part && failure && failure.partId === partId && (
          <div class="pg-band" role="alert">
            <span class="pg-error text">
              {failure.notBuilt ? `${label} has no built model yet.` : `Drawing failed: ${failure.message}`}
            </span>
            {failure.hint && <span class="dim text"> — {failure.hint}</span>}
            <Button onClick={() => setAttempt((n) => n + 1)}>Try again</Button>
          </div>
        )}
        {shown && (
          <>
            <SheetView key={shown.url} url={shown.url} alt={`Drawing · ${label}`} sheet={shown.info?.sheet ?? A4} />
            {shown.info && <Facts info={shown.info} />}
            {/* the print copy: same blob, untransformed, only visible when printing (Export PDF) */}
            <div class="drawing-print" aria-hidden="true"><img src={shown.url} alt="" /></div>
          </>
        )}
        {part && busy && !shown && !failure && <p class="faint pg-empty">making the drawing…</p>}
        {busy && shown && <span class="faint drawing-busy" role="status">updating…</span>}
      </div>
    </PageShell>
  );
}

function Facts({ info }: { info: DrawingInfo }): JSX.Element {
  const [w, h] = info.sheet;
  return (
    <ul class="drawing-facts" aria-label="Drawing facts">
      <li>scale {info.scale_text}</li>
      <li>{w === 297 && h === 210 ? "A4 " : ""}{fmtMm(w)} × {fmtMm(h)} sheet</li>
      <li>overall {info.overall_mm.map(fmtMm).join(" × ")} mm</li>
      {info.holes.map((hole) => <li key={hole} class="text">{hole}</li>)}
      <li class="text">{info.material}</li>
      <li>views {info.views.join(" · ")}</li>
      <li>{info.date}</li>
    </ul>
  );
}

/** The sheet in a pannable / zoomable viewport: drag to pan, wheel or ± to zoom, 0 or Fit to reset. */
function SheetView({ url, alt, sheet }: { url: string; alt: string; sheet: [number, number] }): JSX.Element {
  const content: Size = { w: sheet[0] * SHEET_PX_PER_MM, h: sheet[1] * SHEET_PX_PER_MM };
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<Size>({ w: 0, h: 0 });
  // null: follow the fitted view (until the user pans or zooms)
  const [view, setView] = useState<View | null>(null);
  const drag = useRef<{ x: number; y: number; id: number } | null>(null);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fit = fitView(size, content);
  const v = view ?? fit;
  const centre = () => ({ x: size.w / 2, y: size.h / 2 });
  const zoom = (f: number, at = centre()) => setView(zoomAt(v, f, at.x, at.y));

  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const r = box.current?.getBoundingClientRect();
    const at = r ? { x: e.clientX - r.left, y: e.clientY - r.top } : centre();
    if (e.ctrlKey || e.metaKey || !e.deltaX) zoom(Math.exp(-e.deltaY * 0.0015), at); // pinch / wheel
    else setView(panBy(v, -e.deltaX, -e.deltaY));
  };
  // a non-passive wheel listener, so preventDefault stops the page from scrolling
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  const onKey = (e: KeyboardEvent) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "+" || e.key === "=") zoom(ZOOM_STEP);
    else if (e.key === "-" || e.key === "_") zoom(1 / ZOOM_STEP);
    else if (e.key === "0") setView(null);
    else if (e.key.startsWith("Arrow")) {
      const d = 40;
      setView(panBy(v, e.key === "ArrowLeft" ? d : e.key === "ArrowRight" ? -d : 0, e.key === "ArrowUp" ? d : e.key === "ArrowDown" ? -d : 0));
    } else return;
    e.preventDefault();
  };

  return (
    <div class="drawing-stage">
      <div class="drawing-tools" role="group" aria-label="Zoom">
        <Button variant="ghost" aria-label="Zoom out" onClick={() => zoom(1 / ZOOM_STEP)}>−</Button>
        <span class="drawing-zoom dim" data-testid="zoom">{zoomPercent(v, fit)}</span>
        <Button variant="ghost" aria-label="Zoom in" onClick={() => zoom(ZOOM_STEP)}>+</Button>
        <Button variant="ghost" onClick={() => setView(null)}>Fit</Button>
      </div>
      <div ref={box} class="drawing-viewport" tabIndex={0} aria-label="Drawing view: drag to pan, scroll or ± to zoom, 0 to fit"
        onKeyDown={onKey}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          drag.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
          (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d || d.id !== e.pointerId) return;
          drag.current = { ...d, x: e.clientX, y: e.clientY };
          setView(panBy(v, e.clientX - d.x, e.clientY - d.y));
        }}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
        onDblClick={() => setView(null)}>
        <img class="drawing-sheet" src={url} alt={alt} draggable={false} data-scale={v.scale.toFixed(4)}
          style={{ width: `${content.w}px`, height: `${content.h}px`, transform: `translate(${v.x}px, ${v.y}px) scale(${v.scale})` }} />
      </div>
    </div>
  );
}
