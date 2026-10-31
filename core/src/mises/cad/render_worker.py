"""Sandboxed render worker. Self-contained: stdlib + numpy + Pillow + the sibling render.py
(which imports nothing from mises).

Run as: python -I render_worker.py <job_dir>     (MISES_NONCE in the environment)

<job_dir>/job.json lists the meshes (STL files copied into the job dir by the host), the views
to render and the silhouette masks to compute. Outputs go to <job_dir>/out/; the last file
written is result.json, which carries the nonce the host checks. A mesh the renderer refuses
(too big, too complex, nothing finite to draw) is reported as {"error": ...}, not a crash.

The host runs this under sandbox-exec (writes only in the job dir, no network, no fork) with a
wall-clock timeout and an RSS watchdog (sim/proc.run_sandboxed), so even a hostile mesh can
only cost this process, never the Mises core.
"""
from __future__ import annotations

import json
import os
import resource
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I does not add the script dir

import numpy as np  # noqa: E402

import render  # noqa: E402  (the sibling cad/render.py)


def _peak_rss_mb() -> float:
    # ru_maxrss is bytes on macOS (kilobytes on Linux)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


def _section(spec):
    return (tuple(spec[0]), tuple(spec[1])) if spec else None


def _cameras(spec) -> dict | None:
    """Extra named views from the host: {name: [forward, up]} (render.py checks the vectors)."""
    if not spec:
        return None
    return {str(k): (tuple(v[0]), tuple(v[1])) for k, v in dict(spec).items()}


def _transform(m) -> np.ndarray | None:
    """The mesh's 4x4 world transform (data from the host), re-checked here: finite, bounded,
    affine. None means the part's own frame."""
    if m is None:
        return None
    a = np.asarray(m, dtype=np.float64)
    if (a.shape != (4, 4) or not np.isfinite(a).all() or (np.abs(a) > 1e6).any()
            or not np.allclose(a[3], [0.0, 0.0, 0.0, 1.0], atol=1e-6)):
        raise render.RenderError("an instance transform is not a finite 4x4 affine matrix")
    return a


def _unit(v, default: float) -> float:
    """A 0..1 CMF number from the host (roughness, metalness); anything else is the default."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, x)) if np.isfinite(x) else default


def run(job_dir: Path, nonce: str) -> dict:
    job = json.loads((job_dir / "job.json").read_text())
    if job.get("max_layers"):                  # host-set (tests); never from the agent
        render.MAX_LAYERS = float(job["max_layers"])
    out_dir = job_dir / "out"
    out_dir.mkdir(exist_ok=True)
    meshes, dropped, total = [], 0, 0
    loaded: dict[str, np.ndarray] = {}         # one parse per STL, shared by its instances
    for m in job["meshes"]:
        if m["file"] not in loaded:
            loaded[m["file"]] = render.load_stl((job_dir / m["file"]).read_bytes())
        tris = loaded[m["file"]]
        total += len(tris)
        if total > render.MAX_TRIANGLES:
            raise render.RenderError(f"the meshes have over {render.MAX_TRIANGLES} triangles, "
                                     "over the render limit")
        T = _transform(m.get("transform"))
        if T is not None:
            with np.errstate(invalid="ignore", over="ignore"):
                tris = (tris @ T[:3, :3].T + T[:3, 3]).astype(np.float32)
        tris, n_bad = render.clean_triangles(tris)
        dropped += n_bad
        meshes.append(render.Mesh(tris, color=tuple(m.get("color") or render._DEFAULT_COLOR),
                                  opacity=float(m.get("opacity", 1.0)), name=m.get("name", ""),
                                  roughness=_unit(m.get("roughness"), 0.9),
                                  metalness=_unit(m.get("metalness"), 0.0)))
    if not any(len(m.tris) for m in meshes):
        raise render.RenderError("nothing finite to render (every triangle was invalid)")
    pngs: dict[str, dict[str, str]] = {}
    cameras: dict[str, dict] = {}              # key -> view -> camera_info (actual size)
    for r in job.get("renders", []):
        cams: dict = {}
        imgs = render.render_views(meshes, views=tuple(r["views"]), size=int(r["size"]),
                                   cameras_out=cams,
                                   section=_section(r.get("section")),
                                   explode=float(r.get("explode", 0.0)),
                                   cameras=_cameras(r.get("cameras")),
                                   transparent=bool(r.get("transparent")),
                                   style=str(r.get("style") or "shaded"),
                                   perspective=bool(r.get("perspective")),
                                   shadow=bool(r.get("shadow", True)),
                                   background=str(r.get("background") or "flat"),
                                   figure=bool(r.get("figure")))
        pngs[r["key"]] = {}
        cameras[r["key"]] = cams
        for view, data in imgs.items():
            name = f"{r['key']}_{view}.png"
            (out_dir / name).write_bytes(data)
            pngs[r["key"]][view] = name
    masks = {}
    for m in job.get("masks", []):
        mask = render.render_mask(meshes, m["view"], size=int(m.get("size", 512)),
                                  cameras=_cameras(m.get("cameras")),
                                  section=_section(m.get("section")))
        masks[m["key"]] = np.packbits(mask.astype(np.uint8))
    shapes = {k: [int(m.get("size", 512))] * 2 for m in job.get("masks", []) for k in [m["key"]]}
    if masks:
        np.savez(out_dir / "masks.npz", **masks)
    return {"nonce": nonce, "pngs": pngs, "cameras": cameras, "masks": shapes,
            "dropped": dropped,
            "triangles": total, "peak_rss_mb": round(_peak_rss_mb(), 1)}


def main(argv: list[str]) -> int:
    job_dir = Path(argv[1]).resolve()
    nonce = os.environ.pop("MISES_NONCE", "")
    if not nonce:
        raise RuntimeError("missing render nonce")
    try:
        result = run(job_dir, nonce)
    except render.RenderError as e:
        result = {"nonce": nonce, "error": str(e), "peak_rss_mb": round(_peak_rss_mb(), 1)}
    tmp = job_dir / "out" / "new_result.json"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text(json.dumps(result))
    os.replace(tmp, job_dir / "result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
