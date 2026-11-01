"""Building blocks the mechanism builders share: a plan accumulator, shafts on bearings in a
frame plate, and the planar-linkage builder (nodes + links -> parts, a joint tree, loop
closures and consistent initial joint values)."""
from __future__ import annotations

import math
from typing import Sequence

from .card import (O, InstSpec, JointSpec, LoopSpec, MechPlan, PartSpec, Port, Z, fr)

# catalog deep-groove bearing by shaft bore (catalog/data/bearings.json): id, D, B
BEARINGS = {3: ("bearing_623_deep_groove", 10.0, 4.0), 4: ("bearing_624_deep_groove", 13.0, 5.0),
            5: ("bearing_625_deep_groove", 16.0, 5.0), 6: ("bearing_626_deep_groove", 19.0, 6.0),
            8: ("bearing_608_deep_groove", 22.0, 7.0), 10: ("bearing_6000_deep_groove", 26.0, 8.0),
            12: ("bearing_6001_deep_groove", 28.0, 8.0), 15: ("bearing_6002_deep_groove", 32.0, 9.0),
            17: ("bearing_6003_deep_groove", 35.0, 10.0),
            20: ("bearing_6004_deep_groove", 42.0, 12.0)}
SLIP = 0.10        # bearing seat clearance as modelled [J, FDM]; a press fit prints D - 0.10
SEAT_LIFT = 0.5    # a bearing sits this far above the plate's underside
# shafts are modelled this much under their nominal d, so a shaft never shares a cylindrical
# face with a bearing or gear bore (OCCT booleans misreport coincident cylinders as overlap);
# the design data keeps the nominal d (a k6 / h6 fit in the real build)
SHAFT_UNDERSIZE = 0.02
PIN_CLEAR = 0.3    # printed pin / bore clearance (FDM 0.2-0.4 mm)


def bearing_for(d: float) -> tuple[str, float, float, int]:
    """(catalog id, D, B, bore) of the catalog bearing for a shaft of d mm (the next size up)."""
    for bore in sorted(BEARINGS):
        if bore >= d - 1e-6:
            cid, D, B = BEARINGS[bore]
            return cid, D, B, bore
    raise ValueError(f"no catalog bearing for a {d:g} mm shaft (max 20 mm)")


