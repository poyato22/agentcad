// Every keyboard shortcut in the app, in one table (Plans 5-8 merged, int-678). The handlers stay
// where they live (registerShortcut calls and a few focused-element handlers); this is the single
// list they must agree with — shortcuts.test.ts checks every registerShortcut combo in src/ is
// here, that no two global shortcuts collide, and that docs/shortcuts.md documents each one.
import { PAGES } from "../ui/pages/router";

export type ShortcutScope = "global" | "topbar" | "design" | "viewer" | "parts" | "approvals" | "immersive" | "composer";

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
  // Plan 9 D1: the build's name in the top bar (focused) renames the build in place
  { combo: "F2", keys: "F2", action: "Rename the build (its name in the top bar focused)", scope: "topbar" },
  { combo: "Meta+K", keys: "⌘K", action: "Focus the command line", scope: "global" },
  { combo: "Meta+Shift+A", keys: "⇧⌘A", action: "Agent manager", scope: "global" },
  { combo: "Meta+Shift+.", keys: "⇧⌘.", action: "Stop all runs", scope: "global" },
  // Plan 10 E11: the user's own build edits (state/undo); a text field keeps its native undo
  { combo: "Meta+Z", keys: "⌘Z", action: "Undo your last change to the build (not in a text field)", scope: "global" },
  { combo: "Meta+Shift+Z", keys: "⇧⌘Z", action: "Redo it", scope: "global" },
  // Plan 10 E11: history (state/nav; also the ← → buttons and the mouse's back / forward buttons);
  // not while typing in a field, where they move the caret
  { combo: "Meta+ArrowLeft", keys: "⌘← ⌘→", action: "Back / forward (where you were: page, part, agent, Home view)", scope: "global" },
  // FW-layout (D20): the floating surfaces fold to slim tabs (not under a modal: the immersive
  // views step with ⌘[ / ⌘])
  { combo: "Meta+]", keys: "⌘]", action: "Show / hide the agent dock", scope: "global" },
  { combo: "Meta+[", keys: "⌘[", action: "Show / hide the parts surface", scope: "design" },
  { combo: "F", keys: "F", action: "Fit the assembly in the view (or double-click empty space)", scope: "design" },
  { combo: "Escape", keys: "Esc", action: "Back to the assembly from an open part / close Play", scope: "design" },
  // Plan 10 E19: not for a part a joint places (the caption says why). Only with the assembly or
  // nothing focused (AssemblyView): a focused approval card's R is its Reject (robot.spec)
  { combo: "R", keys: "R", action: "Reset the selected instance to the origin (undo with ⌘Z; a focused approval card's R is Reject)", scope: "design" },
  { combo: "M", keys: "M", action: "Measure (viewer focused; again for Select)", scope: "viewer" },
  { combo: "Escape", keys: "Esc", action: "Back to Select / close a menu or an immersive view", scope: "viewer" },
  { combo: "F2", keys: "F2", action: "Rename the part", scope: "parts" },
  { combo: "H", keys: "H", action: "Hide / show the selected parts", scope: "parts" },
  { combo: "Meta+D", keys: "⌘D", action: "Duplicate the selected parts", scope: "parts" },
  { combo: "Meta+I", keys: "⌘I", action: "Part properties", scope: "parts" },
  { combo: "Backspace", keys: "⌫", action: "Delete the selected parts (and their assembly instances)", scope: "parts" },
  { combo: "Alt+ArrowUp", keys: "⌥↑ ⌥↓", action: "Move the part up / down", scope: "parts" },
  { combo: "Alt+ArrowRight", keys: "⌥→ ⌥←", action: "Nest the part / move it out", scope: "parts" },
  { combo: "approve", keys: "M", action: "Approve (a focused approval card, after it arms)", scope: "approvals" },
  { combo: "reject", keys: "R", action: "Reject (a focused approval card)", scope: "approvals" },
  { combo: "keep", keys: "K", action: "Keep the branch (a focused approval card)", scope: "approvals" },
  { combo: "Meta+[", keys: "⌘[ ⌘]", action: "Previous / next view or study (immersive)", scope: "immersive" },
  { combo: "Escape", keys: "Esc", action: "Stop the agents (the composer focused, while they work)", scope: "composer" },
];
