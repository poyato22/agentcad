// App shell: 44px top bar (--topbar-h; the macOS traffic lights sit on its centre line) across the top. Below it the active page shows:
//   · Design keeps today's layout — left 240px · viewer · console.
//   · Studies / Sketch / Drawing / Print render full-width under the top bar.
// The agent console stays on every page as a right-hand panel that can collapse to a 36px rail.
// The new-run dialog, the Profile panel and the setup gate mount at the root.
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { conn } from "./state/store";
import { needsYou } from "./state/selection";
import { BootOverlay } from "./ui/boot/BootOverlay";
import { ConsolePane } from "./ui/console/ConsolePane";
import { NewRunDialog } from "./ui/dialogs/NewRunDialog";
import { LeftPane } from "./ui/left/LeftPane";
import { activePage, consoleCollapsed, installPageShortcuts, toggleConsole } from "./ui/pages/router";
import { DrawingPage } from "./ui/pages/drawing";
import { PrintPage } from "./ui/pages/print";
import { SketchPage } from "./ui/pages/sketch";
import { StudiesPage } from "./ui/pages/studies";
import { ImmersiveRoot } from "./ui/pages/studies/immersive/Immersive";
import { closeProfile, profileOpen, profileSection } from "./ui/profile/open";
import { ProfilePane } from "./ui/profile/ProfilePane";
import { SetupGate } from "./ui/setup/SetupGate";
import { TopBar } from "./ui/topbar/TopBar";
import { Viewer } from "./ui/viewer/Viewer";

function PageRegion(): JSX.Element {
  // Design keeps the left pane + viewer; every other page renders full-width.
  switch (activePage.value) {
    case "studies": return <div class="region region-page" data-region="page"><StudiesPage /></div>;
    case "sketch": return <div class="region region-page" data-region="page"><SketchPage /></div>;
    case "drawing": return <div class="region region-page" data-region="page"><DrawingPage /></div>;
    case "print": return <div class="region region-page" data-region="page"><PrintPage /></div>;
    default: return (
      <>
        <div class="region region-left" data-region="left"><LeftPane /></div>
        <main class="region region-viewer" data-region="viewer"><Viewer /></main>
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
  return (
    <div class="app" data-region="app" data-conn={conn.value}
      data-page={activePage.value} data-console={consoleCollapsed.value ? "rail" : "open"}>
      <div class="region region-topbar" data-region="topbar"><TopBar /></div>
      <PageRegion />
      <ConsoleRegion />
      <ImmersiveRoot />
      <NewRunDialog />
      <ProfilePane open={profileOpen.value} section={profileSection.value} onClose={closeProfile} />
      <SetupGate />
      <BootOverlay />
    </div>
  );
}
