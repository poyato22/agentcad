from types import SimpleNamespace

import pytest

import mises.runtime as runtime_mod
from mises.cad.tools import (CreateArgs, FacesArgs, PartArgs, mirror_faces, mirror_measure,
                             mirror_part_create)
from mises.gate import MAX_OUTPUT_BYTES
from mises.runtime import Runtime
from mises.sim.tools import result_summary

REC = {"sim_id": "sim_0123456789ab", "part_id": "beam", "status": "done", "warnings": [],
       "material": {"name": "PETG", "knockdown": 0.7},
       "solver": {"name": "CalculiX", "version": "2.23"},
       "mesh": {"coarse": {"size_mm": 5.0}, "fine": {"size_mm": 2.5}},
       "results": {"max_von_mises_mpa": 6.04, "max_displacement_mm": 1.74,
                   "safety_factor": 5.5, "safety_factor_incl_fixture": 4.1,
                   "max_von_mises_at_fixture_mpa": 8.1, "error_band": 0.007,
                   "peak_excludes": "nodes on the fixed face"}}


def test_summary_always_labels_guide_solver_mesh_material_band_and_fixture():
    s = result_summary(REC)
    for part in ("GUIDE ONLY", "CalculiX 2.23", "fine mesh 2.5 mm (coarse 5 mm)",
                 "PETG isotropic linear elastic, strength x0.7 FDM knock-down",
                 "error band 0.7%", "safety factor 5.50",
                 "peak excludes the fixed face; at fixture: 8.1 MPa",
                 "safety factor incl. fixture 4.10"):
        assert part in s, (part, s)


def test_summary_when_the_peak_could_not_exclude_the_fixture():
    rec = {**REC, "results": {**REC["results"], "peak_excludes": None}}
    s = result_summary(rec)
    assert "peak excludes" not in s and "peak includes the fixed face" in s
    zero = {**REC, "results": {**REC["results"], "safety_factor": None}}
    assert "safety factor n/a" in result_summary(zero)


def test_summary_of_a_failed_study():
    s = result_summary({"sim_id": "sim_x", "status": "failed", "error": "CalculiX (ccx) is "
                        "not installed", "hint": "install it with: conda install -c conda-forge calculix"})
    assert "guide only" in s and "conda install -c conda-forge calculix" in s


def test_cli_runtime_without_server_has_sim_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (None, "x"))
    names = {t.name for t in rt.registry.list()}
    assert {"sim_static_run", "sim_result_get", "sim_env_list", "mirror_faces"} <= names
    from mises.sim.tools import register_sim_tools
    register_sim_tools(rt.registry)  # the server's call stays a no-op


# 8 x 8 bumps on a plate: 6 + 64 * 5 = 326 faces
BUMPY = ('from build123d import *\nVARS={"n":8.0}\ndef build(v):\n'
         '    n = int(v["n"])\n'
         '    p = Box(100, 100, 5, align=(Align.MIN, Align.MIN, Align.MIN))\n'
         '    for i in range(n):\n'
         '        for j in range(n):\n'
         '            p += Pos(6 + 12 * i, 6 + 12 * j, 5) * Box(4, 4, 2, '
         'align=(Align.CENTER, Align.CENTER, Align.MIN))\n'
         '    return p\n')


@pytest.mark.slow
@pytest.mark.timeout(120)
async def test_many_face_part_measure_stays_inline_and_faces_page(tmp_path):
    ctx = SimpleNamespace(workdir=tmp_path)
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="bumpy", script=BUMPY))
    assert r.ok, r.summary
    assert len(r.model_dump_json().encode()) < MAX_OUTPUT_BYTES
    m = await mirror_measure.handler(ctx, PartArgs(part_id="bumpy"))
    assert m.data["face_count"] >= 300 and m.data["face_list_truncated"] is True
    assert len(m.data["face_list"]) == 40
    areas = [f["area"] for f in m.data["face_list"]]
    assert areas == sorted(areas, reverse=True) and areas[0] > 9000  # the plate faces first
    assert len(m.model_dump_json().encode()) < MAX_OUTPUT_BYTES
    total = m.data["face_count"]
    seen, offset = [], 0
    while offset is not None:
        page = await mirror_faces.handler(ctx, FacesArgs(part_id="bumpy", offset=offset))
        assert page.data["total"] == total and len(page.data["faces"]) <= 100
        assert len(page.model_dump_json().encode()) < MAX_OUTPUT_BYTES
        seen += [f["id"] for f in page.data["faces"]]
        offset = page.data["next_offset"]
    assert seen == [f"f{i}" for i in range(total)]
    tail = await mirror_faces.handler(ctx, FacesArgs(part_id="bumpy", offset=total + 5))
    assert tail.data["faces"] == [] and tail.data["next_offset"] is None
    with pytest.raises(Exception):
        FacesArgs(part_id="bumpy", limit=101)
