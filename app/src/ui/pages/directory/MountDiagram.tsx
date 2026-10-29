// Mount-pattern diagram: the item's holes seen from the mounting face, drawn like a drafting
// detail (centre lines, the bolt circle for motors, the board outline, spacing dimensions in mm).
// Positions come from model.mountGeometry, which places holes the way the core's envelope does.
import type { JSX } from "preact";
import type { CatalogMount } from "../../../api/directory";
import { mountGeometry } from "./model";

const n = (v: number) => (Math.round(v * 10) / 10).toLocaleString("en-US");

export function MountDiagram({ mount, dims, size = 120, detailed = false }: {
  mount: CatalogMount | null; dims?: Record<string, number>; size?: number; detailed?: boolean;
}): JSX.Element | null {
  const g = mountGeometry(mount, dims);
  if (!g || !mount) return null;
  // mm → px: the pattern fills the box with room for the dimension labels
  const pad = detailed ? 26 : 10;
  const k = (size / 2 - pad) / g.extent;
  const c = size / 2;
  const X = (x: number) => c + x * k;
  const Y = (y: number) => c - y * k;
  const xs = g.holes.map((h) => h.x), ys = g.holes.map((h) => h.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const title = `Mount pattern ${mount.pattern ?? ""}${mount.thread ? ` ${mount.thread}` : ""}${mount.hole_d ? `, holes ⌀${n(mount.hole_d)} mm` : ""}`.trim();
  return (
    <svg class={detailed ? "mount mount-detailed" : "mount"} width={size} height={size} viewBox={`0 0 ${size} ${size}`}
      role="img" aria-label={title} data-mount={mount.pattern ?? ""}>
      <title>{title}</title>
      {g.outline && (
        <rect class="mount-outline" x={X(-g.outline.w / 2)} y={Y(g.outline.h / 2)} width={g.outline.w * k} height={g.outline.h * k} />
      )}
      <line class="mount-axis" x1={4} y1={c} x2={size - 4} y2={c} />
      <line class="mount-axis" x1={c} y1={4} x2={c} y2={size - 4} />
      {g.circle && <circle class="mount-circle" cx={c} cy={c} r={(g.circle / 2) * k} />}
      {g.holes.map((h, i) => (
        <g key={i}>
          <circle class="mount-hole" cx={X(h.x)} cy={Y(h.y)} r={Math.max(1.5, h.r * k)} />
          {detailed && <>
            <line class="mount-mark" x1={X(h.x) - h.r * k - 3} y1={Y(h.y)} x2={X(h.x) + h.r * k + 3} y2={Y(h.y)} />
            <line class="mount-mark" x1={X(h.x)} y1={Y(h.y) - h.r * k - 3} x2={X(h.x)} y2={Y(h.y) + h.r * k + 3} />
          </>}
        </g>
      ))}
      {detailed && g.circle !== null && (
        // motors: the pattern is the distance between opposite holes, along the diagonal
        <text class="mount-dim" x={c} y={size - 6} text-anchor="middle">{`⌀${n(g.circle)} mm bolt circle`}</text>
      )}
      {detailed && g.circle === null && maxX > minX && (
        <g>
          <line class="mount-dimline" x1={X(minX)} y1={size - 16} x2={X(maxX)} y2={size - 16} />
          <line class="mount-ext" x1={X(minX)} y1={Y(minY)} x2={X(minX)} y2={size - 12} />
          <line class="mount-ext" x1={X(maxX)} y1={Y(minY)} x2={X(maxX)} y2={size - 12} />
          <text class="mount-dim" x={(X(minX) + X(maxX)) / 2} y={size - 4} text-anchor="middle">{`${n(maxX - minX)} mm`}</text>
        </g>
      )}
      {detailed && g.circle === null && maxY > minY && (
        <g>
          <line class="mount-dimline" x1={size - 16} y1={Y(maxY)} x2={size - 16} y2={Y(minY)} />
          <line class="mount-ext" x1={X(maxX)} y1={Y(maxY)} x2={size - 12} y2={Y(maxY)} />
          <line class="mount-ext" x1={X(maxX)} y1={Y(minY)} x2={size - 12} y2={Y(minY)} />
          <text class="mount-dim" x={size - 4} y={(Y(maxY) + Y(minY)) / 2} text-anchor="middle"
            transform={`rotate(-90 ${size - 4} ${(Y(maxY) + Y(minY)) / 2})`}>{`${n(maxY - minY)} mm`}</text>
        </g>
      )}
    </svg>
  );
}
