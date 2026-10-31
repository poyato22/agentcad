"""Plan 9 B2 (D8): the worker weighs a build with the part's own material, and measures the
volume with OCCT's precise BRepGProp integration (a loft is checked against its analytic
volume)."""
import json
import math
from types import SimpleNamespace

import pytest

from mises import projects
from mises.cad.tools import CreateArgs, SetVarsArgs, mirror_part_create, mirror_part_set_vars
from mises.events import EventBus
from mises.profile import load_profile, save_profile
from mises.store import Store

pytestmark = pytest.mark.slow

# a loft between two circles is a frustum: V = pi h / 3 (R^2 + R r + r^2)
FRUSTUM = '''from build123d import *
VARS = {"r": 10.0}
def build(v):
    with BuildPart() as p:
        with BuildSketch():
            Circle(20)
        with BuildSketch(Plane.XY.offset(30)):
            Circle(v["r"])
        loft()
    return p.part
'''


def _frustum(R, r, h):
    return math.pi * h / 3 * (R * R + R * r + r * r)


@pytest.fixture
def ctx(tmp_path):
    store = Store(":memory:")
    pid = store.create_project("B").id
    run = store.create_run("designer", "fake", "m", "build", project_id=pid)
    return SimpleNamespace(workdir=tmp_path, store=store, run_id=run.id, events=EventBus(),
                           user="local", call_id="c1")


async def test_a_lofted_volume_matches_the_frustum(ctx):
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="cone", script=FRUSTUM))
    assert r.ok, r.summary
    m = json.loads((ctx.workdir / "parts" / "cone" / "metrics.json").read_text())
    exact = _frustum(20, 10, 30)
    assert m["volume_mm3"] == pytest.approx(exact, rel=1e-6)
    assert m["mass_g"] == pytest.approx(exact / 1000 * 1.24, rel=1e-6)


async def test_the_build_uses_the_parts_material(ctx):
    prof = load_profile(ctx.workdir)
    prof.default_material = "PETG"
    save_profile(ctx.workdir, prof)
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="cone", script=FRUSTUM))
    assert r.ok, r.summary
    d = ctx.workdir / "parts" / "cone"
    assert json.loads((d / "metrics.json").read_text())["mass_g"] == \
        pytest.approx(_frustum(20, 10, 30) / 1000 * 1.27, rel=1e-6)
    # the part's own material wins on the next build, and the tree says the same
    projects.register_built_part(ctx.store, ctx.run_id, "cone")
    projects.patch_part(ctx.store, ctx.workdir, "cone", material="ABS")
    r = await mirror_part_set_vars.handler(ctx, SetVarsArgs(part_id="cone", vars={"r": 5.0}))
    assert r.ok, r.summary
    disk = json.loads((d / "metrics.json").read_text())["mass_g"]
    assert disk == pytest.approx(_frustum(20, 5, 30) / 1000 * 1.04, rel=1e-6)
    tree = projects.part_view(ctx.store, ctx.workdir, ctx.store.get_part("cone"))
    assert tree["mass_g"] == pytest.approx(disk, rel=1e-4) and tree["mass_source"] == "built"
