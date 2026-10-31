"""The worker pipeline on a small part, verified with gmsh + CalculiX (real solver: slow)."""
import asyncio
import json

import pytest

from mises.sim.ccx import find_ccx
from mises.sim.materials import get_material
from mises.topopt import verify, worker

pytestmark = pytest.mark.slow
needs_ccx = pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")

L, W, H = 24.0, 16.0, 8.0


def _job(tmp_path, bore=False, **over):
    from build123d import Align, Box, Cylinder, Pos, export_step
    part = Box(L, W, H, align=(Align.MIN,) * 3)
    if bore:
        part -= Pos(18, 8, 4) * Cylinder(2.0, 20)
    export_step(part, str(tmp_path / "model.step"))
    keep = ([{"kind": "cylinder", "cylinder": {"center": [18, 8, 4], "axis": [0, 0, 1],
                                                "radius": 4.0, "length": 20}}] if bore else [])
    spec = {"design_space": "part", "bbox": {"min": [0, 0, 0], "max": [L, W, H]}, "h": 1.0,
            "filter_radius": 2.0, "material": {"E": 2300.0, "nu": 0.35},
            "volume_fraction": 0.4, "max_iter": 60, "fixtures": [{"face": "x-min"}],
            "keep": keep,
            "load_cases": [{"name": "end", "weight": 1.0, "loads": [
                {"face": "x-max", "newtons": 100.0, "direction": [0, 0, -1]}]}], **over}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    return tmp_path


@pytest.mark.timeout(600)
@needs_ccx
def test_small_part_end_to_end_verifies_within_five_percent(tmp_path):
    from build123d import GeomType, import_step
    job = _job(tmp_path)
    seen = []
    rep = worker.run(job, progress=seen.append)
    assert rep["converged"] and rep["faces"] > 100
    assert 0.3 < rep["final_vf"] < 0.5 and rep["islands_dropped_pct"] < 1
    assert seen[0]["phase"] == "voxelize" and seen[-1]["phase"] == "done"
    assert any(s.get("preview") for s in seen) and (job / "preview.glb").stat().st_size > 1000
    for name in ("part.step", "part.stl", "result.glb", "density.npy", "bc.json"):
        assert (job / name).is_file()
    part = import_step(str(job / "part.step"))
    assert part.is_valid and len(part.solids()) == 1
    # the mounting face is exact and flat at x = 0
    assert any(f.geom_type == GeomType.PLANE and abs(f.center().X) < 1e-9 and f.area > 20
               for f in part.faces())
    v = asyncio.run(verify.verify(job, get_material("PLA"), find_ccx(), size_mm=0.8))
    err = abs(v["compliance"] - rep["compliance_bw"]) / rep["compliance_bw"]
    assert err < 0.05, (v["compliance"], rep["compliance_bw"])
    assert v["max_von_mises_mpa"] > 0 and v["safety_factor"] > 0
    assert v["mesher"].startswith("gmsh OCC")
    assert (job / "stress.glb").is_file()


@pytest.mark.timeout(300)
def test_keep_features_survive_as_exact_geometry(tmp_path):
    from build123d import GeomType, import_step
    job = _job(tmp_path, bore=True)
    worker.run(job)
    part = import_step(str(job / "part.step"))
    assert part.is_valid and len(part.solids()) == 1
    bores = [f for f in part.faces() if f.geom_type == GeomType.CYLINDER]
    assert bores and all(abs(f.radius - 2.0) < 1e-6 for f in bores)
    assert sum(f.area for f in bores) == pytest.approx(2 * 3.14159265 * 2 * H, rel=0.01)


@pytest.mark.timeout(300)
def test_infeasible_keep_regions_fail_with_a_reason(tmp_path):
    job = _job(tmp_path, keep=[{"kind": "box", "box": [0, 0, 0, L, W, H]}])
    with pytest.raises(worker.TopoptError, match="keep regions"):
        worker.run(job)
