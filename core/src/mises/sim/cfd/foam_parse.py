"""Readers for OpenFOAM output (v2512, openfoam.com): the forces / forceCoeffs function-object
tables, raw-format sampled surfaces and the solver log. Pure text parsing, no OpenFOAM needed.

Formats (from the v2512 sources, src/functionObjects/forces/{forces,forceCoeffs}):
- postProcessing/<name>/<startTime>/coefficient.dat: a "# Force and moment coefficients"
  header with "# dragDir : (1 0 0)"-style entries (dragDir, sideDir, liftDir, rollAxis,
  pitchAxis, yawAxis, magUInf, lRef, Aref, CofR), then "# Time" and one tab-separated column
  per selected coefficient in sorted name order (Cd, Cd(f), Cd(r), Cl, ..., CmPitch, CmRoll,
  CmYaw, Cs, ...).
- force.dat / moment.dat: "# Time  total_x total_y total_z  pressure_x ... viscous_z"
  (porous_* with porosity). Vector values may be written bare or in parentheses; both parse.
- surfaceFormat raw: "#"-comment header lines, the last naming the columns ("x y z p" or
  "x y z U_x U_y U_z"), then one row per face (or point).
Columns are always looked up by name, never by position.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def _is_num(tok: str) -> bool:
    return bool(_NUM.match(tok)) or tok.lower() in ("nan", "inf", "-inf")


def read_table(path: Path) -> dict[str, np.ndarray]:
    """A function-object .dat table as {column name: values}, plus "_header": the header
    entries ("# lRef : 0.1") as {name: text}."""
    names: list[str] | None = None
    header: dict[str, str] = {}
    rows: list[list[float]] = []
    for line in Path(path).read_text(errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            body = s[1:].strip()
            if ":" in body and not body.startswith("Time"):
                k, _, v = body.partition(":")
                header[k.strip()] = v.strip()
            elif body.startswith("Time"):
                names = body.split()
            continue
        toks = s.replace("(", " ").replace(")", " ").split()
        if names is None or len(toks) != len(names) or not all(_is_num(t) for t in toks):
            continue           # a partially written last line, or noise
        rows.append([float(t) for t in toks])
    if names is None:
        raise ValueError(f"{Path(path).name}: no '# Time' header")
    arr = np.array(rows, dtype=float).reshape(-1, len(names))
    out: dict = {n: arr[:, i] for i, n in enumerate(names)}
    out["_header"] = header
    return out


def function_object_table(case: Path, name: str, file: str) -> dict[str, np.ndarray] | None:
    """postProcessing/<name>/<time>/<file>, every start-time dir joined in time order (a restart
    writes a new one), or None when the function object wrote nothing."""
    root = Path(case) / "postProcessing" / name
    if not root.is_dir():
        return None
    dirs = sorted((d for d in root.iterdir() if d.is_dir() and _is_num(d.name)),
                  key=lambda d: float(d.name))
    parts = [read_table(d / file) for d in dirs if (d / file).is_file()]
    if not parts:
        return None
    out = {k: np.concatenate([p[k] for p in parts]) for k in parts[0] if k != "_header"}
    out["_header"] = parts[-1]["_header"]
    return out


def read_raw(path: Path) -> tuple[list[str], np.ndarray]:
    """A raw sampled surface: (column names, rows). The names come from the last comment line
    that starts with x y z; without one they are x y z + c0.. ."""
    names: list[str] | None = None
    rows: list[list[float]] = []
    for line in Path(path).read_text(errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            toks = s[1:].split()
            if toks[:3] == ["x", "y", "z"]:
                names = toks
            continue
        toks = s.replace("(", " ").replace(")", " ").split()
        if all(_is_num(t) for t in toks):
            rows.append([float(t) for t in toks])
    width = len(names) if names else (len(rows[0]) if rows else 3)
    rows = [r for r in rows if len(r) == width]
    if names is None:
        names = ["x", "y", "z"] + [f"c{i}" for i in range(width - 3)]
    return names, np.array(rows, dtype=float).reshape(-1, width)


def find_surface(case: Path, fo: str, surface: str, field: str) -> Path | None:
    """The raw file of `field` on `surface` at the last written time. Writers name it
    <surface>/<field>.raw, <field>_<surface>.raw or <surface>.raw across versions; any of them."""
    root = Path(case) / "postProcessing" / fo
    if not root.is_dir():
        return None
    times = sorted((d for d in root.iterdir() if d.is_dir() and _is_num(d.name)),
                   key=lambda d: float(d.name))
    for d in reversed(times):
        cands = [d / surface / f"{field}.raw", d / f"{field}_{surface}.raw",
                 d / f"{surface}_{field}.raw", d / f"{surface}.raw"]
        for c in cands:
            if c.is_file():
                return c
    return None


def surface_values(path: Path, field: str) -> tuple[np.ndarray, np.ndarray]:
    """(xyz (n, 3), values (n,) or (n, 3)) of `field` from a raw surface file."""
    names, a = read_raw(path)
    xyz = a[:, :3]
    if field in names:
        return xyz, a[:, names.index(field)]
    comps = [f"{field}_{c}" for c in "xyz"]
    if all(c in names for c in comps):
        return xyz, a[:, [names.index(c) for c in comps]]
    rest = a[:, 3:]
    return xyz, rest[:, 0] if rest.shape[1] == 1 else rest


_TIME = re.compile(r"^Time = (\S+)\s*$", re.M)
_CONV = re.compile(r"SIMPLE solution converged in (\S+) iterations")


def read_log(text: str) -> dict:
    """The iteration reached, whether SIMPLE converged and any FOAM FATAL ERROR, from a
    simpleFoam log (also usable on a partial log while it runs)."""
    times = _TIME.findall(text)
    conv = _CONV.search(text)
    err = None
    i = text.find("FOAM FATAL")
    if i >= 0:
        err = " ".join(ln.strip() for ln in text[i:].splitlines()[:8] if ln.strip())[:600]
    last = None
    if times:
        try:
            last = float(times[-1])
        except ValueError:
            last = None
    return {"iteration": last, "converged": conv is not None, "error": err,
            "finished": bool(re.search(r"^End\s*$", text, re.M))}
