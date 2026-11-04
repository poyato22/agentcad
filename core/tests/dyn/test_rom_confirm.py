"""DOG demo: the walk study's range-of-motion check failed on bounding-box hits (foot pads of
the front and rear legs, 70 mm apart on the real geometry), and the exact sweep timed out, so
the lead could not tell a near-miss from a clash. dyn_rom_sweep now re-checks its worst hits
on the exact geometry at the colliding pose: a hit the exact check clears passes as a near
miss; one it confirms keeps failing with the real overlap."""
import pytest

from mises.assembly import geometry
from mises.dyn import run
from mises.dyn import tools as dt

from .test_tools import ctx  # noqa: F401 - the quadruped fixture


def _fake_rom(bodies, pose):
    async def fake(home, job):
        assert job["task"] == "rom"
        return {"poses": 100, "joints": list(pose), "ranges": [], "rest_contacts": [],
                "collisions": [{"bodies": bodies, "depth_mm": 16.9, "pose": pose,
                                "count": 3, "first_pose": 1}],
                "colliding_pairs": 1, "wall_s": 0.1}
    return fake


@pytest.mark.parametrize("exact_mm3, status", [(0.0, "pass"), (42.0, "fail")])
async def test_box_hits_are_confirmed_on_the_exact_geometry(ctx, monkeypatch, exact_mm3,  # noqa: F811
                                                            status):
    _asm, ex = dt._export(ctx, "quad", dt.RomArgs(project_id="quad"))
    bodies = sorted(ex.map["bodies"])[:2]
    a_ids, b_ids = ex.map["bodies"][bodies[0]], ex.map["bodies"][bodies[1]]
    pose = {name: 0.1 for name in list(ex.map["joints"])[:2]}
    monkeypatch.setattr(run, "run_job", _fake_rom(bodies, pose))
    seen = {}

    async def fake_geometry(root, asm, res, want="both", **kw):
        seen["instances"] = {i.id for i in asm.instances}
        hits = ([{"a": a_ids[0], "b": b_ids[0], "a_id": a_ids[0], "b_id": b_ids[0],
                  "volume_mm3": exact_mm3}] if exact_mm3 else [])
        return geometry.GeometryResult(interferences=hits)
    monkeypatch.setattr(geometry, "run_geometry", fake_geometry)
    out = await dt.dyn_rom_sweep.handler(ctx, dt.RomArgs(project_id="quad", n=100))
    row = out.data["collisions"][0]
    assert seen["instances"] == set(a_ids) | set(b_ids)       # only the two bodies' parts
    assert row["exact_mm3"] == exact_mm3 and row["status"] == status
    assert ("exact geometry" in row["reason"]) and out.data["status"] == status
