"""C6: system check against a fully faked environment (no real machine probing)."""
from types import SimpleNamespace

import pytest

from mises import system_check as sc
from mises.claude_code.detect import AuthStatus, ClaudeInfo


def _probes(**over):
    base = dict(
        python_version=lambda: "3.13.9",
        in_venv=lambda: True,
        module_version=lambda m: {"build123d": "0.9.0", "cadquery-ocp": "7.8.0"}.get(m),
        slicer_path=lambda: None,
        bundle_mdfind=lambda bid: ["/Applications/PrusaSlicer.app"],
        ccx_path=lambda: None,
        ccx_version=lambda p: None,
        gmsh_version=lambda: "4.13.1",
        brew=lambda: "/opt/homebrew/bin/brew",
        brew_version=lambda p: "4.3.0",
        conda=lambda: None,
        disk_free_gb=lambda: 120.0,
        keychain_ok=lambda: True,
        claude=_claude_signed_in,
        ollama=_ollama_running,
        onshape=_onshape_ok,
        printers=_two_printers,
    )
    base.update(over)
    return sc.Probes(**base)


async def _claude_signed_in():
    return (ClaudeInfo(path="/usr/local/bin/claude", version="2.0.0"),
            AuthStatus(signed_in=True, auth_method="oauth", email=None, config_dir=None,
                       raw_ok=True))


async def _ollama_running():
    return {"installed": True, "running": True, "version": "0.5.0", "models": []}


async def _onshape_ok():
    return {"ok": True, "user": "Ada"}


async def _two_printers():
    return [{"id": "mk4", "name": "MK4", "reachable": True, "detail": "IDLE"},
            {"id": "mini", "name": "Mini", "reachable": False, "detail": "timeout"}]


async def test_check_lists_every_dependency():
    rows = await sc.system_check(SimpleNamespace(), _probes())
    ids = {r["id"] for r in rows}
    assert {"python", "build123d", "gmsh", "calculix", "prusaslicer", "homebrew", "conda",
            "ollama", "claude", "onshape", "disk", "keychain"} <= ids
    assert "printer:mk4" in ids and "printer:mini" in ids
    for r in rows:
        assert r["status"] in {"ok", "missing", "outdated", "error", "optional_missing"}


async def test_missing_tools_offer_install_fixes():
    rows = {r["id"]: r for r in await sc.system_check(SimpleNamespace(), _probes())}
    assert rows["calculix"]["status"] == "missing"
    assert rows["calculix"]["fix"] == {"kind": "install", "label": "Install CalculiX",
                                       "dep": "calculix"}
    assert rows["prusaslicer"]["status"] == "missing"
    assert rows["prusaslicer"]["fix"]["dep"] == "prusaslicer"
    # a conda-less machine points at the conda site, not an install button
    assert rows["conda"]["status"] == "optional_missing"
    assert rows["conda"]["fix"]["kind"] == "open_url"


async def test_slicer_bundle_detected_without_cli():
    rows = {r["id"]: r for r in await sc.system_check(SimpleNamespace(), _probes())}
    assert rows["prusaslicer"]["path"] == "/Applications/PrusaSlicer.app"
    assert "bundle" in rows["prusaslicer"]["detail"]


async def test_claude_not_signed_in_is_error_with_setting_fix():
    async def unsigned():
        return (ClaudeInfo(path="/usr/local/bin/claude", version="2.0.0"),
                AuthStatus(signed_in=False, auth_method="none", email=None, config_dir=None,
                           raw_ok=True))
    rows = {r["id"]: r for r in await sc.system_check(SimpleNamespace(), _probes(claude=unsigned))}
    assert rows["claude"]["status"] == "error"
    assert rows["claude"]["fix"]["kind"] == "setting"


async def test_printer_reachability():
    rows = {r["id"]: r for r in await sc.system_check(SimpleNamespace(), _probes())}
    assert rows["printer:mk4"]["status"] == "ok"
    assert rows["printer:mini"]["status"] == "error"


async def test_onshape_check_is_cached_per_adapter():
    calls = []

    class Client:
        async def request(self, method, path, run_id=None):
            calls.append(path)
            return {"name": "Ada"}

    ad = SimpleNamespace(client=Client())
    rt = SimpleNamespace(gate=SimpleNamespace(services={"onshape": ad}))
    for _ in range(3):
        assert (await sc._onshape_check(rt)) == {"ok": True, "user": "Ada"}
    assert len(calls) == 1                       # one ledgered call, not one per check
    rt.gate.services["onshape"] = SimpleNamespace(client=Client())   # new keys: re-check
    await sc._onshape_check(rt)
    assert len(calls) == 2


async def test_sync_probes_run_off_the_event_loop():
    import threading
    loop_thread = threading.get_ident()
    seen = []
    rows = await sc.system_check(SimpleNamespace(), _probes(
        disk_free_gb=lambda: seen.append(threading.get_ident()) or 50.0))
    assert rows and seen and seen[0] != loop_thread


async def test_low_disk_is_error():
    rows = {r["id"]: r for r in await sc.system_check(SimpleNamespace(),
                                                      _probes(disk_free_gb=lambda: 1.0))}
    assert rows["disk"]["status"] == "error"


async def test_check_includes_c7_cfd_solver_rows():
    fake = [{"id": "cfd_builtin", "name": "Wind solver", "status": "ok", "version": "D3Q19",
             "path": None, "detail": "always available", "fix": None},
            {"id": "su2", "name": "SU2", "status": "optional_missing", "version": None,
             "path": None, "detail": "not installed",
             "fix": {"kind": "open_url", "label": "SU2 downloads", "url": "https://su2code.github.io"}}]
    rows = {r["id"]: r for r in await sc.system_check(SimpleNamespace(), _probes(cfd_status=lambda: fake))}
    assert rows["cfd_builtin"]["status"] == "ok"
    assert rows["su2"]["status"] == "optional_missing" and rows["su2"]["fix"]["kind"] == "open_url"
    assert "su2" not in sc.INSTALLERS and "cfd_builtin" not in sc.INSTALLERS
