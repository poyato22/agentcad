// Words and numbers the build view shows (moved here from the deleted assembly panel, Plan 9 D5).
import type { JointKind } from "../../api/assembly";

export const KIND_LABEL: Record<JointKind, string> = {
  fixed: "Fixed", revolute: "Hinge", slider: "Slide", cylindrical: "Pin and slide", ball: "Ball",
};

export function fmtVolume(v: number): string {
  return v < 10 ? `${v.toFixed(2)} mm³` : `${Math.round(v).toLocaleString("en-US")} mm³`;
}
