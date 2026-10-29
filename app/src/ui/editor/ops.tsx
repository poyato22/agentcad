// Feature ops from a face / edge pick (POST /parts/{id}/ops, C6): hole and shell on a face,
// fillet and chamfer on an edge. Opened by clicking the model (a callout with a leader line to the
// pick) or from the viewer's "Edit features" button, where the face / edge is chosen from a list
// (the keyboard way in). Each op becomes a recorded `# mises-op` block in the part's script, so
// the agent sees it and keeps it. A pick names faces / edges of the version it was made on: if the
// part changed meanwhile the core answers 409 and the op asks for a new pick.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { buildErrorLine, type OpKind, type OpRequest, type PartEdge } from "../../api/builds";
import { CoreError } from "../../api/client";
import type { PartFace, PartSummary, Vec3 } from "../../api/types";
import { partName } from "../../lib/names";
import { fmtNum, noteMyEdit } from "../../state/partEdits";
import { actions } from "../../state/store";
import { describeEdge, describeFace } from "../viewer/pick";
import "./editor.css";

export type FeatureOp = Exclude<OpKind, "extrude">;

export interface OpsTarget { face: PartFace | null; edge: PartEdge | null; point: Vec3 | null }

const FACE_OPS: FeatureOp[] = ["hole", "shell"];
const EDGE_OPS: FeatureOp[] = ["fillet", "chamfer"];
const LABEL: Record<FeatureOp, string> = { hole: "Hole", shell: "Shell", fillet: "Fillet", chamfer: "Chamfer" };

export function opsFor(t: OpsTarget, keyboard: boolean): FeatureOp[] {
  if (keyboard) return [...FACE_OPS, ...EDGE_OPS];
  const out: FeatureOp[] = [];
  if (t.edge) out.push(...EDGE_OPS);
  if (t.face && t.face.type === "plane") out.push(...FACE_OPS);
  return out;
}

export interface OpParams { size: number; through: boolean; depth: number }

/** The request for an op on a target, or why it can't be made. */
export function buildOp(op: FeatureOp, faceId: string | null, edgeId: string | null, p: OpParams, point: Vec3 | null): OpRequest | string {
  if (!(p.size > 0)) return "Enter a size above 0.";
  if (op === "hole") {
    if (!faceId) return "Pick a flat face for the hole.";
    if (!p.through && !(p.depth > 0)) return "Enter a depth above 0, or make it a through hole.";
    return { op, target: { face_id: faceId }, params: { diameter: p.size, through: p.through, ...(p.through ? {} : { depth: p.depth }), ...(point ? { point } : {}) } };
  }
  if (op === "shell") {
    if (!faceId) return "Pick the face to open.";
    return { op, target: { face_ids: [faceId] }, params: { thickness: p.size } };
  }
  if (!edgeId) return "Pick an edge.";
  return op === "fillet"
    ? { op, target: { edge_ids: [edgeId] }, params: { radius: p.size } }
    : { op, target: { edge_ids: [edgeId] }, params: { length: p.size } };
}

/** "added a Ø3 mm through hole on quad arm (face f5)". */
export function describeOp(req: OpRequest, name: string): string {
  switch (req.op) {
    case "hole": {
      const p = req.params;
      return `added a Ø${fmtNum(p.diameter)} mm ${p.through ? "through hole" : `hole ${fmtNum(p.depth ?? 0)} mm deep`} on ${name} (face ${req.target.face_id})`;
    }
    case "shell": return `shelled ${name} to ${fmtNum(req.params.thickness)} mm walls, open at face ${req.target.face_ids.join(", ")}`;
    case "fillet": return `rounded edge ${req.target.edge_ids.join(", ")} of ${name} · r ${fmtNum(req.params.radius)} mm`;
    case "chamfer": return `chamfered edge ${req.target.edge_ids.join(", ")} of ${name} · ${fmtNum(req.params.length)} mm`;
  }
}

function defaultSize(op: FeatureOp, face: PartFace | null): number {
  if (op === "hole") return face ? Math.max(1, Math.min(5, Number((Math.sqrt(face.area) / 4).toFixed(1)))) : 3;
  if (op === "shell") return 1.5;
  return 1;
}

const SIZE_LABEL: Record<FeatureOp, string> = { hole: "Diameter", shell: "Wall", fillet: "Radius", chamfer: "Length" };

export interface OpsMenuProps {
  part: PartSummary;
  target: OpsTarget;
  /** The part version the pick was made on (the op's base_version). */
  baseVersion: number;
  /** Opened without a pick (toolbar / keyboard): choose the face / edge from lists. */
  keyboard: boolean;
  /** Where to draw the callout (px in the viewer), and the pick it points at. */
  at: { x: number; y: number } | null;
  bounds: { w: number; h: number };
  /** The face / edge the menu now targets (the viewer draws the blue marker). */
  onTarget(face: PartFace | null, edge: PartEdge | null): void;
  onClose(): void;
}

