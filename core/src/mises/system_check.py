"""System check: is every dependency Mises needs present, and can it be fixed with one click?

`system_check()` returns one row per dependency with a status light, a version/path when known
and, when something is wrong, a `fix` the UI can act on (install a package, open a URL, or open a
setting). Detection is read-only. All the environment probing goes through an injectable `Probes`
object so the whole check can be driven against a fake environment in tests without touching the
real machine.

Installing is a separate, deliberate action (see server/system_api.py and `INSTALLERS`): the core
never installs anything on its own or at an agent's request.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .claude_code import detect as claude_detect
from .print import slicer
from .profile import load_profile
from .sim import ccx as ccx_mod

# dep -> (fixed command, the tool it needs). The command takes NO arguments from any request.
BREW_URL = "https://brew.sh"
CONDA_URL = "https://docs.conda.io/en/latest/miniconda.html"
CLAUDE_URL = "https://docs.anthropic.com/en/docs/claude-code/setup"
OLLAMA_URL = "https://ollama.com/download"


@dataclass(frozen=True)
class Installer:
    cmd: tuple[str, ...]
    needs: str          # "brew" | "conda": the manager that must be present
    label: str


INSTALLERS: dict[str, Installer] = {
    "prusaslicer": Installer(("install", "--cask", "prusaslicer"), "brew", "Install PrusaSlicer"),
    "calculix": Installer(("install", "-y", "-c", "conda-forge", "calculix"), "conda",
                          "Install CalculiX"),
    "ollama": Installer(("install", "ollama"), "brew", "Install Ollama"),
}

LOW_DISK_GB = 5.0
_BREW_DIRS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
_CONDA_DIRS = (str(Path("~/miniconda3/bin/conda").expanduser()),
               str(Path("~/miniforge3/bin/conda").expanduser()),
               "/opt/homebrew/bin/conda", "/opt/homebrew/Caskroom/miniconda/base/bin/conda")


def _which_mgr(name: str, dirs: tuple[str, ...]) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    return next((d for d in dirs if os.path.isfile(d) and os.access(d, os.X_OK)), None)


def find_brew() -> str | None:
    return _which_mgr("brew", _BREW_DIRS)


def find_conda() -> str | None:
    return _which_mgr("conda", _CONDA_DIRS)


def _cmd_version(argv: list[str]) -> str | None:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=8,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+(?:\.\d+)+)", (r.stdout or "") + (r.stderr or ""))
    return m.group(1) if m else None


def _module_version(mod: str) -> str | None:
    if importlib.util.find_spec(mod) is None:
        return None
    try:
        return importlib.metadata.version(mod)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _bundle_mdfind(bundle_id: str) -> list[str]:
    try:
        r = subprocess.run(["/usr/bin/mdfind",
                           f"kMDItemCFBundleIdentifier == '{bundle_id}'"],
                          capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _keychain_ok() -> bool:
    """Can we reach the login Keychain? A lookup of an item that does not exist returns exit 44
    (reachable); access denied returns another code. Reads nothing and changes nothing."""
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "mises",
                           "-a", "__mises_probe_absent__"], capture_output=True, text=True,
                          timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode in (0, 44)


async def _claude() -> tuple[claude_detect.ClaudeInfo, claude_detect.AuthStatus]:
    info = await asyncio.to_thread(claude_detect.find_claude)   # runs `claude --version`
    return info, await claude_detect.auth_status(info)


ONSHAPE_CACHE_S = 600.0   # a key check costs one ledgered Onshape call; re-check at most every 10 min


async def _onshape_check(runtime) -> dict | None:
    """Present / valid Onshape keys. A successful answer is cached per adapter (new keys swap the
    adapter, so they are re-checked at once) so repeated checks don't spend the budget."""
    ad = runtime.gate.services.get("onshape")
    if ad is None:
        return None
    cached = getattr(runtime, "_system_onshape_cache", None)
    if cached and cached[0] is ad and time.monotonic() - cached[1] < ONSHAPE_CACHE_S:
        return cached[2]
    try:
        info = await ad.client.request("GET", "/api/v17/users/sessioninfo", run_id=None)
    except Exception:  # noqa: BLE001 - not cached: a transient failure is re-checked next time
        return {"ok": False}
    out = {"ok": True, "user": (info.get("name") if isinstance(info, dict) else None)}
    runtime._system_onshape_cache = (ad, time.monotonic(), out)
    return out


