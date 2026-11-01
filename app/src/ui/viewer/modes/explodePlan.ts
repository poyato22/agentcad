// The exploded layout (Plan 10 E4), pure. Parts move out along the assembly's own structure from
// where they really sit, never rotated:
//   · a part a joint places moves away from its parent: along the joint axis when it sits along
//     that axis (a wheel on its axle, a finger on its slide), else along the line from the joint
//     origin to its own center (a link of an arm);
//   · a part sitting inside another (a board in its case) leaves that box through its thinnest
//     side (+Z for a flat case: the way a lid comes off) and clears it;
//   · the rest (roots) go radially out from the middle of the whole; ones at the middle stay;
//   · a child carries its parent's move, so a sub-assembly comes out as a unit and then opens up;
//   · each moves by a share of its OWN size (a washer goes a little, a frame a lot);
//   · parts heading the same way that would land on each other are stacked in the order they
//     sat in along that way, so a stack keeps its order.
import * as THREE from "three";

export interface ExplodeUnit {
  id: string;
  /** World box, assembled (rest pose). */
  box: THREE.Box3;
  /** The unit that places this one (a joint's other side); absent/null: inferred or a root. */
  parent?: string | null;
  /** The placing joint's axis (world) and origin (world), when a joint places it. */
  axis?: THREE.Vector3 | null;
  origin?: THREE.Vector3 | null;
}

/** A unit's own move, as a share of its own diagonal. */
export const OWN_SHARE = 0.35;
/** A part mounted on (or in) its parent ends at least this share of its own size (along its move)
 *  clear of it; stacked parts keep it between them too. */
export const MOUNT_GAP = 0.25;
/** A child's step: this share of its parent's extent along the way it goes ... */
export const PARENT_SHARE = 0.25;
/** ... or this share of its own diagonal, whichever is more. */
export const CHILD_SHARE = 0.2;
/** Each level below the first steps this much less (an arm's links). */
export const DEPTH_DAMP = 0.85;
/** The least move, as a share of the whole's diagonal (a tiny part still visibly comes out). */
export const MIN_SHARE = 0.06;
/** Space left between parts stacked one after another, as a share of the smaller one. */
export const STACK_GAP = 0.3;
/** A box this much inside another (by volume) sits inside it. */
const INSIDE = 0.6;

const vol = (b: THREE.Box3) => { const s = b.getSize(new THREE.Vector3()); return Math.max(s.x, 1e-9) * Math.max(s.y, 1e-9) * Math.max(s.z, 1e-9); };
const proj = (b: THREE.Box3, d: THREE.Vector3, max: boolean) => {
  // the box's extreme along d (support function)
  let best = max ? -Infinity : Infinity;
  for (const x of [b.min.x, b.max.x]) for (const y of [b.min.y, b.max.y]) for (const z of [b.min.z, b.max.z]) {
    const p = x * d.x + y * d.y + z * d.z;
    best = max ? Math.max(best, p) : Math.min(best, p);
  }
  return best;
};
const overlaps = (a: THREE.Box3, b: THREE.Box3, eps: number) =>
  a.min.x < b.max.x - eps && a.max.x > b.min.x + eps && a.min.y < b.max.y - eps && a.max.y > b.min.y + eps && a.min.z < b.max.z - eps && a.max.z > b.min.z + eps;