export function OpsMenu({ part, target, baseVersion, keyboard, at, bounds, onTarget, onClose }: OpsMenuProps): JSX.Element {
  const choices = opsFor(target, keyboard);
  const [op, setOp] = useState<FeatureOp>(choices[0] ?? "hole");
  const [faceId, setFaceId] = useState<string | null>(target.face?.id ?? null);
  const [edgeId, setEdgeId] = useState<string | null>(target.edge?.id ?? null);
  const face = part.metrics?.face_list?.find((f) => f.id === faceId) ?? null;
  const edge = part.metrics?.edge_list?.find((e) => e.id === edgeId) ?? null;
  const [size, setSize] = useState<string>(String(defaultSize(op, target.face)));
  const [through, setThrough] = useState(true);
  const [depth, setDepth] = useState("5");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLFormElement>(null);
  const onFace = op === "hole" || op === "shell";
  const name = partName(part);

  useEffect(() => { onTarget(onFace ? face : null, onFace ? null : edge); }, [op, faceId, edgeId]);
  useEffect(() => { box.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus(); }, []);

  const pickOp = (o: FeatureOp) => { setOp(o); setSize(String(defaultSize(o, face))); setError(null); };

  async function apply(e: Event): Promise<void> {
    e.preventDefault();
    if (busy) return;
    // the click point only places a hole on the face it was clicked on
    const point = target.face && faceId === target.face.id ? target.point : null;
    const req = buildOp(op, faceId, edgeId, { size: Number(size), through, depth: Number(depth) }, point);
    if (typeof req === "string") { setError(req); return; }
    setBusy(true);
    setError(null);
    try {
      const r = await actions.client().partOp(part.part_id, req, baseVersion);
      noteMyEdit(part.part_id, r.version, describeOp(req, name));
      onClose();
    } catch (err) {
      setBusy(false);
      if (err instanceof CoreError && err.status === 409) {
        setError("This part changed since you picked (an agent's edit or yours). Click the model again to pick on the new version.");
      } else if (err instanceof CoreError && err.status === 422) {
        setError(`The ${LABEL[op].toLowerCase()} didn't build: ${buildErrorLine(err.message)}`);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
  }

  // callout placement: right-below the pick, kept inside the sheet
  const W = 248; const H = 210;
  const x = at ? Math.min(Math.max(8, at.x + 28), Math.max(8, bounds.w - W - 8)) : 22;
  const y = at ? Math.min(Math.max(8, at.y + 20), Math.max(8, bounds.h - H - 8)) : 52;
  const faces = part.metrics?.face_list ?? [];
  const edges = part.metrics?.edge_list ?? [];

  return (
    <>
      {at && (
        <svg class="ops-leader" width={bounds.w} height={bounds.h} aria-hidden="true">
          <circle cx={at.x} cy={at.y} r="2.5" />
          <line x1={at.x} y1={at.y} x2={x} y2={y + 10} />
        </svg>
      )}
      <form ref={box} class="ops" style={{ left: `${x}px`, top: `${y}px` }} role="dialog" aria-label={`Add a feature to ${name}`}
        data-testid="ops-menu" onSubmit={(e) => { void apply(e); }}
        onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); } }}>
        <div class="ops-head">
          <span class="ops-title">{name}</span>
          <button type="button" class="ops-x" aria-label="Close" onClick={onClose}>×</button>
        </div>
        {choices.length === 0
          ? <p class="ops-note faint">Nothing to add here: click a flat face for a hole or shell, or near an edge to round or chamfer it.</p>
          : (
            <>
              <div class="ops-kinds" role="radiogroup" aria-label="Feature">
                {choices.map((o, i) => (
                  <label key={o} class={op === o ? "ops-kind on" : "ops-kind"}>
                    <input type="radio" name="op" value={o} checked={op === o} onChange={() => pickOp(o)} data-autofocus={i === 0 ? "" : undefined} />
                    {LABEL[o]}
                  </label>
                ))}
              </div>
              {keyboard || (onFace ? !target.face : !target.edge)
                ? (
                  <label class="ops-field">
                    <span>{onFace ? "Face" : "Edge"}</span>
                    {onFace
                      ? (
                        <select value={faceId ?? ""} onChange={(e) => setFaceId((e.target as HTMLSelectElement).value || null)}>
                          <option value="">Choose a face…</option>
                          {faces.filter((f) => f.type === "plane").map((f) => <option key={f.id} value={f.id}>{describeFace(f)}</option>)}
                        </select>
                      )
                      : (
                        <select value={edgeId ?? ""} onChange={(e) => setEdgeId((e.target as HTMLSelectElement).value || null)}>
                          <option value="">Choose an edge…</option>
                          {edges.map((ed) => <option key={ed.id} value={ed.id}>{describeEdge(ed)}</option>)}
                        </select>
                      )}
                  </label>
                )
                : <div class="ops-target faint" data-target>{onFace ? (face ? describeFace(face) : "") : (edge ? describeEdge(edge) : "")}</div>}
              <label class="ops-field">
                <span>{SIZE_LABEL[op]}</span>
                <input class="ops-num" inputMode="decimal" value={size} aria-label={`${SIZE_LABEL[op]} (mm)`}
                  onInput={(e) => setSize((e.target as HTMLInputElement).value)} />
                <span class="faint">mm</span>
              </label>
              {op === "hole" && (
                <div class="ops-field">
                  <label class="ops-check"><input type="checkbox" checked={through} onChange={(e) => setThrough((e.target as HTMLInputElement).checked)} />Through</label>
                  {!through && (
                    <>
                      <input class="ops-num" inputMode="decimal" value={depth} aria-label="Depth (mm)" onInput={(e) => setDepth((e.target as HTMLInputElement).value)} />
                      <span class="faint">mm deep</span>
                    </>
                  )}
                </div>
              )}
              {error && <p class="ops-error" role="alert">{error}</p>}
              <div class="ops-actions">
                <button type="submit" class="btn" disabled={busy} aria-busy={busy || undefined}>{busy ? "Rebuilding…" : `Add ${LABEL[op].toLowerCase()}`}</button>
                <button type="button" class="btn btn-ghost" onClick={onClose}>Cancel</button>
              </div>
            </>
          )}
      </form>
    </>
  );
}