@dataclass
class Probes:
    """Every environment lookup the check makes. Defaults probe the real machine; tests pass a
    Probes with fakes to drive the whole check against a made-up environment."""
    python_version: Callable[[], str]
    in_venv: Callable[[], bool]
    module_version: Callable[[str], str | None]
    slicer_path: Callable[[], Path | None]
    bundle_mdfind: Callable[[str], list[str]]
    ccx_path: Callable[[], str | None]
    ccx_version: Callable[[str], str | None]
    gmsh_version: Callable[[], str | None]
    brew: Callable[[], str | None]
    brew_version: Callable[[str], str | None]
    conda: Callable[[], str | None]
    disk_free_gb: Callable[[], float]
    keychain_ok: Callable[[], bool]
    claude: Callable[[], Awaitable[tuple]]
    ollama: Callable[[], Awaitable[dict]]
    onshape: Callable[[], Awaitable[dict | None]]
    printers: Callable[[], Awaitable[list[dict]]]
    # C7's wind/CFD solvers (built-in LBM, optional SU2 / OpenFOAM container): detect only
    cfd_status: Callable[[], list[dict]] = field(default_factory=lambda: _cfd_solver_status)


def default_probes(runtime) -> Probes:
    import sys

    async def ollama() -> dict:
        from . import local_models
        try:
            return await local_models.ollama_status("http://127.0.0.1:11434")
        except Exception:  # noqa: BLE001 - a probe failure means "not reachable", not a crash
            return {"installed": local_models.find_ollama() is not None, "running": False,
                    "version": None, "models": []}

    async def onshape() -> dict | None:
        return await _onshape_check(runtime)

    async def printers() -> list[dict]:
        from .print.tools import client_for
        out = []
        for p in load_profile(runtime.home).printers:
            row = {"id": p.id, "name": p.name, "reachable": False, "detail": None}
            try:
                st = await (await client_for(p)).status()
                row.update(reachable=True, detail=getattr(st, "state", None))
            except Exception as e:  # noqa: BLE001
                # never let an API key / access code in an error message reach the UI
                row["detail"] = claude_detect.redact(str(getattr(e, "message", e)))[:200]
            out.append(row)
        return out

    return Probes(
        python_version=lambda: ".".join(map(str, sys.version_info[:3])),
        in_venv=lambda: sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        module_version=_module_version,
        slicer_path=slicer.find_slicer,
        bundle_mdfind=_bundle_mdfind,
        ccx_path=ccx_mod.find_ccx,
        ccx_version=ccx_mod.ccx_version,
        gmsh_version=ccx_mod.gmsh_version,
        brew=find_brew,
        brew_version=lambda p: _cmd_version([p, "--version"]),
        conda=find_conda,
        disk_free_gb=lambda: shutil.disk_usage(runtime.home).free / 1e9,
        keychain_ok=_keychain_ok,
        claude=_claude,
        ollama=ollama,
        onshape=onshape,
        printers=printers,
    )


def _cfd_solver_status() -> list[dict]:
    from .sim.cfd import cfd_solver_status    # lazy: keeps numpy out of the check's import
    return cfd_solver_status()


def _row(id_: str, name: str, status: str, *, version=None, path=None, detail="",
         fix: dict | None = None) -> dict:
    return {"id": id_, "name": name, "status": status, "version": version, "path": path,
            "detail": detail, "fix": fix}


def _install_fix(dep: str) -> dict:
    return {"kind": "install", "label": INSTALLERS[dep].label, "dep": dep}


def _sync_probes(p: Probes) -> dict:
    """Every blocking lookup (subprocesses with timeouts of several seconds), run together in a
    worker thread so /system/check never stalls the event loop (events, the WebSocket)."""
    ccx = p.ccx_path()
    sp = p.slicer_path()
    brew = p.brew()
    return {
        "python": p.python_version(), "venv": p.in_venv(),
        "b3": p.module_version("build123d"),
        "occt": p.module_version("cadquery-ocp") or p.module_version("OCP"),
        "gmsh": p.gmsh_version(), "ccx": ccx, "ccx_version": p.ccx_version(ccx) if ccx else None,
        "slicer": sp, "bundles": p.bundle_mdfind("com.prusa3d.slic3r") if sp is None else [],
        "brew": brew, "brew_version": p.brew_version(brew) if brew else None,
        "conda": p.conda(), "disk": p.disk_free_gb(), "keychain": p.keychain_ok(),
        "cfd": p.cfd_status(),
    }


