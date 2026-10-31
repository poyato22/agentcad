"""Run renders in the sandboxed render worker (cad/render_worker.py), never in the core.

The STL a render reads is the output of a sandboxed (possibly hostile) build, so the
rasterizer is treated like the build itself: it runs as a separate process under sandbox-exec
(writes only in its job dir, no network, no fork), with a wall-clock timeout and an RSS
watchdog, and awaiting it never blocks the event loop. Inputs are capped before they are
copied in (MAX_STL_BYTES per part), and the worker caps triangles, drops non-finite and outlier
vertices, and refuses views over its layer budget. An assembly instance's solved world
transform travels to the worker as data (a 4x4 number matrix, validated on both sides) and is
applied there, so the core never rasterises or transforms a mesh itself.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..sim.proc import run_sandboxed
from ..sim.types import SimError
from ..types import ErrorCode, MisesError
from . import safe_io
from .render import MAX_STL_BYTES

WORKER = Path(__file__).resolve().parent / "render_worker.py"
RENDER_TIMEOUT_S = float(os.environ.get("MISES_RENDER_TIMEOUT_S", "60"))
RENDER_MEMORY_MB = float(os.environ.get("MISES_RENDER_MEMORY_MB", "1024"))
MAX_PNG_BYTES = 16 * 1024 * 1024
# numpy's BLAS would otherwise reserve per-core thread buffers (hundreds of MB on a 15-core
# Mac); the rasterizer is single-threaded element-wise work and gains nothing from them
_ONE_THREAD = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
               "VECLIB_MAXIMUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
_PNG = b"\x89PNG\r\n\x1a\n"
MAX_MESHES = 256                       # meshes (instances) per job; a part's STL is copied once
MAX_TRANSFORM_ABS = 1e6                # mm: any matrix entry beyond this is refused


@dataclass
class MeshIn:
    part_dir: Path
    name: str
    color: tuple[float, float, float]
    opacity: float = 1.0
    # the instance's world transform (4x4, row-major, mm), or None for the part's own frame
    transform: list[list[float]] | None = None
    # CMF from the part's appearance (shaded style): 0..1
    roughness: float = 0.9
    metalness: float = 0.0


def check_transform(m) -> list[list[float]] | None:
    """A 4x4 affine matrix of finite, bounded numbers (bottom row 0 0 0 1), as plain floats;
    None stays None. Anything else is refused: it is data, never evaluated."""
    if m is None:
        return None
    try:
        a = np.asarray(m, dtype=np.float64)
    except (TypeError, ValueError):
        raise MisesError(ErrorCode.INVALID_INPUT, "an instance transform is not numeric") from None
    if a.shape != (4, 4) or not np.isfinite(a).all() or (np.abs(a) > MAX_TRANSFORM_ABS).any():
        raise MisesError(ErrorCode.INVALID_INPUT, "an instance transform is not a finite 4x4 "
                         "matrix", hint="fix the assembly instance's transform")
    if not np.allclose(a[3], [0.0, 0.0, 0.0, 1.0], atol=1e-6):
        raise MisesError(ErrorCode.INVALID_INPUT, "an instance transform is not affine")
    return a.tolist()


@dataclass
class RenderOut:
    pngs: dict[str, dict[str, bytes]] = field(default_factory=dict)   # key -> view -> PNG
    masks: dict[str, np.ndarray] = field(default_factory=dict)         # key -> bool HxW
    dropped: int = 0                                                   # invalid triangles
    triangles: int = 0
    peak_rss_mb: float = 0.0


def _read_stl(part_dir: Path, name: str) -> bytes:
    """The part's model.stl, size-capped BEFORE it is read (a link or special file is refused
    by safe_io; a file swapped after the size check is caught by the length check)."""
    try:
        st = os.lstat(Path(part_dir) / "model.stl")
    except OSError:
        raise MisesError(ErrorCode.NOT_FOUND, f"{name}: model.stl missing; rebuild it") from None
    if st.st_size > MAX_STL_BYTES:
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"{name}: its mesh is {st.st_size / 2**20:.0f} MB, over the "
                         f"{MAX_STL_BYTES // 2**20} MB render limit",
                         hint="simplify the part (fewer, larger faces) before rendering it")
    data = safe_io.read_bytes(Path(part_dir), "model.stl")
    if data is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"{name}: model.stl missing; rebuild it")
    if len(data) > MAX_STL_BYTES:
        raise MisesError(ErrorCode.INVALID_INPUT, f"{name}: its mesh is over the render limit")
    return data


async def run_render_job(jobs_root: Path, meshes: list[MeshIn], *, renders: list[dict] = (),
                         masks: list[dict] = (), timeout_s: float | None = None,
                         memory_mb: float | None = None,
                         max_layers: float | None = None) -> RenderOut:
    """Render `renders` ({key, views, size, section?, explode?, transparent?, style?,
    perspective?, shadow?, background?, figure?}; see render.render_views) and compute
    `masks` ({key, view, size, section?}) of `meshes` in the sandboxed worker. `max_layers`
    overrides the worker's complexity budget (tests only; tools never pass it)."""
    if len(meshes) > MAX_MESHES:
        raise MisesError(ErrorCode.INVALID_INPUT, f"{len(meshes)} meshes to render, over the "
                         f"{MAX_MESHES} limit", hint="render fewer parts or a subset")
    jobs_root = Path(jobs_root)
    jobs_root.mkdir(parents=True, exist_ok=True)
    job_dir = jobs_root / f"job_{secrets.token_hex(8)}"
    job_dir.mkdir()
    try:
        spec = {"meshes": [], "renders": list(renders), "masks": list(masks),
                "max_layers": max_layers}
        files: dict[str, str] = {}             # part dir -> its one copy in the job dir
        for m in meshes:
            key = str(Path(m.part_dir))
            fname = files.get(key)
            if fname is None:
                fname = files[key] = f"m{len(files)}.stl"
                safe_io.write_bytes(job_dir, fname, _read_stl(m.part_dir, m.name))
            spec["meshes"].append({"file": fname, "name": m.name, "color": list(m.color),
                                   "opacity": m.opacity, "roughness": float(m.roughness),
                                   "metalness": float(m.metalness),
                                   "transform": check_transform(m.transform)})
        safe_io.write_text(job_dir, "job.json", json.dumps(spec))
        nonce = secrets.token_hex(16)
        try:
            code, _out, err = await run_sandboxed(
                [sys.executable, "-I", str(WORKER), str(job_dir)], job_dir,
                timeout_s=timeout_s or RENDER_TIMEOUT_S, what="the render",
                env={"MISES_NONCE": nonce, **_ONE_THREAD},
                memory_limit_mb=memory_mb or RENDER_MEMORY_MB)
        except SimError as e:
            raise MisesError(ErrorCode.TOOL_FAILED, f"render stopped: {e}",
                             hint="the model is too large or complex to render; simplify it "
                                  "or render fewer parts") from None
        if code != 0:
            raise MisesError(ErrorCode.TOOL_FAILED,
                             f"the render worker failed: {err.strip()[-400:] or code}")
        return _collect(job_dir, nonce)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _collect(job_dir: Path, nonce: str) -> RenderOut:
    try:
        res = json.loads(safe_io.read_bytes(job_dir, "result.json") or b"null")
    except ValueError:
        res = None
    if not isinstance(res, dict) or res.get("nonce") != nonce:
        raise MisesError(ErrorCode.TOOL_FAILED, "the render did not complete (no valid result)")
    if res.get("error"):
        raise MisesError(ErrorCode.INVALID_INPUT, f"cannot render: {str(res['error'])[:300]}",
                         hint="simplify the model, render fewer parts, or use a section")
    out = RenderOut(dropped=int(res.get("dropped") or 0),
                    triangles=int(res.get("triangles") or 0),
                    peak_rss_mb=float(res.get("peak_rss_mb") or 0.0))
    out_dir = job_dir / "out"
    for key, views in (res.get("pngs") or {}).items():
        out.pngs[key] = {}
        for view, name in views.items():
            data = safe_io.read_bytes(out_dir, Path(str(name)).name)
            if not data or len(data) > MAX_PNG_BYTES or not data.startswith(_PNG):
                raise MisesError(ErrorCode.TOOL_FAILED, "the render produced an invalid image")
            out.pngs[key][view] = data
    shapes = res.get("masks") or {}
    if shapes:
        raw = safe_io.read_bytes(out_dir, "masks.npz")
        if raw is None:
            raise MisesError(ErrorCode.TOOL_FAILED, "the render lost its masks")
        with np.load(io.BytesIO(raw), allow_pickle=False) as z:
            for key, (h, w) in shapes.items():
                bits = np.unpackbits(z[key])[: int(h) * int(w)]
                out.masks[key] = bits.reshape(int(h), int(w)).astype(bool)
    return out
