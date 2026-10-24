// App shell: 44px top bar across; below it left 240px · viewer · console 420px (full height).
// The center holds only the Viewer. The new-run dialog mounts at the root.
import type { JSX } from "preact";
import { conn } from "./state/store";
import { ConsolePane } from "./ui/console/ConsolePane";
import { NewRunDialog } from "./ui/dialogs/NewRunDialog";
import { LeftPane } from "./ui/left/LeftPane";
import { TopBar } from "./ui/topbar/TopBar";
import { Viewer } from "./ui/viewer/Viewer";

export function App(): JSX.Element {
  return (
    <div class="app" data-region="app" data-conn={conn.value}>
      <div class="region region-topbar" data-region="topbar"><TopBar /></div>
      <div class="region region-left" data-region="left"><LeftPane /></div>
      <main class="region region-viewer" data-region="viewer"><Viewer /></main>
      <div class="region region-console" data-region="console"><ConsolePane /></div>
      <NewRunDialog />
    </div>
  );
}
