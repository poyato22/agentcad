// Home (the launcher): whether the app is showing the build grid, and the navigation between the
// grid and a build tab. Launch lands on the grid (R1); opening a tile opens that build as a tab
// and leaves the grid; the Home button / ⌘0 returns to it.
import { signal, type Signal } from "@preact/signals";
import { buildActions } from "./builds";

/** Launch shows the grid. A localStorage override ("mises.home.open": "0"/"1") is a test seam
 *  only — real users never set it, so every launch lands on Home (R1). */
function initialHome(): boolean {
  try {
    const v = localStorage.getItem("mises.home.open");
    if (v === "0") return false;
    if (v === "1") return true;
  } catch { /* storage may be unavailable */ }
  return true;
}

/** True while the full-screen build grid is shown instead of a build. Starts true (launch). */
export const atHome: Signal<boolean> = signal<boolean>(initialHome());

export function goHome(): void { atHome.value = true; }

/** Build overview (R17): the full-build "All parts" grid, shown over the viewer. */
export const overviewOpen: Signal<boolean> = signal<boolean>(false);
export function toggleOverview(): void { overviewOpen.value = !overviewOpen.value; }
export function closeOverview(): void { overviewOpen.value = false; }

/** Open a build as a tab and leave the grid. */
export async function openBuild(id: string): Promise<void> {
  await buildActions.switchTo(id);
  atHome.value = false;
}

/** Back to the launch state (tests). */
export function resetHome(): void { atHome.value = true; overviewOpen.value = false; }
