"""Build every mises_shapes recipe once, write an STL per recipe, and render them when the shared
renderer exists (Plan 8 G1's mises.cad.render.render_views); otherwise only the STLs.

    cd core && uv run python scripts/recipe_gallery.py <out_dir>

`examples()` is also what the recipe tests build, so the gallery and the tests cover the same
calls. Runs in-process (not in the sandbox): it only executes the library's own code.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path


def examples() -> dict:
    """recipe name -> zero-arg callable returning its example shape (a solid, or for
    enclosure the assembled base + lid compound)."""
    import build123d as bd

    from mises.cad import mises_shapes as ms

    grid = [[(x * 10.0, y * 10.0, 3 * math.sin(x * 0.8) + 2 * math.cos(y * 0.7))
             for x in range(6)] for y in range(5)]
    path = [(0, 0, 0), (30, 10, 20), (60, 0, 40)]
    ring = [[(10 * math.cos(a * math.tau / 8), 10 * math.sin(a * math.tau / 8), 0)
             for a in range(8)],
            [(5 * math.cos(a * math.tau / 8), 7 * math.sin(a * math.tau / 8), 20)
             for a in range(8)], (0, 0, 32)]

    def rounded_block():
        return bd.Solid.make_box(40, 30, 20)

    def variable():
        b = rounded_block()
        top = b.edges().filter_by(bd.Axis.X).sort_by(bd.Axis.Z)[-1]
        return ms.variable_fillet(b, top, 1.0, 6.0)

    def vents():
        plate = bd.Solid.make_box(60, 40, 3, bd.Plane.XY.move(bd.Location((-30, -20, 0))))
        return plate - ms.vent_slots((0, 0, 1.5), length=30, width=2.5, count=6, pitch=5,
                                     depth=6)

    return {
        "naca4": lambda: ms.smooth_loft([ms.airfoil_wire(ms.naca4("2412"), 100, (0, 0, 0)),
                                         ms.airfoil_wire(ms.naca4("2412"), 100, (0, 60, 0))]),
        "naca5": lambda: ms.smooth_loft([ms.airfoil_wire(ms.naca5("23012"), 100, (0, 0, 0)),
                                         ms.airfoil_wire(ms.naca5("23012"), 100, (0, 60, 0))]),
        "airfoil_from_points": lambda: ms.smooth_loft([
            ms.airfoil_wire(ms.airfoil_from_points([(x * 2, y * 2) for x, y in ms.naca4("0012")]),
                            80, (0, y, 0)) for y in (0, 40)]),
        "airfoil_wire": lambda: ms.smooth_loft([ms.airfoil_wire("4412", 120, (0, 0, 0)),
                                                ms.airfoil_wire("0010", 70, (30, 150, 10), -3)]),
        "wing": lambda: ms.wing(),
        "blended_wing_body": lambda: ms.blended_wing_body(),
        "fuselage": lambda: ms.fuselage(),
        "sweep_profile": lambda: ms.sweep_profile(path, [(-5, -2), (5, -2), (5, 2), (-5, 2)],
                                                  twist_deg=90),
        "smooth_loft": lambda: ms.smooth_loft(ring),
        "spline_surface": lambda: ms.thicken(ms.spline_surface(grid), 2.0),
        "thicken": lambda: ms.thicken(ms.spline_surface(grid), 3.0, both=True),
        "blend_edges": lambda: ms.blend_edges(rounded_block(), 4.0),
        "variable_fillet": variable,
        "enclosure": lambda: ms.enclosure(ribs=2, snap_fits=2).compound(explode=15),
        "standoffs": lambda: ms.standoffs([(0, 0), (58, 0), (0, 49), (58, 49)], 6.0),
        "vent_slots": vents,
        "cable_path": lambda: ms.cable_path([(0, 0, 0), (20, 20, 10), (40, 0, 20), (60, 10, 0)]),
        "check_solid": lambda: ms.check_solid(rounded_block()),
        # Plan 9 G-B: skin over skeleton (a thigh-like limb) and archetype massing
        "squircle": lambda: ms.station_loft([(0, 30, 20, 3.0), (60, 24, 18, 3.0)]),
        "station_loft": lambda: ms.station_loft(THIGH),
        "skin_shell": lambda: ms.skin_shell(THIGH, wall=2.5),
        "tapered_capsule": lambda: ms.tapered_capsule(20, 12, start=(0, 0, 0), end=(40, 30, 90)),
        "joint_cowl": lambda: ms.joint_cowl(34, 50, rom_deg=120),
        "panel_split": lambda: bd.Compound(list(ms.panel_split(
            ms.skin_shell(THIGH, wall=2.5), bd.Plane.XY.offset(-110), gap=0.8))),
        "clamshell": lambda: bd.Compound(list(ms.clamshell(ms.skin_shell(THIGH, wall=2.5)))),
        "cover_for": lambda: ms.cover_for(THIGH, components=[((0, 2, -60), (40, 20, 40))],
                                          bulkheads=[-90]),
        "trust": lambda: ms.trust(ms.skin_shell(THIGH, wall=2.5), "thigh shell"),
        "humanoid": lambda: ms.humanoid(shell=False).compound(),
        "quadruped": lambda: ms.quadruped(shell=False).compound(),
        "car_body": lambda: ms.car_body(shell=False).compound(),
    }


# a thigh shell's stations (z, half width, half depth, n, dx, dy): hip at z=0, knee below
THIGH = [(-10, 38, 34, 2.2), (-60, 42, 40, 2.6, 0, -4), (-120, 36, 34, 2.6, 0, -3),
         (-180, 30, 28, 2.4), (-212, 28, 26, 2.2)]


def main(out_dir: str) -> int:
    import build123d as bd

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        from mises.cad.render import load_stl, render_views  # Plan 8 G1's rasterizer
    except ImportError:
        render_views = None
    for name, make in examples().items():
        shape = make()
        bd.export_stl(shape, str(out / f"{name}.stl"), tolerance=0.05, angular_tolerance=0.1)
        if render_views is not None:   # G1's renderer takes triangles (here: the STL just written)
            tris = load_stl((out / f"{name}.stl").read_bytes())
            for view, png in render_views(tris, views=("iso", "top", "front", "side"), size=512).items():
                (out / f"{name}_{view}.png").write_bytes(png)
        print(f"{name}: volume {shape.volume:.0f} mm3")
    if render_views is None:
        print("render_views unavailable (G1 not merged): wrote STLs only")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "recipe_gallery"))
