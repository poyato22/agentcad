"""gmsh mesh worker. Self-contained: stdlib + gmsh only (no mises imports).

Run as: python -I mesh_worker.py <model.step> <out.json> <size_mm>
gmsh is GPL, so it is imported only here, in a child process, never in the Mises core.
Writes a second-order (10-node) tetra mesh plus every boundary surface's 6-node triangles,
centre of mass, bounding box and area.
"""
from __future__ import annotations

import json
import os
import sys


def main(argv: list[str]) -> int:
    step, out, size = argv[1], argv[2], float(argv[3])
    import gmsh

    gmsh.initialize(["gmsh", "-noenv"], readConfigFiles=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.model.occ.importShapes(step)
        gmsh.model.occ.synchronize()
        if not gmsh.model.getEntities(3):
            raise ValueError("the STEP file has no solid to mesh")
        gmsh.option.setNumber("Mesh.MeshSizeMax", size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", size / 4)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)  # refine small holes/fillets
        gmsh.option.setNumber("Mesh.ElementOrder", 2)
        # straight-sided quadratic elements: no inverted elements on curved faces
        gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)
        gmsh.model.mesh.generate(3)

        tags, coords, _ = gmsh.model.mesh.getNodes()
        _, tet_nodes = gmsh.model.mesh.getElementsByType(11)  # 10-node tetrahedra
        if len(tet_nodes) == 0:
            raise ValueError("gmsh produced no tetrahedra")
        surfaces = []
        for _, s in gmsh.model.getEntities(2):
            tris: list[int] = []
            types, _, nodes = gmsh.model.mesh.getElements(2, s)
            for t, n in zip(types, nodes):
                if t == 9:  # 6-node triangle
                    tris.extend(int(x) for x in n)
            surfaces.append({"tag": int(s), "center": list(gmsh.model.occ.getCenterOfMass(2, s)),
                             "bbox": list(gmsh.model.getBoundingBox(2, s)),
                             "area": float(gmsh.model.occ.getMass(2, s)), "tris": tris})
        data = {"gmsh_version": gmsh.__version__, "size_mm": size,
                "node_tags": [int(t) for t in tags], "coords": [float(c) for c in coords],
                "tets": [int(t) for t in tet_nodes], "surfaces": surfaces}
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
