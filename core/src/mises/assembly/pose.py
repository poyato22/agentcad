"""The ONE pose of an assembly's instances (P11 F4), shared by every consumer.

Every place that needs where an instance is — assembly_get and GET /projects/{id}/assembly (the
app draws exactly these transforms), assembly_health and its review, render_views /
critique_model / present_view, the build cover, auto-placement, feedback pins, internals — asks
here: the joint solve (C2) over the parts' own frames, each instance's world transform; when the
solve fails (a broken joint, a missing face), every instance's stored transform, so a view still
shows the layout. A part's own frame is its geometry as built (metrics.json, model.step/.stl,
and — since the frame fix in mises.cad.glb_frame — model.glb, which the app draws).

The P11 divergence was not here (all consumers already solved the same way) but in the mesh
the app drew under these poses: see mises.cad.glb_frame.
"""
from __future__ import annotations

from pathlib import Path

from ..types import MisesError
from .geometry import face_resolver
from .solve import Mat, SolveResult, solve


def solve_assembly(assembly, parts_root: Path) -> SolveResult:
    """The joint solve of `assembly` with faces resolved from `parts_root` (raises MisesError
    on a broken joint)."""
    return solve(assembly, resolve_face=face_resolver(Path(parts_root)))


def world_poses(assembly, parts_root: Path) -> tuple[dict[str, Mat], str | None]:
    """{instance id: world transform} for every instance, and the solve's error (None when it
    solved). A failed solve keeps the stored transforms."""
    try:
        res = solve_assembly(assembly, parts_root)
    except MisesError as e:
        return {i.id: [r[:] for r in i.transform] for i in assembly.instances}, e.message
    return {i.id: res.transforms.get(i.id) or [r[:] for r in i.transform]
            for i in assembly.instances}, None


def stored_result(assembly) -> SolveResult:
    """A SolveResult of the stored transforms (the fallback when the solve failed)."""
    return SolveResult(transforms={i.id: i.transform for i in assembly.instances},
                       dof_remaining=0)
