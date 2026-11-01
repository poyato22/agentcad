"""The Plan 10 shared contract between C2 (mechanisms, assembly solve), C3 (dynamics export)
and C1 (assembly health), as the coordinator ruled it. The models live in assembly/model.py;
this note is the one place that states the units.

Couplings: `Joint.coupling = {kind, ratio, partner_joint, offset}` on the FOLLOWER joint.
    follower.value = offset + ratio * partner.value
  in JOINT VALUE UNITS, i.e. what `Joint.value` stores:
    - revolute values are DEGREES, slider values are MILLIMETRES;
    - gear / belt (revolute follows revolute): ratio in deg per deg (external gear pair: -z1/z2);
    - rack / screw (slider follows revolute): ratio in mm per deg (rack: r * pi / 180;
      screw: lead / 360);
    - a revolute following a slider: ratio in deg per mm;
    - `offset` is the follower's value when the partner is at 0, in the follower's units (a
      planet's mesh phase). It is NOT optional for an exporter: drop it and the start pose
      violates the equality.
  MuJoCo (C3): `equality joint` with `polycoef = [offset_si, ratio_si, 0, 0, 0]`, converting
  with the factors below (SI: rad for hinges, m for slides).

Loops: `Assembly.loops[] = {id, kind: "loop", joint_a, joint_b, frame_a, frame_b, type,
  drivers}`. `joint_a` / `joint_b` are INSTANCE ids (the two bodies); `frame_a` / `frame_b`
  the loop-closing joint's frames on them (part mm), which coincide when the loop is closed.
  type "revolute" (point + axis) and "ball" (point) map to MuJoCo `equality connect`, "fixed" to
  `equality weld`. `drivers` are the joints held as inputs.
"""
from __future__ import annotations

import math

DEG = math.pi / 180.0          # deg -> rad
MM = 1e-3                      # mm -> m

#: (follower kind, partner kind) -> factor turning a stored ratio into SI (rad / m) units
RATIO_TO_SI = {
    ("revolute", "revolute"): 1.0,                 # deg/deg == rad/rad
    ("slider", "revolute"): MM / DEG,              # mm/deg -> m/rad
    ("revolute", "slider"): DEG / MM,              # deg/mm -> rad/m
    ("slider", "slider"): 1.0,                     # mm/mm == m/m
}
#: follower kind -> factor turning a stored offset into SI
OFFSET_TO_SI = {"revolute": DEG, "slider": MM}
LOOP_TO_MJCF = {"revolute": "connect", "ball": "connect", "fixed": "weld"}


def polycoef(follower_kind: str, partner_kind: str, ratio: float, offset: float) -> list[float]:
    """MuJoCo `equality joint` polycoef for a stored coupling (SI units)."""
    return [offset * OFFSET_TO_SI[follower_kind],
            ratio * RATIO_TO_SI[(follower_kind, partner_kind)], 0.0, 0.0, 0.0]
