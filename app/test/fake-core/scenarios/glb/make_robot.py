"""Regenerates the Plan 10 robot fixture: a rover with an arm, as an assembly of 8 parts and 13
instances five joint levels deep (chassis → turret → upper arm → forearm → palm → fingers, plus
four wheels on axles, a lidar on a fixed mount and a battery sitting loose in the chassis pocket).

    cd core && uv run python ../app/test/fake-core/scenarios/glb/make_robot.py

Writes robot_*.glb here and ../robot/parts.json (the parts with metrics) plus
../robot/assembly.json (stored instances and joints in the core's shapes). Unlike the Pi case,
every part is modelled about its OWN origin: the assembly's transforms and joints place them.
"""
import json
from pathlib import Path

import build123d as b3
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.TopLoc import TopLoc_Location

OUT = Path(__file__).resolve().parent


def bake(s):
    """The shape's location folded into its geometry: export_gltf writes a located shape's
    translation in the Y-up frame unrotated, which the app (Z-up) would read sideways."""
    trsf = s.wrapped.Location().Transformation()
    bare = s.wrapped.Located(TopLoc_Location())
    out = BRepBuilderAPI_Transform(bare, trsf, True).Shape()
    return b3.Solid(out) if isinstance(s, b3.Solid) else b3.Compound(out)


def chassis():
    body = b3.Pos(0, 0, 15) * b3.Box(120, 80, 30)
    pocket = b3.Pos(0, 0, 20) * b3.Box(100, 60, 20)
    return body - pocket


def battery():
    return b3.Box(60, 40, 15)


def wheel():
    tyre = b3.Cylinder(22, 12) - b3.Cylinder(14, 12)
    return tyre + b3.Cylinder(15, 6)


def lidar():
    return b3.Pos(0, 0, 6) * b3.Cylinder(10, 12)


def turret():
    return b3.Pos(0, 0, 10) * b3.Cylinder(25, 20)


def upper_arm():
    return b3.Pos(0, 0, 40) * b3.Box(14, 14, 80)


def forearm():
    return b3.Pos(35, 0, 0) * b3.Box(70, 12, 12)


def palm():
    return b3.Pos(5, 0, 0) * b3.Box(10, 30, 16)


def finger():
    return b3.Pos(9, 0, 0) * b3.Box(18, 4, 10)


PARTS = {"robot_chassis": ("Chassis", chassis), "robot_battery": ("Battery", battery),
         "robot_wheel": ("Wheel", wheel), "robot_lidar": ("Lidar", lidar),
         "robot_turret": ("Turret", turret), "robot_upper_arm": ("Upper arm", upper_arm),
         "robot_forearm": ("Forearm", forearm), "robot_palm": ("Palm", palm),
         "robot_finger": ("Finger", finger)}
COLORS = {"robot_chassis": "#5b6b7a", "robot_battery": "#c9a227", "robot_wheel": "#2b2f36",
          "robot_lidar": "#3d8bff", "robot_turret": "#8a6fd1", "robot_upper_arm": "#e07a3f",
          "robot_forearm": "#e07a3f", "robot_palm": "#4fae7a", "robot_finger": "#4fae7a"}

I = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def frame(origin, axis):
    return {"origin": origin, "axis": axis, "face_id": None}