async def system_check(runtime, probes: Probes | None = None) -> list[dict]:
    p = probes or default_probes(runtime)
    s = await asyncio.to_thread(_sync_probes, p)
    rows: list[dict] = []

    # core Python and venv
    rows.append(_row("python", "Python & venv", "ok", version=s["python"], path=None,
                     detail="in a virtualenv" if s["venv"] else "not in a virtualenv"))

    # build123d / OCCT (core dependency)
    b3, occt = s["b3"], s["occt"]
    rows.append(_row("build123d", "build123d / OCCT", "ok" if b3 else "error", version=b3,
                     detail=(f"OCCT {occt}" if occt else "the CAD kernel") if b3
                     else "missing; reinstall the core (uv sync)"))

    # gmsh (core dependency)
    gm = s["gmsh"]
    rows.append(_row("gmsh", "gmsh", "ok" if gm else "error", version=gm,
                     detail="meshing" if gm else "missing; run `uv sync` in core/"))

    # CalculiX
    ccx = s["ccx"]
    rows.append(_row("calculix", "CalculiX", "ok" if ccx else "missing", path=ccx,
                     version=s["ccx_version"],
                     detail="FEM solver" if ccx else "needed to run simulations",
                     fix=None if ccx else _install_fix("calculix")))

    # wind / CFD solvers (C7): the built-in one always works; SU2 and an OpenFOAM container are
    # optional and only detected. No installer: C7's rows carry their own fix (a link) or none.
    for r in s["cfd"]:
        rows.append(_row(r["id"], r["name"], r["status"], version=r.get("version"),
                         path=r.get("path"), detail=r.get("detail") or "", fix=r.get("fix")))

    # PrusaSlicer (every place it can live, including the bundle id)
    sp = s["slicer"]
    if sp is None:
        bundles = s["bundles"]
        rows.append(_row("prusaslicer", "PrusaSlicer", "missing", path=bundles[0] if bundles else None,
                         detail=("found an app bundle but no CLI binary" if bundles
                                 else "needed to slice for printing"),
                         fix=_install_fix("prusaslicer")))
    else:
        rows.append(_row("prusaslicer", "PrusaSlicer", "ok", path=str(sp), detail="slicer"))

    # Homebrew
    brew = s["brew"]
    rows.append(_row("homebrew", "Homebrew", "ok" if brew else "optional_missing", path=brew,
                     version=s["brew_version"],
                     detail="package manager" if brew else "needed to install some tools",
                     fix=None if brew else {"kind": "open_url", "label": "Get Homebrew",
                                            "url": BREW_URL}))

    # conda
    conda = s["conda"]
    rows.append(_row("conda", "conda", "ok" if conda else "optional_missing", path=conda,
                     detail="package manager" if conda else "needed to install CalculiX",
                     fix=None if conda else {"kind": "open_url", "label": "Get Miniconda",
                                             "url": CONDA_URL}))

    # Ollama (installed / running)
    ol = await p.ollama()
    if not ol.get("installed"):
        rows.append(_row("ollama", "Ollama", "optional_missing",
                         detail="needed for local models", fix=_install_fix("ollama")))
    else:
        running = bool(ol.get("running"))
        rows.append(_row("ollama", "Ollama", "ok", version=ol.get("version"),
                         detail="running" if running else "installed, not running"))

    # Claude Code CLI (installed / signed in; status via `claude auth status` only)
    info, auth = await p.claude()
    if not info.path:
        rows.append(_row("claude", "Claude Code CLI", "missing",
                         detail="the coding agent underneath Mises is not installed",
                         fix={"kind": "open_url", "label": "Install the CLI", "url": CLAUDE_URL}))
    elif not auth.signed_in:
        rows.append(_row("claude", "Claude Code CLI", "error", version=info.version, path=info.path,
                         detail="installed but not signed in",
                         fix={"kind": "setting", "label": "Sign in", "setting": "claude_login"}))
    else:
        rows.append(_row("claude", "Claude Code CLI", "ok", version=info.version, path=info.path,
                         detail=f"signed in ({auth.auth_method})"))

    # Onshape keys (present / valid)
    osh = await p.onshape()
    if osh is None:
        rows.append(_row("onshape", "Onshape keys", "optional_missing",
                         detail="add keys to open Onshape documents",
                         fix={"kind": "setting", "label": "Add Onshape keys",
                              "setting": "onshape_keys"}))
    elif osh.get("ok"):
        who = osh.get("user")
        rows.append(_row("onshape", "Onshape keys", "ok",
                         detail=f"valid ({who})" if who else "valid"))
    else:
        rows.append(_row("onshape", "Onshape keys", "error", detail="the stored keys did not work",
                         fix={"kind": "setting", "label": "Update Onshape keys",
                              "setting": "onshape_keys"}))

    # each printer (reachable)
    for pr in await p.printers():
        rows.append(_row(f"printer:{pr['id']}", f"Printer · {pr['name']}",
                         "ok" if pr.get("reachable") else "error",
                         detail=(f"reachable ({pr.get('detail')})" if pr.get("reachable")
                                 else f"unreachable: {pr.get('detail') or 'no response'}"),
                         fix=None if pr.get("reachable") else {"kind": "setting",
                             "label": "Check printer", "setting": "printers"}))

    # free disk space
    free = s["disk"]
    rows.append(_row("disk", "Free disk space", "ok" if free >= LOW_DISK_GB else "error",
                     detail=f"{free:.0f} GB free"))

    # Keychain access
    kc = s["keychain"]
    rows.append(_row("keychain", "Keychain access", "ok" if kc else "error",
                     detail="reachable" if kc else "the login Keychain could not be reached"))

    return rows


__all__ = ["Probes", "Installer", "INSTALLERS", "default_probes", "system_check",
           "find_brew", "find_conda"]
