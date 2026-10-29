"""Wind study post-processing: coefficients, the skin-friction estimate, the empirical fallback,
surface Cp at the part's vertices and velocity slice images."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

# Drag coefficients at Re ~ 1e4-1e5 on the frontal area (Hoerner, Fluid-Dynamic Drag, 1965;
# White, Fluid Mechanics, table 7.3): a guide for the "estimate" fallback only.
CD_TABLE = {"sphere": 0.47, "cube": 1.05, "cylinder": 1.0, "flat_plate": 1.17,
            "streamlined": 0.04, "bluff": 1.0}
EMPIRICAL_BAND = 0.4     # +-40%: shape-class estimates are rough


def skin_friction_cf(re: float) -> float:
    """Flat-plate mean skin-friction coefficient: Blasius laminar 1.328/sqrt(Re) below 5e5,
    else the turbulent 0.074 Re^-0.2 (Prandtl)."""
    re = max(re, 1.0)
    return 1.328 / math.sqrt(re) if re < 5e5 else 0.074 * re ** -0.2


def friction_cd(re: float, wetted_m2: float, ref_m2: float) -> float:
    """Skin-friction drag coefficient on the reference area. The attached share of the
    wetted area is taken as half (the front of a bluff body; the rest sits in separated flow).
    An approximation: the lattice is far too coarse to resolve the boundary layer."""
    return skin_friction_cf(re) * 0.5 * wetted_m2 / max(ref_m2, 1e-12)


def classify(ext: np.ndarray, volume_mm3: float) -> str:
    """Shape class from the wind-frame extent (x along the flow) and the volume."""
    lx, ly, lz = (max(float(e), 1e-9) for e in ext)
    d = max(ly, lz)
    fill = volume_mm3 / (lx * ly * lz)
    ratio = max(lx, ly, lz) / min(lx, ly, lz)
    if lx < 0.25 * d:
        return "flat_plate"
    if fill > 0.9 and ratio < 1.3:
        return "cube"
    if abs(fill - math.pi / 6) < 0.06 and ratio < 1.15:
        return "sphere"
    if abs(fill - math.pi / 4) < 0.06:
        return "cylinder"
    if lx > 2.5 * d and fill < 0.7:
        return "streamlined"
    return "bluff"


def mesh_volume_mm3(tris: np.ndarray) -> float:
    return float(abs(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2])).sum())
                 / 6.0)


def surface_cp(verts_wind: np.ndarray, origin: np.ndarray, h: float, solid: np.ndarray,
               cp: np.ndarray) -> np.ndarray:
    """Cp at each vertex: the mean over the fluid cells in its 3x3x3 neighbourhood (5x5x5 if
    none is fluid)."""
    g = np.rint((verts_wind - origin) / h).astype(int)
    hi = np.array(solid.shape) - 1
    out = np.full(len(g), np.nan)
    for r in (1, 2):
        todo = np.isnan(out)
        if not todo.any():
            break
        s = np.zeros(todo.sum())
        n = np.zeros(todo.sum())
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    q = np.clip(g[todo] + (dx, dy, dz), 0, hi)
                    fl = ~solid[q[:, 0], q[:, 1], q[:, 2]]
                    s += np.where(fl, cp[q[:, 0], q[:, 1], q[:, 2]], 0.0)
                    n += fl
        vals = np.where(n > 0, s / np.maximum(n, 1), np.nan)
        out[todo] = vals
    return np.nan_to_num(out, nan=0.0)


def slice_png(path: Path, speed2d: np.ndarray, solid2d: np.ndarray, vmax: float,
              min_px: int = 320) -> None:
    """Speed on a plane (rows = x downstream, cols = the other axis), body in grey; written
    with the first axis horizontal and 'up' at the top."""
    from PIL import Image

    from ..results import colormap

    t = np.clip(speed2d / vmax if vmax > 0 else speed2d * 0, 0, 1)
    lut = np.array([[c ** (1 / 2.2) for c in colormap(x)] for x in np.linspace(0, 1, 256)])
    rgb = (lut[(t * 255).astype(int)] * 255).astype(np.uint8)
    rgb[solid2d] = (110, 110, 110)
    img = np.transpose(rgb, (1, 0, 2))[::-1]          # x to the right, second axis up
    im = Image.fromarray(np.ascontiguousarray(img), "RGB")
    scale = max(1, int(math.ceil(min_px / max(im.size))))
    im = im.resize((im.size[0] * scale, im.size[1] * scale), Image.NEAREST)
    tmp = Path(str(path) + ".tmp")
    im.save(tmp, format="PNG")
    tmp.replace(path)