class Kit:
    """Accumulates a MechPlan."""

    def __init__(self, root: str):
        self.plan = MechPlan(root=root, parts=[], instances=[], joints=[])

    # ---- parts and instances
    def part(self, key: str, recipe: str = "", label: str = "", material: str | None = "PLA",
             catalog: str | None = None, **kwargs) -> str:
        if not any(p.key == key for p in self.plan.parts):
            self.plan.parts.append(PartSpec(key=key, recipe=recipe, kwargs=kwargs,
                                            label=label or key.replace("_", " "),
                                            material=material, catalog=catalog))
        return key

    def inst(self, key: str, part: str | None = None, name: str | None = None,
             transform=None) -> str:
        self.plan.instances.append(InstSpec(key=key, part=part or key,
                                            name=name or key.replace("_", " "),
                                            transform=transform))
        return key

    # ---- joints
    def joint(self, key: str, kind: str, a: str, fa: tuple, b: str, fb: tuple, value=0.0,
              limits=None, design: dict | None = None, coupling: dict | None = None) -> str:
        self.plan.joints.append(JointSpec(key=key, kind=kind, a=a, fa=fa, b=b, fb=fb,
                                          value=value, limits=limits, design=design,
                                          coupling=coupling))
        return key

    def fixed(self, key: str, a: str, fa: tuple, b: str, fb: tuple = None) -> str:
        return self.joint(key, "fixed", a, fa, b, fb or fr())

    def couple(self, follower: str, kind: str, ratio: float, partner: str,
               offset: float = 0.0) -> None:
        j = next(j for j in self.plan.joints if j.key == follower)
        j.coupling = {"kind": kind, "ratio": float(ratio), "partner": partner,
                      "offset": float(offset)}
        pv = next(x.value for x in self.plan.joints if x.key == partner)
        pv = pv[0] if isinstance(pv, (list, tuple)) else pv
        j.value = float(offset) + float(ratio) * float(pv)

    def loop(self, key: str, a: str, fa: tuple, b: str, fb: tuple, type: str = "revolute",
             drivers: list[str] | None = None) -> str:
        self.plan.loops.append(LoopSpec(key=key, a=a, fa=fa, b=b, fb=fb, type=type,
                                        drivers=drivers))
        return key

    def port(self, name: str, kind: str, inst: str, origin, axis=Z, note: str = "") -> None:
        self.plan.ports.append(Port(name=name, kind=kind, instance=inst,
                                    origin=[float(c) for c in origin],
                                    axis=[float(c) for c in axis], note=note))

    # ---- common sub-assemblies
    def plate(self, key: str, length: float, width: float, thickness: float,
              bores: Sequence[Sequence[float]], origin_x: float = 0.0,
              material: str = "PETG") -> str:
        """The grounded frame plate (top face z = 0) and its instance."""
        self.part(key, "frame_plate", "frame plate", material, length=length, width=width,
                  thickness=thickness, bores=[list(map(float, b)) for b in bores],
                  origin_x=origin_x)
        return self.inst(key, key, "frame")

    def shaft_on_bearing(self, key: str, plate: str, x: float, y: float, plate_t: float,
                         shaft_d: float, length: float, value: float = 0.0, limits=None,
                         load_n: float | None = None, retention: str = "snap ring") -> tuple:
        """A shaft on +Z through a catalog bearing pressed into the plate at (x, y): the bearing
        sits in the seat (z = -plate_t..), the shaft's local z = 0 is 2 mm below the plate.
        Returns (shaft instance, joint key, world z of the shaft's local origin)."""
        cid, D, B, bore = bearing_for(shaft_d)
        bkey = self.part(cid, catalog=cid, label=f"bearing {cid.split('_')[1]}", material=None)
        bi = self.inst(f"{key}_bearing", bkey, f"{key} bearing")
        # 0.5 mm above the plate's underside: no coplanar faces for the interference check
        self.fixed(f"{key}_bearing_seat", plate, fr((x, y, -plate_t + SEAT_LIFT)), bi, fr())
        skey = self.part(f"{key}_shaft", "shaft", f"{key} shaft", "Steel 1018",
                         d=float(bore) - SHAFT_UNDERSIZE, length=float(length),
                         grooves=[2.0 + plate_t + 1.0])
        si = self.inst(f"{key}_shaft", skey, f"{key} shaft")
        z0 = -plate_t - 2.0
        j = self.joint(f"{key}_rev", "revolute", plate, fr((x, y, z0)), si, fr(), value=value,
                       limits=limits,
                       design={"bearing": cid, "shaft": {"d_mm": float(bore),
                                                        "material": "Steel 1018"},
                               "retention": retention, "clearance_mm": 0.0,
                               **({"load_n": load_n} if load_n else {})})
        self.plan.contacts += [(bi, si), (plate, bi)]
        return si, j, z0

    def on_shaft(self, key: str, shaft: str, part: str, z_world: float, z0: float,
                 name: str | None = None) -> str:
        """Instance `key` of `part` fixed on a shaft with its local z = 0 at world z_world."""
        i = self.inst(key, part, name)
        self.fixed(f"{key}_on_shaft", shaft, fr((0, 0, z_world - z0)), i, fr())
        self.plan.contacts.append((shaft, i))
        return i


def pin_design(d: float = 3.0, bearing: str = "bushing", retention: str = "e-clip") -> dict:
    """E27 metadata of a printed pin joint: pin, bushing, retention and FDM clearance."""
    return {"bearing": bearing, "shaft": {"d_mm": d, "material": "Steel 1018"},
            "retention": retention, "clearance_mm": PIN_CLEAR, "fastener": f"M{d:g}"}


# ---------------------------------------------------------------- planar linkages

def _ang(p, q) -> float:
    return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))


def _local(p0, alpha_deg: float, p) -> tuple[float, float]:
    a = math.radians(-alpha_deg)
    dx, dy = p[0] - p0[0], p[1] - p0[1]
    return (dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a))


