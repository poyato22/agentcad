"""PrusaSlicer CLI: detection, bundled printer/filament settings, slicing, estimate parsing.

PrusaSlicer is GPL: it only ever runs as a separate process (never imported or linked).
Slicing uses `PrusaSlicer --export-gcode --load <ini> --center X,Y --output <gcode> <stl>`.
We ask for ASCII G-code (`binary_gcode = 0`) so the estimate comments and the PNG thumbnail
block can be read back. The bundled ini files are simplified MK4 / MINI settings, not Prusa's
vendor bundle.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import os
import re
import shutil
import signal
from dataclasses import dataclass
from pathlib import Path

from ..types import ErrorCode, MisesError

INSTALL_COMMAND = "brew install --cask prusaslicer"
_BUNDLE_BINARY = "Contents/MacOS/PrusaSlicer"
APP_PATH = Path("/Applications/PrusaSlicer.app") / _BUNDLE_BINARY
USER_APP_PATH = (Path("~/Applications/PrusaSlicer.app") / _BUNDLE_BINARY).expanduser()
BUNDLE_ID = "com.prusa3d.slic3r"
SLICE_TIMEOUT_S = 300.0
_TAIL = 1500


@dataclass(frozen=True)
class Material:
    density_g_cm3: float
    nozzle_c: int
    bed_c: int
    usd_per_kg: float


MATERIALS: dict[str, Material] = {
    "PLA": Material(1.24, 215, 60, 22.0),
    "PETG": Material(1.27, 240, 80, 25.0),
    "ABS": Material(1.04, 255, 100, 22.0),
    "ASA": Material(1.07, 260, 100, 28.0),
    "TPU": Material(1.21, 230, 50, 35.0),
}


@dataclass(frozen=True)
class PrinterModel:
    bed: tuple[int, int]
    max_height: int
    center: tuple[int, int]


PRINTER_MODELS: dict[str, PrinterModel] = {
    "MK4": PrinterModel((250, 210), 220, (125, 105)),
    "MINI": PrinterModel((180, 180), 180, (90, 90)),
}


@dataclass
class Estimate:
    time_s: int
    time_text: str
    filament_mm: float
    filament_g: float
    cost_usd: float

    def to_dict(self) -> dict:
        return {"time_s": self.time_s, "time_text": self.time_text,
                "filament_mm": round(self.filament_mm, 2), "filament_g": round(self.filament_g, 2),
                "cost_usd": round(self.cost_usd, 4)}


@dataclass
class SliceResult:
    gcode_path: Path
    estimate: Estimate
    preview_png: Path | None


def _missing() -> MisesError:
    return MisesError(ErrorCode.TOOL_FAILED, "PrusaSlicer is not installed",
                      hint=f"install it with: {INSTALL_COMMAND}")


def _mdfind_bundles() -> list[Path]:
    """PrusaSlicer.app bundles anywhere on disk, by bundle id (Spotlight). A last resort: it
    finds the app when the user dragged it somewhere other than /Applications."""
    import subprocess
    try:
        r = subprocess.run(["/usr/bin/mdfind", f"kMDItemCFBundleIdentifier == '{BUNDLE_ID}'"],
                           capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    return [Path(ln) for ln in r.stdout.splitlines() if ln.strip().endswith(".app")]


def _usable(c: Path | None) -> bool:
    return c is not None and c.is_file() and os.access(c, os.X_OK)


def find_slicer() -> Path | None:
    """The PrusaSlicer CLI binary, everywhere it can live: MISES_PRUSASLICER, /Applications,
    ~/Applications, a `prusa-slicer`/`PrusaSlicer` on PATH, then any bundle Spotlight knows of."""
    env = os.environ.get("MISES_PRUSASLICER")
    fixed = [Path(env) if env else None, APP_PATH, USER_APP_PATH,
             Path(w) if (w := shutil.which("prusa-slicer")) else None,
             Path(w2) if (w2 := shutil.which("PrusaSlicer")) else None]
    for c in fixed:
        if _usable(c):
            return c
    for bundle in _mdfind_bundles():
        c = bundle / _BUNDLE_BINARY
        if _usable(c):
            return c
    return None


def require_slicer() -> Path:
    p = find_slicer()
    if p is None:
        raise _missing()
    return p


def slicer_status() -> dict:
    p = find_slicer()
    return {"installed": p is not None, "path": str(p) if p else None,
            "install_command": INSTALL_COMMAND}


def material_of(name: str) -> Material:
    m = MATERIALS.get(str(name).upper())
    if m is None:
        raise MisesError(ErrorCode.INVALID_INPUT, f"unknown material {name!r}",
                         hint=f"materials: {', '.join(MATERIALS)}")
    return m


def model_of(name: str) -> PrinterModel:
    m = PRINTER_MODELS.get(str(name).upper())
    if m is None:
        raise MisesError(ErrorCode.INVALID_INPUT, f"unknown printer model {name!r}",
                         hint=f"models: {', '.join(PRINTER_MODELS)}")
    return m


def build_ini(model: str, material: str) -> str:
    pm, mat = model_of(model), material_of(material)
    bw, bd = pm.bed
    start = ("M862.3 P \"" + model.upper() + "\"\\nM17\\nM83\\nM140 S[first_layer_bed_temperature]\\n"
             "M104 S[first_layer_temperature]\\nG28\\nM190 S[first_layer_bed_temperature]\\n"
             "M109 S[first_layer_temperature]\\nG1 X0 Y-3 Z0.3 F4000\\nG1 X60 E9 F1000\\n"
             "G1 X100 E12.5 F1000\\nG92 E0")
    end = "G4\\nM104 S0\\nM140 S0\\nG1 X0 Y200 F3000\\nM84"
    lines = [
        f"bed_shape = 0x0,{bw}x0,{bw}x{bd},0x{bd}",
        f"max_print_height = {pm.max_height}",
        "nozzle_diameter = 0.4", "filament_diameter = 1.75",
        f"filament_density = {mat.density_g_cm3:g}",
        f"filament_cost = {mat.usd_per_kg:g}",
        f"temperature = {mat.nozzle_c}", f"first_layer_temperature = {mat.nozzle_c}",
        f"bed_temperature = {mat.bed_c}", f"first_layer_bed_temperature = {mat.bed_c}",
        "layer_height = 0.2", "first_layer_height = 0.2",
        "perimeters = 2", "top_solid_layers = 5", "bottom_solid_layers = 4", "fill_density = 15%",
        "gcode_flavor = marlin2", "binary_gcode = 0", "thumbnails = 440x240/PNG",
        "retract_length = 0.8", f"start_gcode = {start}", f"end_gcode = {end}",
    ]
    return "\n".join(lines) + "\n"


_TIME_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def _num(text: str, key: str) -> float | None:
    m = re.search(rf"^; {re.escape(key)} = ([0-9.]+)\s*$", text, re.M)
    return float(m.group(1)) if m else None


def parse_estimate(gcode: str, usd_per_kg: float) -> Estimate:
    """Reads PrusaSlicer's footer comments; cost is grams x the material's $/kg."""
    t = re.search(r"^; estimated printing time \(normal mode\) = (.+?)\s*$", gcode, re.M)
    mm = _num(gcode, "filament used [mm]")
    g = _num(gcode, "filament used [g]")
    if not t or mm is None or g is None:
        raise MisesError(ErrorCode.TOOL_FAILED, "the G-code has no PrusaSlicer estimate comments",
                         hint="the slice may have failed; check the slicer output")
    secs = sum(int(n) * _TIME_UNITS[u] for n, u in re.findall(r"(\d+)([dhms])", t.group(1)))
    return Estimate(time_s=secs, time_text=t.group(1), filament_mm=mm, filament_g=g,
                    cost_usd=g / 1000.0 * usd_per_kg)


def extract_thumbnail(gcode: str) -> bytes | None:
    m = re.search(r"^; thumbnail begin \d+x\d+ \d+[ \t]*\n((?:; [A-Za-z0-9+/=]*[ \t]*\n)+); thumbnail end",
                  gcode, re.M)
    if not m:
        return None
    b64 = "".join(line[2:].strip() for line in m.group(1).splitlines())
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return None
    return data if data.startswith(b"\x89PNG") else None


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def run_slice(stl: Path, out_dir: Path, *, model: str, material: str,
                    timeout_s: float = SLICE_TIMEOUT_S) -> SliceResult:
    """Slice `stl` into out_dir/model.gcode (+ preview.png when a thumbnail is embedded).
    Killed after timeout_s; cancelling the awaiting task kills it too."""
    exe = require_slicer()
    pm, mat = model_of(model), material_of(material)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ini = out_dir / "settings.ini"
    ini.write_text(build_ini(model, material))
    gcode = out_dir / "model.gcode"
    gcode.unlink(missing_ok=True)
    data = out_dir / "datadir"
    data.mkdir(exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(out_dir), "TMPDIR": str(out_dir)}
    proc = await asyncio.create_subprocess_exec(
        str(exe), "--datadir", str(data), "--export-gcode", "--load", str(ini),
        "--center", f"{pm.center[0]},{pm.center[1]}", "--output", str(gcode), str(stl),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env, cwd=out_dir, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        _kill_group(proc)
        await proc.wait()
        raise MisesError(ErrorCode.TRANSIENT, f"slicing timed out after {timeout_s:g}s",
                         hint="the part may be too complex; simplify it or retry") from None
    except BaseException:
        _kill_group(proc)
        await proc.wait()
        raise
    _kill_group(proc)
    if proc.returncode != 0 or not gcode.is_file():
        tail = (err.decode(errors="replace").strip() or out.decode(errors="replace").strip()
                or f"exit {proc.returncode}")[-_TAIL:]
        raise MisesError(ErrorCode.TOOL_FAILED, f"PrusaSlicer failed: {tail}")
    text = gcode.read_text(errors="replace")
    est = parse_estimate(text, mat.usd_per_kg)
    png = extract_thumbnail(text)
    preview = None
    if png:
        preview = out_dir / "preview.png"
        preview.write_bytes(png)
    return SliceResult(gcode, est, preview)
