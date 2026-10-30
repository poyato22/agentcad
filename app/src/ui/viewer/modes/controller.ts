// One viewer's view modes: X-ray, section caps, explode and an isolated subset, applied together
// and re-applied after every restyle of the viewer (new parts, shaded / wire, theme), since a part
// that reloads comes with fresh materials.
import type * as THREE from "three";
import type { ModeHost, ShownPart } from "../scene";
import { Exploder } from "./explode";
import { realClock, type Clock } from "./motion";
import { SectionCaps } from "./section";
import { MODES_OFF, type ModesState } from "./state";
import { applyXray } from "./xray";

export class ModesController {
  readonly caps: SectionCaps;
  readonly exploder: Exploder;
  private state: ModesState = MODES_OFF;
  private readonly off: () => void;
  private applying = false;

  constructor(readonly host: ModeHost, clock: Clock = realClock) {
    this.caps = new SectionCaps(host);
    this.exploder = new Exploder(host, clock);
    this.exploder.onChange = () => { if (!this.applying) this.applySection(); };
    this.off = host.onRestyle(() => this.reapply());
  }

  get modes(): ModesState { return this.state; }

  /** Apply `next`. The explode glides from where it is when `animate` (and motion is allowed). */
  set(next: ModesState, opts: { animate?: boolean } = {}): void {
    const prev = this.state;
    this.state = next;
    this.applying = true;
    try {
      const parts = this.host.parts();
      applyXray(parts, next);
      this.exploder.sync(parts);
      if (prev.explode !== next.explode || this.exploder.value !== next.explode) this.exploder.set(next.explode, opts.animate ?? false);
    } finally { this.applying = false; }
    this.applySection();
    this.host.requestRender();
  }

  private reapply(): void {
    this.applying = true;
    try {
      const parts = this.host.parts();
      applyXray(parts, this.state);
      this.exploder.sync(parts);
    } finally { this.applying = false; }
    this.applySection();
  }

  private applySection(): void {
    const parts = this.host.parts();
    // the plane's default (through the middle) is taken from the assembled layout, so exploding
    // does not slide the cut
    this.caps.apply(this.state.section, parts, restBox(parts));
    this.host.requestRender();
  }

  dispose(): void {
    this.off();
    this.exploder.dispose();
    this.caps.dispose();
  }
}

/** Everything shown, assembled (the section plane's range and default are taken from it). */
export function restBox(parts: readonly ShownPart[]): THREE.Box3 | null {
  if (!parts.length) return null;
  const b = parts[0].scene.bbox.clone();
  for (const p of parts) b.union(p.scene.bbox);
  return b;
}
