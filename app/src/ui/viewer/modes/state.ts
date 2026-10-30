// The view modes' state: X-ray (all parts or some), a section plane, the explode amount and an
// isolated subset. One plain object, so the Design viewer, the thread's view cards and the
// immersive view all drive the same ModesController with it.
import { signal, type Signal } from "@preact/signals";

export type Vec3 = [number, number, number];

/** opacity 1 is off. `parts` null: every part; else only these (by part id). */
export interface XrayMode { opacity: number; parts: readonly string[] | null }

/** The plane keeps the side n·p <= at (you look at the cut from +n). `at` null: through the middle
 *  of what is shown. `normal` need not be unit length. */
export interface SectionMode { on: boolean; normal: Vec3; at: number | null }

export interface ModesState {
  xray: XrayMode;
  section: SectionMode;
  /** 0 (assembled) … 1 (fully apart). */
  explode: number;
  /** Shown at full strength; the other parts stay as faint ghosts. null: no subset. */
  isolate: readonly string[] | null;
}

export const XRAY_OPACITY = 0.25;
/** The parts outside an isolated subset: there for context, not in the way. */
export const GHOST_OPACITY = 0.08;

export const MODES_OFF: ModesState = {
  xray: { opacity: 1, parts: null },
  section: { on: false, normal: [1, 0, 0], at: null },
  explode: 0,
  isolate: null,
};

/** The Design viewer's modes (the sheet's own; cards and the immersive view keep theirs). */
export const designModes: Signal<ModesState> = signal<ModesState>(MODES_OFF);
