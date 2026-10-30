// The Design sheet's view modes (its hook into Viewer.tsx), bound to designModes. Their toggles are
// items of the sheet's View ▾ menu (U2b: the sheet keeps only Part | Assembly and View), see
// designModeItems; the controls of a mode that is on (sliders, axes) still show on the sheet.
import { signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useRef } from "preact/hooks";
import type { ModeHost } from "../scene";
import type { ViewMenuItem } from "../ViewerTools";
import { clearReference, reference } from "./ReferenceOverlay";
import { designModes, MODES_OFF, XRAY_OPACITY } from "./state";
import { ViewModes } from "./ViewModes";

export function DesignViewModes({ host, focusId, partIds }: { host: ModeHost | null; focusId: string | null; partIds: readonly string[] }): JSX.Element {
  // another part (or the build view): a plane placed for the last one goes back to the middle
  const shown = partIds.join("|");
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const m = designModes.peek();
    if (m.section.at !== null) designModes.value = { ...m, section: { ...m.section, at: null } };
  }, [focusId, shown]);
  return <ViewModes host={host} modes={designModes.value} onModes={(m) => { designModes.value = m; }} focusId={focusId} partIds={partIds}
    strip={false} animateRef={designAnimate} onCanExplode={(c) => { designCanExplode.value = c; }} pickReference={pickReferenceImage} />;
}

/** Explode needs two or more parts or bodies (known once the modes are applied). */
const designCanExplode = signal(false);
/** Set before a menu toggle that should glide (the explode). */
const designAnimate: { current: boolean } = { current: false };
/** Bumped to open the reference image chooser. */
const pickReferenceImage = signal(0);

/** The View ▾ menu items for the Design sheet's view modes. */
export function designModeItems(): ViewMenuItem[] {
  const m = designModes.value;
  const xrayOn = m.xray.opacity < 0.999;
  const exploding = m.explode > 0;
  const ref = reference.value;
  const set = (patch: Partial<typeof m>) => { designModes.value = { ...designModes.peek(), ...patch }; };
  const items: ViewMenuItem[] = [
    { id: "xray", label: "X-ray", checked: xrayOn, onSelect: () => set({ xray: { ...m.xray, opacity: xrayOn ? 1 : XRAY_OPACITY } }) },
    { id: "section", label: "Section", checked: m.section.on, onSelect: () => set({ section: { ...m.section, on: !m.section.on } }) },
    { id: "explode", label: "Explode", checked: exploding, disabled: !designCanExplode.value && !exploding,
      hint: designCanExplode.value || exploding ? undefined : "2+ parts", onSelect: () => { designAnimate.current = true; set({ explode: exploding ? 0 : 1 }); } },
    { id: "reference", label: "Reference image…", checked: ref !== null,
      onSelect: () => { if (ref) clearReference(); else pickReferenceImage.value += 1; } },
  ];
  if (xrayOn || m.section.on || exploding) items.push({ id: "modes-off", label: "Clear view modes", onSelect: () => { designModes.value = MODES_OFF; } });
  return items;
}
