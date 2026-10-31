"""gmsh tet mesher for a closed STL surface (topology-optimization results). Self-contained:
stdlib + gmsh only (no mises imports); gmsh is GPL, so it runs only here, in a child process.

Run as: python -I stl_mesh_worker.py <surface.stl> <out.json> <size_mm>

The STL triangles are the tet mesh's boundary as they are (no re-parametrization of a 5k-face
organic B-rep). Writes the same JSON as sim/mesh_worker.py (second-order tets, straight-sided,
one boundary surface). HXT is tried first (fast, strict), then the default Delaunay mesher.
"""
from __future__ import annotations

import json
import os
import sys


def main(argv: list[str]) -> int:
    stl, out, size = argv[1], argv[2], float(argv[3])
    import gmsh

    gmsh.initialize(["gmsh", "-noenv"], readConfigFiles=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.merge(stl)
        surfs = gmsh.model.getEntities(2)
        if not surfs:
            raise ValueError("the STL has no surface")
        loop = gmsh.model.geo.addSurfaceLoop([s[1] for s in surfs])
        gmsh.model.geo.addVolume([loop])
        gmsh.model.geo.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMax", size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", size / 4)
        algo = "hxt"
        try:
            gmsh.option.setNumber("Mesh.Algorithm3D", 10)
            gmsh.model.mesh.generate(3)
            _, t, _ = gmsh.model.mesh.getElements(3)
            if not t or not len(t[0]):
                raise ValueError("no tetrahedra")
        except Exception:  # noqa: BLE001 - HXT is strict: fall back to Delaunay
            algo = "delaunay"
            gmsh.model.mesh.clear()
            gmsh.option.setNumber("Mesh.Algorithm3D", 1)
            gmsh.model.mesh.generate(3)
        gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)
        gmsh.model.mesh.setOrder(2)
        tags, coords, _ = gmsh.model.mesh.getNodes()
        _, tet_nodes = gmsh.model.mesh.getElementsByType(11)
        if len(tet_nodes) == 0:
            raise ValueError("gmsh produced no tetrahedra")
        tris: list[int] = []
        for _, s in surfs:
            types, _, nodes = gmsh.model.mesh.getElements(2, s)
            for t, n in zip(types, nodes):
                if t == 9:
                    tris.extend(int(x) for x in n)
        bb = gmsh.model.getBoundingBox(-1, -1)
        data = {"gmsh_version": gmsh.__version__, "size_mm": size, "order": 2,
                "algorithm": algo, "node_tags": [int(t) for t in tags],
                "coords": [float(c) for c in coords], "tets": [int(t) for t in tet_nodes],
                "surfaces": [{"tag": 1, "center": [(bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2,
                                                   (bb[2] + bb[5]) / 2],
                              "bbox": list(bb), "area": 0.0, "tris": tris}]}
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