def circle_intersect(p1, r1: float, p2, r2: float, side: int = 1) -> tuple[float, float]:
    """The intersection of two circles left (side 1) or right (side -1) of p1 -> p2."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    d = math.hypot(dx, dy)
    if d > r1 + r2 + 1e-9 or d < abs(r1 - r2) - 1e-9 or d == 0:
        raise ValueError("the linkage cannot be assembled at this input (circles do not meet)")
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(0.0, r1 * r1 - a * a))
    mx, my = p1[0] + a * dx / d, p1[1] + a * dy / d
    return (mx - side * h * dy / d, my + side * h * dx / d)


def planar_linkage(kit: Kit, nodes: dict[str, tuple[float, float]],
                   links: list[tuple[str, list[str]]], ground_nodes: list[str],
                   driver: tuple[str, str], *, width: float = 10.0, thickness: float = 4.0,
                   pin_d: float = 3.0, gap: float = 1.0, material: str = "PETG",
                   ground_key: str = "ground", ground_part: dict | None = None,
                   limits: dict | None = None) -> dict:
    """Add a planar linkage (in the XY plane, pins on +Z) to `kit`.

    `nodes`: name -> (x, y) at the initial pose. `links`: (name, [node, ...]) for every moving
    link (2 nodes: a bar; 3+: a plate whose local X runs from its first node to its second).
    `ground_nodes`: the pivots on the ground (a plate through them is the root unless the kit
    already has `ground_key`). `driver`: (link, ground node) of the input joint. Each link gets
    its own Z layer so stacked links clear each other. Returns {link: instance, "joints":
    {(link, node): joint key}, "driver": joint key}."""
    bore = pin_d + PIN_CLEAR
    have_ground = any(i.key == ground_key for i in kit.plan.instances)
    if not have_ground:
        gpts = [nodes[n] for n in ground_nodes]
        if len(gpts) == 1:
            gpts = [gpts[0], (gpts[0][0] + 2 * width, gpts[0][1])]
        kit.part(ground_key, "link_plate", "ground link", material,
                 points=[list(map(float, p)) for p in gpts], width=width * 1.6,
                 thickness=thickness, bore=bore)
        kit.inst(ground_key, ground_key, "ground")
    layer = {ground_key: 0}
    t = thickness + gap
    # spanning tree: BFS from the ground through shared nodes
    owner: dict[str, str] = {n: ground_key for n in ground_nodes}   # node -> placed link
    placed = {ground_key}
    alpha: dict[str, float] = {ground_key: 0.0}
    origin: dict[str, tuple[float, float]] = {ground_key: (0.0, 0.0)}
    for name, ns in links:
        alpha[name] = _ang(nodes[ns[0]], nodes[ns[1]])
        origin[name] = nodes[ns[0]]
    for k, (name, ns) in enumerate(links):
        layer[name] = k + 1
    joints: dict[tuple[str, str], str] = {}
    pending = list(links)
    loops = []

    def local(link: str, node: str) -> tuple[float, float]:
        if link == ground_key:          # the ground's local frame is the mechanism's
            return nodes[node]
        return _local(origin[link], alpha[link], nodes[node])

    def z_of(link: str) -> float:
        return layer[link] * t

    progress = True
    while pending and progress:
        progress = False
        for name, ns in list(pending):
            hit = next((n for n in ns if n in owner), None)
            if hit is None:
                continue
            parent = owner[hit]
            jkey = f"{name}_{hit}"
            pl, cl = local(parent, hit), local(name, hit)
            dz = z_of(name) - z_of(parent)
            value = alpha[name] - alpha[parent]
            lim = (limits or {}).get(name)
            kit.joint(jkey, "revolute", parent, fr((pl[0], pl[1], dz)), name,
                      fr((cl[0], cl[1], 0.0)), value=value, limits=lim, design=pin_design(pin_d))
            joints[(name, hit)] = jkey
            kit.plan.contacts.append((parent, name))
            for n in ns:
                if n not in owner:
                    owner[n] = name
            placed.add(name)
            pending.remove((name, ns))
            progress = True
    if pending:
        raise ValueError(f"links not connected to the ground: {[n for n, _ in pending]}")
    # every (link, node) incidence not used by the tree closes a loop
    for name, ns in links:
        for n in ns:
            if (name, n) in joints:
                continue
            other = owner[n]
            if other == name:
                continue
            dz = z_of(name) - z_of(other)
            loops.append((name, n, other, dz))
    dkey = joints.get((driver[0], driver[1]))
    if dkey is None:
        raise ValueError("the driver must be a link joined to a ground pivot")
    for name, n, other, dz in loops:
        la, lb = local(other, n), local(name, n)
        kit.plan.contacts.append((other, name))
        kit.loop(f"loop_{name}_{n}", other, fr((la[0], la[1], dz)), name, fr((lb[0], lb[1], 0)),
                 drivers=[dkey])
    for name, ns in links:
        pts = [local(name, n) for n in ns]
        if len(ns) == 2:
            L = math.dist(nodes[ns[0]], nodes[ns[1]])
            kit.part(name, "link_bar", name.replace("_", " "), material, length=round(L, 4),
                     width=width, thickness=thickness, bore=bore)
        else:
            kit.part(name, "link_plate", name.replace("_", " "), material,
                     points=[[round(x, 4), round(y, 4)] for x, y in pts], width=width,
                     thickness=thickness, bore=bore)
        kit.inst(name, name)
    kit.plan.planar = True
    kit.plan.inputs.append(dkey)
    return {"joints": joints, "driver": dkey, "layer": layer, "pitch": t}
