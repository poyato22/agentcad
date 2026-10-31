// App shell. Home (the build grid and its Onshape / parts-library sub-views) fills the whole
// window with no top bar (D23). Inside a build: a 44px top bar (--topbar-h; the macOS traffic
// lights sit on its centre line) across the top, then the active page:
//   · Design keeps its layout — left 240px · viewer · console. Its viewer is the build's
//     assembly; a part clicked in the left tree opens alone (ui/assembly/focus, ← Assembly / Esc).
//   · Studies / Print (3D print | Drawing) / BOM render full-width under the top bar.
// The agent console stays on every page as a right-hand panel that can collapse to a 36px rail.
// The new-run dialog, the Profile panel and the setup gate mount at the root.
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { conn } from "./state/store";
import { atHome, goHome } from "./state/home";
import { isTrustedKey, registerShortcut } from "./lib/keys";
import { needsYou } from "./state/selection";
import { HomePage } from "./ui/pages/home";
import { AgentManager } from "./ui/agents/AgentManager";
import { AssemblyView } from "./ui/assembly/AssemblyView";
import { openPartId } from "./ui/assembly/focus";
import { BootOverlay } from "./ui/boot/BootOverlay";
import { ConsolePane } from "./ui/console/ConsolePane";
import { dockLayout } from "./ui/console/dockLayout";
import { NewRunDialog } from "./ui/dialogs/NewRunDialog";
import { LeftPane } from "./ui/left/LeftPane";
import { activePage, consoleCollapsed, installPageShortcuts, modalOpen, toggleConsole } from "./ui/pages/router";
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
import { installStopAllShortcut } from "./ui/topbar/stopAll";
import { TopBar } from "./ui/topbar/TopBar";
import { Viewer } from "./ui/viewer/Viewer";

function PageRegion(): JSX.Element {
  // Design keeps the left pane + viewer; every other page renders full-width.
  switch (activePage.value) {
    case "studies": return <div class="region region-page" data-region="page"><StudiesPage /></div>;
    case "print": return <div class="region region-page" data-region="page"><PrintSection /></div>;
    case "bom": return <div class="region region-page" data-region="page"><BomPage /></div>;
    default: return (
      <>
        <div class="region region-left" data-region="left"><LeftPane /></div>
        <main class="region region-viewer" data-region="viewer" data-mode={openPartId.value === null ? "assembly" : "part"}>
          {openPartId.value === null ? <AssemblyView /> : <Viewer />}
          <DrawingDrop variant="overlay" projectId={activeBuildId.value} />
        </main>
      </>
    );
  }
}

function ConsoleRegion(): JSX.Element {
  if (consoleCollapsed.value) {
    const needs = needsYou.value.count;
    return (
      <div class="region region-console" data-region="console">
        <div class="console-rail">
          <button type="button" class="console-rail-btn" title="Expand the agent console"
            aria-label={needs > 0 ? `Expand the agent console · ${needs} needs you` : "Expand the agent console"}
            aria-expanded={false} onClick={() => toggleConsole()}>Console</button>
          {needs > 0 && (
            <span class="console-rail-needs needs-you" data-needs-you aria-hidden="true"
              title={`${needs} needs you`}>{needs}</span>
          )}
        </div>
      </div>
    );
  }
  return (
    <div class="region region-console" data-region="console">
      <button type="button" class="console-collapse" title="Collapse the agent console" aria-expanded={true}
        aria-label="Collapse the agent console" onClick={() => toggleConsole()}>⟩</button>
      <ConsolePane />
    </div>
  );
}

export function App(): JSX.Element {
  useEffect(() => installPageShortcuts(), []);
  // ⇧⌘. works on Home too, which has no top bar (D23)
  useEffect(() => installStopAllShortcut(), []);
  // ⌘0 returns to the Home grid (not while a modal is open, and only on a real key press).
  useEffect(() => registerShortcut("Meta+0", (e) => {
    if (isTrustedKey(e) && !modalOpen()) goHome();
  }), []);
  const home = atHome.value;
  return (
    <div class="app" data-region="app" data-conn={conn.value} data-home={home ? "" : undefined}
      data-page={activePage.value} data-console={consoleCollapsed.value ? "rail" : "open"}
      // the agent dock's width (360–640 px, dragged at its left edge; ui/console/dockLayout)
      style={consoleCollapsed.value ? undefined : { "--console-w": `${dockLayout.value.width}px` }}>
      {home
        ? <div class="region region-page" data-region="page"><HomePage /></div>
        : <><div class="region region-topbar" data-region="topbar"><TopBar /></div><PageRegion /><ConsoleRegion /></>}
      <ImmersiveRoot />
      <ViewImmersiveRoot />
      <GenerativeRoot />
      <SketchReviewRoot />
      <NewRunDialog />
      <AgentManager />
      <ProfilePane open={profileOpen.value} section={profileSection.value} onClose={closeProfile} />
      <SetupGate />
      <BootOverlay />
    </div>
  );
}
