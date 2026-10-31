"""FW-core I-5: a part an agent accepts as NEW (topopt_accept mode=new) is auto-placed in the
build's assembly, like mirror_part_create — the generative seam D10 missed."""
from types import SimpleNamespace

import pytest

import mises.cad.tools as cad_tools
from mises.store import Store
from mises.topopt import accept

pytestmark = pytest.mark.slow

SRC = "from build123d import *\ndef build(v):\n    return Box(12, 8, 6)\n"


async def test_new_part_from_a_variant_is_auto_placed(tmp_path, monkeypatch):
    store = Store(":memory:")
    project = store.ensure_default_project().id
    run = store.create_run("sim", "fake", "m", "optimize", project_id=project)
    ctx = SimpleNamespace(workdir=tmp_path, store=store, run_id=run.id,
                          events=SimpleNamespace(publish=lambda *a, **k: None), services={})
    placements = []

    async def fake_place(c, pid, d):
        placements.append(pid)
        return {"project_id": project, "instance": {"id": "inst1", "part_id": pid,
                                                    "name": pid, "fixed": True}}

    monkeypatch.setattr(cad_tools, "_auto_place", fake_place)
    out = await accept._new_part(ctx, "block_generative", SRC, b"solid x\nendsolid x\n")
    assert out.ok, out.summary
    assert placements == ["block_generative"]                    # the agent's part was placed
    assert out.data["placed"]["instance"]["id"] == "inst1"
    assert "placed in the assembly" in out.summary
