"""A mechanism plan as assembly rows (Instance / Joint / Loop) and, for tests and previews only,
as in-process geometry. mech_insert builds the real parts in the sandboxed worker; nothing in
the server process imports build123d through this module unless `preview_*` is called."""
from __future__ import annotations

import json
import math
import re

from ..assembly.model import Assembly, Coupling, Frame, Instance, Joint, JointDesign, JointRef, Loop
from ..assembly.solve import frame_matrix, inverse_rigid, matmul, solve
from .card import MechPlan


def _frame(f) -> Frame:
    origin, axis = f
    return Frame(origin=[float(c) for c in origin], axis=[float(c) for c in axis])


def root_transform(at: dict | None) -> list[list[float]]:
    """The root instance's world transform for `at` {origin, axis}: the mechanism's own +Z goes
    along `at.axis` (identity for the default origin 0, axis +Z)."""
    at = at or {}
    origin = [float(c) for c in (at.get("origin") or (0.0, 0.0, 0.0))]
    axis = [float(c) for c in (at.get("axis") or (0.0, 0.0, 1.0))]
    return matmul(frame_matrix(origin, axis), inverse_rigid(frame_matrix([0, 0, 0], [0, 0, 1])))


def to_models(plan: MechPlan, inst_ids: dict[str, str], part_ids: dict[str, str],
              root: list[list[float]] | None = None, *, fixed_root: bool = True
              ) -> tuple[list[Instance], list[Joint], list[Loop]]:
    """Assembly rows for a plan: instance keys -> `inst_ids`, part keys -> `part_ids`, joint and
    loop ids derived from the instance ids so they are unique per insert."""
    instances = []
    for i in plan.instances:
        tr = root if (i.key == plan.root and root is not None) else None
        kw = {"transform": tr} if tr is not None else {}
        instances.append(Instance(id=inst_ids[i.key], part_id=part_ids[i.part], name=i.name,
                                  fixed=fixed_root and i.key == plan.root, **kw))
    jid = {j.key: f"{inst_ids[plan.root][:6]}_{_short(j.key)}" for j in plan.joints}
    joints = []
    for j in plan.joints:
        coupling = None
        if j.coupling:
            c = j.coupling
            coupling = Coupling(kind=c["kind"], ratio=c["ratio"], partner_joint=jid[c["partner"]],
                                offset=c.get("offset", 0.0))
        joints.append(Joint(id=jid[j.key], kind=j.kind,
                            a=JointRef(instance_id=inst_ids[j.a], frame=_frame(j.fa)),
                            b=JointRef(instance_id=inst_ids[j.b], frame=_frame(j.fb)),
                            value=j.value, limits=j.limits, coupling=coupling,
                            design=JointDesign(**j.design) if j.design else None))
    loops = [Loop(id=f"{inst_ids[plan.root][:6]}_{_short(lp.key)}", joint_a=inst_ids[lp.a],
                  joint_b=inst_ids[lp.b], frame_a=_frame(lp.fa), frame_b=_frame(lp.fb),
                  type=lp.type, drivers=[jid[d] for d in lp.drivers] if lp.drivers else None)
             for lp in plan.loops]
    return instances, joints, loops


