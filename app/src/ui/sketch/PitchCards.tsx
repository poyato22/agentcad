// The agent's design directions (D25), one card per option: front and side silhouettes, the
// 3/4 sketch, three mood words, the proportion note, CMF swatches and the trade-offs, with a
// Recommended badge (and why) and the honest-engineering Baseline. Pick one, or Mix: tick the
// options to combine, say what to take from each, and add a comment.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { ConceptPitch, PitchOption } from "../../api/concept";
import { activeBuildId } from "../../state/builds";
import { Button } from "../common/Button";
import { useImageUrl } from "../console/cards/critique/images";
import { useConcept } from "./SketchCard";
import { SketchImage } from "./SketchImage";
import { fmtHM } from "../../lib/format";
import { resolved, sending } from "../console/decisions/once";
import { choose, notices } from "./state";
import "./sketch.css";

const HEX = /^#[0-9a-fA-F]{6}$/;

function Silhouette({ id, label }: { id: string | undefined; label: string }): JSX.Element {
  const url = useImageUrl(id ? { id } : null);
  return (
    <figure class="sk-sil">
      <div class="sk-sil-tile">{url && <img src={url} alt={`${label} silhouette`} draggable={false} />}</div>
      <figcaption>{label}</figcaption>
    </figure>
  );
}

const TRADEOFFS: { key: keyof PitchOption["tradeoffs"]; label: string; num?: boolean }[] = [
  { key: "mass_delta", label: "Mass", num: true }, { key: "part_count", label: "Parts", num: true },
  { key: "molding", label: "Molding" }, { key: "cost_tier", label: "Cost", num: true },
  { key: "risk", label: "Risk" }, { key: "gives_up", label: "You give up" },
];

interface MixPick { on: boolean; take: string }

function OptionCard({ o, chosen, mixing, mix, onMix, onPick, busy }: {
  o: PitchOption; chosen: boolean; mixing: boolean; mix: MixPick; onMix: (m: MixPick) => void; onPick: () => void; busy: boolean;
}): JSX.Element {
  return (
    <article class={`sk-option${o.recommended ? " is-rec" : ""}${chosen ? " is-chosen" : ""}`} data-testid="pitch-option" data-option={o.name}>
      {o.sketch && <SketchImage image={o.sketch} callouts={o.callouts} alt={`${o.name}, 3/4 sketch`} class="is-option" />}
      <div class="sk-sils">
        <Silhouette id={o.silhouettes.front} label="Front" />
        <Silhouette id={o.silhouettes.side} label="Side" />
      </div>
      <div class="sk-option-body">
        <div class="sk-option-head">
          <h4 class="sk-option-name text">{o.name}</h4>
          {o.recommended && <span class="sk-tag is-rec">Recommended</span>}
          {o.baseline && <span class="sk-tag">Baseline</span>}
        </div>
        <p class="sk-mood text">{o.mood.join(" · ")}</p>
        {o.proportion_note && <p class="sk-dim sk-small text">{o.proportion_note}</p>}
        <ul class="sk-cmf" aria-label="Colours, materials and finishes">
          {o.cmf.map((c) => (
            <li key={c.name}><span class="sk-swatch" style={{ background: HEX.test(c.color) ? c.color : "transparent" }} aria-hidden="true" />
              <span class="text">{c.name}</span> <span class="sk-dim text">{c.finish}</span></li>
          ))}
        </ul>
        <dl class="sk-trade">
          {TRADEOFFS.map((t) => {
            const v = o.tradeoffs[t.key];
            if (v === "" || v === undefined || v === null) return null;
            return (<div key={t.key}><dt>{t.label}</dt><dd class={t.num ? "num" : "text"}>{String(v)}</dd></div>);
          })}
        </dl>
        {o.recommended && o.reason && <p class="sk-why text"><span class="sk-dim">Why: </span>{o.reason}</p>}
      </div>
      <footer class="sk-option-foot">
        {mixing ? (
          <>
            <label class="sk-check"><input type="checkbox" checked={mix.on} onChange={(e) => onMix({ ...mix, on: e.currentTarget.checked })} /> Use</label>
            <input class="sk-input sk-take" value={mix.take} placeholder="Take, e.g. torso" disabled={!mix.on} aria-label={`What to take from ${o.name}`}
              onInput={(e) => onMix({ ...mix, take: e.currentTarget.value })} />
          </>
        ) : (
          <Button class="sk-btn sk-wide" variant={o.recommended ? "primary" : "default"} disabled={busy} onClick={onPick} data-testid="pitch-pick">Pick {o.name}</Button>
        )}
      </footer>
    </article>
  );
}

