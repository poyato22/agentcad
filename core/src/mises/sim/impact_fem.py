"""The CalculiX tier of the impact study (see impact.py): the stack as 3D hex blocks dropped on
a rigid floor, *DYNAMIC (implicit HHT, NLGEOM) with penalty surface-to-surface contact.

Each part is a square block (its footprint area x its thickness, its mass spread through it);
crushable cushions are J2 elastic-plastic fitted to the crush curve (crush_hardening: they
dissipate), recovering cushions Ramberg-Osgood *DEFORMATION PLASTICITY (nonlinear elastic
with a knee at the plateau), rubbery solids (TPU, silicone: hyperelastic=True) compressible
Neo-Hooke *HYPERELASTIC (neo_hooke_params), other solids *ELASTIC. (*HYPERFOAM was tried first: with CalculiX 2.23 a hyperfoam block lost its contact
with the floor on the first increments and fell through, so it is not used.) CalculiX runs
sandboxed (proc.run_sandboxed via ccx.run_ccx) with a time limit; the mesh is capped by
FEM_MAX_ELEMENTS. Units in the deck: mm, t, s, N, MPa.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from .impact import G, Cushion, Layer

FEM_MAX_ELEMENTS = {"coarse": 600, "fine": 2500}
FEM_IN_PLANE = {"coarse": 3, "fine": 5}


NU_CRUSH = 0.05     # crushable cushions: elastic Poisson ratio (foams barely bulge)
NU_RUBBERY = 0.3    # elastic (rubbery) cushions in the Neo-Hooke model
RO_EXPONENT = 12.0  # recovering foams: Ramberg-Osgood exponent (a sharp knee at the plateau)
RO_ALPHA = 1.0


def crush_hardening(c: Cushion, points: int = 14) -> list[tuple[float, float]]:
    """(true stress MPa, true plastic strain) for *PLASTIC that reproduces the cushion's
    NOMINAL crush curve in a free-sided uniaxial crush.

    CalculiX has no crushable-foam plasticity, so a crushable cushion is J2 elastic-plastic.
    J2 flow is isochoric: the block bulges (area A0 / (1 - eps)), so the true stress table is
    the nominal curve times (1 - eps) at plastic strain -ln(1 - eps). The force-stroke curve
    and the dissipated energy match the cushion in a straight crush; the bulge (a real foam
    hardly bulges) and the multiaxial response do not."""
    ey = c.plateau_mpa / c.e_mpa
    out = [(c.plateau_mpa, 0.0)]
    for k in range(1, points + 1):
        eps = ey + (0.93 - ey) * k / points
        sig = c.stress(eps) * (1.0 - eps)
        ep = -math.log(1.0 - eps) - sig / c.e_mpa
        if ep > out[-1][1] + 1e-6:
            out.append((sig, ep))   # softening in true stress is the flat nominal plateau
    return out


def neo_hooke_params(e_mpa: float, nu: float = NU_RUBBERY) -> tuple[float, float]:
    """(C10, D1) of a compressible Neo-Hooke solid with small-strain E and nu:
    C10 = G / 2 = E / (4 (1 + nu)), D1 = 2 / K = 6 (1 - 2 nu) / E."""
    return e_mpa / (4.0 * (1.0 + nu)), 6.0 * (1.0 - 2.0 * nu) / e_mpa


@dataclass
class Block:
    name: str
    nodes: dict          # id -> (x, y, z) mm
    elems: dict          # id -> 8 node ids
    bottom: list         # element ids on the bottom face
    top: list            # element ids on the top face
    side_mm: float
    z0: float
    z1: float
    nz: int


def _block(name: str, side: float, z0: float, z1: float, n: int, nz: int,
           nid0: int, eid0: int) -> Block:
    nodes, elems, ids = {}, {}, {}
    nid = nid0
    for k in range(nz + 1):
        for j in range(n + 1):
            for i in range(n + 1):
                nid += 1
                ids[(i, j, k)] = nid
                nodes[nid] = (-side / 2 + side * i / n, -side / 2 + side * j / n,
                              z0 + (z1 - z0) * k / nz)
    eid = eid0
    bottom, top = [], []
    for k in range(nz):
        for j in range(n):
            for i in range(n):
                eid += 1
                elems[eid] = [ids[(i, j, k)], ids[(i + 1, j, k)], ids[(i + 1, j + 1, k)],
                              ids[(i, j + 1, k)], ids[(i, j, k + 1)], ids[(i + 1, j, k + 1)],
                              ids[(i + 1, j + 1, k + 1)], ids[(i, j + 1, k + 1)]]
                if k == 0:
                    bottom.append(eid)
                if k == nz - 1:
                    top.append(eid)
    return Block(name, nodes, elems, bottom, top, side, z0, z1, nz)


def _lines(ids, per: int = 12) -> str:
    ids = list(ids)
    return "\n".join(",".join(str(i) for i in ids[k:k + per]) for k in range(0, len(ids), per))


def write_impact_deck(layers: list[Layer], velocity_m_s: float, *, payload: str,
                      duration_s: float, quality: str = "coarse", g: float = G
                      ) -> tuple[str, list[Block]]:
    """The CalculiX *DYNAMIC deck of the stack (bottom first) landing on a rigid floor."""
    n = FEM_IN_PLANE.get(quality, 3)
    budget = FEM_MAX_ELEMENTS.get(quality, 600)
    per_block = max(n * n, budget // max(len(layers), 1))
    blocks: list[Block] = []
    z, nid, eid = 0.0, 0, 0
    for L in layers:
        side = math.sqrt(L.area_m2) * 1000.0
        t = L.thickness_m * 1000.0
        nz = int(max(1, min(per_block // (n * n), round(t / (side / n)) or 1, 8)))
        if L.cushion is not None:
            nz = max(nz, 3)
        b = _block(L.name, side, z, z + t, n, nz, nid, eid)
        nid, eid = max(b.nodes), max(b.elems)
        blocks.append(b)
        z += t
    w = 2.0 * max(b.side_mm for b in blocks)   # the rigid floor, top face at z = 0
    fl = {nid + 1: (-w, -w, -w / 4), nid + 2: (w, -w, -w / 4), nid + 3: (w, w, -w / 4),
          nid + 4: (-w, w, -w / 4), nid + 5: (-w, -w, 0.0), nid + 6: (w, -w, 0.0),
          nid + 7: (w, w, 0.0), nid + 8: (-w, w, 0.0)}
    ref, fe = nid + 9, eid + 1
    out = ["** Mises impact study: the stack on a rigid floor (mm, t, s, N, MPa)", "*NODE"]
    for b in blocks:
        out += [f"{k},{x:.6g},{y:.6g},{zz:.6g}" for k, (x, y, zz) in b.nodes.items()]
    out += [f"{k},{x:.6g},{y:.6g},{zz:.6g}" for k, (x, y, zz) in fl.items()]
    out.append(f"{ref},0,0,{-w / 8:.6g}")
    for i, b in enumerate(blocks):
        out.append(f"*ELEMENT,TYPE=C3D8,ELSET=B{i}")
        out += [f"{e}," + ",".join(map(str, c)) for e, c in b.elems.items()]
    out.append(f"*ELEMENT,TYPE=C3D8,ELSET=FLOOR\n{fe}," + ",".join(str(k) for k in fl))
    for i, b in enumerate(blocks):
        out.append(f"*NSET,NSET=N{i}\n{_lines(b.nodes)}")
        out.append(f"*ELSET,ELSET=B{i}BOT\n{_lines(b.bottom)}")
        out.append(f"*ELSET,ELSET=B{i}TOP\n{_lines(b.top)}")
        out.append(f"*SURFACE,NAME=S{i}BOT,TYPE=ELEMENT\nB{i}BOT,S1")
        out.append(f"*SURFACE,NAME=S{i}TOP,TYPE=ELEMENT\nB{i}TOP,S2")
    out.append(f"*ELSET,ELSET=FLOORT\n{fe}\n*SURFACE,NAME=SFLOOR,TYPE=ELEMENT\nFLOORT,S2")
    out.append(f"*NSET,NSET=FREF\n{ref}")
    out.append("*ELSET,ELSET=BODY\n" + ",\n".join(f"B{i}" for i in range(len(blocks))))
    out.append("*NSET,NSET=BODYN\n" + ",\n".join(f"N{i}" for i in range(len(blocks))))
    pi = next(i for i, L in enumerate(layers) if L.name == payload)
    out.append(f"*NSET,NSET=PAYN\nN{pi}")
    for i, (L, b) in enumerate(zip(layers, blocks)):
        vol = b.side_mm ** 2 * (b.z1 - b.z0)
        rho = max(L.mass_kg, 1e-7) * 1e-3 / vol            # t / mm3
        out.append(f"*MATERIAL,NAME=M{i}")
        if L.cushion is not None:
            if L.cushion.crushable:
                out.append(f"*ELASTIC\n{L.cushion.e_mpa:.6g},{NU_CRUSH}\n*PLASTIC")
                out += [f"{sg:.6g},{ep:.6g}" for sg, ep in crush_hardening(L.cushion)]
            else:
                # a recovering foam: Ramberg-Osgood *DEFORMATION PLASTICITY is nonlinear
                # ELASTIC (it unloads on its loading curve) with a knee at the plateau
                out.append(f"*DEFORMATION PLASTICITY\n{L.cushion.e_mpa:.6g},{NU_CRUSH},"
                           f"{L.cushion.plateau_mpa:.6g},{RO_EXPONENT},{RO_ALPHA}")
        elif L.hyperelastic:
            c10, d1 = neo_hooke_params(L.solid_e)
            out.append(f"*HYPERELASTIC,NEO HOOKE\n{c10:.6g},{d1:.6g}")
        else:
            out.append(f"*ELASTIC\n{L.solid_e:.6g},{L.nu:.4g}")
        out.append(f"*DENSITY\n{rho:.6g}")
        out.append(f"*SOLID SECTION,ELSET=B{i},MATERIAL=M{i}")
    out.append("*MATERIAL,NAME=RIGID\n*ELASTIC\n210000,0.3\n*DENSITY\n7.8e-9")
    out.append("*SOLID SECTION,ELSET=FLOOR,MATERIAL=RIGID")
    out.append(f"*RIGID BODY,ELSET=FLOOR,REF NODE={ref}")
    for i, (L, b) in enumerate(zip(layers, blocks)):
        # penalty stiffness (MPa / mm): a few times the softer side's element stiffness, so
        # the penetration stays a small part of the stroke
        e_here = L.cushion.tangent(0.6) if L.cushion is not None else L.solid_e
        if i > 0:
            lo = layers[i - 1]
            e_lo = lo.cushion.tangent(0.6) if lo.cushion is not None else lo.solid_e
            e_here = min(e_here, e_lo)
        h_el = (b.z1 - b.z0) / b.nz
        kpen = max(1.0, 5.0 * e_here / max(h_el, 0.1))
        out.append(f"*SURFACE INTERACTION,NAME=SI{i}\n"
                   f"*SURFACE BEHAVIOR,PRESSURE-OVERCLOSURE=LINEAR\n{kpen:.6g}")
        if i == 0:
            pair = "S0BOT,SFLOOR"
        else:
            lo_b = blocks[i - 1]
            pair = (f"S{i}BOT,S{i - 1}TOP" if b.side_mm <= lo_b.side_mm
                    else f"S{i - 1}TOP,S{i}BOT")
        out.append(f"*CONTACT PAIR,INTERACTION=SI{i},TYPE=SURFACE TO SURFACE\n{pair}")
    out.append("*BOUNDARY\nFREF,1,6")
    out.append(f"*INITIAL CONDITIONS,TYPE=VELOCITY\nBODYN,3,{-velocity_m_s * 1000.0:.8g}")
    dt0 = duration_s / 400.0
    out.append("*STEP,NLGEOM,INC=20000")
    out.append(f"*DYNAMIC\n{dt0:.6g},{duration_s:.6g},{dt0 * 1e-4:.6g},{duration_s / 200.0:.6g}")
    out.append(f"*DLOAD\nBODY,GRAV,{g * 1000.0:.8g},0.,0.,-1.")
    out.append("*NODE PRINT,NSET=PAYN,FREQUENCY=1\nV")
    out.append("*NODE PRINT,NSET=FREF,FREQUENCY=1,TOTALS=ONLY\nRF")
    for i in range(len(blocks)):
        out.append(f"*EL PRINT,ELSET=B{i},FREQUENCY=1,TOTALS=ONLY\nELSE")
        out.append(f"*EL PRINT,ELSET=B{i},FREQUENCY=2\nS")
    out.append("*END STEP")
    return "\n".join(out) + "\n", blocks


# ---------------------------------------------------------------- reading the .dat

_HDR = re.compile(r"^\s*(velocities|total force|total internal energy|stresses)\b.*?"
                  r"for set (\S+) and time\s+(\S+)")


def parse_dat(text: str) -> dict:
    """{'v': {t: [vz...]}, 'rf': {t: fz}, 'else': {set: {t: e}}, 'smax': {set: {t: (vm, elem)}}}
    from the *NODE PRINT / *EL PRINT blocks of a CalculiX .dat."""
    out: dict = {"v": {}, "rf": {}, "else": {}, "smax": {}}
    kind = setname = None
    t = 0.0
    for line in text.splitlines():
        m = _HDR.match(line)
        if m:
            kind, setname, t = m.group(1), m.group(2), float(m.group(3))
            continue
        p = line.split()
        if not p or kind is None:
            continue
        try:
            vals = [float(x) for x in p]
        except ValueError:
            kind = None
            continue
        if kind == "velocities" and len(vals) == 4:
            out["v"].setdefault(t, []).append(vals[3])
        elif kind == "total force" and len(vals) == 3:
            out["rf"][t] = vals[2]
        elif kind == "total internal energy" and len(vals) == 1:
            out["else"].setdefault(setname, {})[t] = vals[0]
        elif kind == "stresses" and len(vals) == 8:
            sx, sy, sz, sxy, sxz, syz = vals[2:]
            vm = math.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2)
                           + 3 * (sxy ** 2 + sxz ** 2 + syz ** 2))
            cur = out["smax"].setdefault(setname, {}).get(t)
            if cur is None or vm > cur[0]:
                out["smax"][setname][t] = (vm, int(vals[0]))
    return out


def _smooth(ts: list[float], ys: list[float], window_s: float) -> list[float]:
    """Centered moving average over window_s (a simple low-pass, like a CFC filter's role:
    implicit dynamics with contact rings at the element frequencies)."""
    out = []
    j0 = j1 = 0
    for i, t in enumerate(ts):
        while ts[j0] < t - window_s / 2:
            j0 += 1
        while j1 + 1 < len(ts) and ts[j1 + 1] <= t + window_s / 2:
            j1 += 1
        seg = ys[j0:j1 + 1]
        out.append(sum(seg) / len(seg))
    return out


def _where(block: Block, elem: int) -> str:
    """A plain-words location of an element in its block."""
    xs = [block.nodes[n] for n in block.elems[elem]]
    cz = sum(p[2] for p in xs) / 8
    cx, cy = sum(p[0] for p in xs) / 8, sum(p[1] for p in xs) / 8
    f = (cz - block.z0) / max(block.z1 - block.z0, 1e-9)
    level = "bottom (impact side)" if f < 0.34 else "top" if f > 0.66 else "middle"
    edge = max(abs(cx), abs(cy)) > block.side_mm / 4
    return f"{level}, {'edge' if edge else 'centre'} of {block.name}"


def post_process(dat: dict, layers: list[Layer], blocks: list[Block], payload: str,
                 velocity_m_s: float, g: float = G) -> dict:
    """The payload's accelerometer (g), peak stresses per part, rebound and energies."""
    ts = sorted(dat["v"])
    if len(ts) < 5:
        raise ValueError("CalculiX wrote too few increments to read an impact")
    vz = [sum(dat["v"][t]) / len(dat["v"][t]) / 1000.0 for t in ts]   # m/s, payload mean
    acc = [0.0]
    for i in range(1, len(ts)):
        acc.append((vz[i] - vz[i - 1]) / max(ts[i] - ts[i - 1], 1e-12))
    felt_raw = [(a + g) / G for a in acc]
    contact_ts = [t for t in ts if abs(dat["rf"].get(t, 0.0)) > 1e-6]
    dur = (contact_ts[-1] - contact_ts[0]) if len(contact_ts) > 1 else ts[-1]
    felt = _smooth(ts, felt_raw, max(dur / 25.0, 1e-6))
    k = max(range(len(felt)), key=lambda i: felt[i])
    names = [L.name for L in layers]
    energy, stress = {}, {}
    for i, L in enumerate(layers):
        e = dat["else"].get(f"B{i}", {})
        es = [e[t] for t in sorted(e)]
        energy[L.name] = {"peak_j": max(es) / 1000.0 if es else 0.0,
                          "end_j": es[-1] / 1000.0 if es else 0.0}
        sm = dat["smax"].get(f"B{i}", {})
        if sm:
            tpk, (vm, el) = max(sm.items(), key=lambda kv: kv[1][0])
            stress[L.name] = {"von_mises_mpa": vm, "time_s": tpk,
                              "where": _where(blocks[i], el), "element": el}
    floor_n = [abs(dat["rf"].get(t, 0.0)) for t in ts]
    rebound = max(vz[-1], 0.0) if floor_n[-1] < 1e-6 else 0.0
    step = max(1, len(ts) // 300)
    return {"peak_g": felt[k], "peak_g_time_s": ts[k], "peak_g_unfiltered": max(felt_raw),
            "filter": f"centered moving average over {max(dur / 25.0, 1e-6) * 1e3:.3g} ms",
            # the payload's upward speed at the end of the run: with recovering foams and no
            # material damping in CalculiX this is an upper bound on the bounce
            "payload_rebound_velocity_m_s": rebound,
            "payload_restitution": rebound / velocity_m_s if velocity_m_s else 0.0,
            "peak_floor_force_n": max(floor_n), "contact_time_s": dur,
            "end_time_s": ts[-1], "increments": len(ts),
            "energy_j": energy, "peak_stress": stress, "parts": names,
            "trace": {"t_s": ts[::step], "payload_g": felt[::step],
                      "floor_force_n": floor_n[::step]}}


async def run_fem_impact(layers: list[Layer], velocity_m_s: float, *, payload: str,
                         duration_s: float, job_dir: Path, ccx: str, quality: str = "coarse",
                         timeout_s: float = 240.0, threads: int = 2) -> dict:
    """Write the deck, run CalculiX sandboxed (time-limited), read the .dat back."""
    from .ccx import run_ccx
    job_dir.mkdir(parents=True, exist_ok=True)
    deck, blocks = write_impact_deck(layers, velocity_m_s, payload=payload,
                                     duration_s=duration_s, quality=quality)
    (job_dir / "impact.inp").write_text(deck)
    await run_ccx(ccx, job_dir, job="impact", timeout_s=timeout_s, threads=threads,
                  allow_partial=True)
    dat = job_dir / "impact.dat"
    if not dat.exists():
        raise RuntimeError("CalculiX wrote no results")
    res = post_process(parse_dat(dat.read_text(errors="replace")), layers, blocks, payload,
                       velocity_m_s)
    errs = job_dir / "ccx_errors.txt"
    res["solver_errors"] = [ln for ln in errs.read_text().splitlines() if ln.strip()] \
        if errs.exists() else []
    res["elements"] = sum(len(b.elems) for b in blocks)
    res["completed"] = res["end_time_s"] >= 0.98 * duration_s
    return res
