"""Sandboxed thumbnail worker: reads <part_dir>/model.stl, writes <out_dir>/thumb.png.

Run as: python -I thumb_worker.py <part_dir> <out_dir> <size>

Runs under the build sandbox profile (platform.sandboxed_command): no network, no new processes,
writes only <out_dir>, and the Mises home is unreadable except the parts tree (geometry, never
secrets), which is where model.stl lives. The only import is mises.cad.thumb_render, which is
stdlib-only and never touches the home.
"""
import sys
from pathlib import Path

from mises.cad.thumb_render import render_stl_png


def main(argv: list[str]) -> int:
    part_dir, out_dir, size = Path(argv[1]), Path(argv[2]), int(argv[3])
    stl = (part_dir / "model.stl").read_bytes()
    png = render_stl_png(stl, size)
    (out_dir / "thumb.png").write_bytes(png)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
