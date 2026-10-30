"""Generate an OpenFOAM (v2512, openfoam.com) external-aero case from a part: blockMesh
background box, snappyHexMesh around the part's STL, simpleFoam with k-omega SST and
forces / forceCoeffs / sampled-surface function objects. Pure text generation, no OpenFOAM.

Frame and units: the case is in the wind frame (flow along +x, lift along +z, see
setup.wind_frame) in metres. The part is written as constant/triSurface/part.stl with the
solid name "body"; snappyHexMesh names its patch from it (body or body_<region>, matched with
the regex "body.*" everywhere).

Domain (S2 ruling): upstream domain_multiplier x D, downstream 2 x domain_multiplier x D, a
square cross-section of 2 x domain_multiplier x D (D = the frontal size). That is twice the
built-in tunnel's cross-section: a RANS run is cheap far from the body and the lateral blockage
stays under 1% at the default multiplier (the built-in solver's 5 x 5 D tunnel is ~3%, which
alone inflates Cd ~5%).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# refinement -> (background cells across D, snappy surface levels (min, max), boundary layers)
LEVELS = {1: (2, (3, 3), 0), 2: (3, (3, 3), 0), 3: (4, (3, 4), 3), 4: (4, (4, 5), 3)}
# Flow regime (benchmark fix, 2026-10-30): fully turbulent k-omega SST puts a sphere's
# separation far too late below the drag crisis (real run: Cd 0.34 vs 0.47 at Re 2e4, -28%).
# Below LAMINAR_RE_MAX (on the frontal size D) the boundary layer is laminar, so the case runs
# laminar (no turbulence model). k-omega SST-LM (kOmegaSSTLM, in v2512) was tried on the same
# mesh and gave Cd 0.37 (-21%): it needs y+ ~ 1, which wall-function meshes do not give.
LAMINAR_RE_MAX = 1e5
# a bluff body (streamwise length < BLUFF_ASPECT x D) separates from its curved surface, so at
# refinement >= 3 its whole surface is meshed at the finest level (min level = max level), not
# only where the facet angle trips resolveFeatureAngle
BLUFF_ASPECT = 3.0
TURBULENCE_INTENSITY = 0.01      # inlet: 1% free-stream turbulence
VISCOSITY_RATIO = 10.0           # inlet nut / nu
BYTES_PER_CELL = 3000            # snappyHexMesh peak per cell (simpleFoam needs ~1 KB); a guide
FIXED_MB = 400                   # the container's OpenFOAM processes before any mesh


@dataclass
class CasePlan:
    lo: np.ndarray                 # body box (m, wind frame)
    hi: np.ndarray
    d_ref_m: float
    dom_lo: np.ndarray
    dom_hi: np.ndarray
    cells: tuple[int, int, int]    # blockMesh background
    base_cell_m: float
    surface_levels: tuple[int, int]
    layers: int
    cells_estimate: int
    end_iterations: int
    cpus: int
    reynolds: float = 0.0          # on D
    laminar: bool = False          # no turbulence model (Re < LAMINAR_RE_MAX)
    bluff: bool = False

    @property
    def turbulence(self) -> str:
        return ("laminar (no turbulence model; Re < 1e5)" if self.laminar
                else "k-omega SST (RANS, wall functions)")

    @property
    def surface_cell_m(self) -> float:
        return self.base_cell_m / 2 ** self.surface_levels[1]

    @property
    def memory_mb_estimate(self) -> float:
        return FIXED_MB + self.cells_estimate * BYTES_PER_CELL / 1e6

    def to_dict(self) -> dict:
        return {"background_cells": list(self.cells), "base_cell_mm": 1e3 * self.base_cell_m,
                "surface_cell_mm": 1e3 * self.surface_cell_m,
                "surface_levels": list(self.surface_levels), "boundary_layers": self.layers,
                "cells_estimate": self.cells_estimate,
                "memory_mb_estimate": round(self.memory_mb_estimate),
                "cells_across": round(self.d_ref_m / self.surface_cell_m),
                "domain_m": [self.dom_lo.tolist(), self.dom_hi.tolist()],
                "iterations_cap": self.end_iterations, "cpus": self.cpus,
                "reynolds": self.reynolds, "regime": "laminar" if self.laminar else "turbulent",
                "bluff_body": self.bluff}


def plan_case(tris_w_m: np.ndarray, s: dict) -> CasePlan:
    lo, hi = tris_w_m.reshape(-1, 3).min(0), tris_w_m.reshape(-1, 3).max(0)
    ext = hi - lo
    d = max(float(ext[1]), float(ext[2]), 1e-6)
    m = float(s["domain_multiplier"])
    c = 0.5 * (lo + hi)
    dom_lo = np.array([lo[0] - m * d, c[1] - m * d, c[2] - m * d])
    dom_hi = np.array([hi[0] + 2 * m * d, c[1] + m * d, c[2] + m * d])
    base, levels, layers = LEVELS[int(s["refinement"])]
    bluff = float(ext[0]) < BLUFF_ASPECT * d
    if bluff and int(s["refinement"]) >= 3:
        levels = (levels[1], levels[1])
    re_d = float(s["speed_m_s"]) * d * float(s["air_density"]) / float(s["air_viscosity"])
    h0 = d / base
    cells = tuple(int(math.ceil((dom_hi[i] - dom_lo[i]) / h0)) for i in range(3))
    # estimate: background + a few cells deep at each surface level + the wake box
    area = surface_area_m2(tris_w_m)
    n = int(np.prod(cells))
    for lv in range(1, levels[1] + 1):
        hs = h0 / 2 ** lv
        n += int(3 * 3 * area / (hs * hs))          # nCellsBetweenLevels 3, ~3 cells deep
    hw = h0 / 2 ** max(levels[0] - 1, 0)
    wake = (ext[0] + 3.5 * d) * (ext[1] + 1.5 * d) * (ext[2] + 1.5 * d)
    n += int(wake / hw ** 3)
    n += int(layers * area / (h0 / 2 ** levels[1]) ** 2)
    return CasePlan(lo, hi, d, dom_lo, dom_hi, cells, h0, levels, layers, n,
                    int(s.get("openfoam_iterations", 1500)), int(s.get("cpus", 1)),
                    reynolds=re_d, laminar=re_d < LAMINAR_RE_MAX, bluff=bluff)


def surface_area_m2(tris: np.ndarray) -> float:
    return float(0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0],
                                               tris[:, 2] - tris[:, 0]), axis=1).sum())


def _v(x) -> str:
    return "(" + " ".join(f"{float(c):.9g}" for c in x) + ")"


def _header(cls: str, obj: str, loc: str | None = None) -> str:
    loc_line = f'    location    "{loc}";\n' if loc else ""
    return ("FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
            f"    class       {cls};\n{loc_line}    object      {obj};\n}}\n"
            "// generated by Mises (S2): edit the study settings, not this file\n\n")


def write_stl(path: Path, tris_m: np.ndarray, name: str = "body") -> None:
    lines = [f"solid {name}"]
    for a, b, c in tris_m:
        n = np.cross(b - a, c - a)
        ln = float(np.linalg.norm(n))
        n = n / ln if ln > 0 else n
        lines.append(f"  facet normal {n[0]:.6e} {n[1]:.6e} {n[2]:.6e}")
        lines.append("    outer loop")
        for p in (a, b, c):
            lines.append(f"      vertex {p[0]:.9e} {p[1]:.9e} {p[2]:.9e}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines) + "\n")


def inlet_turbulence(U: float, nu: float) -> tuple[float, float]:
    """(k, omega) at the inlet: k = 1.5 (U I)^2, omega = k / (nu x viscosity ratio)."""
    k = 1.5 * (U * TURBULENCE_INTENSITY) ** 2
    return k, k / (nu * VISCOSITY_RATIO)


def write_case(case: Path, tris_w_m: np.ndarray, s: dict, a_ref_m2: float,
               plan: CasePlan | None = None) -> CasePlan:
    """Write the whole case under `case` (created). Returns the plan it used."""
    plan = plan or plan_case(tris_w_m, s)
    case = Path(case)
    for sub in ("0", "constant/triSurface", "system"):
        (case / sub).mkdir(parents=True, exist_ok=True)
    write_stl(case / "constant/triSurface/part.stl", tris_w_m)
    U, rho = float(s["speed_m_s"]), float(s["air_density"])
    nu = float(s["air_viscosity"]) / rho
    k, om = inlet_turbulence(U, nu)
    files = {
        "system/blockMeshDict": _block_mesh(plan),
        "system/snappyHexMeshDict": _snappy(plan),
        "system/controlDict": _control(plan, U, rho, a_ref_m2),
        "system/fvSchemes": FV_SCHEMES,
        "system/fvSolution": FV_SOLUTION,
        "system/decomposeParDict": _decompose(plan.cpus),
        "constant/transportProperties": (_header("dictionary", "transportProperties",
                                                 "constant")
                                         + f"transportModel  Newtonian;\n\nnu              "
                                           f"{nu:.9g};\n"),
        "constant/turbulenceProperties": (_header("dictionary", "turbulenceProperties",
                                                  "constant")
                                          + ("simulationType  laminar;\n" if plan.laminar else
                                             "simulationType  RAS;\n\nRAS\n{\n    RASModel    "
                                             "    kOmegaSST;\n    turbulence      on;\n    "
                                             "printCoeffs     on;\n}\n")),
        "0/U": _field("volVectorField", "U", "[0 1 -1 0 0 0 0]", f"uniform ({U:.9g} 0 0)",
                      inlet=f"type fixedValue; value uniform ({U:.9g} 0 0);",
                      outlet="type inletOutlet; inletValue uniform (0 0 0); "
                             "value $internalField;",
                      body="type noSlip;"),
        "0/p": _field("volScalarField", "p", "[0 2 -2 0 0 0 0]", "uniform 0",
                      inlet="type zeroGradient;", outlet="type fixedValue; value uniform 0;",
                      body="type zeroGradient;"),
        "0/k": _field("volScalarField", "k", "[0 2 -2 0 0 0 0]", f"uniform {k:.9g}",
                      inlet=f"type fixedValue; value uniform {k:.9g};",
                      outlet=f"type inletOutlet; inletValue uniform {k:.9g}; "
                             "value $internalField;",
                      body=f"type kqRWallFunction; value uniform {k:.9g};"),
        "0/omega": _field("volScalarField", "omega", "[0 0 -1 0 0 0 0]", f"uniform {om:.9g}",
                          inlet=f"type fixedValue; value uniform {om:.9g};",
                          outlet=f"type inletOutlet; inletValue uniform {om:.9g}; "
                                 "value $internalField;",
                          body=f"type omegaWallFunction; value uniform {om:.9g};"),
        "0/nut": _field("volScalarField", "nut", "[0 2 -1 0 0 0 0]", "uniform 0",
                        inlet="type calculated; value uniform 0;",
                        outlet="type calculated; value uniform 0;",
                        body="type nutkWallFunction; value uniform 0;"),
        "Allrun": _allrun(plan.cpus),
    }
    for rel, text in files.items():
        (case / rel).write_text(text)
    (case / "Allrun").chmod(0o755)
    (case / "case.foam").write_text("")
    return plan


def _block_mesh(p: CasePlan) -> str:
    x0, y0, z0 = p.dom_lo
    x1, y1, z1 = p.dom_hi
    pts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
           (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    nx, ny, nz = p.cells
    return (_header("dictionary", "blockMeshDict", "system")
            + "scale 1;\n\nvertices\n(\n" + "".join(f"    {_v(q)}\n" for q in pts) + ");\n\n"
            f"blocks\n(\n    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)\n);\n\n"
            "edges\n(\n);\n\nboundary\n(\n"
            "    inlet\n    {\n        type patch;\n        faces\n        (\n"
            "            (0 4 7 3)\n        );\n    }\n"
            "    outlet\n    {\n        type patch;\n        faces\n        (\n"
            "            (1 2 6 5)\n        );\n    }\n"
            "    sides\n    {\n        type symmetry;\n        faces\n        (\n"
            "            (0 1 5 4)\n            (3 7 6 2)\n            (0 3 2 1)\n"
            "            (4 5 6 7)\n        );\n    }\n);\n\nmergePatchPairs\n(\n);\n")


def _location_in_mesh(p: CasePlan) -> np.ndarray:
    """A fluid point: halfway between the inlet and the body, off every cell face."""
    c = 0.5 * (p.lo + p.hi)
    h = p.base_cell_m
    return np.array([0.5 * (p.dom_lo[0] + p.lo[0]) + 0.137 * h, c[1] + 0.291 * h,
                     c[2] + 0.373 * h])


def min_vol(p: CasePlan) -> float:
    """snappyHexMesh's minVol scaled to the part: 1e-4 of a surface cell. The layers' thinnest
    cells (minThickness 0.1 of a surface cell) stay ~1000x above it, so a 10 mm part at
    refinement 4 keeps its layers (an absolute 1e-13 m3 rejected them)."""
    return p.surface_cell_m ** 3 * 1e-4


def _snappy(p: CasePlan) -> str:
    d = p.d_ref_m
    lmin, lmax = p.surface_levels
    wake_lo = p.lo - np.array([0.5 * d, 0.75 * d, 0.75 * d])
    wake_hi = p.hi + np.array([3.0 * d, 0.75 * d, 0.75 * d])
    add_layers = "true" if p.layers else "false"
    return (_header("dictionary", "snappyHexMeshDict", "system")
            + f"castellatedMesh true;\nsnap            true;\naddLayers       {add_layers};\n\n"
            "geometry\n{\n    part.stl\n    {\n        type triSurfaceMesh;\n"
            "        name body;\n    }\n    wake\n    {\n        type searchableBox;\n"
            f"        min {_v(wake_lo)};\n        max {_v(wake_hi)};\n    }}\n}}\n\n"
            "castellatedMeshControls\n{\n    maxLocalCells 2000000;\n"
            "    maxGlobalCells 20000000;\n    minRefinementCells 10;\n"
            "    maxLoadUnbalance 0.10;\n    nCellsBetweenLevels 3;\n    features ();\n"
            "    refinementSurfaces\n    {\n        body\n        {\n"
            f"            level ({lmin} {lmax});\n"
            "            patchInfo { type wall; }\n        }\n    }\n"
            "    resolveFeatureAngle 30;\n    refinementRegions\n    {\n        wake\n"
            f"        {{\n            mode inside;\n            levels ((1e15 {max(lmin - 1, 0)}));"
            "\n        }\n    }\n"
            f"    locationInMesh {_v(_location_in_mesh(p))};\n"
            "    allowFreeStandingZoneFaces true;\n}\n\n"
            "snapControls\n{\n    nSmoothPatch 3;\n    tolerance 2.0;\n    nSolveIter 30;\n"
            "    nRelaxIter 5;\n    nFeatureSnapIter 10;\n    implicitFeatureSnap true;\n"
            "    explicitFeatureSnap false;\n    multiRegionFeatureSnap false;\n}\n\n"
            "addLayersControls\n{\n    relativeSizes true;\n    layers\n    {\n"
            f"        \"body.*\"\n        {{\n            nSurfaceLayers {max(p.layers, 1)};\n"
            "        }\n    }\n    expansionRatio 1.3;\n    finalLayerThickness 0.4;\n"
            "    minThickness 0.1;\n    nGrow 0;\n    featureAngle 60;\n"
            "    slipFeatureAngle 30;\n    nRelaxIter 3;\n    nSmoothSurfaceNormals 1;\n"
            "    nSmoothNormals 3;\n    nSmoothThickness 10;\n    maxFaceThicknessRatio 0.5;\n"
            "    maxThicknessToMedialRatio 0.3;\n    minMedialAxisAngle 90;\n"
            "    nBufferCellsNoExtrude 0;\n    nLayerIter 50;\n}\n\n"
            "meshQualityControls\n{\n    maxNonOrtho 65;\n    maxBoundarySkewness 20;\n"
            f"    maxInternalSkewness 4;\n    maxConcave 80;\n    minVol {min_vol(p):.3g};\n"
            "    minTetQuality 1e-15;\n    minArea -1;\n    minTwist 0.02;\n"
            "    minDeterminant 0.001;\n    minFaceWeight 0.05;\n    minVolRatio 0.01;\n"
            "    minTriangleTwist -1;\n    nSmoothScale 4;\n    errorReduction 0.75;\n"
            "    relaxed\n    {\n        maxNonOrtho 75;\n    }\n}\n\n"
            "mergeTolerance 1e-6;\n")


def _control(p: CasePlan, U: float, rho: float, a_ref: float) -> str:
    cofr = _v(0.5 * (p.lo + p.hi))
    c = 0.5 * (p.lo + p.hi)
    common = (f"        patches         (\"body.*\");\n        rho             rhoInf;\n"
              f"        rhoInf          {rho:.9g};\n        CofR            {cofr};\n"
              "        e1              (1 0 0);\n        e3              (0 0 1);\n"
              "        writeControl    timeStep;\n        writeInterval   1;\n"
              "        log             false;\n")
    n = p.end_iterations
    return (_header("dictionary", "controlDict", "system")
            + f"application     simpleFoam;\n\nstartFrom       startTime;\nstartTime       0;\n"
            f"stopAt          endTime;\nendTime         {n};\ndeltaT          1;\n"
            f"writeControl    timeStep;\nwriteInterval   {n};\npurgeWrite      0;\n"
            "writeFormat     ascii;\nwritePrecision  8;\nwriteCompression off;\n"
            "timeFormat      general;\ntimePrecision   6;\nrunTimeModifiable false;\n\n"
            "functions\n{\n"
            "    forces\n    {\n        type            forces;\n        libs            (forces);\n"
            + common + "    }\n"
            "    forceCoeffs\n    {\n        type            forceCoeffs;\n"
            "        libs            (forces);\n" + common
            + f"        magUInf         {U:.9g};\n        lRef            {p.d_ref_m:.9g};\n"
            f"        Aref            {a_ref:.9g};\n        dragDir         (1 0 0);\n"
            "        liftDir         (0 0 1);\n"
            "        coefficients    (Cd Cs Cl CmRoll CmPitch CmYaw);\n    }\n"
            "    surfaces\n    {\n        type            surfaces;\n"
            "        libs            (sampling);\n        writeControl    writeTime;\n"
            "        surfaceFormat   raw;\n        fields          (p U);\n"
            "        interpolationScheme cellPoint;\n        surfaces\n        {\n"
            "            body\n            {\n                type        patch;\n"
            "                patches     (\"body.*\");\n                interpolate false;\n"
            "            }\n"
            + "".join(f"            {name}\n            {{\n                type        cuttingPlane;\n"
                      "                planeType   pointAndNormal;\n"
                      f"                pointAndNormalDict\n                {{\n"
                      f"                    point   {_v(c)};\n"
                      f"                    normal  {normal};\n                }}\n"
                      "                interpolate false;\n            }\n"
                      for name, normal in (("slice_xz", "(0 1 0)"), ("slice_xy", "(0 0 1)")))
            + "        }\n    }\n}\n")


def _field(cls: str, name: str, dims: str, internal: str, *, inlet: str, outlet: str,
           body: str) -> str:
    def blk(patch: str, spec: str) -> str:
        inner = "".join(f"        {t.strip()};\n" for t in spec.split(";") if t.strip())
        return f"    {patch}\n    {{\n{inner}    }}\n"
    return (_header(cls, name, "0") + f"dimensions      {dims};\n\ninternalField   {internal};\n\n"
            "boundaryField\n{\n" + blk("inlet", inlet) + blk("outlet", outlet)
            + blk("sides", "type symmetry;") + blk('"body.*"', body) + "}\n")


def _decompose(n: int) -> str:
    return (_header("dictionary", "decomposeParDict", "system")
            + f"numberOfSubdomains {max(n, 1)};\n\nmethod          scotch;\n")


def _allrun(cpus: int) -> str:
    solve = (f"run decomposePar decomposePar -force\nrun simpleFoam mpirun -np {cpus} "
             "simpleFoam -parallel" if cpus > 1 else "run simpleFoam simpleFoam")
    return ("#!/bin/bash\n# generated by Mises (S2); runs inside the OpenFOAM container, cwd "
            "/case\ncd /case || exit 1\n"
            "run() {\n    local name=$1; shift\n    echo \"$name\" > stage\n"
            "    \"$@\" > \"log.$name\" 2>&1 || {\n"
            "        echo \"MISES_STAGE_FAILED $name\" >&2\n"
            "        tail -n 40 \"log.$name\" >&2\n        exit 1\n    }\n}\n"
            "run blockMesh blockMesh\nrun snappyHexMesh snappyHexMesh -overwrite\n"
            "checkMesh > log.checkMesh 2>&1 || true\n"
            f"{solve}\necho done > stage\n")


FV_SCHEMES = (_header("dictionary", "fvSchemes", "system") + """ddtSchemes
{
    default         steadyState;
}

gradSchemes
{
    default         Gauss linear;
    grad(U)         cellLimited Gauss linear 1;
}

divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwindV grad(U);
    div(phi,k)      bounded Gauss upwind;
    div(phi,omega)  bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear limited corrected 0.33;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         limited corrected 0.33;
}

wallDist
{
    method          meshWave;
}
""")

FV_SOLUTION = (_header("dictionary", "fvSolution", "system") + """solvers
{
    p
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-7;
        relTol          0.01;
    }

    "(U|k|omega)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0.1;
    }
}

SIMPLE
{
    nNonOrthogonalCorrectors 0;
    consistent      yes;

    residualControl
    {
        p               1e-4;
        U               1e-5;
        "(k|omega)"     1e-5;
    }
}

relaxationFactors
{
    equations
    {
        U               0.9;
        "(k|omega)"     0.7;
    }
}
""")
