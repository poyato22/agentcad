// Every keyboard shortcut in the app, in one table (Plans 5-8 merged, int-678). The handlers stay
// where they live (registerShortcut calls and a few focused-element handlers); this is the single
// list they must agree with — shortcuts.test.ts checks every registerShortcut combo in src/ is
// here, that no two global shortcuts collide, and that docs/shortcuts.md documents each one.
import { PAGES } from "../ui/pages/router";

export type ShortcutScope = "global" | "design" | "viewer" | "parts" | "approvals" | "immersive";

export interface Shortcut {
  /** The registerShortcut combo ("Meta+Shift+A"), or the key a focused handler reads. */
  combo: string;
  /** How the UI writes it. */
  keys: string;
  action: string;
  scope: ShortcutScope;
}

export const SHORTCUTS: readonly Shortcut[] = [
  { combo: "Meta+0", keys: "⌘0", action: "Home (all builds)", scope: "global" },
  // the mode tabs, in tab order (ui/pages/router.ts PAGES)
  ...PAGES.map((p): Shortcut => ({ combo: `Meta+${p.key}`, keys: `⌘${p.key}`, action: `${p.title} page`, scope: "global" })),
  { combo: "Meta+K", keys: "⌘K", action: "Focus the command line", scope: "global" },
  { combo: "Meta+N", keys: "⌘N", action: "New agent run", scope: "global" },
  { combo: "Meta+Shift+A", keys: "⇧⌘A", action: "Agent manager", scope: "global" },
  { combo: "Meta+Shift+.", keys: "⇧⌘.", action: "Stop all runs", scope: "global" },
  { combo: "Meta+Alt+A", keys: "⌥⌘A", action: "Part / Assembly (Design page)", scope: "design" },
  { combo: "M", keys: "M", action: "Measure (viewer focused; again for Select)", scope: "viewer" },
  { combo: "Escape", keys: "Esc", action: "Back to Select / close the overview, a menu or an immersive view", scope: "viewer" },
  { combo: "F2", keys: "F2", action: "Rename the part", scope: "parts" },
  { combo: "H", keys: "H", action: "Hide / show the selected parts", scope: "parts" },
  { combo: "Meta+D", keys: "⌘D", action: "Duplicate the selected parts", scope: "parts" },
  { combo: "Meta+I", keys: "⌘I", action: "Part properties", scope: "parts" },
  { combo: "Alt+ArrowUp", keys: "⌥↑ ⌥↓", action: "Move the part up / down", scope: "parts" },
  { combo: "Alt+ArrowRight", keys: "⌥→ ⌥←", action: "Nest the part / move it out", scope: "parts" },
  { combo: "approve", keys: "M", action: "Approve (a focused approval card, after it arms)", scope: "approvals" },
  { combo: "reject", keys: "R", action: "Reject (a focused approval card)", scope: "approvals" },
  { combo: "keep", keys: "K", action: "Keep the branch (a focused approval card)", scope: "approvals" },
  { combo: "Meta+[", keys: "⌘[ ⌘]", action: "Previous / next view or study (immersive)", scope: "immersive" },
];
