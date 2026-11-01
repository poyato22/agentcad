// App shell. Home (the build grid and its Onshape / parts-library sub-views) fills the whole
// window with no top bar (D23). Inside a build: a 44px top bar (--topbar-h; the macOS traffic
// lights sit on its centre line) across the top, then the active page (FW-layout, D20):
//   · Design is the build view full-bleed under the top bar — the assembly (or a part opened alone
//     from the tree: ui/assembly/focus, ← Assembly / Esc) — with the Parts surface floating over
//     its left edge. The 3D views centre the model in what the surfaces leave (ui/shell/insets).
//   · Studies / Print (3D print | Drawing) / BOM render full-width under the top bar.
// The agent dock floats at the right on every page (a page makes room for it while it is open) and
// folds to a slim "Agents" tab; the Parts surface folds to a "Parts" tab (⌘[ / ⌘], or their
// buttons). The dock starts open on Design only (router DOCK_OPEN_DEFAULT).
// The new-run dialog, the Profile panel and the setup gate mount at the root.
import type { JSX } from "preact";
import { useEffect, useRef } from "preact/hooks";
import { conn } from "./state/store";
import { atHome, goHome } from "./state/home";
import { installNav } from "./state/nav";
import { isTrustedKey, registerShortcut } from "./lib/keys";
import { needsYou } from "./state/selection";
import { HomePage } from "./ui/pages/home";
import { AgentManager } from "./ui/agents/AgentManager";
import { AssemblyView } from "./ui/assembly/AssemblyView";
import { openPartId } from "./ui/assembly/focus";
import { BootOverlay } from "./ui/boot/BootOverlay";
import { ConsolePane } from "./ui/console/ConsolePane";
import { dockLayout, dockWidth } from "./ui/console/dockLayout";
import { LeftPane } from "./ui/left/LeftPane";
import {
  activePage, consoleCollapsed, installPageShortcuts, installSurfaceShortcuts, leftCollapsed, modalOpen, toggleConsole, toggleLeft,
} from "./ui/pages/router";
import { activeBuildId } from "./state/builds";
import { DrawingDrop } from "./ui/imports";
import { BomPage } from "./ui/pages/bom";
import { PrintSection } from "./ui/pages/print";
import { StudiesPage } from "./ui/pages/studies";
import { ImmersiveRoot } from "./ui/pages/studies/immersive/Immersive";
import { ViewImmersiveRoot } from "./ui/console/cards/critique";
import { GenerativeRoot } from "./ui/topopt";
import { SketchReviewRoot } from "./ui/sketch/register";
import { closeProfile, profileOpen, profileSection } from "./ui/profile/open";
import { ProfilePane } from "./ui/profile/ProfilePane";
import { SetupGate } from "./ui/setup/SetupGate";
import { viewInsets, watchInsets } from "./ui/shell/insets";
import { installStopAllShortcut } from "./ui/topbar/stopAll";
import { TopBar } from "./ui/topbar/TopBar";
import { Viewer } from "./ui/viewer/Viewer";

/** A sidebar glyph: the panel on the left or the right of a window. */
export function SidebarIcon({ side }: { side: "left" | "right" }): JSX.Element {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2" fill="none" stroke="currentColor" stroke-width="1.2" />
      <path d={side === "left" ? "M6 3v10" : "M10 3v10"} stroke="currentColor" stroke-width="1.2" />
    </svg>
  );
}

function LeftRegion(): JSX.Element {
  if (leftCollapsed.value) {
    return (
      <div class="region region-left" data-region="left" data-folded="">
        <button type="button" class="surface surface-tab" data-surface="" title="Show the parts (⌘[)" aria-label="Show the parts"
          aria-expanded={false} onClick={() => toggleLeft()}>
          <SidebarIcon side="left" /><span>Parts</span>
        </button>
      </div>
    );
  }
  return (
    <aside class="region region-left surface" data-region="left" data-surface="">
      <button type="button" class="surface-fold" title="Hide the parts (⌘[)" aria-label="Hide the parts" aria-expanded={true}
        onClick={() => toggleLeft()}><SidebarIcon side="left" /></button>
      <LeftPane />
    </aside>
  );
}

