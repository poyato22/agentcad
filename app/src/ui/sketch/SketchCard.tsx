// The concept sketch card (View stream): the build's sketch, big, with its versions (v1 v2 …),
// a side-by-side compare with the version before, and the decisions — mark it up (opens the
// full-window review), approve it, or skip the sketch. It follows the build's concept view, so
// a card for v1 shows v2 as soon as the agent answers.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { ConceptSketch, ConceptView } from "../../api/concept";
import { activeBuildId } from "../../state/builds";
import { Button } from "../common/Button";
import { useImageUrl } from "../console/cards/critique/images";
import { SketchImage, type NumberedMark } from "./SketchImage";
import { approve, concepts, loadConcept, notices, openReview, skip } from "./state";
import "./sketch.css";

export function useConcept(projectId: string | null): ConceptView | null {
  const view = projectId ? concepts.value[projectId] ?? null : null;
  useEffect(() => { if (projectId && !view) void loadConcept(projectId); }, [projectId]);
  return view;
}

const STATUS_TEXT: Readonly<Record<string, string>> = {
  none: "No sketch yet", pitching: "Choosing a direction", sketching: "Waiting for your review",
  approved: "Approved", skipped: "Sketch skipped",
};

/** The marks of the newest feedback round on `sketch`, for the "what you marked" overlay. */
function lastMarks(sketch: ConceptSketch, image: number): NumberedMark[] {
  const fb = sketch.feedback?.[sketch.feedback.length - 1];
  return fb ? fb.marks.filter((m) => m.image === image).map((m) => ({ n: m.n, mark: m })) : [];
}

function SupersededRow({ projectId, sketch, latest }: { projectId: string; sketch: ConceptSketch; latest: number }): JSX.Element {
  const img = sketch.images[0];
  const url = useImageUrl(img ? { id: img.render_id } : null);
  return (
    <button type="button" tabIndex={0} class="sk sk-row" data-testid="sketch-card-old" data-version={sketch.version}
      onClick={() => openReview({ projectId, sketchId: sketch.sketch_id, image: 0 })} aria-label={`Open concept sketch v${sketch.version}`}>
      <span class="sk-row-thumb">{url && <img src={url} alt="" draggable={false} />}</span>
      <span class="sk-row-text"><span class="sk-title">Concept sketch v{sketch.version}</span>
        <span class="sk-dim">Revised as v{latest} below{sketch.feedback?.length ? " after your marks" : ""}</span></span>
    </button>
  );
}

export interface SketchCardProps { projectId: string | null; sketchId?: string | null }

