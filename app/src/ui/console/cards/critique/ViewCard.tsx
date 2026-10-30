// A view an agent presents in its reply (G-R7): a small live 3D stage showing exactly what the
// view spec asks for (an orbit, the inside under X-ray, a capped section, an exploded layout, a
// subset with the rest ghosted) and the agent's caption. Only one card renders live at a time (the
// newest, or the one under the pointer / focus); the others show their last frame or the core's
// thumbnail. Click (or Enter) opens it full window.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { parts } from "../../../../state/store";
import { LiveView, type RendererFactory } from "../../../viewer/modes/LiveView";
import { prefersReducedMotion } from "../../../viewer/modes/motion";
import type { ModeHost } from "../../../viewer/scene";
import { useModesController } from "../../../viewer/modes/ViewModes";
import { modesForSpec, partsForSpec, VIEW_KIND_TEXT } from "../../../viewer/modes/viewSpec";
import { partLabel } from "../../../../lib/names";
import type { PresentedView } from "./announce";
import { useImageUrl } from "./images";
import { liveCard, openViewImmersive, viewImmersive } from "./viewState";
import "./critique.css";

export interface ViewCardDeps { createRenderer?: RendererFactory }

/** The live stage of a card: modes from the spec, the explode gliding in once loaded. */
export function SpecStage({ view, playing, onSnapshot, deps, class: cls }: { view: PresentedView; playing: boolean; onSnapshot?(u: string): void; deps?: ViewCardDeps; class?: string }): JSX.Element {
  const [host, setHost] = useState<ModeHost | null>(null);
  const target = modesForSpec(view.spec);
  const [modes, setModes] = useState(() => ({ ...target, explode: 0 }));
  const animate = useRef(false);
  useModesController(host, modes, animate);
  const ids = partsForSpec(view.spec, parts.value);
  return (
    <LiveView class={cls} partIds={ids} camera={view.spec.camera} orbit={view.spec.orbit} playing={playing}
      label={`${VIEW_KIND_TEXT[view.spec.kind]} view${view.spec.caption ? `: ${view.spec.caption}` : ""}`}
      // a card is a picture to click, not a viewer to drag: orbiting is for the full-window view
      onHost={(h) => { if (h) h.controls.enabled = false; setHost(h); }} onSnapshot={onSnapshot} createRenderer={deps?.createRenderer}
      onLoaded={() => { animate.current = true; setModes(target); }} />
  );
}

export function ViewCard({ view, group, deps }: { view: PresentedView; group: readonly PresentedView[]; deps?: ViewCardDeps }): JSX.Element {
  // the newest card takes the live context when it appears
  useEffect(() => {
    liveCard.value = view.key;
    return () => { if (liveCard.peek() === view.key) liveCard.value = null; };
  }, [view.key]);
  const live = liveCard.value === view.key && viewImmersive.value === null;
  const [still, setStill] = useState<string | null>(null);
  const thumb = useImageUrl(view.thumbnail);
  const reduced = prefersReducedMotion();
  const [play, setPlay] = useState(!reduced);
  const spec = view.spec;
  const ids = partsForSpec(spec, parts.value);
  const index = Math.max(0, group.findIndex((v) => v.key === view.key));
  const claim = () => { if (liveCard.peek() !== view.key) liveCard.value = view.key; };
  const open = () => openViewImmersive(group.length ? group : [view], index);
  const kind = VIEW_KIND_TEXT[spec.kind];
  const image = still ?? thumb;

  return (
    <article class="vcard" data-testid="view-card" data-kind={spec.kind} data-live={live ? "true" : "false"} aria-label={`${kind} view`}
      onPointerEnter={claim} onFocusIn={claim}>
      <div class="vcard-stage">
        <button type="button" class="vcard-open" onClick={open} aria-label={`Open the ${kind.toLowerCase()} view full window`} data-testid="view-card-open">
          {live
            ? <SpecStage view={view} playing={play} onSnapshot={setStill} deps={deps} />
            : image
              ? <img class="vcard-still" src={image} alt="" data-testid="view-card-still" />
              : <span class="vcard-blank faint" data-testid="view-card-still">Point here to render</span>}
        </button>
        <span class="vcard-kind label" aria-hidden="true">{kind}</span>
        {spec.orbit && live && (
          <button type="button" class="vcard-play" aria-pressed={play} data-testid="view-card-play"
            aria-label={play ? "Pause the orbit" : "Play the orbit"} onClick={() => setPlay(!play)}>{play ? "pause" : "play"}</button>
        )}
      </div>
      <div class="vcard-foot">
        {spec.caption && <p class="vcard-caption text" data-agent-text>{spec.caption}</p>}
        <p class="vcard-meta faint">
          <span class="text">{ids.length === 1 ? partLabel(ids[0]) : `${ids.length} parts`}</span>
          {group.length > 1 && <span>{`${index + 1} of ${group.length}`}</span>}
        </p>
      </div>
    </article>
  );
}
