import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from mises.cad.tools import CreateArgs, ExportArgs, mirror_export, mirror_part_create

pytestmark = pytest.mark.slow

BOX = 'from build123d import *\nVARS={"length":40.0}\ndef build(v):\n    return Box(v["length"],20,10)\n'


async def test_export_glb_is_self_contained_binary(tmp_path):
    ctx = SimpleNamespace(workdir=tmp_path)
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="bracket", script=BOX))
    assert r.ok, r.summary
    r = await mirror_export.handler(ctx, ExportArgs(part_id="bracket", format="glb"))
    path = Path(r.handles[0])
    data = path.read_bytes()
    assert data[:4] == b"glTF"
    assert struct.unpack("<I", data[8:12])[0] == len(data)
    jlen, jtype = struct.unpack("<I4s", data[12:20])
    assert jtype == b"JSON"
    gltf = json.loads(data[20:20 + jlen])
    assert not [b for b in gltf.get("buffers", []) if "uri" in b]
    assert not [i for i in gltf.get("images", []) if "uri" in i]
    assert list(path.parent.glob("*.bin")) == []
