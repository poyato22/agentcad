"""P11 F4 parity: what the app draws is what the core has, for every instance.

A build with a fixed instance, a joint-placed one, an auto-placed one and a moved one, whose parts
are built the way agents build them (several end in a located shape: `Pos(...) * (Rot(...) * x)`,
the ornicopter's gears and rods, the rocket's coupler). For each instance:
- the pose the app draws is GET /projects/{id}/assembly's transform; every core consumer
  (assembly_get, the renders' solved instances, the health report) uses that same pose;
- the box the app draws (the served model.glb, read as app/src/ui/viewer/loadPart.ts reads it,
  through that pose) equals the core's box (the part's measured box through the pose);
- the health report's extents include every box the app draws.
MISES_DUMP_PARITY=<dir> also writes the payload, the GLBs and the core's boxes for the app's own
parity test (app/src/ui/assembly/parity.test.ts reads core/tests/fixtures/ui/p11/parity).
"""
import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mises.runtime as runtime_mod
from mises.assembly.pose import world_poses
from mises.assembly.solve import transform_point
from mises.cad.glb_frame import cad_bbox
from mises.runtime import Runtime
from mises.server.app import create_app

pytestmark = pytest.mark.slow
TOKEN = "parity-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

PARTS = {
    # the grounded base: its own frame
    "base_plate": "from build123d import *\nVARS={}\ndef build(v):\n    return Box(120, 80, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))\n",
    # joint-placed, built located and turned (the ornicopter's crank rods)
    "crank_rod": "from build123d import *\nVARS={}\ndef build(v):\n    return Pos(20, -15, 40) * (Rot(X=90) * Cylinder(4, 60))\n",
    # auto-placed, located (the ornicopter's gears)
    "gear_pair": "from build123d import *\nVARS={}\ndef build(v):\n    return Pos(-30, 10, 6) * (Rot(X=-90) * Cylinder(12, 6))\n",
    # moved by the agent; built in its own frame
    "side_block": "from build123d import *\nVARS={}\ndef build(v):\n    return Box(20, 30, 15)\n",
}


def _box(lo, hi, t):
    pts = [transform_point(t, [x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
           for z in (lo[2], hi[2])]
    return ([min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)])


@pytest.fixture
def rt(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(home))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return Runtime(home, provider_resolver=lambda ref: (_ for _ in ()).throw(AssertionError(ref)))


async def test_the_app_draws_every_instance_where_the_core_has_it(rt):
    pid = rt.active_project_id()
    rid = rt.create_external_run("engineer", provider="claude-code", model="sonnet",
                                 project_id=pid)
    wd = rt.workdir(rid)
    n = 0

    async def call(name, inp):
        nonlocal n
        n += 1
        out = await rt.gate.call(rid, name, inp, call_id=f"p{n}", workdir=wd)
        assert out.ok, out.summary
        return out

    for part, script in PARTS.items():       # each lands in the assembly by itself (D10)
        await call("mirror_part_create", {"part_id": part, "script": script})
    asm = rt.assembly_store.get(pid)
    by_part = {i.part_id: i for i in asm.instances}
    assert set(by_part) == set(PARTS) and by_part["base_plate"].fixed
    # the crank rod rides a revolute joint on the base, turned 30 degrees
    await call("assembly_add_joint", {
        "kind": "revolute", "value": 30.0,
        "a": {"instance_id": by_part["base_plate"].id,
              "frame": {"origin": [0, 0, 10], "axis": [0, 0, 1]}},
        "b": {"instance_id": by_part["crank_rod"].id,
              "frame": {"origin": [0, 0, 0], "axis": [0, 0, 1]}}})
    # the side block is moved and turned by the agent
    await call("assembly_move", {"instance_id": by_part["side_block"].id,
                                 "translate": [90, 40, 0],
                                 "rotate": {"axis": [0, 0, 1], "angle_deg": 45}})

    with TestClient(create_app(rt, TOKEN)) as client:
        payload = client.get(f"/projects/{pid}/assembly", headers=AUTH).json()
        glbs = {p: client.get(f"/parts/{p}/model.glb", headers=AUTH).content for p in PARTS}
    drawn_pose = {i["id"]: i["transform"] for i in payload["instances"]}

    # every core consumer uses the same pose
    asm = rt.assembly_store.get(pid)
    poses, err = world_poses(asm, rt.home / "parts")
    assert err is None and poses == drawn_pose
    got = (await call("assembly_get", {})).data
    assert {i["id"]: i["world_transform"] for i in got["instances"]} == drawn_pose
    from types import SimpleNamespace
    from mises.cad.views import _solved_instances
    ctx = SimpleNamespace(services={"assembly": rt.assembly_store}, store=rt.store, run_id=rid,
                          workdir=wd)
    rendered = _solved_instances(ctx)
    assert sorted(map(json.dumps, (t for _p, t in rendered))) == sorted(
        map(json.dumps, drawn_pose.values()))

    # the box the app draws is the core's box, for every instance
    boxes = {}
    for inst in asm.instances:
        m = json.loads((rt.home / "parts" / inst.part_id / "metrics.json").read_text())["bbox"]
        core = _box(m["min"], m["max"], drawn_pose[inst.id])
        lo, hi = cad_bbox(glbs[inst.part_id])
        drawn = _box(lo, hi, drawn_pose[inst.id])
        assert drawn[0] == pytest.approx(core[0], abs=0.6), inst.part_id
        assert drawn[1] == pytest.approx(core[1], abs=0.6), inst.part_id
        boxes[inst.id] = {"part_id": inst.part_id, "min": core[0], "max": core[1]}

    # the health report's extents include every instance the app draws
    health = (await call("assembly_health", {})).data["assembly_health"]
    ext = health["extents"]
    for b in boxes.values():
        assert all(ext["min"][k] <= b["min"][k] + 0.6 and b["max"][k] - 0.6 <= ext["max"][k]
                   for k in range(3)), b

    out = os.environ.get("MISES_DUMP_PARITY")
    if out:
        d = Path(out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "assembly.json").write_text(json.dumps(payload, indent=1))
        (d / "core_boxes.json").write_text(json.dumps(boxes, indent=1))
        for p, data in glbs.items():
            (d / f"{p}.glb").write_bytes(data)
        shutil.copy(rt.home / "parts" / "crank_rod" / "metrics.json", d / "crank_rod.metrics.json")