function PageRegion(): JSX.Element {
  // Design is the build view with the floating Parts surface; every other page renders full-width.
  switch (activePage.value) {
    case "studies": return <div class="region region-page" data-region="page"><StudiesPage /></div>;
    case "print": return <div class="region region-page" data-region="page"><PrintSection /></div>;
    case "bom": return <div class="region region-page" data-region="page"><BomPage /></div>;
    default: return (
      <>
        <main class="region region-viewer" data-region="viewer" data-mode={openPartId.value === null ? "assembly" : "part"}
          style={{ "--inset-l": `${viewInsets.value.left}px`, "--inset-r": `${viewInsets.value.right}px` }}>
          {openPartId.value === null ? <AssemblyView /> : <Viewer />}
          <DrawingDrop variant="overlay" projectId={activeBuildId.value} />
        </main>
        <LeftRegion />
      </>
    );
  }
}

function ConsoleRegion(): JSX.Element {
  if (consoleCollapsed.value) {
    const needs = needsYou.value.count;
    return (
      <div class="region region-console" data-region="console" data-folded="">
        <button type="button" class="surface surface-tab console-rail-btn" data-surface="" title="Show the agents (⌘])"
          aria-label={needs > 0 ? `Expand the agent console · ${needs} needs you` : "Expand the agent console"}
          aria-expanded={false} onClick={() => toggleConsole()}>
          <SidebarIcon side="right" /><span>Agents</span>
          {needs > 0 && (
            <span class="console-rail-needs needs-you" data-needs-you aria-hidden="true" title={`${needs} needs you`}>{needs}</span>
          )}
        </button>
      </div>
    );
  }
  return (
    <div class="region region-console surface" data-region="console" data-surface="">
      <button type="button" class="surface-fold console-collapse" title="Hide the agents (⌘])" aria-expanded={true}
        aria-label="Collapse the agent console" onClick={() => toggleConsole()}><SidebarIcon side="right" /></button>
      <ConsolePane />
    </div>
  );
}

export function App(): JSX.Element {
  useEffect(() => installPageShortcuts(), []);
  useEffect(() => installSurfaceShortcuts(), []);
  // ⇧⌘. works on Home too, which has no top bar (D23)
  useEffect(() => installStopAllShortcut(), []);
  // Plan 10 E11: back / forward through where the user went (← → buttons, ⌘← / ⌘→, mouse buttons)
  useEffect(() => installNav(), []);
  // ⌘0 returns to the Home grid (not while a modal is open, and only on a real key press).
  useEffect(() => registerShortcut("Meta+0", (e) => {
    if (isTrustedKey(e) && !modalOpen()) goHome();
  }), []);
  const home = atHome.value;
  const page = activePage.value;
  const folded = consoleCollapsed.value;
  const leftFolded = leftCollapsed.value;
  const partOpen = openPartId.value !== null;
  // the floating surfaces' footprint on the build view (re-measured as they mount, fold or resize)
  const appRef = useRef<HTMLDivElement>(null);
  useEffect(() => (appRef.current ? watchInsets(appRef.current) : undefined), [home, page, folded, leftFolded, partOpen]);
  return (
    <div class="app" ref={appRef} data-region="app" data-conn={conn.value} data-home={home ? "" : undefined}
      data-page={page} data-console={folded ? "rail" : "open"} data-left={leftFolded ? "rail" : "open"}
      // the agent dock's width: its open columns plus a tab per closed one (P10 E6; ui/console/dockLayout)
      // (capped in base.css so the model always keeps 360 px)
      style={folded ? undefined : { "--dock-w": `${dockWidth(dockLayout.value)}px` }}>
      {home
        ? <div class="region region-page" data-region="page"><HomePage /></div>
        : <><div class="region region-topbar" data-region="topbar"><TopBar /></div><PageRegion /><ConsoleRegion /></>}
      <ImmersiveRoot />
      <ViewImmersiveRoot />
      <GenerativeRoot />
      <SketchReviewRoot />
      <AgentManager />
      <ProfilePane open={profileOpen.value} section={profileSection.value} onClose={closeProfile} />
      <SetupGate />
      <BootOverlay />
    </div>
  );
}