/** Who holds whom: given parents (joints), else the smallest box this one sits inside. */
export function explodeParents(units: readonly ExplodeUnit[]): (number | null)[] {
  const idx = new Map(units.map((u, i) => [u.id, i]));
  const parent: (number | null)[] = units.map((u) => (u.parent != null && idx.has(u.parent) && u.parent !== u.id ? idx.get(u.parent) as number : null));
  const ancestorOf = (a: number, b: number): boolean => {    // is a an ancestor of b (or b itself)?
    let k: number | null = b; let n = 0;
    while (k !== null && n++ <= units.length) { if (k === a) return true; k = parent[k]; }
    return false;
  };
  const vols = units.map((u) => vol(u.box));
  // the largest go first, so a container already knows its own place when its contents look
  const order = units.map((_, i) => i).sort((a, b) => vols[b] - vols[a] || a - b);
  for (const i of order) {
    if (parent[i] !== null) continue;
    let best: number | null = null;
    for (let j = 0; j < units.length; j++) {
      if (j === i || vols[j] <= vols[i] * 1.0001 || ancestorOf(i, j)) continue;
      const inside = units[i].box.clone().intersect(units[j].box);
      if (inside.isEmpty() || vol(inside) < INSIDE * vols[i]) continue;
      if (best === null || vols[j] < vols[best]) best = j;
    }
    parent[i] = best;
  }
  return parent;
}

