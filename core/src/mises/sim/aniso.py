"""Orthotropic FDM material in CalculiX, and the failure check across the layers.

Deck (CalculiX 2.21 manual, section 7.46 *ELASTIC and 7.102 *ORIENTATION,
https://www.dhondt.de/ccx_2.21.pdf):
- `*ELASTIC, TYPE=ENGINEERING CONSTANTS` takes a pair of lines: E1, E2, E3, nu12, nu13, nu23,
  G12, G13 on the first, G23 (and a temperature) on the second.
- `*ORIENTATION, NAME=..., SYSTEM=RECTANGULAR` takes point a on the local X' axis and point b
  in the local X'-Y' plane; the system is right-handed. `*SOLID SECTION, ..., ORIENTATION=`
  applies it to the elements. The local 3-axis is the print's build direction (layer normal).
- `*EL FILE` writes stresses in the GLOBAL system by default (GLOBAL=YES, section 7.51), so the
  stress normal to the layers is d^T sigma d with d the build direction in part coordinates.
"""
from __future__ import annotations

import numpy as np

from .materials import Material

ORIENTATION = "ORFDM"


def local_axes(d) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(e1, e2, e3 = d): a right-handed frame whose 3-axis is the build direction; for d = +z
    it is the global frame."""
    e3 = np.asarray(d, dtype=float)
    e3 = e3 / np.linalg.norm(e3)
    ref = np.array([1.0, 0.0, 0.0]) if abs(e3[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = ref - np.dot(ref, e3) * e3
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(e3, e1), e3


def orthotropic_cards(cards: list[str], m: Material) -> list[str]:
    """The model cards with the isotropic `*ELASTIC` data line swapped for engineering
    constants, an `*ORIENTATION` in front of `*MATERIAL`, and the solid section pointing at it.
    Every other material card (density, expansion, plasticity, ...) is kept as written."""
    e1, e2, _ = local_axes(m.build_dir)
    c = m.engineering_constants()
    out: list[str] = []
    skip = False
    for line in cards:
        key = line.split(",")[0].strip().upper()
        if skip:
            skip = False
            continue
        if key == "*MATERIAL":
            out += [f"*ORIENTATION, NAME={ORIENTATION}, SYSTEM=RECTANGULAR",
                    ",".join(f"{v:.9g}" for v in (*e1, *e2)), line]
        elif key == "*ELASTIC":
            out += ["*ELASTIC, TYPE=ENGINEERING CONSTANTS",
                    ",".join(f"{v:.9g}" for v in c[:8]), f"{c[8]:.9g}"]
            skip = True  # the isotropic E, nu line
        elif key == "*SOLID SECTION" and "ORIENTATION=" not in line.upper():
            out.append(f"{line}, ORIENTATION={ORIENTATION}")
        else:
            out.append(line)
    return out


def layer_normal_stress(S: np.ndarray, d) -> np.ndarray:
    """sigma_nn = d^T sigma d for S (n, 6) [sxx, syy, szz, sxy, syz, szx] in global axes."""
    x, y, z = (float(v) for v in np.asarray(d, dtype=float) / np.linalg.norm(d))
    return (S[:, 0] * x * x + S[:, 1] * y * y + S[:, 2] * z * z
            + 2 * (S[:, 3] * x * y + S[:, 4] * y * z + S[:, 5] * z * x))


def layer_peaks(mesh, frd: dict, m: Material, exclude: set[int]) -> dict:
    """The peak tension across the layers away from the supports and its safety factor
    against the Z strength. {} for an isotropic material."""
    if not m.orthotropic:
        return {}
    S = frd.get("STRESS", {})
    ids = [n for n in S if n not in exclude] or list(S)
    if not ids:
        return {}
    sn = layer_normal_stress(np.array([S[n][:6] for n in ids], dtype=float), m.build_dir)
    i = int(np.argmax(sn))
    peak = max(0.0, float(sn[i]))
    return {"max_layer_tension_mpa": peak,
            "max_layer_tension_at": list(mesh.nodes.get(ids[i], (0, 0, 0))),
            "allowable_z_mpa": m.allowable_z_mpa,
            "layer_safety_factor": m.allowable_z_mpa / peak if peak > 0 else None}
