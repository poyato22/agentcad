// App shell: 44px top bar (--topbar-h; the macOS traffic lights sit on its centre line) across the top. Below it the active page shows:
//   · Design keeps today's layout — left 240px · viewer · console. Its viewer is either one part
//     or the build's assembly (ui/assembly/mode: the Part | Assembly switch, ⌥⌘A).
//   · Studies / Drawing / Print / Onshape / Parts render full-width under the top bar.
// The agent console stays on every page as a right-hand panel that can collapse to a 36px rail.
// The new-run dialog, the Profile panel and the setup gate mount at the root.
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { conn } from "./state/store";
import { atHome, goHome, overviewOpen } from "./state/home";
import { isTrustedKey, registerShortcut } from "./lib/keys";
import { needsYou } from "./state/selection";
import { HomePage } from "./ui/pages/home";
import { AgentManager } from "./ui/agents/AgentManager";
import { AssemblyView } from "./ui/assembly/AssemblyView";
import { designMode, installModeShortcut } from "./ui/assembly/mode";
import { BootOverlay } from "./ui/boot/BootOverlay";
import { ConsolePane } from "./ui/console/ConsolePane";
import { NewRunDialog } from "./ui/dialogs/NewRunDialog";
import { LeftPane } from "./ui/left/LeftPane";
import { Overview } from "./ui/viewer/Overview";
import { activePage, consoleCollapsed, installPageShortcuts, modalOpen, toggleConsole } from "./ui/pages/router";
import { activeBuildId } from "./state/builds";
import { DrawingDrop } from "./ui/imports";
import { DirectoryPage } from "./ui/pages/directory";
import { BomPage } from "./ui/pages/bom";
import { DrawingPage } from "./ui/pages/drawing";
import { OnshapePage } from "./ui/pages/onshape";
import { PrintPage } from "./ui/pages/print";
import { StudiesPage } from "./ui/pages/studies";
import { ImmersiveRoot } from "./ui/pages/studies/immersive/Immersive";
import { ViewImmersiveRoot } from "./ui/console/cards/critique";
import { SketchReviewRoot } from "./ui/sketch/register";
import { closeProfile, profileOpen, profileSection } from "./ui/profile/open";
import { ProfilePane } from "./ui/profile/ProfilePane";
import { SetupGate } from "./ui/setup/SetupGate";
import { TopBar } from "./ui/topbar/TopBar";
import { Viewer } from "./ui/viewer/Viewer";

function PageRegion(): JSX.Element {
  // Design keeps the left pane + viewer; every other page renders full-width.
  switch (activePage.value) {
    case "studies": return <div class="region region-page" data-region="page"><StudiesPage /></div>;
    case "drawing": return <div class="region region-page" data-region="page"><DrawingPage /></div>;
    case "print": return <div class="region region-page" data-region="page"><PrintPage /></div>;
    case "onshape": return <div class="region region-page" data-region="page"><OnshapePage /></div>;
    case "directory": return <div class="region region-page" data-region="page"><DirectoryPage /></div>;
    case "bom": return <div class="region region-page" data-region="page"><BomPage /></div>;
    default: return (
      <>
        <div class="region region-left" data-region="left"><LeftPane /></div>
        <main class="region region-viewer" data-region="viewer" data-mode={designMode.value}>
          {designMode.value === "assembly" ? <AssemblyView /> : <Viewer />}
          {/* "All parts" lives in the Part viewer's tool row (Viewer → ViewerTools); Assembly view
              lists its parts in its own panel, so the overview isn't offered over it (I3) */}
          {overviewOpen.value && designMode.value !== "assembly" && <Overview />}
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
  useEffect(() => installModeShortcut(() => activePage.peek() === "design"), []);
  // ⌘0 returns to the Home grid (not while a modal is open, and only on a real key press).
  useEffect(() => registerShortcut("Meta+0", (e) => {
    if (isTrustedKey(e) && !modalOpen()) goHome();
  }), []);
  const home = atHome.value;
  return (
    <div class="app" data-region="app" data-conn={conn.value} data-home={home ? "" : undefined}
      data-page={activePage.value} data-console={consoleCollapsed.value ? "rail" : "open"}>
      <div class="region region-topbar" data-region="topbar"><TopBar /></div>
      {home
        ? <div class="region region-page" data-region="page"><HomePage /></div>
        : <><PageRegion /><ConsoleRegion /></>}
      <ImmersiveRoot />
      <ViewImmersiveRoot />
      <SketchReviewRoot />
      <NewRunDialog />
      <AgentManager />
      <ProfilePane open={profileOpen.value} section={profileSection.value} onClose={closeProfile} />
      <SetupGate />
      <BootOverlay />
    </div>
  );
}
