"""AgentCAD-Functional: 20 auto-checked engineering tasks (research note section 6).

Five categories x 4 tasks: fits/mates (F), mechanisms (K, swept), structural FEA (S,
CalculiX), manufacturability (P), edit regression (E, two messages). Each task is a prompt
(plus follow-ups for E) and a checker that reads the exported result (result.step and
components/<name>.step; for E also stage_0/, the export before the edit) and returns typed
requirements. Score = fraction of requirements passed (Hephaestus-CCX "mean requirement
pass"); strict = all passed. A checker never raises: a requirement whose measurement fails
is a failed requirement with the error as its detail.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import geom as G

NAMES_NOTE = "Make them separate parts with exactly these names: {names}."


@dataclass
class Req:
    name: str
    passed: bool
    detail: str = ""


class Checks:
    def __init__(self):
        self.items: list[Req] = []

    def add(self, name: str, fn: Callable[[], tuple[bool, str] | bool]) -> bool:
        try:
            r = fn()
            ok, detail = (r if isinstance(r, tuple) else (bool(r), ""))
        except Exception as e:  # noqa: BLE001 - a failed measurement fails the requirement
            ok, detail = False, f"{type(e).__name__}: {str(e)[:200]}"
        self.items.append(Req(name, bool(ok), str(detail)[:300]))
        return bool(ok)


@dataclass
class FTask:
    id: str
    category: str
    prompt: str
    check: Callable[[Path, Checks], None]
    followups: list[str] = field(default_factory=list)


def _near(v, target, tol) -> tuple[bool, str]:
    return abs(v - target) <= tol, f"{v:.4f} (want {target}±{tol})"


def _in(v, lo, hi) -> tuple[bool, str]:
    return lo <= v <= hi, f"{v:.4f} (want {lo}..{hi})"


def _dims_ok(shape, want, tol) -> tuple[bool, str]:
    d = G.dims_sorted(shape)
    w = sorted(want)
    return all(abs(a - b) <= tol for a, b in zip(d, w)), f"{[round(x, 3) for x in d]} want {w}±{tol}"


def _parts(out: Path, names: list[str]) -> tuple[dict, dict]:
    """Named components (by substring, first match, each used once); missing ones are
    filled by volume order from the unmatched solids."""
    whole, comps = G.load_result(out)
    got, used = {}, set()
    for n in names:
        for cname, s in comps.items():
            if cname in used:
                continue
            if n.lower() in cname.lower():
                got[n], _ = s, used.add(cname)
                break
    return got, {k: v for k, v in comps.items() if k not in used}


def _need(got: dict, names: list[str]) -> tuple[bool, str]:
    miss = [n for n in names if n not in got]
    return not miss, f"missing {miss}" if miss else "all present"


def _no_clash(a, b, tol=1e-2) -> tuple[bool, str]:
    v = G.common_volume(a, b)
    return v <= tol, f"overlap {v:.4f} mm3"


def _mass_g(shape, rho_g_cm3) -> float:
    return G.volume(shape) * rho_g_cm3 / 1000.0


def _single(out: Path):
    whole, comps = G.load_result(out)
    if whole is None:
        raise RuntimeError("no result.step")
    return whole


# ============================================================================ F: fits/mates
def chk_pin_hole(out: Path, c: Checks):
    got, _ = _parts(out, ["block", "pin"])
    if not c.add("parts block + pin", lambda: _need(got, ["block", "pin"])):
        return
    b, p = got["block"], got["pin"]
    c.add("block 40x40x20", lambda: _dims_ok(b, [40, 40, 20], 0.05))
    hole = lambda: max(G.holes(b, 4.5, 5.5), key=lambda h: h.length)  # noqa: E731
    pin = lambda: max(G.bosses(p, 4.5, 5.5), key=lambda h: h.area)  # noqa: E731
    c.add("hole H7 (10.000..10.015)", lambda: _in(2 * hole().radius, 10.0, 10.015))
    c.add("pin g6 (9.986..9.995)", lambda: _in(2 * pin().radius, 9.986, 9.995))
    c.add("coaxial", lambda: (hole().same_axis(pin(), 0.01), "pin axis on hole axis"))
    c.add("no interference", lambda: _no_clash(b, p, 1e-3))

    def protrude():
        h, a = hole(), hole().axis
        tb = [(np.array(v) - h.point) @ a for v in G.bbox(b)]
        tp = [(np.array(v) - h.point) @ a for v in G.bbox(p)]
        lo_b, hi_b = min(tb), max(tb)
        lo_p, hi_p = min(tp), max(tp)
        e1, e2 = lo_b - lo_p, hi_p - hi_b
        return (4.5 <= e1 <= 5.5 and 4.5 <= e2 <= 5.5), f"protrudes {e1:.2f} / {e2:.2f} mm"
    c.add("pin through, 5 mm each side", protrude)


def chk_flanges(out: Path, c: Checks):
    got, _ = _parts(out, ["flange_a", "flange_b"])
    if not c.add("parts flange_a + flange_b", lambda: _need(got, ["flange_a", "flange_b"])):
        return
    fa, fb = got["flange_a"], got["flange_b"]
    for n, f in (("a", fa), ("b", fb)):
        c.add(f"{n}: OD 120, 12 thick", lambda f=f: _dims_ok(f, [120, 120, 12], 0.2))
        c.add(f"{n}: bore 50", lambda f=f: (len(G.holes(f, 24.9, 25.1)) == 1, "one Ø50 bore"))

        def bolt(f=f):
            hs = G.holes(f, 4.45, 4.55)
            bore = G.holes(f, 24.9, 25.1)[0]
            if len(hs) != 6:
                return False, f"{len(hs)} Ø9 holes"
            r = [np.linalg.norm((h.point - bore.point) - ((h.point - bore.point) @ bore.axis)
                                * bore.axis) for h in hs]
            ang = sorted(math.degrees(math.atan2(*(_inplane(h.point - bore.point, bore.axis))))
                         % 360 for h in hs)
            gaps = [(ang[(i + 1) % 6] - ang[i]) % 360 for i in range(6)]
            ok = all(abs(x - 45) <= 0.1 for x in r) and all(abs(g - 60) <= 0.5 for g in gaps)
            return ok, f"PCD {2 * np.mean(r):.3f}, gaps {[round(g, 1) for g in gaps]}"
        c.add(f"{n}: 6 x Ø9 on PCD 90, 60° apart", bolt)
    c.add("face to face (gap ≤ 0.05)", lambda: (G.min_distance(fa, fb) <= 0.05,
                                                 f"gap {G.min_distance(fa, fb):.4f}"))
    c.add("no interference", lambda: _no_clash(fa, fb, 1e-2))

    def aligned():
        ha, hb = G.holes(fa, 4.45, 4.55), G.holes(fb, 4.45, 4.55)
        n = sum(any(x.same_axis(y, 0.05) for y in hb) for x in ha)
        return n == 6 and len(ha) == 6, f"{n}/6 holes aligned"
    c.add("bolt holes aligned", aligned)


def _inplane(v, axis):
    ref = np.cross(axis, [1, 0, 0]) if abs(axis[0]) < 0.9 else np.cross(axis, [0, 1, 0])
    u = ref / np.linalg.norm(ref)
    w = np.cross(axis, u)
    return float(v @ u), float(v @ w)


def chk_box_lid(out: Path, c: Checks):
    got, _ = _parts(out, ["box", "lid"])
    if not c.add("parts box + lid", lambda: _need(got, ["box", "lid"])):
        return
    box, lid = got["box"], got["lid"]
    c.add("box 60x40x30", lambda: _dims_ok(box, [60, 40, 30], 0.05))
    c.add("box 2 mm walls + floor (volume)", lambda: _near(G.volume(box), 15552, 155))
    c.add("lid 60x40, 6 mm total height", lambda: _dims_ok(lid, [60, 40, 6], 0.05))

    def lip_region():
        import build123d as b3
        lo, hi = G.bbox(lid)
        slab = b3.Pos((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2] + 1.95) * \
            b3.Box(200, 200, 3.9)
        op = lid.intersect(slab)
        return op[0] if isinstance(op, list) else op

    def lip_dims():
        r = lip_region()
        lo, hi = G.bbox(r)
        d = sorted((hi - lo)[:2].tolist())
        return (abs(d[0] - 35.6) <= 0.05 and abs(d[1] - 55.6) <= 0.05), f"lip {d} want [35.6, 55.6]"
    c.add("lip 0.2 mm clearance (55.6 x 35.6)", lip_dims)
    c.add("lip wall 1.2 mm (volume)", lambda: _near(G.volume(lip_region()) * 4 / 3.9, 852.5, 26))
    c.add("lid seated (contact, no interference)",
          lambda: (G.min_distance(lid, box) <= 0.05 and G.common_volume(lid, box) <= 1e-2,
                   f"gap {G.min_distance(lid, box):.3f}"))

    def centred():
        lb, hb = G.bbox(box)
        ll, hl = G.bbox(lid)
        d = np.abs(((lb + hb) - (ll + hl)) / 2)[:2]
        return bool(np.all(d <= 0.05)), f"xy offset {d.round(3).tolist()}"
    c.add("lid centred on box", centred)


def chk_shaft_key(out: Path, c: Checks):
    got, _ = _parts(out, ["shaft", "key"])
    if not c.add("parts shaft + key", lambda: _need(got, ["shaft", "key"])):
        return
    sh, key = got["shaft"], got["key"]
    ax = lambda: max(G.bosses(sh, 9.9, 10.1), key=lambda h: h.area)  # noqa: E731
    c.add("shaft Ø20", lambda: _near(2 * ax().radius, 20, 0.02))

    def length():
        a = ax()
        ts = [(np.array(v) - a.point) @ a.axis for v in G.bbox(sh)]
        return _near(abs(ts[1] - ts[0]), 80, 0.05)
    c.add("shaft 80 long", length)

    def planes():
        import build123d as b3
        a = ax()
        out_ = []
        for f in sh.faces():
            if f.geom_type != b3.GeomType.PLANE:
                continue
            n = np.array(tuple(f.normal_at(f.center())))
            if abs(n @ a.axis) > 1e-3:
                continue
            p = np.array(tuple(f.center()))
            out_.append((n, abs((p - a.point) @ n)))
        return out_
    def keyway():
        ps = planes()
        sides = [(n, d) for n, d in ps if any(abs(n @ m + 1) < 1e-3 for m, _ in ps)]
        floor = [(n, d) for n, d in ps if not any(abs(n @ m + 1) < 1e-3 for m, _ in ps)]
        return sides, floor
    c.add("keyway depth 3.5 (floor 6.5 from axis)",
          lambda: _near(min(d for _, d in keyway()[1]), 6.5, 0.05))

    def width():
        sides = keyway()[0]
        if len(sides) < 2:
            return False, f"{len(sides)} side walls"
        n0 = sides[0][0]
        a = ax()
        offs = sorted({round(float((np.array(tuple(f.center())) - a.point) @ n0), 4)
                       for f in sh.faces() if f.geom_type.name == "PLANE"
                       and abs(abs(np.array(tuple(f.normal_at(f.center()))) @ n0) - 1) < 1e-3})
        return _near(offs[-1] - offs[0], 6, 0.03)
    c.add("keyway width 6", width)
    c.add("keyway volume (form A, ~578 mm3)",
          lambda: _near(math.pi * 100 * 80 - G.volume(sh), 578, 30))
    c.add("key 6x6x30", lambda: _dims_ok(key, [6, 6, 30], 0.05))
    c.add("key seated (contact, no interference)",
          lambda: (G.min_distance(key, sh) <= 0.05 and G.common_volume(key, sh) <= 1e-2,
                   f"gap {G.min_distance(key, sh):.3f}, overlap {G.common_volume(key, sh):.4f}"))

    def proud():
        a = ax()
        v, _ = G.tessellate(key, 0.02)
        r = np.linalg.norm((v - a.point) - np.outer((v - a.point) @ a.axis, a.axis), axis=1)
        return _near(r.max() - 10, 2.5 + (math.hypot(12.5, 3) - 12.5), 0.12)
    c.add("key stands 2.5 mm proud", proud)


# ============================================================================ K: mechanisms
def _two_holes(link, r=3.0):
    hs = G.holes(link, r - 0.05, r + 0.05)
    return hs


def chk_four_bar(out: Path, c: Checks):
    names = ["ground", "crank", "coupler", "rocker"]
    got, _ = _parts(out, names)
    if not c.add("parts ground/crank/coupler/rocker", lambda: _need(got, names)):
        return
    want = {"ground": 100, "crank": 40, "coupler": 120, "rocker": 80}
    H = {n: _two_holes(got[n]) for n in names}
    for n in names:
        def ln(n=n):
            hs = H[n]
            if len(hs) < 2:
                return False, f"{len(hs)} Ø6 holes"
            d = max(np.linalg.norm(_perp(x.point - y.point, x.axis)) for x in hs for y in hs)
            return _near(d, want[n], 0.1)
        c.add(f"{n} centre distance {want[n]}", ln)

    def joint(a, b):
        return any(x.same_axis(y, 0.1) for x in H[a] for y in H[b])
    pairs = [("ground", "crank"), ("crank", "coupler"), ("coupler", "rocker"), ("rocker", "ground")]
    c.add("loop closes (4 joints coaxial)",
          lambda: (all(joint(*p) for p in pairs), str([joint(*p) for p in pairs])))

    def grashof():
        L = sorted(want[n] for n in names)
        return L[0] + L[3] <= L[1] + L[2], "s+l <= p+q (crank-rocker)"
    c.add("crank can rotate fully (Grashof)", grashof)

    def pose():
        o2 = _shared(H["ground"], H["crank"])
        o4 = _shared(H["ground"], H["rocker"])
        a = _shared(H["crank"], H["coupler"])
        ax = H["ground"][0].axis
        g = _perp(o4 - o2, ax)
        k = _perp(a - o2, ax)
        ang = math.degrees(math.acos(np.clip(g @ k / np.linalg.norm(g) / np.linalg.norm(k), -1, 1)))
        return _near(ang, 60, 2)
    c.add("assembled at crank 60°", pose)

    def sweep():
        o2 = _shared(H["ground"], H["crank"])
        o4 = _shared(H["ground"], H["rocker"])
        a0 = _shared(H["crank"], H["coupler"])
        b0 = _shared(H["coupler"], H["rocker"])
        ax = H["ground"][0].axis
        worst, prev_b = 0.0, b0
        for th in np.arange(0, 360, 30):
            cr = G.rotate(got["crank"], o2, ax, th)
            a1 = _rot_pt(a0, o2, ax, th)
            b1 = _circle_int(a1, np.linalg.norm(_perp(b0 - a0, ax)), o4,
                             np.linalg.norm(_perp(b0 - o4, ax)), ax, prev_b)
            if b1 is None:
                return False, f"no closure at {th}°"
            phi_r = _angle(b0 - o4, b1 - o4, ax)
            ro = G.rotate(got["rocker"], o4, ax, phi_r)
            # coupler: rotate so a0->a1 and b0->b1, then translate
            phi_c = _angle(b0 - a0, b1 - a1, ax)
            co = G.translate(G.rotate(got["coupler"], a0, ax, phi_c), a1 - a0)
            parts = {"ground": got["ground"], "crank": cr, "coupler": co, "rocker": ro}
            ks = list(parts)
            for i in range(4):
                for j in range(i + 1, 4):
                    worst = max(worst, G.common_volume(parts[ks[i]], parts[ks[j]]))
            prev_b = b1
        return worst <= 0.5, f"worst overlap {worst:.3f} mm3 over 12 crank angles"
    c.add("full-rotation sweep without collision", sweep)


def _perp(v, axis):
    v = np.asarray(v, float)
    return v - np.outer(v @ axis, axis) if v.ndim == 2 else v - (v @ axis) * axis


def _shared(ha, hb):
    """A point on the axis two links share (their coaxial holes)."""
    for x in ha:
        for y in hb:
            if x.same_axis(y, 0.1):
                return x.point
    raise RuntimeError("no shared joint")


def _rot_pt(p, c, axis, deg):
    from scipy.spatial.transform import Rotation
    return c + Rotation.from_rotvec(np.asarray(axis) * math.radians(deg)).apply(p - c)


def _angle(u, v, axis):
    u, v = _perp(u, axis), _perp(v, axis)
    return math.degrees(math.atan2(np.cross(u, v) @ axis, u @ v))


def _circle_int(c1, r1, c2, r2, axis, near):
    """Intersection of two circles in the plane normal to axis through c2 (c1 projected onto
    it); of the two points, the one nearer `near` (keeps the linkage on its branch)."""
    d = _perp(c2 - c1, axis)
    L = float(np.linalg.norm(d))
    if L == 0 or L > r1 + r2 or L < abs(r1 - r2):
        return None
    a = (r1 ** 2 - r2 ** 2 + L ** 2) / (2 * L)
    h = math.sqrt(max(r1 ** 2 - a ** 2, 0))
    e = d / L
    base = (c2 - d) + a * e                   # c1 projected into c2's plane, then along e
    perp = np.cross(axis, e)
    cands = [base + h * perp, base - h * perp]
    return min(cands, key=lambda p: np.linalg.norm(_perp(p - near, axis)))


def _gear_axis(g, bore_r):
    hs = G.holes(g, bore_r - 0.06, bore_r + 0.06)
    if not hs:
        raise RuntimeError(f"no Ø{2 * bore_r} bore")
    return max(hs, key=lambda h: h.area)


def _tip_d(g, ax):
    v, _ = G.tessellate(g, 0.01)
    return 2 * float(np.linalg.norm(_perp(v - ax.point, ax.axis), axis=1).max())


def chk_spur(out: Path, c: Checks):
    whole, comps = G.load_result(out)
    pin = G.find(comps, "pinion")
    gear = next((s for n, s in comps.items() if "gear" in n.lower() and "pinion" not in n.lower()),
                None)
    if not c.add("parts pinion + gear", lambda: (pin is not None and gear is not None, "")):
        return
    ap, ag = (lambda: _gear_axis(pin, 4)), (lambda: _gear_axis(gear, 4))
    c.add("bores Ø8", lambda: (ap() is not None and ag() is not None, "Ø8 bores"))
    c.add("pinion tip Ø44", lambda: _near(_tip_d(pin, ap()), 44, 0.3))
    c.add("gear tip Ø84", lambda: _near(_tip_d(gear, ag()), 84, 0.3))
    c.add("pinion 20 teeth", lambda: _near(G.count_teeth(pin, ap(), 20.0), 20, 0))
    c.add("gear 40 teeth", lambda: _near(G.count_teeth(gear, ag(), 40.0), 40, 0))

    def cd():
        a, b = ap(), ag()
        par = abs(abs(a.axis @ b.axis) - 1) < 1e-4
        return (par and abs(np.linalg.norm(_perp(b.point - a.point, a.axis)) - 60) <= 0.05,
                f"centre distance {np.linalg.norm(_perp(b.point - a.point, a.axis)):.3f}")
    c.add("centre distance 60, axes parallel", cd)
    c.add("no interference as assembled", lambda: _no_clash(pin, gear, 0.5))

    def sweep():
        a, b = ap(), ag()
        axis = a.axis
        worst, gap = 0.0, 0.0
        for d in np.linspace(0, 18, 7):
            p2 = G.rotate(pin, a.point, axis, d)
            g2 = G.rotate(gear, b.point, axis, -d / 2)
            worst = max(worst, G.common_volume(p2, g2))
            gap = max(gap, G.min_distance(p2, g2))
        return worst <= 0.5 and gap <= 0.3, f"worst overlap {worst:.3f} mm3, max gap {gap:.3f}"
    c.add("meshes through one tooth pitch at ratio 2:1", sweep)


def chk_rack(out: Path, c: Checks):
    whole, comps = G.load_result(out)
    pin, rack = G.find(comps, "pinion"), G.find(comps, "rack")
    if not c.add("parts pinion + rack", lambda: (pin is not None and rack is not None, "")):
        return
    ap = lambda: _gear_axis(pin, 4)  # noqa: E731
    c.add("pinion tip Ø30", lambda: _near(_tip_d(pin, ap()), 30, 0.3))
    c.add("pinion 18 teeth", lambda: _near(G.count_teeth(pin, ap(), 13.5), 18, 0))

    def rack_frame():
        lo, hi = G.bbox(rack)
        ext = hi - lo
        ax = ap().axis
        # rack length direction: longest bbox extent; height direction: toward the pinion
        i_len = int(np.argmax(ext))
        L = np.eye(3)[i_len]
        hdir = np.cross(ax, L)
        hdir /= np.linalg.norm(hdir)
        centre = (lo + hi) / 2
        if (ap().point - centre) @ hdir < 0:
            hdir = -hdir
        v, _ = G.tessellate(rack, 0.01)
        tip = float(((v - ap().point) @ hdir).max())   # signed: tip line relative to axis
        return L, hdir, ext[i_len], tip, lo, hi
    c.add("rack 150 long", lambda: _near(rack_frame()[2], 150, 0.5))
    c.add("rack tip line 12.0 from pinion axis (pitch line tangent)",
          lambda: _near(-rack_frame()[3], 12.0, 0.1))

    def pitch():
        L, hdir, length, tip, lo, hi = rack_frame()
        a = ap()
        y = a.point + hdir * (tip - 0.75)                 # mid-addendum line
        t0 = float((lo - a.point) @ L)
        t1 = float((hi - a.point) @ L)
        ts = np.linspace(min(t0, t1) + 0.01, max(t0, t1) - 0.01, 3000)
        lo_a, hi_a = G.bbox(rack)
        mid = ((lo_a + hi_a) / 2) @ a.axis
        pts = [y + L * t + a.axis * (mid - y @ a.axis) for t in ts]
        occ = [G.inside(rack, p) for p in pts]
        starts = [ts[i] for i in range(1, len(occ)) if occ[i] and not occ[i - 1]]
        if len(starts) < 3:
            return False, f"{len(starts)} teeth found"
        p = float(np.mean(np.diff(starts)))
        return _near(p, math.pi * 1.5, 0.05)
    c.add("rack pitch π·m = 4.712", pitch)
    c.add("no interference as assembled", lambda: _no_clash(pin, rack, 0.5))

    def sweep():
        L, hdir, *_ = rack_frame()
        a = ap()
        best = None
        for sign in (1, -1):
            worst = 0.0
            for s in np.linspace(0, math.pi * 1.5, 6):
                p2 = G.rotate(pin, a.point, a.axis, sign * math.degrees(s / 13.5))
                worst = max(worst, G.common_volume(p2, G.translate(rack, L * s)))
            best = worst if best is None else min(best, worst)
        return best <= 0.5, f"worst overlap {best:.3f} mm3 over one pitch"
    c.add("meshes through one pitch (rack travel = pinion pitch arc)", sweep)


def chk_hinge(out: Path, c: Checks):
    names = ["leaf_a", "leaf_b", "pin"]
    got, _ = _parts(out, names)
    if not c.add("parts leaf_a/leaf_b/pin", lambda: _need(got, names)):
        return
    la, lb, pn = got["leaf_a"], got["leaf_b"], got["pin"]
    pax = lambda: max(G.bosses(pn, 1.95, 2.05), key=lambda h: h.area)  # noqa: E731
    c.add("pin Ø4", lambda: _near(2 * pax().radius, 4, 0.02))
    c.add("pin 50 long", lambda: _near(max(G.dims_sorted(pn)), 50, 0.5))
    for n, l in (("leaf_a", la), ("leaf_b", lb)):
        c.add(f"{n} knuckle bore Ø4.2 on pin axis",
              lambda l=l: (any(h.same_axis(pax(), 0.05) for h in G.holes(l, 2.05, 2.15)),
                           f"{len(G.holes(l, 2.05, 2.15))} Ø4.2 bores"))
    c.add("no interference as assembled",
          lambda: (max(G.common_volume(la, lb), G.common_volume(la, pn),
                       G.common_volume(lb, pn)) <= 0.05, "pairwise overlap"))

    def opening():
        a = pax()
        for sign in (1, -1):
            free = all(G.common_volume(la, G.rotate(lb, a.point, a.axis, sign * d)) <= 1.0
                       for d in range(10, 91, 10))
            if free:
                blocked = G.common_volume(la, G.rotate(lb, a.point, a.axis, sign * 110)) > 1.0
                return True, sign, blocked
        return False, 0, False
    c.add("opens freely 0..90°", lambda: (opening()[0], ""))
    c.add("stop blocks it by 110°", lambda: (opening()[0] and opening()[2], ""))


# ============================================================================ S: structural
def _fea(out: Path, **kw):
    from . import fea
    return fea.solve(out / "result.step", out / "fea", **kw)


def _struct_common(out, c, rho):
    whole = _single(out)
    from ..geometry import validity
    c.add("valid single solid", lambda: (validity(out / "result.step")[0].valid
                                         and len(whole.solids()) == 1,
                                         f"{len(whole.solids())} solids"))
    return whole


def chk_cantilever(out: Path, c: Checks):
    from . import fea
    w = _struct_common(out, c, 2.70)
    lo, hi = G.bbox(w)
    c.add("fixed face on x=0, free end x=150",
          lambda: (abs(lo[0]) <= 0.5 and abs(hi[0] - 150) <= 0.5, f"x {lo[0]:.2f}..{hi[0]:.2f}"))
    c.add("mass ≤ 150 g", lambda: (_mass_g(w, 2.70) <= 150, f"{_mass_g(w, 2.70):.1f} g"))
    res = {}

    def run():
        if not res:
            res["r"] = _fea(out, E=68900, nu=0.33,
                            fixed=lambda x: fea.plane_mask(x, 0, lo[0], 1e-3 + 1e-4 * 150),
                            loads=lambda x, free: fea.split_force(
                                fea.plane_mask(x, 0, hi[0], 1e-3 + 1e-4 * 150) & free, [0, 0, -500]))
        return res["r"]
    c.add("von Mises ≤ 138 MPa (FEA p99.5)",
          lambda: (run().vm_p995 <= 138, f"p99.5 {run().vm_p995:.1f}, max {run().vm_max:.1f} MPa"))
    c.add("tip deflection ≤ 1.0 mm (FEA)", lambda: (run().u_max <= 1.0, f"{run().u_max:.3f} mm"))


def chk_shelf(out: Path, c: Checks):
    from . import fea
    w = _struct_common(out, c, 7.85)
    lo, hi = G.bbox(w)
    c.add("wall face x=0, arm to x=200, top face z=0",
          lambda: (abs(lo[0]) <= 0.5 and abs(hi[0] - 200) <= 1 and abs(hi[2]) <= 0.5,
                   f"x {lo[0]:.1f}..{hi[0]:.1f}, ztop {hi[2]:.2f}"))
    c.add("mass ≤ 400 g", lambda: (_mass_g(w, 7.85) <= 400, f"{_mass_g(w, 7.85):.1f} g"))
    res = {}

    def run():
        if not res:
            res["r"] = _fea(out, E=210000, nu=0.3,
                            fixed=lambda x: fea.plane_mask(x, 0, lo[0], 0.02),
                            loads=lambda x, free: fea.split_force(
                                fea.plane_mask(x, 2, hi[2], 0.02) & free & (x[:, 0] > lo[0] + 1),
                                [0, 0, -1000]))
            from . import fea as _f
            m = _f.mesh_step(out / "result.step")
            top = m.nodes[np.abs(m.nodes[:, 2] - hi[2]) <= 0.02]
            res["w"] = float(np.ptp(top[:, 1])) if len(top) else 0.0
        return res["r"]
    c.add("top face ≥ 20 mm wide", lambda: (run() is not None and res["w"] >= 20, f"{res.get('w', 0):.1f} mm"))
    c.add("von Mises ≤ 117 MPa (FEA p99.5)",
          lambda: (run().vm_p995 <= 117, f"p99.5 {run().vm_p995:.1f}, max {run().vm_max:.1f} MPa"))
    c.add("deflection ≤ 0.5 mm (FEA)", lambda: (run().u_max <= 0.5, f"{run().u_max:.3f} mm"))


def chk_link(out: Path, c: Checks):
    from . import fea
    w = _struct_common(out, c, 7.85)
    hs = lambda: sorted(G.holes(w, 4.95, 5.05), key=lambda h: h.point[0])  # noqa: E731

    def holes_ok():
        h = hs()
        if len(h) != 2:
            return False, f"{len(h)} Ø10 holes"
        d = np.linalg.norm(_perp(h[1].point - h[0].point, h[0].axis))
        z = abs(abs(h[0].axis[2]) - 1) < 1e-4
        at0 = np.linalg.norm(_perp(h[0].point, h[0].axis)) <= 0.05
        return (abs(d - 100) <= 0.05 and z and at0), f"centres {d:.3f} apart, axis Z {z}, first at origin {at0}"
    c.add("two Ø10 holes 100 apart (axes Z, first at origin)", holes_ok)
    c.add("thickness ≤ 10 mm", lambda: (np.ptp([G.bbox(w)[0][2], G.bbox(w)[1][2]]) <= 10.01,
                                        f"{G.bbox(w)[1][2] - G.bbox(w)[0][2]:.2f}"))
    c.add("mass ≤ 250 g", lambda: (_mass_g(w, 7.85) <= 250, f"{_mass_g(w, 7.85):.1f} g"))
    res = {}

    def run():
        if not res:
            h = hs()
            c1, c2 = h[0].point, h[1].point
            ax = np.array([0, 0, 1.0])

            def rad(x, cc):
                return np.linalg.norm(_perp(x - cc, ax), axis=1)
            dirv = _perp(c2 - c1, ax)
            dirv /= np.linalg.norm(dirv)
            res["r"] = fea.solve(
                out / "result.step", out / "fea", E=210000, nu=0.3,
                fixed=lambda x: (rad(x, c1) <= 5.05) & ((x - c1) @ dirv <= 0),
                loads=lambda x, free: fea.split_force(
                    (rad(x, c2) <= 5.05) & ((x - c2) @ dirv >= 0) & free, 12000 * dirv),
                exclude=lambda e: ((rad(e, c1) <= 7.5) & ((e - c1) @ dirv <= 0))
                | ((rad(e, c2) <= 7.5) & ((e - c2) @ dirv >= 0)))
        return res["r"]
    c.add("von Mises ≤ 178 MPa outside the pin bearing zones (FEA p99.5)",
          lambda: (run().vm_p995 <= 178, f"p99.5 {run().vm_p995:.1f}, max {run().vm_max:.1f} MPa"))


def chk_torsion(out: Path, c: Checks):
    from . import fea
    w = _struct_common(out, c, 7.85)
    lo, hi = G.bbox(w)
    od = lambda: 2 * max(b.radius for b in G.bosses(w, 1, 100))  # noqa: E731
    c.add("z 0..200 on the Z axis", lambda: (abs(lo[2]) <= 0.1 and abs(hi[2] - 200) <= 0.2
                                             and np.all(np.abs((lo + hi)[:2] / 2) <= 0.05),
                                             f"z {lo[2]:.2f}..{hi[2]:.2f}"))
    c.add("OD ≤ 30", lambda: (od() <= 30.001, f"OD {od():.3f}"))
    c.add("mass ≤ 850 g", lambda: (_mass_g(w, 7.85) <= 850, f"{_mass_g(w, 7.85):.1f} g"))
    res = {}

    def run():
        if not res:
            res["r"] = _fea(out, E=210000, nu=0.3,
                            fixed=lambda x: fea.plane_mask(x, 2, lo[2], 0.02),
                            loads=lambda x, free: fea.torque_forces(
                                x, fea.plane_mask(x, 2, hi[2], 0.02) & free, [0, 0, 0],
                                [0, 0, 1], 250_000))
        return res["r"]
    c.add("von Mises ≤ 178 MPa (FEA p99.5)",
          lambda: (run().vm_p995 <= 178, f"p99.5 {run().vm_p995:.1f}, max {run().vm_max:.1f} MPa"))
    def twist():
        r = run()
        end = np.abs(r.coords[:, 2] - hi[2]) <= 0.02
        rv = r.coords[end] * np.array([1, 1, 0])
        rr = np.linalg.norm(rv, axis=1)
        tang = np.cross([0, 0, 1], rv) / np.maximum(rr, 1e-9)[:, None] ** 2
        ok = rr > 0.5 * rr.max()                       # outer half of the end face
        th = math.degrees(float(np.mean(np.sum(r.disp[end] * tang, axis=1)[ok])))
        return th <= 0.6, f"{th:.3f}° (mean rotation of the loaded end)"
    c.add("twist ≤ 0.6° (FEA)", twist)


# ============================================================================ P: manufacturing
def chk_cnc(out: Path, c: Checks):
    w = _single(out)
    c.add("plate 100x60x12", lambda: _dims_ok(w, [100, 60, 12], 0.05))
    lo, hi = G.bbox(w)
    c.add("bottom on z=0", lambda: (abs(lo[2]) <= 0.01 and np.ptp([lo[2], hi[2]]) > 11, f"zmin {lo[2]:.3f}"))

    def corner_holes():
        hs = [h for h in G.holes(w, 3.25, 3.35) if abs(abs(h.axis[2]) - 1) < 1e-4]
        if len(hs) != 4:
            return False, f"{len(hs)} Ø6.6 holes"
        off = sorted(min(h.point[0] - lo[0], hi[0] - h.point[0]) for h in hs) + \
            sorted(min(h.point[1] - lo[1], hi[1] - h.point[1]) for h in hs)
        return all(abs(o - 8) <= 0.05 for o in off), f"edge offsets {np.round(off, 3).tolist()}"
    c.add("4 x Ø6.6 holes 8 mm from edges", corner_holes)
    c.add("pocket 50x30x6, R3 corners (volume)",
          lambda: _near(G.volume(w), 72000 - 4 * math.pi * 3.3 ** 2 * 12 - (9000 - (4 - math.pi) * 9 * 6), 300))

    def radii():
        vert = [cc for cc in G.cylinders(w) if cc.concave and abs(abs(cc.axis[2]) - 1) < 1e-3]
        through = [cc for cc in vert if abs(cc.radius - 3.3) <= 0.05 and cc.length >= 11.9]
        corners = [cc for cc in vert if not any(cc is t for t in through)]
        small = [cc for cc in corners if cc.radius < 2.99]
        return (not small and len(corners) >= 4), \
            f"{len(corners)} pocket corner arcs, radii {sorted({round(x.radius, 2) for x in corners})}"
    c.add("pocket corner radii ≥ 3 mm", radii)

    def top_access():
        runs = G.column_crossings(w, 2, 60)
        return bool(runs.max() <= 1), f"max {int(runs.max())} material runs per Z column"
    c.add("no undercuts from +Z (3-axis, one setup)", top_access)


def chk_fdm(out: Path, c: Checks):
    w = _single(out)
    lo, hi = G.bbox(w)
    cen, nor, area = G.facets(w, 0.05)
    c.add("valid single solid", lambda: (len(w.solids()) == 1 and w.is_valid, ""))
    c.add("footprint ≤ 100 x 100", lambda: ((hi - lo)[0] <= 100.01 and (hi - lo)[1] <= 100.01,
                                            f"{(hi - lo)[:2].round(2).tolist()}"))
    c.add("height 80..120", lambda: _in(hi[2] - lo[2], 80, 120))
    plate = cen[:, 2] <= lo[2] + 0.05
    c.add("base contact ≥ 1500 mm2",
          lambda: (area[plate & (nor[:, 2] < -0.99)].sum() >= 1500,
                   f"{area[plate & (nor[:, 2] < -0.99)].sum():.0f} mm2"))
    above = cen[:, 2] > lo[2] + 0.2
    over = above & (nor[:, 2] < -math.cos(math.radians(45))) & (nor[:, 2] > -0.99)
    c.add("no >45° overhangs (≤1% of area)",
          lambda: (area[over].sum() <= 0.01 * area.sum(), f"{100 * area[over].sum() / area.sum():.2f}%"))
    bridge = above & (nor[:, 2] <= -0.99)
    c.add("flat bridges ≤ 3% of area",
          lambda: (area[bridge].sum() <= 0.03 * area.sum(), f"{100 * area[bridge].sum() / area.sum():.2f}%"))

    def wall():
        t = G.wall_thickness_samples(w, 400)
        p5 = float(np.percentile(t, 5))
        return p5 >= 1.52, f"5th pct wall {p5:.2f} mm"
    c.add("min wall 1.6 mm", wall)


def chk_sheet(out: Path, c: Checks):
    w = _single(out)
    c.add("40 x 30 x 50 envelope", lambda: _dims_ok(w, [30, 40, 50], 0.2))

    def bend():
        ins = [x for x in G.cylinders(w) if x.concave and abs(x.radius - 2) <= 0.05]
        outs = [x for x in G.cylinders(w) if not x.concave and abs(x.radius - 4) <= 0.05]
        return any(a.same_axis(b, 0.05) for a in ins for b in outs), \
            f"{len(ins)} R2 inside, {len(outs)} R4 outside"
    c.add("bend R2 inside / R4 outside, coaxial", bend)

    def thick():
        t = G.wall_thickness_samples(w, 400)
        frac = float(((t >= 1.95) & (t <= 2.05)).mean())
        return frac >= 0.85, f"{100 * frac:.0f}% of samples 2.00±0.05"
    c.add("constant 2 mm thickness", thick)
    c.add("sheet volume (developed length)",
          lambda: _near(G.volume(w), (36 + 26 + math.pi / 2 * (2 + 0.44 * 2)) * 50 * 2
                        - 4 * math.pi * 2.75 ** 2 * 2, 140))

    def holes():
        hs = G.holes(w, 2.70, 2.80)
        if len(hs) != 4:
            return False, f"{len(hs)} Ø5.5 holes"
        bend_ax = [x for x in G.cylinders(w) if x.concave and abs(x.radius - 2) <= 0.05][0]
        dist = [np.linalg.norm(_perp(h.point - bend_ax.point, bend_ax.axis)) for h in hs]
        pairs = [(i, j) for i in range(4) for j in range(i + 1, 4) if hs[i].same_axis(hs[j], 0.05)
                 is False and abs(abs(hs[i].axis @ hs[j].axis) - 1) < 1e-4]
        spac = sorted(np.linalg.norm(hs[i].point - hs[j].point
                                     - ((hs[i].point - hs[j].point) @ hs[i].axis) * hs[i].axis)
                      for i, j in pairs)
        ok = min(dist) >= 10 and len(spac) >= 2 and all(abs(s - 20) <= 0.1 for s in spac[:2])
        return ok, f"hole-to-bend-axis {np.round(dist, 2).tolist()}, spacing {np.round(spac, 2).tolist()}"
    c.add("4 x Ø5.5, 20 apart, ≥ 8 from bend", holes)


def chk_mould(out: Path, c: Checks):
    w = _single(out)
    lo, hi = G.bbox(w)
    c.add("80 x 50 x 30 envelope", lambda: _dims_ok(w, [80, 50, 30], 0.3))
    c.add("bottom z=0, opening +Z", lambda: (abs(lo[2]) <= 0.05, f"zmin {lo[2]:.2f}"))

    def wall():
        t = G.wall_thickness_samples(w, 500)
        med = float(np.median(t))
        frac = float(((t >= 1.7) & (t <= 2.4)).mean())
        return abs(med - 2) <= 0.15 and frac >= 0.8, f"median {med:.2f}, {100 * frac:.0f}% in 1.7..2.4"
    c.add("uniform 2 mm walls", wall)
    cen, nor, area = G.facets(w, 0.05)

    def draft():
        side = np.abs(nor[:, 2]) < 0.3
        bad = side & (np.abs(nor[:, 2]) < math.sin(math.radians(0.9)))
        return area[bad].sum() <= 0.01 * area[side].sum(), \
            f"{100 * area[bad].sum() / max(area[side].sum(), 1e-9):.1f}% of side area < 0.9° draft"
    c.add("≥ 1° draft on side walls", draft)
    c.add("no undercuts along Z", lambda: (int(G.column_crossings(w, 2, 70).max()) <= 1,
                                           f"max {int(G.column_crossings(w, 2, 70).max())} runs"))


# ============================================================================ E: edit regression
def _stage(out: Path):
    s0 = out / "stage_0" / "result.step"
    if not s0.is_file():
        raise RuntimeError("no stage_0 export (first build produced nothing)")
    import build123d as b3
    return b3.import_step(str(s0)), _single(out)


def chk_edit_plate(out: Path, c: Checks):
    def plate_ok(s, r, off):
        lo, hi = G.bbox(s)
        hs = [h for h in G.holes(s, r - 0.05, r + 0.05)]
        offs = sorted(min(abs(h.point[i] - lo[i]), abs(hi[i] - h.point[i]))
                      for h in hs for i in range(3) if (hi - lo)[i] > 20)
        bore = G.holes(s, 14.95, 15.05)
        return (len(hs) == 4 and all(abs(o - off) <= 0.05 for o in offs) and len(bore) == 1,
                f"{len(hs)} Ø{2 * r} holes, offsets {np.round(offs, 2).tolist()}, bore {len(bore)}")
    try:
        b, a = _stage(out)
    except Exception as e:  # noqa: BLE001
        c.add("first build present", lambda: (False, str(e)))
        b, a = None, _single(out) if (out / "result.step").is_file() else None
    if b is not None:
        c.add("before: 120x80x8, 4 x Ø6 at 10, Ø30 bore",
              lambda: (_dims_ok(b, [120, 80, 8], 0.05)[0] and plate_ok(b, 3, 10)[0],
                       plate_ok(b, 3, 10)[1]))
    if a is None:
        c.add("edited part present", lambda: (False, "no result"))
        return
    c.add("after: plate still 120x80x8", lambda: _dims_ok(a, [120, 80, 8], 0.05))
    c.add("after: 4 x Ø8 at 12 mm from edges", lambda: plate_ok(a, 4, 12))
    c.add("after: no Ø6 holes left", lambda: (not G.holes(a, 2.95, 3.05), ""))

    def bore_same():
        b0 = G.holes(b, 14.95, 15.05)[0]
        a0 = G.holes(a, 14.95, 15.05)[0]
        return b0.same_axis(a0, 0.01), "bore unchanged"
    c.add("bore unchanged (same place, Ø30)", bore_same)
    c.add("volume change = hole change (-703.7 mm3)",
          lambda: _near(G.volume(a) - G.volume(b), -4 * math.pi * (16 - 9) * 8, 14))

    def nothing_else():
        import build123d as b3
        ref_b = b3.Box(120, 80, 8)
        ref_a = b3.Box(120, 80, 8)
        for sx in (-1, 1):
            for sy in (-1, 1):
                ref_b -= b3.Pos(sx * 50, sy * 30, 0) * b3.Cylinder(3, 8)
                ref_a -= b3.Pos(sx * 48, sy * 28, 0) * b3.Cylinder(4, 8)
        want = G.sym_diff_volume(ref_b, ref_a)
        got = G.sym_diff_volume(b, a)
        return abs(got - want) <= 0.05 * want, f"changed volume {got:.1f} (only the holes: {want:.1f})"
    c.add("nothing else changed (symmetric difference)", nothing_else)


def chk_edit_shaft(out: Path, c: Checks):
    def shaft_ok(s, main_r):
        mains = G.bosses(s, main_r - 0.02, main_r + 0.02)
        spig = G.bosses(s, 5.98, 6.02)
        cones = [f for f in s.faces() if f.geom_type.name == "CONE"]
        ln = max(G.dims_sorted(s))
        ok = bool(mains) and bool(spig) and len(cones) >= 2 and abs(ln - 115) <= 0.05
        if ok:
            ok = mains[0].same_axis(spig[0], 0.01)
        return ok, f"main {len(mains)}, spigot {len(spig)}, chamfers {len(cones)}, length {ln:.2f}"
    try:
        b, a = _stage(out)
    except Exception as e:  # noqa: BLE001
        c.add("first build present", lambda: (False, str(e)))
        b, a = None, _single(out) if (out / "result.step").is_file() else None
    if b is not None:
        c.add("before: Ø20 body, Ø12x15 spigot, 2 chamfers, 115 long", lambda: shaft_ok(b, 10))
    if a is None:
        c.add("edited part present", lambda: (False, "no result"))
        return
    c.add("after: Ø25 body, spigot + chamfers kept, 115 long", lambda: shaft_ok(a, 12.5))
    c.add("after: no Ø20 body left", lambda: (not G.bosses(a, 9.98, 10.02), ""))

    def spigot_len():
        sp = G.bosses(a, 5.98, 6.02)[0]
        return _near(sp.length, 15, 0.05 + 1.0)   # chamfer may shorten the cylindrical face
    c.add("after: spigot still ~15 long", spigot_len)
    c.add("volume change ≈ π(12.5²-10²)·100",
          lambda: _near(G.volume(a) - G.volume(b), math.pi * (12.5 ** 2 - 100) * 100, 600))


def chk_edit_enclosure(out: Path, c: Checks):
    def encl_ok(s):
        bo = G.bosses(s, 3.45, 3.55)
        ho = G.holes(s, 1.2, 1.3)
        return (_dims_ok(s, [100, 60, 40], 0.05)[0] and len(bo) == 4 and len(ho) == 4,
                f"{len(bo)} bosses, {len(ho)} screw holes, dims {G.dims_sorted(s)}")
    try:
        b, a = _stage(out)
    except Exception as e:  # noqa: BLE001
        c.add("first build present", lambda: (False, str(e)))
        b, a = None, _single(out) if (out / "result.step").is_file() else None
    if b is not None:
        c.add("before: 100x60x40 box, 4 bosses with holes", lambda: encl_ok(b))
    if a is None:
        c.add("edited part present", lambda: (False, "no result"))
        return
    c.add("after: box + bosses intact", lambda: encl_ok(a))
    c.add("volume change = cutout (-180 mm3)", lambda: _near(G.volume(a) - G.volume(b), -180, 18))
    c.add("nothing else changed (symmetric difference ≈ 180)",
          lambda: _near(G.sym_diff_volume(a, b), 180, 18))

    def where():
        lo, hi = G.bbox(G.cut_shape(b, a))
        blo, bhi = G.bbox(b)
        d = hi - lo
        ds = sorted(d.tolist())
        floor_in = blo[2] + 2.5
        zok = abs((lo[2] - floor_in) - 8) <= 0.2 and abs(d[2] - 6) <= 0.2
        end = ((abs(lo[0] - blo[0]) <= 0.05 or abs(hi[0] - bhi[0]) <= 0.05) and abs(d[1] - 12) <= 0.2
               and abs((lo[1] + hi[1]) / 2 - (blo[1] + bhi[1]) / 2) <= 0.2) or \
              ((abs(lo[1] - blo[1]) <= 0.05 or abs(hi[1] - bhi[1]) <= 0.05) and abs(d[0] - 12) <= 0.2
               and abs((lo[0] + hi[0]) / 2 - (blo[0] + bhi[0]) / 2) <= 0.2)
        # the 60 mm end walls are the short ones: the cut must be along the long axis
        long_axis = int(np.argmax((bhi - blo)[:2]))
        short_wall = d[long_axis] <= 2.6
        return zok and end and short_wall, f"cut {np.round(ds, 2).tolist()} at z {lo[2] - floor_in:.2f} above floor"
    c.add("12x6 cutout centred on a 60 mm end wall, 8 above floor", where)


def chk_edit_bar(out: Path, c: Checks):
    def bar_ok(s):
        hs = sorted(G.holes(s, 3.95, 4.05), key=lambda h: h.point.tolist())
        return _dims_ok(s, [150, 30, 6], 0.05)[0] and len(hs) == 3, f"{len(hs)} Ø8 holes"
    try:
        b, a = _stage(out)
    except Exception as e:  # noqa: BLE001
        c.add("first build present", lambda: (False, str(e)))
        b, a = None, _single(out) if (out / "result.step").is_file() else None
    if b is not None:
        c.add("before: 150x30x6 with 3 x Ø8", lambda: bar_ok(b))
    if a is None:
        c.add("edited part present", lambda: (False, "no result"))
        return
    c.add("after: still 150x30x6", lambda: _dims_ok(a, [150, 30, 6], 0.05))

    def ends_kept():
        hb = G.holes(b, 3.95, 4.05)
        ha = [h for h in G.holes(a, 3.95, 4.05) if h.length > 5]
        lo, hi = G.bbox(b)
        L = int(np.argmax(hi - lo))
        ends = [h for h in hb if abs(h.point[L] - (lo[L] + hi[L]) / 2) > 30]
        kept = sum(any(x.same_axis(y, 0.01) for y in ha) for x in ends)
        return kept == 2 and len(ends) == 2, f"{kept}/2 end holes unchanged"
    c.add("end holes unchanged", ends_kept)

    def slot():
        cz = [x for x in G.cylinders(a) if x.concave and abs(x.radius - 4) <= 0.05]
        lo, hi = G.bbox(a)
        L = int(np.argmax(hi - lo))
        mid = (lo[L] + hi[L]) / 2
        near = [x for x in cz if abs(x.point[L] - mid) <= 7]
        if len(near) < 2:
            return False, f"{len(near)} slot-end arcs"
        span = max(x.point[L] for x in near) - min(x.point[L] for x in near)
        return abs(span - 12) <= 0.1, f"slot arc centres {span:.2f} apart (want 12: 20 long, 8 wide)"
    c.add("middle hole is a 20 x 8 slot", slot)
    c.add("4 x R5 corners", lambda: (len([x for x in G.bosses(a, 4.95, 5.05)]) == 4,
                                     f"{len(G.bosses(a, 4.95, 5.05))} R5 corners"))
    want_dv = -(12 * 8 * 6 + 4 * (1 - math.pi / 4) * 25 * 6)
    c.add("volume change = slot + corners (-704.8)", lambda: _near(G.volume(a) - G.volume(b), want_dv, 21))
    c.add("nothing else changed (symmetric difference)",
          lambda: _near(G.sym_diff_volume(a, b), -want_dv, 35))


# ============================================================================ the suite
TASKS: list[FTask] = [
    FTask("F1_pin_hole_fit", "fits", (
        "Design a pin-in-block fit. Block: 40 x 40 x 20 mm steel block with a Ø10 H7 "
        "through-hole along its 20 mm direction, centred. Pin: Ø10 g6 cylindrical pin, 30 mm "
        "long, assembled in the hole, coaxial, centred so 5 mm protrudes on each side. Model "
        "both diameters at the middle of their ISO 286 tolerance bands. "
        + NAMES_NOTE.format(names="block, pin")), chk_pin_hole),
    FTask("F2_bolted_flanges", "fits", (
        "Two identical bolted flanges: each Ø120 mm outside diameter, 12 mm thick, with a Ø50 "
        "mm central bore and six Ø9 mm bolt holes (M8 clearance, ISO 273 medium) equally spaced "
        "on a Ø90 mm pitch circle. Assemble them face to face, coaxial, with the bolt holes "
        "aligned. " + NAMES_NOTE.format(names="flange_a, flange_b")), chk_flanges),
    FTask("F3_box_lid_fit", "fits", (
        "An open-top box and its lid. Box: outside 60 x 40 x 30 mm (30 high), 2 mm walls and "
        "a 2 mm floor, opening facing +Z. Lid: a 60 x 40 x 2 mm plate with a 4 mm deep lip "
        "underneath that fits inside the box opening with 0.2 mm clearance on every side; the "
        "lip wall is 1.2 mm thick. Show the lid seated on the box (plate resting on the rim). "
        + NAMES_NOTE.format(names="box, lid")), chk_box_lid),
    FTask("F4_shaft_keyway", "fits", (
        "A Ø20 mm steel shaft, 80 mm long, axis along Z, with a DIN 6885 form A parallel "
        "keyway for a 6 x 6 mm key: 6 mm wide, 3.5 mm deep into the shaft, 30 mm long with "
        "rounded ends, centred along the shaft length. Also model the key (6 x 6 x 30 mm, "
        "rounded ends) seated in the keyway. " + NAMES_NOTE.format(names="shaft, key")),
          chk_shaft_key),
    FTask("K1_four_bar", "mechanisms", (
        "A planar four-bar linkage. Pin-joint centre distances: ground 100 mm, crank 40 mm, "
        "coupler 120 mm, rocker 80 mm. Each moving link is a 12 mm wide, 5 mm thick flat bar "
        "with a Ø6 mm hole at each joint centre; the ground link is a plate with two Ø6 mm "
        "holes 100 mm apart. Joint axes are parallel to Z and the links sit in different Z "
        "layers so they can pass each other through a full crank rotation. Assemble it at a "
        "crank angle of 60° from the ground line. "
        + NAMES_NOTE.format(names="ground, crank, coupler, rocker")), chk_four_bar),
    FTask("K2_spur_gear_pair", "mechanisms", (
        "A spur gear pair: module 2 mm, 20° pressure angle, involute teeth, pinion 20 teeth "
        "and gear 40 teeth, 10 mm face width, a Ø8 mm bore in each. Assemble them meshing at "
        "the standard centre distance, axes along Z, phased so the teeth do not overlap. "
        + NAMES_NOTE.format(names="pinion, gear")), chk_spur),
    FTask("K3_rack_and_pinion", "mechanisms", (
        "A rack and pinion: module 1.5 mm, 20° pressure angle, standard (unshifted) involute "
        "teeth. Pinion: 18 teeth, 12 mm face width, Ø8 mm bore, axis along Z. Rack: straight, "
        "150 mm long along X, 12 mm face width, 15 mm total height. Assemble them meshing at the "
        "standard distance, phased so the teeth do not overlap. "
        + NAMES_NOTE.format(names="pinion, rack")), chk_rack),
    FTask("K4_hinge_with_stop", "mechanisms", (
        "A hinge: two leaves, each 50 x 30 x 3 mm, with alternating knuckles around a Ø4 mm "
        "pin along their 50 mm edges (knuckle bores Ø4.2 mm), and the pin itself (Ø4 mm, 50 mm "
        "long). Add a mechanical stop so leaf_b can swing open from 0° (both leaves flat and "
        "coplanar) to 90° but collides with leaf_a before 110°. Assemble it flat at 0°. "
        + NAMES_NOTE.format(names="leaf_a, leaf_b, pin")), chk_hinge),
    FTask("S1_cantilever_arm", "structural", (
        "Design a cantilever arm in aluminium 6061-T6 (E = 68.9 GPa, ν = 0.33, yield 276 MPa, "
        "density 2.70 g/cm³). Its end face on the plane x = 0 is fully fixed and it extends "
        "150 mm along +X; a 500 N load acts downward (−Z), spread over the free end face on the "
        "plane x = 150. Requirements: von Mises stress ≤ 138 MPa (safety factor 2 on yield), "
        "tip deflection ≤ 1.0 mm, mass ≤ 150 g. One solid."), chk_cantilever),
    FTask("S2_shelf_bracket", "structural", (
        "Design a wall shelf bracket in S235 steel (E = 210 GPa, ν = 0.3, yield 235 MPa, "
        "density 7.85 g/cm³). Its back face is fixed to the wall on the plane x = 0; its "
        "horizontal top arm extends 200 mm along +X with its flat top face on the plane z = 0 "
        "(the bracket hangs below it). A shelf load of 1000 N acts downward (−Z), spread over "
        "the top face. Requirements: von Mises ≤ 117 MPa (safety factor 2), maximum deflection "
        "≤ 0.5 mm, mass ≤ 400 g, top face at least 20 mm wide. One solid."), chk_shelf),
    FTask("S3_tension_link", "structural", (
        "Design a steel tension link (E = 210 GPa, ν = 0.3, yield 355 MPa, density 7.85 "
        "g/cm³) joining two Ø10 mm pins at 100 mm centres: two Ø10.0 mm holes 100 mm apart "
        "along X, axes along Z, the first hole centred on the origin. It carries 12 kN of "
        "tension between the pins. Requirements: von Mises ≤ 178 MPa (safety factor 2) outside the "
        "pin bearing contact, mass "
        "≤ 250 g, thickness ≤ 10 mm along Z. One solid."), chk_link),
    FTask("S4_torsion_shaft", "structural", (
        "Design a drive shaft in steel (E = 210 GPa, ν = 0.3, yield 355 MPa, density 7.85 "
        "g/cm³), 200 mm long along +Z from z = 0 with its axis on the Z axis, outer diameter at "
        "most 30 mm, transmitting 250 N·m: the end face at z = 0 is fixed and the torque is "
        "applied about Z on the end face at z = 200. Requirements: von Mises ≤ 178 MPa (safety "
        "factor 2), angle of twist ≤ 0.6° over the length, mass ≤ 850 g. One solid."),
          chk_torsion),
    FTask("P1_cnc_plate", "manufacturability", (
        "A 6061 aluminium mounting plate for 3-axis CNC milling from the top in one setup: "
        "100 x 60 x 12 mm, bottom face on z = 0, four Ø6.6 mm through-holes with centres 8 mm "
        "from both adjacent edges, and a central rectangular pocket 50 x 30 mm, 6 mm deep, "
        "with internal corner radii of at least 3 mm (Ø6 end mill). No undercuts."), chk_cnc),
    FTask("P2_fdm_phone_stand", "manufacturability", (
        "A phone stand to be FDM 3D printed on its flat base (build plate z = 0) without "
        "supports. It holds a phone up to 12 mm thick leaning back at about 60–70° from "
        "horizontal; footprint within 100 x 100 mm, height 80–120 mm. Printability: no "
        "downward-facing surface steeper than 45° from vertical except on the build plate, "
        "minimal flat bridging, minimum wall 1.6 mm, base contact area at least 1500 mm². "
        "One solid."), chk_fdm),
    FTask("P3_sheet_metal_bracket", "manufacturability", (
        "An L-bracket bent from 2 mm steel sheet: inside bend radius 2 mm, outside flange "
        "lengths 40 mm and 30 mm (measured from the outside corner), 50 mm wide. Two Ø5.5 mm "
        "holes in each flange, 20 mm apart across the width and at least 8 mm from the bend. "
        "Constant 2 mm thickness everywhere, as a real bent part (no sharp inside corner)."),
          chk_sheet),
    FTask("P4_moulded_box", "manufacturability", (
        "An injection-moulded open-top ABS box: outside 80 x 50 x 30 mm (30 high), bottom on "
        "z = 0, opening facing +Z, uniform 2 mm walls and floor, at least 1° of draft on every "
        "side wall inside and outside (the mould opens along Z), and no undercuts in the pull "
        "direction."), chk_mould),
    FTask("E1_edit_hole_pattern", "edit", (
        "A 6061 aluminium mounting plate, 120 x 80 x 8 mm, with four Ø6 mm through-holes whose "
        "centres are 10 mm from both adjacent edges, and a central Ø30 mm through-bore."),
          chk_edit_plate,
          ["Edit that plate: change the four corner holes to Ø8 mm and move their centres to "
           "12 mm from the edges. Change nothing else."]),
    FTask("E2_edit_shaft_diameter", "edit", (
        "A turned steel shaft: a Ø20 x 100 mm main body with a Ø12 x 15 mm spigot on one end "
        "(115 mm long overall), and a 1 x 45° chamfer on the free end of the main body and on "
        "the free end of the spigot."), chk_edit_shaft,
          ["Edit the shaft: make the main body Ø25 mm. Keep everything else (lengths, spigot, "
           "chamfers) the same."]),
    FTask("E3_edit_enclosure_cutout", "edit", (
        "An electronics enclosure base: an open-top box 100 x 60 x 40 mm outside, 2.5 mm walls "
        "and floor, bottom on z = 0, opening +Z, with four Ø7 mm screw bosses with Ø2.5 mm "
        "holes (M3 self-tapping), one in each inside corner, boss axes 6 mm from the two "
        "adjacent outer walls, rising from the floor to 5 mm below the rim."),
          chk_edit_enclosure,
          ["Edit it: add a 12 x 6 mm rectangular cutout through one of the 60 mm end walls for a "
           "USB-C connector, centred across that wall, its bottom edge 8 mm above the inside "
           "floor surface. Change nothing else."]),
    FTask("E4_edit_bar_slot", "edit", (
        "A flat steel bar, 150 x 30 x 6 mm, with three Ø8 mm through-holes on its centre line "
        "at 25, 75 and 125 mm from one end."), chk_edit_bar,
          ["Edit the bar: turn the middle hole into a slot 20 mm long (end to end) and 8 mm wide "
           "along the bar's length, centred where the hole was, and round the bar's four corners "
           "(seen from above) with R5. Keep the other two holes exactly where they are."]),
]

BY_ID = {t.id: t for t in TASKS}


def score(task_id: str, out: Path) -> dict:
    t = BY_ID[task_id]
    c = Checks()
    try:
        if not (Path(out) / "result.step").is_file():
            c.add("result exported", lambda: (False, "no result.step"))
        else:
            t.check(Path(out), c)
    except Exception as e:  # noqa: BLE001
        c.add("checker", lambda: (False, f"{type(e).__name__}: {e}"))
    n = len(c.items)
    passed = sum(r.passed for r in c.items)
    return {"id": task_id, "category": t.category, "n_req": n, "passed": passed,
            "score": passed / n if n else 0.0, "strict": n > 0 and passed == n,
            "requirements": [r.__dict__ for r in c.items]}