export interface PitchCardsProps { projectId: string | null; pitchId: string | null }

export function PitchCards({ projectId: given, pitchId }: PitchCardsProps): JSX.Element {
  const projectId = given ?? activeBuildId.value;
  const view = useConcept(projectId);
  const [mixing, setMixing] = useState(false);
  const [mix, setMix] = useState<Record<string, MixPick>>({});
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const pitch: ConceptPitch | null = view?.pitches.find((p) => p.pitch_id === pitchId) ?? view?.pitches[view.pitches.length - 1] ?? null;
  if (!projectId || !pitch) return <div class="sk sk-card sk-card-empty" data-testid="pitch-cards" data-state="loading"><span class="sk-dim">Loading the design directions…</span></div>;
  // view-tab: chosen once — by the core's record, or by this session's decision the moment it lands
  const res = resolved.value.get(`pitch:${pitch.pitch_id}`) ?? null;
  const chosen = pitch.chosen;
  const decided = !!chosen || res !== null;
  const inFlight = busy || sending.value.has(`pitch:${pitch.pitch_id}`);
  const send = async (body: Parameters<typeof choose>[2]) => {
    if (decided || inFlight) return;
    setBusy(true); try { await choose(projectId, pitch.pitch_id, body); } finally { setBusy(false); }
  };
  const picks = pitch.options.filter((o) => mix[o.name]?.on).map((o) => ({ option: o.name, take: (mix[o.name]?.take ?? "").trim() }));
  const note = notices.value[projectId];
  return (
    <section class="sk sk-card sk-pitch" data-testid="pitch-cards" aria-label="Design directions">
      <header class="sk-head">
        <h3 class="sk-title">Design directions</h3>
        <span class="sk-dim sk-small">{pitch.options.length} options</span>
        {!decided && (
          <button type="button" tabIndex={0} class={`sk-toggle sk-push${mixing ? " is-on" : ""}`} aria-pressed={mixing} onClick={() => setMixing(!mixing)} data-testid="pitch-mix">Mix options</button>
        )}
      </header>
      {pitch.question && <p class="sk-notes text">{pitch.question}</p>}
      <div class="sk-options">
        {pitch.options.map((o) => (
          <OptionCard key={o.name} o={o} busy={inFlight || decided} mixing={mixing && !decided}
            chosen={!!chosen && (chosen.option === o.name || chosen.mix.some((m) => m.option === o.name))}
            mix={mix[o.name] ?? { on: false, take: "" }} onMix={(m) => setMix({ ...mix, [o.name]: m })}
            onPick={() => void send({ option: o.name, comment: comment.trim() })} />
        ))}
      </div>
      {chosen ? (
        <p class="sk-chosen" data-testid="pitch-chosen" data-resolved="">You chose <strong class="text">{chosen.label}</strong>
          {(chosen.decided_at ?? res?.at) ? <span class="sk-dim num"> · {fmtHM((chosen.decided_at ?? res?.at) as number)}</span> : null}
          {chosen.comment ? <span class="sk-dim text"> — {chosen.comment}</span> : null}</p>
      ) : res ? (
        <p class="sk-chosen" data-testid="pitch-chosen" data-resolved="">{res.text} <span class="sk-dim num">{fmtHM(res.at)}</span></p>
      ) : (
        <div class="sk-pitch-foot">
          <textarea class="sk-input" rows={2} value={comment} placeholder={mixing ? "How should they combine?" : "Comment (optional)"} aria-label="Comment on the directions"
            onInput={(e) => setComment(e.currentTarget.value)} />
          {mixing && (
            <Button class="sk-btn" variant="primary" disabled={inFlight || picks.length < 2} onClick={() => void send({ mix: picks, comment: comment.trim() })} data-testid="pitch-send-mix">
              Send mix
            </Button>
          )}
        </div>
      )}
      {note && <p class={`sk-notice${note.error ? " is-error" : ""}`} role="status">{note.text}</p>}
    </section>
  );
}
