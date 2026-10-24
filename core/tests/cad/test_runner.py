import os
import time
from pathlib import Path

import pytest

from mises.cad.runner import run_build

pytestmark = pytest.mark.slow

BOX = 'from build123d import *\nVARS={"length":40.0}\ndef build(v):\n    return Box(v["length"],20,10)\n'
PLATE = '''from build123d import *
VARS = {}
def build(v):
    with BuildPart() as p:
        Box(60, 30, 5)
        with Locations((10, 0, 0), (-10, 0, 0)):
            Hole(2.1)
    return p.part
'''


def mk(tmp_path: Path, script: str) -> Path:
    d = tmp_path / "p"
    d.mkdir()
    (d / "part.py").write_text(script)
    return d


async def test_box_metrics(tmp_path):
    r = await run_build(mk(tmp_path, BOX), {"length": 40})
    assert r.ok, r.error
    m = r.metrics
    assert m["volume_mm3"] == pytest.approx(8000)
    assert m["mass_g"] == pytest.approx(9.92)
    assert m["bbox"]["size"] == pytest.approx([40, 20, 10])
    assert (tmp_path / "p" / "model.glb").exists()


async def test_holes_found(tmp_path):
    r = await run_build(mk(tmp_path, PLATE), {})
    assert r.ok, r.error
    holes = r.metrics["holes"]
    assert len(holes) == 2
    assert all(h["diameter_mm"] == pytest.approx(4.2) for h in holes)
    assert all(h["depth_mm"] == pytest.approx(5) for h in holes)


async def test_timeout_killed(tmp_path):
    d = mk(tmp_path, "def build(v):\n    while True: pass\n")
    t = time.monotonic()
    r = await run_build(d, {}, timeout_s=2)
    assert not r.ok and "timeout" in r.error
    assert time.monotonic() - t < 5


async def test_network_denied(tmp_path):
    d = mk(tmp_path, "import socket\ndef build(v):\n    socket.create_connection(('1.1.1.1', 80), 3)\n")
    r = await run_build(d, {})
    assert not r.ok


async def test_write_outside_denied(tmp_path):
    target = Path.home() / "mises_escape.txt"
    target.unlink(missing_ok=True)
    d = mk(tmp_path, f"def build(v):\n    open({str(target)!r}, 'w').write('x')\n")
    r = await run_build(d, {})
    assert not r.ok and not target.exists()
    assert "Operation not permitted" in r.error or "Permission" in r.error


async def test_script_error_reported(tmp_path):
    r = await run_build(mk(tmp_path, "def build(v):\n    raise ValueError('boom')\n"), {})
    assert not r.ok and "boom" in r.error