export function SketchCard({ projectId: given, sketchId }: SketchCardProps): JSX.Element {
  const projectId = given ?? activeBuildId.value;
  const view = useConcept(projectId);
  const sketches = view?.sketches ?? [];
  const latest = sketches[sketches.length - 1] ?? null;
  // the card opens on the newest version; the user can step back through the chips
  const [picked, setPicked] = useState<string | null>(null);
  const [image, setImage] = useState(0);
  const [compare, setCompare] = useState(false);
  const [busy, setBusy] = useState(false);
  const current = sketches.find((s) => s.sketch_id === picked) ?? latest ?? sketches.find((s) => s.sketch_id === sketchId) ?? null;
  if (!projectId || !view || !current) {
    return <div class="sk sk-card sk-card-empty" data-testid="sketch-card" data-state="loading"><span class="sk-dim">Loading the concept sketch…</span></div>;
  }
  // an older version's card stays in the thread as one quiet line: the newest card below holds
  // every version (chips) and the decisions, so there is one place to act
  const own = sketchId ? sketches.find((s) => s.sketch_id === sketchId) : null;
  if (own && latest && own !== latest) return <SupersededRow projectId={projectId} sketch={own} latest={latest.version} />;
  const rec = view.concept;
  const open = rec.status === "approved" || rec.status === "skipped";
  const prev = sketches.find((s) => s.sketch_id === current.parent_sketch_id) ?? sketches[sketches.indexOf(current) - 1] ?? null;
  const img = current.images[Math.min(image, current.images.length - 1)];
  const prevImg = prev?.images.find((i) => i.view === img?.view) ?? prev?.images[0] ?? null;
  const fbCount = current.feedback?.length ?? 0;
  const isLatest = current === latest;
  const approvedHere = rec.status === "approved" && rec.approved_sketch_id === current.sketch_id;
  const note = notices.value[projectId];
  const act = async (f: () => Promise<boolean>) => { setBusy(true); try { await f(); } finally { setBusy(false); } };
  return (
    <section class="sk sk-card" data-testid="sketch-card" data-version={current.version} data-status={rec.status} aria-label={`Concept sketch v${current.version}`}>
      <header class="sk-head">
        <h3 class="sk-title">Concept sketch</h3>
        <div class="sk-chips" role="group" aria-label="Versions">
          {sketches.map((s) => (
            <button key={s.sketch_id} type="button" tabIndex={0} class={`sk-chip${s === current ? " is-on" : ""}`} aria-pressed={s === current}
              onClick={() => { setPicked(s.sketch_id); setImage(0); }} data-testid="sketch-version">v{s.version}</button>
          ))}
        </div>
        <span class={`sk-status is-${rec.status}`} data-testid="sketch-status">{approvedHere ? `Approved v${current.version}` : STATUS_TEXT[rec.status]}</span>
      </header>
      {img && (
        <div class={`sk-stage${compare && prevImg ? " is-compare" : ""}`}>
          {compare && prevImg && prev && (
            <figure class="sk-fig">
              <SketchImage image={prevImg} callouts={prev.callouts} alt={`Concept sketch v${prev.version}, ${prevImg.label}`} />
              <figcaption>v{prev.version}</figcaption>
            </figure>
          )}
          <figure class="sk-fig">
            <button type="button" tabIndex={0} class="sk-open" aria-label={`Open v${current.version} full window to mark it up`}
              onClick={() => openReview({ projectId, sketchId: current.sketch_id, image })} data-testid="sketch-open">
              <SketchImage image={img} callouts={current.callouts} marks={lastMarks(current, image)} alt={`Concept sketch v${current.version}, ${img.label}`} />
            </button>
            {compare && prevImg && <figcaption>v{current.version}</figcaption>}
          </figure>
        </div>
      )}
      <div class="sk-bar">
        {current.images.length > 1 && (
          <div class="sk-seg" role="group" aria-label="Views">
            {current.images.map((im, i) => (
              <button key={im.view} type="button" tabIndex={0} class={i === image ? "is-on" : ""} aria-pressed={i === image} onClick={() => setImage(i)}>{im.label}</button>
            ))}
          </div>
        )}
        {prev && (
          <button type="button" tabIndex={0} class={`sk-toggle${compare ? " is-on" : ""}`} aria-pressed={compare} onClick={() => setCompare(!compare)} data-testid="sketch-compare">
            Compare with v{prev.version}
          </button>
        )}
      </div>
      {current.notes && <p class="sk-notes text">{current.notes}</p>}
      {fbCount > 0 && <p class="sk-dim sk-fb">You sent {fbCount === 1 ? "feedback" : `${fbCount} rounds of feedback`} on v{current.version}{isLatest && !open ? "; the agent is revising" : ""}.</p>}
      {!open && isLatest && (
        <div class="sk-actions">
          <Button class="sk-btn" onClick={() => openReview({ projectId, sketchId: current.sketch_id, image })} data-testid="sketch-markup">Mark up</Button>
          <span class="sk-grow" />
          <Button class="sk-btn sk-quiet" variant="ghost" disabled={busy} onClick={() => void act(() => skip(projectId))} data-testid="sketch-skip">Skip sketch</Button>
          <Button class="sk-btn" variant="primary" disabled={busy} onClick={() => void act(() => approve(projectId, current.sketch_id))} data-testid="sketch-approve">Approve v{current.version}</Button>
        </div>
      )}
      {note && <p class={`sk-notice${note.error ? " is-error" : ""}`} role="status">{note.text}</p>}
    </section>
  );
}
