"""gmsh mesh worker. Self-contained: stdlib + gmsh only (no mises imports).

Run as: python -I mesh_worker.py <model.step> <out.json> <size_mm> [<options.json>]
gmsh is GPL, so it is imported only here, in a child process, never in the Mises core.
Writes a tetra mesh (second order, 10-node, by default; 4-node with "order": 1) plus every
boundary surface's triangles (6- or 3-node), centre of mass, bounding box and area.

options.json (optional): {"order": 1|2, "refine": [{"size": mm, "center": [x,y,z]} |
{"size": mm, "axis": 0..2, "value": v} | {"size": mm, "point": [x,y,z], "radius": mm}],
"instances": [{"id": str, "step": path, "transform": 4x4 row-major}]}: local refinement on the
surfaces whose centre of mass is nearest `center`, that lie flat on the plane axis=value (a
bounding-box preset), or inside a ball. With instances (an assembly study) each instance's STEP
is placed by its transform and meshed as its own solid (no shared nodes between instances: they
interact only through contact or tie constraints); the output adds "tet_instances" ([id, count]
runs in tet order) and each surface's "instance".
"""
from __future__ import annotations

import json
import math
import os
import sys


def _refine_surfaces(gmsh, spec: dict, tol: float) -> list[int]:
    surfs = [s for _, s in gmsh.model.getEntities(2)]
    if "point" in spec:
        return []  # a ball field, not surfaces (see _ball)
    if "center" in spec:
        c = spec["center"]
        best = min(surfs, key=lambda s: math.dist(gmsh.model.occ.getCenterOfMass(2, s), c))
        return [best]
    ax, val = int(spec["axis"]), float(spec["value"])
    out = []
    for s in surfs:
        bb = gmsh.model.getBoundingBox(2, s)
        if abs(bb[ax + 3] - bb[ax]) <= tol and abs(bb[ax] - val) <= tol:
            out.append(s)
    return out


def _ball(gmsh, spec: dict, size: float) -> int:
    p = spec["point"]
    b = gmsh.model.mesh.field.add("Ball")
    gmsh.model.mesh.field.setNumber(b, "Radius", float(spec["radius"]))
    gmsh.model.mesh.field.setNumber(b, "VIn", float(spec["size"]))
    gmsh.model.mesh.field.setNumber(b, "VOut", size)
    gmsh.model.mesh.field.setNumber(b, "Thickness", float(spec["radius"]))
    for k, v in zip(("XCenter", "YCenter", "ZCenter"), p):
        gmsh.model.mesh.field.setNumber(b, k, float(v))
    return b


def _import(gmsh, step: str, instances: list) -> dict:
    """Import the part (or each assembly instance, placed by its transform); returns
    volume tag -> instance id (empty for a single part)."""
    if not instances:
        gmsh.model.occ.importShapes(step)
        gmsh.model.occ.synchronize()
        return {}
    owner = {}
    for inst in instances:
        dim_tags = gmsh.model.occ.importShapes(inst["step"])
        m = inst["transform"]
        gmsh.model.occ.affineTransform(dim_tags, [float(m[r][c]) for r in range(3)
                                                  for c in range(4)])
        for d, t in dim_tags:
            if d == 3:
                owner[t] = str(inst["id"])
    gmsh.model.occ.synchronize()
    return owner


def main(argv: list[str]) -> int:
    step, out, size = argv[1], argv[2], float(argv[3])
    opts = {}
    if len(argv) > 4:
        with open(argv[4]) as f:
            opts = json.load(f)
    order = 1 if int(opts.get("order", 2)) == 1 else 2
    import gmsh

    gmsh.initialize(["gmsh", "-noenv"], readConfigFiles=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        owner = _import(gmsh, step, opts.get("instances") or [])
        if not gmsh.model.getEntities(3):
            raise ValueError("the STEP file has no solid to mesh")
        refine = opts.get("refine") or []
        local_min = min([size] + [float(r["size"]) for r in refine])
        gmsh.option.setNumber("Mesh.MeshSizeMax", size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", min(size / 4, local_min))
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)  # refine small holes/fillets
        if refine:
            bb = gmsh.model.getBoundingBox(-1, -1)
            tol = 1e-4 * math.dist(bb[:3], bb[3:]) + 1e-6
            fields = []
            for r in refine:
                if "point" in r:
                    fields.append(_ball(gmsh, r, size))
                    continue
                tags = _refine_surfaces(gmsh, r, tol)
                if not tags:
                    continue
                d = gmsh.model.mesh.field.add("Distance")
                gmsh.model.mesh.field.setNumbers(d, "SurfacesList", tags)
                gmsh.model.mesh.field.setNumber(d, "Sampling", 40)
                t = gmsh.model.mesh.field.add("Threshold")
                gmsh.model.mesh.field.setNumber(t, "InField", d)
                gmsh.model.mesh.field.setNumber(t, "SizeMin", float(r["size"]))
                gmsh.model.mesh.field.setNumber(t, "SizeMax", size)
                gmsh.model.mesh.field.setNumber(t, "DistMin", 0.0)
                gmsh.model.mesh.field.setNumber(t, "DistMax", 3 * size)
                fields.append(t)
            if fields:
                m = gmsh.model.mesh.field.add("Min")
                gmsh.model.mesh.field.setNumbers(m, "FieldsList", fields)
                gmsh.model.mesh.field.setAsBackgroundMesh(m)
        gmsh.option.setNumber("Mesh.ElementOrder", order)
        if order == 2:
            # straight-sided quadratic elements: no inverted elements on curved faces
            gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)
        gmsh.model.mesh.generate(3)

        tags, coords, _ = gmsh.model.mesh.getNodes()
        tet_type, tri_type = (11, 9) if order == 2 else (4, 2)
        runs = []
        if owner:  # per instance, in a known order, so each tet's instance is known
            tet_nodes = []
            for _, v in gmsh.model.getEntities(3):
                _, nodes = gmsh.model.mesh.getElementsByType(tet_type, v)
                tet_nodes.extend(int(x) for x in nodes)
                runs.append([owner.get(v, ""), len(nodes) // (10 if order == 2 else 4)])
        else:
            _, tet_nodes = gmsh.model.mesh.getElementsByType(tet_type)
        if len(tet_nodes) == 0:
            raise ValueError("gmsh produced no tetrahedra")
        surfaces = []
        for _, s in gmsh.model.getEntities(2):
            tris: list[int] = []
            types, _, nodes = gmsh.model.mesh.getElements(2, s)
            for t, n in zip(types, nodes):
                if t == tri_type:
                    tris.extend(int(x) for x in n)
            surf = {"tag": int(s), "center": list(gmsh.model.occ.getCenterOfMass(2, s)),
                    "bbox": list(gmsh.model.getBoundingBox(2, s)),
                    "area": float(gmsh.model.occ.getMass(2, s)), "tris": tris}
            if owner:
                up, _ = gmsh.model.getAdjacencies(2, s)
                surf["instance"] = next((owner[v] for v in up if v in owner), None)
            surfaces.append(surf)
        data = {"gmsh_version": gmsh.__version__, "size_mm": size, "order": order,
                "node_tags": [int(t) for t in tags], "coords": [float(c) for c in coords],
                "tets": [int(t) for t in tet_nodes], "surfaces": surfaces}
        if runs:
            data["tet_instances"] = runs
    finally:
        gmsh.finalize()
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"MESH_ERROR: {type(e).__name__}: {e}\n")
        sys.exit(1)
