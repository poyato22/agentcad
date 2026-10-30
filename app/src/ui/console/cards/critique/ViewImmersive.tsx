// A presented view, full window. It opens in exactly the agent's view (camera, X-ray, section,
// explode, subset, orbit) and keeps every view mode of the Design sheet at hand to look further.
// Esc closes; ⌘[ / ⌘] step through the other views of the same reply. Shares the study immersive
// view's shell (Plan 5 A8: .imm layout, focus trap, keys) so both feel like one place.
import type { JSX } from "preact";
import { useLayoutEffect, useRef, useState } from "preact/hooks";
import { partLabel } from "../../../../lib/names";
import { parts, runs } from "../../../../state/store";
import { Button } from "../../../common/Button";
import { useFocusTrap } from "../../../common/focusTrap";
import { LiveView } from "../../../viewer/modes/LiveView";
import { prefersReducedMotion } from "../../../viewer/modes/motion";
import type { ModesState } from "../../../viewer/modes/state";
import { ViewModes } from "../../../viewer/modes/ViewModes";
import { ghostsForSpec, modesForSpec, partsForSpec, VIEW_KIND_TEXT } from "../../../viewer/modes/viewSpec";
import type { ModeHost } from "../../../viewer/scene";
import "../../../pages/studies/immersive/immersive.css";
import type { PresentedView } from "./announce";
import type { ViewCardDeps } from "./ViewCard";
import { closeViewImmersive, stepViewImmersive, viewImmersive } from "./viewState";
import "./critique.css";

export function ViewImmersiveRoot({ deps }: { deps?: ViewCardDeps }): JSX.Element | null {
  const t = viewImmersive.value;
  if (!t) return null;
  const view = t.views[t.index];
  if (!view) return null;
  return <ViewImmersive view={view} index={t.index} count={t.views.length} deps={deps} />;
}

function ViewImmersive({ view, index, count, deps }: { view: PresentedView; index: number; count: number; deps?: ViewCardDeps }): JSX.Element {
  const root = useRef<HTMLDivElement>(null);
  useFocusTrap(root, () => root.current?.querySelector<HTMLElement>("[data-close]") ?? null);
  const [ready, setReady] = useState(false);
  useLayoutEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (e.defaultPrevented) return;
        e.preventDefault();
        closeViewImmersive();
        return;
      }
      if (e.metaKey && (e.key === "[" || e.key === "]") && !e.repeat) { e.preventDefault(); stepViewImmersive(e.key === "]" ? 1 : -1); }
    };
    window.addEventListener("keydown", k);
    setReady(true);
    return () => window.removeEventListener("keydown", k);
  }, []);
  const kind = VIEW_KIND_TEXT[view.spec.kind];
  const agent = runs.value.get(view.runId)?.agent ?? null;
  return (
    <div class="imm vimm" ref={root} role="dialog" aria-modal="true" aria-label={`${kind} view, full window`}
      data-testid="view-immersive" data-kind={view.spec.kind} data-index={index} data-ready={ready ? "true" : "false"}>
      <header class="imm-top">
        <span class="imm-title">{kind}</span>
        {view.spec.caption && <span class="imm-part text vimm-caption">{view.spec.caption}</span>}
        <span class="imm-nav">
          {count > 1 && (
            <>
              <Button variant="ghost" onClick={() => stepViewImmersive(-1)} aria-label="Previous view (⌘[)" data-testid="vimm-prev">⌘[</Button>
              <span class="faint" data-testid="vimm-pos">{index + 1} / {count}</span>
              <Button variant="ghost" onClick={() => stepViewImmersive(1)} aria-label="Next view (⌘])" data-testid="vimm-next">⌘]</Button>
            </>
          )}
          <Button variant="ghost" onClick={closeViewImmersive} aria-label="Close the full-window view (Esc)" data-close data-testid="vimm-close">Esc ✕</Button>
        </span>
      </header>
      {/* a fresh stage per view: its own camera, modes and orbit */}
      <ImmersiveStage key={view.key} view={view} agent={agent} deps={deps} />
    </div>
  );
}

function ImmersiveStage({ view, agent, deps }: { view: PresentedView; agent: string | null; deps?: ViewCardDeps }): JSX.Element {
  const target = modesForSpec(view.spec, parts.value);
  const [host, setHost] = useState<ModeHost | null>(null);
  const [modes, setModes] = useState<ModesState>(() => ({ ...target, explode: 0 }));
  const animate = useRef(false);
  const [play, setPlay] = useState(!prefersReducedMotion());
  const ids = partsForSpec(view.spec, parts.value);
  const ghosts = ghostsForSpec(view.spec, parts.value);
  return (
    <div class="imm-body">
      <div class="imm-stage vimm-stage">
        <LiveView class="vimm-live" partIds={ids} camera={view.spec.camera} orbit={view.spec.orbit} playing={play}
          label={`${VIEW_KIND_TEXT[view.spec.kind]} view`} onHost={setHost} createRenderer={deps?.createRenderer}
          onLoaded={() => { animate.current = true; setModes(target); }} />
        <ViewModes host={host} modes={modes} onModes={(m) => setModes(m)} animateRef={animate} focusId={ids[0] ?? null} partIds={ids} />
      </div>
      <aside class="imm-side" aria-label="About this view">
        <section class="imm-sec">
          <h2 class="label">Why this view</h2>
          <p class="text" data-agent-text>{agent ? <b>{agent}: </b> : null}{view.spec.caption || view.title || "No caption."}</p>
        </section>
        {view.spec.orbit && (
          <section class="imm-sec">
            <h2 class="label">Orbit</h2>
            <p><Button onClick={() => setPlay(!play)} aria-pressed={play} data-testid="vimm-play">{play ? "Pause the orbit" : "Play the orbit"}</Button></p>
          </section>
        )}
        <section class="imm-sec">
          <h2 class="label">Parts</h2>
          <ul class="vimm-parts">
            {ids.map((id) => (
              <li key={id} class="text" data-ghost={ghosts.includes(id) ? "" : undefined}>{partLabel(id)}{ghosts.includes(id) ? " (context)" : ""}</li>
            ))}
          </ul>
        </section>
        <p class="faint vimm-hint">Drag to orbit · scroll to zoom · the strip at the top left cuts, X-rays and explodes.</p>
      </aside>
    </div>
  );
}
