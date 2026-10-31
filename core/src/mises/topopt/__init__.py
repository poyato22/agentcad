"""Topology optimization and generative design for a part (Plan 9 D14).

A 3D SIMP voxel optimizer in numpy/scipy (research/2026-10-30-topopt.md): a structured hex grid,
a Galerkin-multigrid-preconditioned CG solver, a density filter (plus symmetry, extrusion and
3D-print overhang filters) and an OC update. The density field becomes geometry through surface
nets + Taubin smoothing + quadric decimation, built straight into an OCCT solid with shared
topology; the design space's exact faces and the keep-out bores are re-imposed with transversal
B-rep booleans. Every variant is verified with gmsh + CalculiX on the same load cases.

- fe.py        hex8 stiffness, grid levels, MGCG (multigrid-preconditioned CG)
- filters.py   density filter, symmetry, extrusion, AM overhang (Langelaar)
- simp.py      the optimizer
- voxelize.py  B-rep / STL -> grid masks, face node selection
- geometry.py  density -> surface -> decimated mesh -> B-rep, booleans, previews
- worker.py    the sandboxed child process (spec in, progress + artifacts out)
- verify.py    gmsh + CalculiX verification of a variant
- jobs.py      background jobs: variants, records, events, cancel, Pareto ranking
- tools.py     the topopt_* agent tools
"""
