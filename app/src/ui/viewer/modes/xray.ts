// X-ray: the faces of the chosen parts turn see-through (and stop hiding what is behind them), their
// edges stay solid, so the internals read as a line drawing through the shell. Parts outside an
// isolated subset fade to ghosts, edges and all.
import * as THREE from "three";
import type { ShownPart } from "../scene";
import { GHOST_OPACITY, type ModesState } from "./state";

/** How see-through a part is under these modes: [face opacity, edge opacity]. */
export function partOpacity(id: string, m: Pick<ModesState, "xray" | "isolate">): [number, number] {
  if (m.isolate && !m.isolate.includes(id)) return [GHOST_OPACITY, Math.min(1, GHOST_OPACITY * 2.5)];
  const chosen = m.xray.parts === null || m.xray.parts.includes(id);
  const o = chosen ? Math.min(1, Math.max(0.02, m.xray.opacity)) : 1;
  return [o, 1];
}

function setOpacity(m: THREE.Material, o: number, faces: boolean): void {
  const see = o < 0.999;
  if (m.transparent !== see) { m.transparent = see; m.needsUpdate = true; }
  m.opacity = see ? o : 1;
  // a see-through face must not hide what is behind it; edges keep their depth so the shell's own
  // hidden lines stay hidden unless its faces are see-through
  if (faces) m.depthWrite = !see;
}

export function applyXray(parts: readonly ShownPart[], m: Pick<ModesState, "xray" | "isolate">): void {
  for (const p of parts) {
    const [face, edge] = partOpacity(p.id, m);
    p.scene.object.traverse((o) => {
      if (o instanceof THREE.LineSegments) setOpacity(o.material as THREE.Material, edge, false);
      else if (o instanceof THREE.Mesh) setOpacity(o.material as THREE.Material, face, true);
    });
  }
}
