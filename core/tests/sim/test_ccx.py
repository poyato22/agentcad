import os
import time

import pytest

from mises.sim import ccx
from mises.sim.ccx import INSTALL_HINT, find_ccx, run_ccx, solver_status
from mises.sim.types import SimError


def _fake_bin(tmp_path, body: str):
    p = tmp_path / "bin" / "ccx"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return p


def test_install_hint_is_the_conda_command():
    assert INSTALL_HINT == "conda install -c conda-forge calculix"


def test_find_ccx_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("MISES_CCX", raising=False)
    monkeypatch.setattr(ccx, "CANDIDATES", [str(tmp_path / "nope" / "ccx")])
    assert find_ccx() is None
    st = solver_status()
    assert st["ccx"]["found"] is False and st["ccx"]["install"] == INSTALL_HINT
    assert st["gmsh"]["found"] is True  # the pip wheel is a core dependency


def test_find_ccx_candidates_and_path(monkeypatch, tmp_path):
    fake = _fake_bin(tmp_path, "echo 'This is Version 2.23'\n")
    monkeypatch.delenv("MISES_CCX", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(ccx, "CANDIDATES", ["/nonexistent/ccx", str(fake)])
    assert find_ccx() == str(fake)
    assert ccx.ccx_version(str(fake)) == "2.23"
    monkeypatch.setattr(ccx, "CANDIDATES", [])
    monkeypatch.setenv("PATH", str(fake.parent))
    assert find_ccx() == str(fake)


async def test_run_ccx_timeout_kills_process(tmp_path):
    pidfile = tmp_path / "job" / "pid"
    # `exec`: the sandbox denies process-fork, so the fake must not fork `sleep` as a child
    fake = _fake_bin(tmp_path, f"echo $$ > {pidfile}\nexec sleep 30\n")
    job = tmp_path / "job"
    job.mkdir()
    (job / "job.inp").write_text("*NODE\n")
    t0 = time.monotonic()
    with pytest.raises(SimError) as e:
        await run_ccx(str(fake), job, timeout_s=1.0)
    assert time.monotonic() - t0 < 10
    assert "timed out" in e.value.message
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_run_ccx_failure_reports_output(tmp_path):
    fake = _fake_bin(tmp_path, "echo '*ERROR in calinput: bad card'\nexit 201\n")
    job = tmp_path / "job"
    job.mkdir()
    (job / "job.inp").write_text("*NODE\n")
    with pytest.raises(SimError) as e:
        await run_ccx(str(fake), job, timeout_s=10)
    assert "bad card" in e.value.message


async def test_run_ccx_crash_names_the_signal_not_the_omp_warning(tmp_path):
    """DRONE2: ccx segfaulted on a 379k-node mesh and the error read only 'OMP: Warning
    #179: ... /tmp file failed', which sent the agent chasing a /tmp problem."""
    fake = _fake_bin(tmp_path, "echo ' Frequency analysis was selected'\n"
                               "echo 'OMP: Warning #179: Function Cannot set size of /tmp file "
                               "failed:' >&2\nkill -SEGV $$\n")
    job = tmp_path / "job"
    job.mkdir()
    (job / "job.inp").write_text("*NODE\n")
    with pytest.raises(SimError) as e:
        await run_ccx(str(fake), job, timeout_s=10)
    msg = e.value.message
    assert "crashed" in msg and "SIGSEGV" in msg
    assert "OMP" not in msg
    assert "coarser" in (e.value.hint or "")
