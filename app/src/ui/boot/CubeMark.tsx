// The static grey Mises cube mark — the same three-tone iso cube as the app icon: a light top, a
// mid-tone left face and a darker right face, outlined in the theme's --mesh-edge. Used on the
// loading screen (where a live WebGL cube would be wasteful during startup).
import type { JSX } from "preact";

export interface CubeMarkProps {
  size?: number;
  /** A slow, gentle spin while the core starts. */
  spin?: boolean;
  /** Dimmed, for the error state. */
  muted?: boolean;
}

export function CubeMark({ size = 72, spin = false, muted = false }: CubeMarkProps): JSX.Element {
  return (
    <svg class={`cube-mark${spin ? " is-spin" : ""}${muted ? " is-muted" : ""}`} width={size} height={size}
      viewBox="0 0 100 100" role="img" aria-label="AgentCAD">
      {/* top (lightest) */}
      <polygon points="50,16 84,35 50,54 16,35" fill="#cfcfcf" />
      {/* left (mid) */}
      <polygon points="16,35 50,54 50,92 16,73" fill="#9a9a9a" />
      {/* right (darkest) */}
      <polygon points="50,54 84,35 84,73 50,92" fill="#6f6f6f" />
      <g fill="none" stroke="var(--mesh-edge, #e6e6e6)" stroke-width="2.2" stroke-linejoin="round">
        <polygon points="50,16 84,35 84,73 50,92 16,73 16,35" />
        <polyline points="16,35 50,54 84,35" />
        <line x1="50" y1="54" x2="50" y2="92" />
      </g>
    </svg>
  );
}
