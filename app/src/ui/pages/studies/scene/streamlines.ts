// Wind: drawing the core's 3D streamlines (traced from the solver's velocity field). Every sample
// along a line is the same time step after the last, so a comet that moves one sample per tick
// shows the real relative speed: it bunches up where the flow slows and stretches where it speeds up.
import type { StudyStreamlines } from "../../../../api/studies";

type Ramp = (t: number) => [number, number, number];

/** Samples per second of screen time: a slowed-down view, the same factor for every line. */
export const SAMPLES_PER_SECOND = 90;
export const TRAIL = 14;
export const GAP = 70;

const tone = (speed: number, s: StudyStreamlines) => (s.speed_max > 0 ? Math.min(1, speed / s.speed_max) : 0);

export function baseLines(s: StudyStreamlines, ramp: Ramp, gain: number): { positions: Float32Array; colors: Float32Array } {
  let segs = 0;
  for (const l of s.lines) segs += Math.max(0, l.points.length - 1);
  const positions = new Float32Array(segs * 6); const colors = new Float32Array(segs * 6);
  let k = 0;
  for (const l of s.lines) for (let i = 1; i < l.points.length; i++) for (const j of [i - 1, i]) {
    positions.set(l.points[j], k);
    const c = ramp(tone(l.speed[j], s));
    colors[k] = c[0] * gain; colors[k + 1] = c[1] * gain; colors[k + 2] = c[2] * gain;
    k += 3;
  }
  return { positions, colors };
}

export interface Comets { positions: Float32Array; colors: Float32Array; perLine: number[]; ramp: Ramp }

export function cometBuffers(s: StudyStreamlines, ramp: Ramp): Comets {
  const perLine = s.lines.map((l) => Math.max(1, Math.floor(l.points.length / GAP)));
  const n = perLine.reduce((a, b) => a + b, 0) * TRAIL;
  return { positions: new Float32Array(n * 6), colors: new Float32Array(n * 6), perLine, ramp };
}

/** Each comet's head is at sample (clock + its offset) along its line, wrapping to the seed. */
export function writeComets(s: StudyStreamlines, c: Comets, clock: number): void {
  let k = 0;
  s.lines.forEach((l, li) => {
    const len = l.points.length;
    const phase = (li * 37) % GAP;       // lines are not in step
    for (let m = 0; m < c.perLine[li]; m++) {
      const head = Math.floor(clock + phase + (m * len) / c.perLine[li]) % len;
      for (let t = 0; t < TRAIL; t++) {
        const a = head - t; const b = head - t - 1;
        for (const [j, fade] of [[a, 1 - t / TRAIL], [b, 1 - (t + 1) / TRAIL]] as const) {
          const q = l.points[Math.max(0, j)];
          c.positions[k] = q[0]; c.positions[k + 1] = q[1]; c.positions[k + 2] = q[2];
          const on = a > 0 ? fade : 0;    // a comet entering at the seed has no tail behind it yet
          const col = c.ramp(tone(l.speed[Math.max(0, j)], s));
          c.colors[k] = col[0] * on; c.colors[k + 1] = col[1] * on; c.colors[k + 2] = col[2] * on;
          k += 3;
        }
      }
    }
  });
}