def assembly():
    inst = []

    def add(iid, part, name, fixed=False, t=None):
        inst.append({"id": iid, "part_id": part, "name": name, "fixed": fixed, "transform": t or I})

    add("r_chassis", "robot_chassis", "chassis", fixed=True)
    # loose in the pocket (no joint): the explode finds it inside the chassis
    add("r_battery", "robot_battery", "battery",
        t=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 18.5], [0, 0, 0, 1]])
    for k in ("fl", "fr", "rl", "rr"):
        add(f"r_wheel_{k}", "robot_wheel", f"wheel {k}")
    add("r_lidar", "robot_lidar", "lidar")
    add("r_turret", "robot_turret", "turret")
    add("r_upper", "robot_upper_arm", "upper arm")
    add("r_fore", "robot_forearm", "forearm")
    add("r_palm", "robot_palm", "palm")
    add("r_finger_l", "robot_finger", "finger left")
    add("r_finger_r", "robot_finger", "finger right")
    joints = []

    def j(jid, kind, a, fa, b, fb, value=0, limits=None):
        joints.append({"id": jid, "kind": kind, "a": {"instance_id": a, "frame": fa},
                       "b": {"instance_id": b, "frame": fb}, "value": value, "limits": limits})

    for k, x, side in (("fl", 40, 1), ("fr", 40, -1), ("rl", -40, 1), ("rr", -40, -1)):
        j(f"j_wheel_{k}", "revolute", "r_chassis", frame([x, side * 46, 12], [0, side, 0]),
          f"r_wheel_{k}", frame([0, 0, 0], [0, 0, 1]))
    j("j_lidar", "fixed", "r_chassis", frame([-42, 0, 30], [0, 0, 1]),
      "r_lidar", frame([0, 0, 0], [0, 0, 1]))
    j("j_turret", "revolute", "r_chassis", frame([30, 0, 30], [0, 0, 1]),
      "r_turret", frame([0, 0, 0], [0, 0, 1]), value=20, limits=[-170, 170])
    j("j_shoulder", "revolute", "r_turret", frame([0, 0, 20], [0, 1, 0]),
      "r_upper", frame([0, 0, 0], [0, 1, 0]), value=-15, limits=[-90, 90])
    j("j_elbow", "revolute", "r_upper", frame([0, 0, 80], [0, 1, 0]),
      "r_fore", frame([0, 0, 0], [0, 1, 0]), value=30, limits=[-120, 120])
    j("j_wrist", "revolute", "r_fore", frame([70, 0, 0], [1, 0, 0]),
      "r_palm", frame([0, 0, 0], [1, 0, 0]), value=0, limits=[-180, 180])
    j("j_finger_l", "slider", "r_palm", frame([10, 4, 0], [0, 1, 0]),
      "r_finger_l", frame([0, 0, 0], [0, 1, 0]), value=4, limits=[0, 10])
    j("j_finger_r", "slider", "r_palm", frame([10, -4, 0], [0, -1, 0]),
      "r_finger_r", frame([0, 0, 0], [0, -1, 0]), value=4, limits=[0, 10])
    return {"instances": inst, "joints": joints}


def main() -> None:
    parts = []
    for order, (pid, (label, make)) in enumerate(PARTS.items()):
        s = bake(make())
        b3.export_gltf(s, str(OUT / f"{pid}.glb"), binary=True)
        bb = s.bounding_box()
        parts.append({
            "part_id": pid, "vars": {}, "label": label, "order": order, "color": COLORS[pid],
            "kind": "part", "version": 1, "mtime": 1793520000.0 + order,
            "metrics": {
                "valid": s.is_valid, "volume_mm3": round(s.volume, 3), "area_mm2": round(s.area, 3),
                "bbox": {"min": [round(v, 4) for v in (bb.min.X, bb.min.Y, bb.min.Z)],
                         "max": [round(v, 4) for v in (bb.max.X, bb.max.Y, bb.max.Z)],
                         "size": [round(v, 4) for v in (bb.size.X, bb.size.Y, bb.size.Z)]},
                "center_of_mass": [round(v, 4) for v in s.center()], "holes": [],
                "faces": len(s.faces()), "edges": len(s.edges()), "solids": len(s.solids()),
                "mass_g": round(s.volume * 1.24e-3, 3),
            },
        })
    (OUT.parent / "robot" / "parts.json").write_text(json.dumps(parts, indent=1) + "\n")
    (OUT.parent / "robot" / "assembly.json").write_text(json.dumps(assembly(), indent=1) + "\n")


if __name__ == "__main__":
    main()