/** World offsets at full explode, one per unit (same order). */
export function explodePlan(units: readonly ExplodeUnit[]): THREE.Vector3[] {
  const n = units.length;
  const out = units.map(() => new THREE.Vector3());
  if (n < 2) return out;
  const overall = new THREE.Box3();
  for (const u of units) overall.union(u.box);
  const whole = Math.max(overall.getSize(new THREE.Vector3()).length(), 1e-6);
  const mid = overall.getCenter(new THREE.Vector3());
  const centers = units.map((u) => u.box.getCenter(new THREE.Vector3()));
  const diags = units.map((u) => u.box.getSize(new THREE.Vector3()).length());
  const parent = explodeParents(units);
  const given = units.map((u, i) => u.parent != null && parent[i] !== null && units[parent[i] as number].id === u.parent);
  const dirs = units.map(() => new THREE.Vector3());
  const own = units.map(() => 0);
  const gap = (i: number) => Math.max(OWN_SHARE * diags[i], MIN_SHARE * whole);
  // E4 review: a child's step comes from its level (its parent's extent along the way it goes),
  // not only its own size, so siblings separate by comparable amounts; deep chains step a little
  // less each level so an arm's links don't run away. Then it clears the parent's box.
  const depth = units.map(() => 0);
  for (let i = 0; i < n; i++) { let k = parent[i]; let g = 0; while (k !== null && g++ < n) { depth[i]++; k = parent[k]; } }
  const extent = (b: THREE.Box3, d: THREE.Vector3) => proj(b, d, true) - proj(b, d, false);
  const childStep = (i: number, p: number) => {
    const step = Math.max(PARENT_SHARE * extent(units[p].box, dirs[i]), CHILD_SHARE * diags[i]) * DEPTH_DAMP ** Math.max(0, depth[i] - 1);
    // clears the parent where it overlaps it (a part sitting on or in it), never more
    // mounted on it (touching) or in it: first clear the parent's box along the way, then leave a
    // visible gap of at least MOUNT_GAP of the child's own size that way
    const eps = -whole * 1e-3;
    const mounted = overlaps(units[i].box, units[p].box, eps);
    const clear = mounted ? Math.max(0, proj(units[p].box, dirs[i], true) - proj(units[i].box, dirs[i], false)) : 0;
    return clear + Math.max(step, MIN_SHARE * whole) + (mounted ? MOUNT_GAP * extent(units[i].box, dirs[i]) : 0);
  };

  // roots: the one holding the most (the frame everything hangs off; else the biggest) stays
  // put; the others go radially out from the middle, and ones at the middle step off the
  // anchor along the axis they differ most on
  const roots = units.map((_, i) => i).filter((i) => parent[i] === null);
  const held = units.map(() => 0);
  for (let i = 0; i < n; i++) { let k = parent[i]; let g = 0; while (k !== null && g++ < n) { held[k]++; k = parent[k]; } }
  const anchor = roots.reduce((a, b) => (held[b] > held[a] || (held[b] === held[a] && vol(units[b].box) > vol(units[a].box)) ? b : a));
  for (const i of roots) {
    if (i === anchor) continue;
    const v = centers[i].clone().sub(mid);
    if (v.length() < whole * 0.05) {
      const d = centers[i].clone().sub(centers[anchor]);
      const k = Math.abs(d.x) > Math.abs(d.y) && Math.abs(d.x) > Math.abs(d.z) ? "x" : Math.abs(d.y) > Math.abs(d.z) ? "y" : "z";
      dirs[i].set(0, 0, 0)[k] = Math.abs(d[k]) > 1e-9 ? Math.sign(d[k]) : 1;
    } else dirs[i].copy(v.normalize());
    own[i] = gap(i);
  }
  for (let i = 0; i < n; i++) {
    const p = parent[i];
    if (p === null) continue;
    const u = units[i];
    const from = given[i] && u.origin ? u.origin : centers[p];
    const v = centers[i].clone().sub(from);
    if (given[i] && u.axis && u.axis.lengthSq() > 1e-12) {
      const a = u.axis.clone().normalize();
      const along = v.length() > 1e-6 ? v.clone().normalize().dot(a) : 1;
      if (Math.abs(along) >= 0.5 || v.length() < whole * 0.01) {
        // sits along its axis: out along it, away from the parent
        const s = Math.sign(along) || Math.sign(centers[i].clone().sub(centers[p]).dot(a)) || 1;
        dirs[i].copy(a).multiplyScalar(s);
      } else dirs[i].copy(v.normalize());
      own[i] = childStep(i, p);
    } else if (!given[i]) {
      // inside its parent's box: out through the parent's thinnest side, clearing it
      const s = units[p].box.getSize(new THREE.Vector3());
      const k = s.z <= s.x && s.z <= s.y ? "z" : s.y <= s.x ? "y" : "x";
      dirs[i].set(0, 0, 0)[k] = k === "z" ? 1 : (Math.sign(v[k]) || 1);
      own[i] = childStep(i, p);
    } else {
      dirs[i].copy(v.lengthSq() > 1e-12 ? v.normalize() : new THREE.Vector3(0, 0, 1));
      own[i] = childStep(i, p);
    }
  }

  // parents before children; among the ready ones, the one furthest back along its way first
  const done: boolean[] = units.map(() => false);
  const placed: number[] = [];
  const ancestors = (i: number) => { const s = new Set<number>(); let k = parent[i]; while (k !== null && !s.has(k)) { s.add(k); k = parent[k]; } return s; };
  const shifted = (i: number) => units[i].box.clone().translate(out[i]);
  for (let step = 0; step < n; step++) {
    let pick = -1; let key = Infinity;
    for (let i = 0; i < n; i++) {
      if (done[i] || (parent[i] !== null && !done[parent[i] as number])) continue;
      const k = dirs[i].lengthSq() ? centers[i].dot(dirs[i]) : -Infinity;
      if (pick < 0 || k < key) { pick = i; key = k; }
    }
    if (pick < 0) break;
    const i = pick;
    const p = parent[i];
    out[i].copy(p === null ? new THREE.Vector3() : out[p]).addScaledVector(dirs[i], own[i]);
    if (dirs[i].lengthSq()) {
      // would land on a part already out the same way (or one that stays): stack past it
      const anc = ancestors(i);
      const eps = whole * 1e-4;
      for (let guard = 0; guard < n; guard++) {
        const me = shifted(i);
        let push = 0;
        for (const j of placed) {
          if (anc.has(j)) continue;
          if (dirs[j].lengthSq() && dirs[j].dot(dirs[i]) < 0.7) continue;
          const other = shifted(j);
          if (!overlaps(me, other, eps)) continue;
          // stacked siblings: apart by a share of the smaller and of the mover's own size that way
          const sep = STACK_GAP * Math.min(diags[i], diags[j]) + MOUNT_GAP * extent(units[i].box, dirs[i]);
          push = Math.max(push, proj(other, dirs[i], true) - proj(me, dirs[i], false) + sep);
        }
        if (push <= eps) break;
        out[i].addScaledVector(dirs[i], push);
      }
    }
    done[i] = true;
    placed.push(i);
  }
  return out;
}
