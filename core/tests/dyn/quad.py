"""A small quadruped fixture (Plan 10 C3): a 300 x 160 x 50 mm torso, four legs of a 120 mm
thigh (hip about y) and a 130 mm shank (knee about y), parts modelled in place and posed by
the joint values (hip +30 deg, knee -60 deg: feet under the hips, 208 mm below the hip axis).

`write_metrics=True` writes each part's metrics.json by hand (bbox, volume, B-rep-style box
inertia) so the dynamics tests need no build; the proof script builds the same parts for real.
"""
from __future__ import annotations

import json
from pathlib import Path

from mises.assembly.model import Assembly, AssemblyStore, Frame, Instance, Joint, JointRef

PLA = 1.24
LEGS = {"fl": (130.0, 92.0), "fr": (130.0, -92.0), "hl": (-130.0, 92.0), "hr": (-130.0, -92.0)}
HIP, KNEE = 30.0, -60.0
TORSO = ((-150, -80, -25), (150, 80, 25))
TORSO_VOLUME = 1.6e6          # a hollowed torso: ~2 kg in PLA


def leg_boxes(x: float, y: float):
    thigh = ((x - 12, y - 12, -120), (x + 12, y + 12, 0))
    shank = ((x - 10, y - 10, -250), (x + 10, y + 10, -120))
    return thigh, shank


def _metrics(lo, hi, volume=None) -> dict:
    size = [h - l for l, h in zip(lo, hi)]
    vol = volume if volume is not None else size[0] * size[1] * size[2]
    m = vol * PLA * 1e-6
    a, b, c = (s / 1000 for s in size)
    return {"valid": True, "volume_mm3": vol, "mass_g": m * 1000,
            "bbox": {"min": list(lo), "max": list(hi), "size": size},
            "center_of_mass": [(l + h) / 2 for l, h in zip(lo, hi)],
            "inertia": {"mass_kg": m, "com_mm": [(l + h) / 2 for l, h in zip(lo, hi)],
                        "inertia_kgm2": [[m * (b * b + c * c) / 12, 0, 0],
                                         [0, m * (a * a + c * c) / 12, 0],
                                         [0, 0, m * (a * a + b * b) / 12]],
                        "density_g_cm3": PLA},
            "face_list": [], "holes": []}


def part_boxes() -> dict[str, tuple]:
    out = {"quad_torso": TORSO}
    for leg, (x, y) in LEGS.items():
        th, sh = leg_boxes(x, y)
        out[f"quad_{leg}_thigh"] = th
        out[f"quad_{leg}_shank"] = sh
    return out


def write_parts(home: Path) -> None:
    for pid, (lo, hi) in part_boxes().items():
        d = Path(home) / "parts" / pid
        d.mkdir(parents=True, exist_ok=True)
        vol = TORSO_VOLUME if pid == "quad_torso" else None
        (d / "metrics.json").write_text(json.dumps(_metrics(lo, hi, vol)))


def assembly(project_id: str = "quad", store: AssemblyStore | None = None,
             hip: float = HIP, knee: float = KNEE, limits: bool = True) -> Assembly:
    insts = [Instance(id="torso", part_id="quad_torso", name="torso", fixed=True)]
    joints = []
    for leg, (x, y) in LEGS.items():
        insts.append(Instance(id=f"{leg}_thigh", part_id=f"quad_{leg}_thigh", name=f"{leg} thigh"))
        insts.append(Instance(id=f"{leg}_shank", part_id=f"quad_{leg}_shank", name=f"{leg} shank"))
        hf = Frame(origin=[x, y, 0.0], axis=[0.0, 1.0, 0.0])
        kf = Frame(origin=[x, y, -120.0], axis=[0.0, 1.0, 0.0])
        joints.append(Joint(id=f"{leg}_hip", kind="revolute", value=hip,
                            limits=[-90.0, 90.0] if limits else None,
                            a=JointRef(instance_id="torso", frame=hf),
                            b=JointRef(instance_id=f"{leg}_thigh", frame=hf)))
        joints.append(Joint(id=f"{leg}_knee", kind="revolute", value=knee,
                            limits=[-150.0, 0.0] if limits else None,
                            a=JointRef(instance_id=f"{leg}_thigh", frame=kf),
                            b=JointRef(instance_id=f"{leg}_shank", frame=kf)))
    asm = Assembly(instances=insts, joints=joints)
    if store is not None:
        for i in insts:
            store.add_instance(project_id, i)
        for j in joints:
            store.add_joint(project_id, j)
    return asm
