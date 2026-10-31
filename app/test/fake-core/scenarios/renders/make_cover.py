"""Render a sample build cover (two parts, three instances) to look at."""
import asyncio
import sys
import tempfile
from pathlib import Path

from mises import platform
from mises.cad import thumbs
from mises.cad.runner import run_build

BODY = 'from build123d import *\ndef build(v):\n    return fillet(Box(120, 60, 30).edges(), 8)\n'
LEG = 'from build123d import *\ndef build(v):\n    return Cylinder(8, 70)\n'


def shift(x, y, z):
    return [[1, 0, 0, x], [0, 1, 0, y], [0, 0, 1, z], [0, 0, 0, 1]]


async def main(out: Path) -> None:
    home = Path(tempfile.mkdtemp()) / "home"
    platform.register_sandbox_home(home)
    for pid, src in (("body", BODY), ("leg", LEG)):
        d = home / "parts" / pid
        d.mkdir(parents=True)
        (d / "part.py").write_text(src)
        r = await run_build(d, {})
        assert r.ok, r.error
    placed = [("body", 1, shift(0, 0, 50), {"color": "#c9ccd2"})]
    for x in (-45, 45):
        for y in (-22, 22):
            placed.append(("leg", 1, shift(x, y, 0), {"color": "#3a3f47"}))
    png = await thumbs.project_cover(home, "demo", placed)
    out.write_bytes(png)
    print(out, len(png))


asyncio.run(main(Path(sys.argv[1])))