def _short(key: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", key.lower())[:40]


def joint_ids(plan: MechPlan, inst_ids: dict[str, str]) -> dict[str, str]:
    return {j.key: f"{inst_ids[plan.root][:6]}_{_short(j.key)}" for j in plan.joints}


def plan_assembly(plan: MechPlan, at: dict | None = None) -> Assembly:
    """The plan as an Assembly whose instance ids are the plan keys (tests, previews)."""
    ids = {i.key: i.key for i in plan.instances}
    parts = {p.key: _safe_pid(p.key) for p in plan.parts}
    ins, js, lps = to_models(plan, ids, parts, root_transform(at))
    return Assembly(instances=ins, joints=js, loops=lps)


def _safe_pid(key: str) -> str:
    s = re.sub(r"[^a-z0-9_]", "_", key.lower())
    if not s or not s[0].isalpha():
        s = "p_" + s
    return s[:40] if len(s) >= 2 else s + "_p"


def numeric_mobility(plan: MechPlan) -> tuple[int, str]:
    """Mobility from the actual constraints: the tree joints' freedoms, minus one per coupling,
    minus the rank of the loop-closure Jacobian at the plan's pose. Exact where Grubler /
    Kutzbach miscount (planar or spherical loops inside a spatial chain)."""
    import numpy as np

    from ..assembly.solve import _NCOMP, _loop_residual, _Tree, apply_couplings
    asm = plan_assembly(plan)
    tree = _Tree(asm, None)
    coupled = {j.id for j in tree.order}
    slots = [(j, k) for j in tree.joints if j.id not in coupled
             for k in range(_NCOMP[j.kind])]
    free = len(slots)
    rank = 0
    if tree.loops and slots:
        base = {j.id: j.value for j in tree.joints}

        def res(dx):
            vals = dict(base)
            for (j, k), d in zip(slots, dx):
                v = vals[j.id]
                if isinstance(v, (list, tuple)):
                    v = list(v) + [0.0] * (_NCOMP[j.kind] - len(v))
                    v[k] = float(v[k]) + d
                    vals[j.id] = v
                else:
                    vals[j.id] = float(v) + d
            apply_couplings(tree.order, vals)
            tr, _a, _v = tree.place(vals)
            out: list[float] = []
            for lp in tree.loops:
                out += _loop_residual(tree, lp, tr)
            return np.array(out)
        h = 1e-4
        r0 = res(np.zeros(len(slots)))
        jac = np.zeros((len(r0), len(slots)))
        for c in range(len(slots)):
            e = np.zeros(len(slots))
            e[c] = h
            jac[:, c] = (res(e) - r0) / h
        sv = np.linalg.svd(jac, compute_uv=False)
        rank = int(np.sum(sv > 1e-5 * max(1.0, sv[0] if len(sv) else 1.0)))
    m = free - rank
    return m, (f"numeric: {free} tree freedoms" + (f" - rank {rank} of the loop closure"
                                                  if tree.loops else "") + f" = {m}")


# ---- previews (tests and the contact sheet): geometry in this process

def preview_part(spec):
    """Build one PartSpec in this process (a catalog item through its own catalog script)."""
    if spec.catalog:
        from ..catalog.builtin import builtin_by_id
        from ..catalog.geometry import script_for
        item = builtin_by_id()[spec.catalog]
        src, vars_ = script_for(item)
        import build123d
        ns: dict = {"math": math, "build123d": build123d, "bd": build123d}
        exec("from build123d import *", ns)  # noqa: S102 - our own catalog script
        ns["math"] = math
        exec(compile(src, f"<catalog {spec.catalog}>", "exec"), ns)  # noqa: S102
        v = dict(vars_)
        from .shapes import _flat
        return _flat(ns["build"](v))
    from . import shapes
    fn = getattr(shapes, spec.recipe)
    return fn(**json.loads(json.dumps(spec.kwargs)))


def preview_shapes(plan: MechPlan, at: dict | None = None, values: dict | None = None):
    """{instance key: build123d shape placed in the world} for the plan's solved pose (joint
    values overridden by `values`, keyed by joint key)."""
    import build123d as bd
    asm = plan_assembly(plan, at)
    if values:
        jid = joint_ids(plan, {i.key: i.key for i in plan.instances})
        asm = asm.model_copy(update={"joints": [
            j.model_copy(update={"value": values[k]}) if (k := next(
                (kk for kk, vv in jid.items() if vv == j.id), None)) in values else j
            for j in asm.joints]})
    res = solve(asm)
    built = {}
    out = {}
    for i in plan.instances:
        if i.part not in built:
            built[i.part] = preview_part(plan.part(i.part))
        m = res.transforms[i.key]
        from OCP.gp import gp_Trsf
        t = gp_Trsf()
        t.SetValues(m[0][0], m[0][1], m[0][2], m[0][3], m[1][0], m[1][1], m[1][2], m[1][3],
                    m[2][0], m[2][1], m[2][2], m[2][3])
        out[i.key] = built[i.part].moved(bd.Location(t))
    return out, res
