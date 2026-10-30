"""S2: OpenFOAM airflow. The case writer, the container command's hard rules, the output
readers, and whole studies driven by a fake docker runner that replays recorded OpenFOAM output
(tests/fixtures/openfoam). No container is ever started and no image is pulled here; the
real-solver benchmarks (openfoam_real) run only in the user-approved QA step."""
import asyncio
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from mises.sim import charts, studies
from mises.sim.cfd import foam_case, foam_parse, openfoam
from mises.sim.cfd.validation import (OPENFOAM_TARGET_BAND, openfoam_error_band,
                                      openfoam_validated)
from mises.sim.foam_image import OPENFOAM_IMAGE
from mises.sim.results import read_glb
from mises.sim.settings import WindSettings
from mises.sim.studies import wind_payload
from mises.sim.types import SimError

from .test_cfd import box, sphere

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "openfoam"
DOCKER = "/usr/local/bin/docker"


def settings(**kw):
    return wind_payload(WindSettings(**{"solver": "openfoam", **kw}))


class FakeRunner:
    """Replays a recorded case: copies the fixture's logs and postProcessing into the mounted
    case dir, as the container would have written them."""

    def __init__(self, fixture: str | None, rc: int = 0, out: str = "", delay: float = 0.0,
                 raise_exc: BaseException | None = None, extra: dict | None = None,
                 ncpus: int | None = 8, kill_problem: str | None = None):
        self.fixture, self.rc, self.out, self.delay = fixture, rc, out, delay
        self.raise_exc, self.extra = raise_exc, extra or {}
        self.ncpus, self.kill_problem = ncpus, kill_problem
        self.calls: list[list[str]] = []
        self.killed: list[str] = []
        self.cleanups = 0

    async def run(self, argv, *, timeout_s):
        self.calls.append(list(argv))
        vols = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        host = Path(vols[0].rsplit(":", 2)[0])
        assert (host / "Allrun").is_file() and (host / "system/controlDict").is_file()
        if self.delay:
            await asyncio.wait_for(asyncio.sleep(self.delay), timeout_s)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.fixture:
            shutil.copytree(FIX / self.fixture, host, dirs_exist_ok=True)
        for rel, text in self.extra.items():
            (host / rel).write_text(text)
        return self.rc, self.out

    async def kill(self, name):
        self.killed.append(name)
        return self.kill_problem

    async def cleanup(self):
        self.cleanups += 1

    async def ncpu(self):
        return self.ncpus


def use(monkeypatch, runner):
    monkeypatch.setattr(openfoam, "make_runner", lambda docker: runner)
    return runner


# ---- readers

def test_coefficient_table_is_read_by_column_name():
    t = foam_parse.function_object_table(FIX / "sphere_re2e4", "forceCoeffs",
                                         "coefficient.dat")
    assert {"Time", "Cd", "Cl", "Cs", "CmPitch", "CmRoll", "CmYaw"} <= set(t)
    assert len(t["Time"]) == 236 and t["Time"][-1] == 236
    assert t["_header"]["magUInf"] == "3" and t["_header"]["dragDir"] == "(1 0 0)"


def test_force_tables_parse_with_and_without_parentheses():
    for case in ("sphere_re2e4", "plate_re1e4"):       # sphere: "(x y z)", plate: bare
        f = foam_parse.function_object_table(FIX / case, "forces", "force.dat")
        assert {"total_x", "pressure_x", "viscous_z"} <= set(f)
        assert np.allclose(f["total_x"], f["pressure_x"] + f["viscous_x"], rtol=1e-6)


def test_partial_last_line_is_skipped(tmp_path):
    src = (FIX / "sphere_re2e4/postProcessing/forceCoeffs/0/coefficient.dat").read_text()
    d = tmp_path / "postProcessing/forceCoeffs/0"
    d.mkdir(parents=True)
    (d / "coefficient.dat").write_text(src + "237\t4.5e-01\t1.2e-")    # killed mid-write
    t = foam_parse.function_object_table(tmp_path, "forceCoeffs", "coefficient.dat")
    assert len(t["Time"]) == 236


def test_raw_surfaces_and_log():
    p = foam_parse.find_surface(FIX / "sphere_re2e4", "surfaces", "body", "p")
    xyz, vals = foam_parse.surface_values(p, "p")
    assert xyz.shape[1] == 3 and vals.ndim == 1 and len(vals) == len(xyz) > 1000
    u = foam_parse.find_surface(FIX / "sphere_re2e4", "surfaces", "slice_xz", "U")
    _, U = foam_parse.surface_values(u, "U")
    assert U.shape[1] == 3
    st = foam_parse.read_log((FIX / "sphere_re2e4/log.simpleFoam").read_text())
    assert st == {"iteration": 236.0, "converged": True, "error": None, "finished": True}
    st = foam_parse.read_log("Time = 12\n\n--> FOAM FATAL ERROR: (openfoam-2512)\n"
                             "    Maximum number of iterations exceeded\n")
    assert st["iteration"] == 12 and "Maximum number of iterations" in st["error"]


# ---- the case

def test_case_has_sst_snappy_and_the_force_function_objects(tmp_path):
    s = settings(speed_m_s=3)
    tris = sphere() * 1e-3
    plan = foam_case.write_case(tmp_path / "c", tris, s, a_ref_m2=0.00785)
    c = tmp_path / "c"
    for rel in ("system/blockMeshDict", "system/snappyHexMeshDict", "system/controlDict",
                "system/fvSchemes", "system/fvSolution", "system/decomposeParDict",
                "constant/transportProperties", "constant/turbulenceProperties",
                "constant/triSurface/part.stl", "0/U", "0/p", "0/k", "0/omega", "0/nut",
                "Allrun"):
        assert (c / rel).is_file(), rel
    # Re 2e4: laminar; the same sphere at 30 m/s (Re 2e5) runs k-omega SST
    assert plan.laminar and plan.reynolds == pytest.approx(2.03e4, rel=0.01)
    assert "simulationType  laminar;" in (c / "constant/turbulenceProperties").read_text()
    fast = foam_case.write_case(tmp_path / "fast", tris, settings(speed_m_s=30), 0.00785)
    assert not fast.laminar
    assert "kOmegaSST" in (tmp_path / "fast/constant/turbulenceProperties").read_text()
    ctl = (c / "system/controlDict").read_text()
    assert "application     simpleFoam" in ctl and "type            forceCoeffs" in ctl
    assert "magUInf         3;" in ctl and "Aref            0.00785;" in ctl
    assert "lRef            0.1;" in ctl and 'patches         ("body.*")' in ctl
    assert f"endTime         {s['openfoam_iterations']};" in ctl
    nu = 1.81e-5 / 1.225
    assert f"nu              {nu:.9g};" in (c / "constant/transportProperties").read_text()
    assert (c / "constant/triSurface/part.stl").read_text().startswith("solid body")
    allrun = (c / "Allrun").read_text()
    assert "snappyHexMesh -overwrite" in allrun and "mpirun -np 4 simpleFoam -parallel" in allrun
    # the domain: D upstream x multiplier, 2 x downstream, 2 x multiplier cross-section
    assert plan.d_ref_m == pytest.approx(0.1, rel=0.01)
    assert plan.dom_lo[0] == pytest.approx(-0.05 - 0.5, rel=0.02)
    assert plan.dom_hi[0] == pytest.approx(0.05 + 1.0, rel=0.02)
    assert plan.dom_hi[1] - plan.dom_lo[1] == pytest.approx(1.0, rel=0.02)
    # locationInMesh is in the fluid: inside the domain, outside the body
    loc = foam_case._location_in_mesh(plan)
    assert np.all(loc > plan.dom_lo) and np.all(loc < plan.dom_hi)
    assert np.linalg.norm(loc) > 0.05


def test_serial_run_without_mpi_and_refinement_sets_levels(tmp_path):
    s = settings(cpus=1, refinement=4)
    plan = foam_case.write_case(tmp_path, box(4, 100, 100) * 1e-3, s, 0.01)
    allrun = (tmp_path / "Allrun").read_text()
    assert "mpirun" not in allrun and "run simpleFoam simpleFoam" in allrun
    # a bluff body (the plate) is meshed at the finest level all over; a streamlined one keeps
    # the curvature-driven (min, max) range
    assert plan.bluff and plan.surface_levels == (5, 5) and plan.layers == 3
    slender = foam_case.plan_case(box(400, 40, 40) * 1e-3, s)
    assert not slender.bluff and slender.surface_levels == (4, 5)
    assert foam_case.plan_case(box(4, 100, 100) * 1e-3,
                               settings(refinement=2)).surface_levels == (3, 3)
    assert "addLayers       true;" in (tmp_path / "system/snappyHexMeshDict").read_text()
    lo = foam_case.plan_case(box(4, 100, 100) * 1e-3, settings(refinement=1))
    assert lo.cells_estimate < plan.cells_estimate


# ---- the container command

def test_docker_command_has_no_network_and_only_the_case_mount(tmp_path):
    case = tmp_path / "job" / "openfoam_0"
    case.mkdir(parents=True)
    argv = openfoam.docker_argv(DOCKER, case, "mises-of-x", cpus=3, memory_mb=2048)
    assert argv[:2] == [DOCKER, "run"] and "--rm" in argv
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--pull") + 1] == "never"
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a in ("-v", "--volume", "--mount")]
    assert mounts == [f"{case.resolve()}:/case:rw"]
    # read-only root; the one other writable place is exactly an in-memory /tmp (no host path)
    assert "--read-only" in argv
    tmpfs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
    assert tmpfs == ["/tmp:rw,nosuid,nodev,size=256m"]
    assert argv[argv.index("TMPDIR=/tmp") - 1] == "-e"
    for banned in ("--privileged", "--volumes-from", "--net", "--network=host",
                   "--ipc", "--pid", "--device"):
        assert banned not in argv
    assert not any(a.startswith(("--mount", "--volume=", "-v=", "--tmpfs=")) for a in argv)
    assert "--cpus=3" in argv and "--memory=2048m" in argv and "--memory-swap=2048m" in argv
    assert any(a.startswith("--pids-limit=") for a in argv)
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert OPENFOAM_IMAGE in argv and argv[-2:] == ["/bin/bash", "/case/Allrun"]


def test_docker_command_refuses_a_path_that_would_split_the_mount(tmp_path):
    bad = tmp_path / "a:b"
    bad.mkdir()
    with pytest.raises(SimError):
        openfoam.docker_argv(DOCKER, bad, "n", cpus=1, memory_mb=512)


def test_a_symlinked_case_dir_is_refused_not_resolved(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (job / "openfoam_0").symlink_to(elsewhere)
    with pytest.raises(SimError) as e:
        openfoam.docker_argv(DOCKER, job / "openfoam_0", "n", cpus=1, memory_mb=512,
                             job_root=job)
    assert "symlink" in e.value.message
    (job / "real").mkdir()
    with pytest.raises(SimError):          # outside the job root
        openfoam.docker_argv(DOCKER, elsewhere, "n", cpus=1, memory_mb=512, job_root=job)
    argv = openfoam.docker_argv(DOCKER, job / "real", "n", cpus=1, memory_mb=512, job_root=job)
    assert f"{(job / 'real').resolve()}:/case:rw" in argv


def test_min_vol_scales_with_the_part(tmp_path):
    big = foam_case.plan_case(sphere() * 1e-3, settings(refinement=4))
    small = foam_case.plan_case(sphere() * 1e-4, settings(refinement=4))   # D = 10 mm
    assert foam_case.min_vol(small) == pytest.approx(foam_case.min_vol(big) * 1e-3, rel=0.05)
    # the thinnest layer cell (0.1 of a surface cell thick) stays far above minVol
    assert 0.1 * small.surface_cell_m ** 3 > 100 * foam_case.min_vol(small)
    foam_case.write_case(tmp_path, sphere() * 1e-4, settings(refinement=4), 7.85e-5, small)
    assert f"minVol {foam_case.min_vol(small):.3g};" in (
        tmp_path / "system/snappyHexMeshDict").read_text()


# ---- whole studies through the fake runner ("benchmarks" replayed from recorded output)

@pytest.mark.timeout(60)
async def test_sphere_study_from_recorded_output(tmp_path, monkeypatch):
    run = use(monkeypatch, FakeRunner("sphere_re2e4"))
    seen = []
    r = await openfoam.run_openfoam_study(sphere(n=24), settings(speed_m_s=3), tmp_path, DOCKER,
                                          progress=seen.append)
    assert len(run.calls) == 1 and run.calls[0][0] == DOCKER
    # Re 2e4 < 1e5: the case runs laminar (fully turbulent SST separates far too late)
    assert r["method"] == "openfoam" and r["fidelity"] == "OpenFOAM laminar"
    assert r["mesh"]["regime"] == "laminar" and r["mesh"]["turbulence"].startswith("laminar")
    # Re 2e4 sphere, reference Cd 0.47 (subcritical plateau): within 10%
    assert abs(r["cd"] - 0.47) / 0.47 < 0.10, r["cd"]
    coef = foam_parse.function_object_table(FIX / "sphere_re2e4", "forceCoeffs",
                                            "coefficient.dat")
    assert r["cd"] == pytest.approx(np.mean(coef["Cd"][-118:]))      # laminar: last 50% of 236
    assert r["reynolds"] == pytest.approx(1.225 * 3 * 0.1 / 1.81e-5, rel=0.02)
    assert r["drag_n"] == pytest.approx(r["cd"] * r["dynamic_pressure_pa"] * r["ref_area_m2"])
    assert r["converged"] is True and r["stopped"] == "converged" and r["steps"] == 236
    assert r["model_error_band"] == OPENFOAM_TARGET_BAND
    assert [v["body"] for v in r["validation"]] == ["sphere", "flat plate"]   # recorded rows
    assert r["force_n_wind"][0] > 0 and len(r["moment_nm"]) == 3
    assert r["history"][0]["step"] == 1 and "cd" in r["history"][-1]
    # outputs in the existing formats: a Cp GLB and two slice PNGs
    gltf, _ = read_glb(tmp_path / "result.glb")
    assert gltf["asset"]["extras"]["mises_sim"]["field"] == "cp"
    assert "_CP" in gltf["meshes"][0]["primitives"][0]["attributes"]
    lo, hi = r["cp_range"]
    assert hi == pytest.approx(1.0, abs=0.05) and lo < -0.3        # stagnation, suction
    assert r["outputs"] == {"glb": "result.glb", "slices": ["slice_xz.png", "slice_xy.png"]}
    for name in ("slice_xz.png", "slice_xy.png"):
        assert (tmp_path / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert r["mesh"]["image"] == OPENFOAM_IMAGE and r["mesh"]["cells_estimate"] > 0


@pytest.mark.timeout(60)
async def test_flat_plate_study_from_recorded_output(tmp_path, monkeypatch):
    use(monkeypatch, FakeRunner("plate_re1e4"))
    r = await openfoam.run_openfoam_study(box(4, 100, 100), settings(speed_m_s=1.5), tmp_path,
                                          DOCKER)
    # square plate normal to the flow, Hoerner Cd 1.17: within 10%
    assert abs(r["cd"] - 1.17) / 1.17 < 0.10, r["cd"]
    assert r["ref_area_m2"] == pytest.approx(0.01, rel=0.03)
    assert r["converged"] is False and r["stopped"] == "iterations"


@pytest.mark.timeout(60)
async def test_yaw_sweep_runs_one_container_per_angle(tmp_path, monkeypatch):
    run = use(monkeypatch, FakeRunner("sphere_re2e4"))
    r = await openfoam.run_openfoam_study(sphere(n=16), settings(yaw_deg=[30]), tmp_path,
                                          DOCKER)
    assert len(run.calls) == 2
    mounts = {c[c.index("-v") + 1] for c in run.calls}
    assert len(mounts) == 2           # each angle its own case dir
    assert [s["yaw_deg"] for s in r["yaw_sweep"]] == [0.0, 30]


async def test_timeout_and_cancel_kill_the_container(tmp_path, monkeypatch):
    run = use(monkeypatch, FakeRunner(None, delay=30))
    with pytest.raises(SimError) as e:
        await openfoam.run_openfoam_study(sphere(n=8), settings(timeout_s=5), tmp_path, DOCKER)
    assert e.value.code == "timeout"
    name = run.calls[0][run.calls[0].index("--name") + 1]
    assert run.killed == [name]

    run2 = use(monkeypatch, FakeRunner(None, delay=30))
    task = asyncio.ensure_future(openfoam.run_openfoam_study(sphere(n=8), settings(),
                                                             tmp_path / "b", DOCKER))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(run2.killed) == 1


async def test_out_of_memory_and_stage_failures_are_named(tmp_path, monkeypatch):
    use(monkeypatch, FakeRunner(None, rc=137))
    with pytest.raises(SimError) as e:
        await openfoam.run_openfoam_study(sphere(n=8), settings(), tmp_path / "a", DOCKER)
    assert e.value.code == "memory"
    log = ("--> FOAM FATAL ERROR: (openfoam-2512)\n    Cannot find patch body in the mesh\n")
    use(monkeypatch, FakeRunner(None, rc=1, out="MISES_STAGE_FAILED snappyHexMesh\n",
                                extra={"log.snappyHexMesh": log}))
    with pytest.raises(SimError) as e:
        await openfoam.run_openfoam_study(sphere(n=8), settings(), tmp_path / "b", DOCKER)
    assert e.value.code == "solve_failed" and "snappyHexMesh" in e.value.message
    assert "Cannot find patch" in e.value.message and "STL" in e.value.hint
    use(monkeypatch, FakeRunner(None, rc=0))          # ran, but wrote nothing
    with pytest.raises(SimError) as e:
        await openfoam.run_openfoam_study(sphere(n=8), settings(), tmp_path / "c", DOCKER)
    assert "no force coefficients" in e.value.message


async def test_a_mesh_over_the_memory_budget_never_starts(tmp_path, monkeypatch):
    run = use(monkeypatch, FakeRunner("sphere_re2e4"))
    with pytest.raises(SimError) as e:
        await openfoam.run_openfoam_study(sphere(n=8), settings(refinement=4, memory_mb=256),
                                          tmp_path, DOCKER)
    assert e.value.code == "memory" and run.calls == []


# ---- the study record: label, fallback

def _job(tmp_path, tris):
    from mises.sim.cfd.foam_case import write_stl
    job = tmp_path / "job"
    job.mkdir()
    write_stl(job / "model.stl", tris)
    return job


def _noop(*a, **k):
    pass


@pytest.mark.timeout(60)
async def test_record_is_labelled_openfoam_with_its_band(tmp_path, monkeypatch):
    from mises.sim.cfd import validation
    monkeypatch.setattr(validation, "OPENFOAM_VALIDATION", [])      # the pre-benchmark state
    use(monkeypatch, FakeRunner("sphere_re2e4"))
    monkeypatch.setattr("mises.sim.cfd.openfoam_available", lambda: (DOCKER, None))
    s = studies.validate_settings("wind", {"solver": "openfoam", "speed_m_s": 3})
    out = await studies._run_wind(_job(tmp_path, sphere(n=24)), {}, s, _noop, 8192)
    assert out["fidelity_class"] == "cfd_openfoam"
    assert out["solver"]["name"] == "OpenFOAM" and out["solver"]["image"] == OPENFOAM_IMAGE
    assert out["caveats"] == studies.CAVEATS_WIND_OPENFOAM
    r = out["results"]
    assert r["error_band"] == r["model_error_band"] == 0.10 and r["validated"] is False
    assert "validation pending" in out["warnings"][0]
    rec = {"status": "done", "type": "wind", "part_id": "ball", "sim_id": "sim_0000000000aa",
           "fidelity_class": out["fidelity_class"], "results": r, "solver": out["solver"],
           "mesh": out["mesh"], "warnings": out["warnings"]}
    assert studies.fidelity_note(rec) == "OpenFOAM k-ω SST · ±10% (target; validation pending)"
    assert "OpenFOAM k-ω SST · ±10%" in studies.summary(rec)
    c = studies.card(rec)
    assert c["fidelity_note"].startswith("OpenFOAM k-ω SST")
    # I1: nothing on an unvalidated card says "validated"
    band = next(m for m in c["metrics"] if m["key"] == "error_band_pct")
    assert band["label"] == "model error (target, validation pending)"
    assert not any("validated" in m["label"] for m in c["metrics"])
    assert "validated" not in c["fidelity_note"].replace("validation pending", "")
    ch = {c["id"]: c for c in charts.charts(rec, tmp_path)["charts"]}
    assert ch["cd_history"]["x"]["label"] == "iteration"
    assert [s["name"] for s in ch["cd_history"]["series"]] == ["Cd"]
    # once a real benchmark row is recorded the label says validated
    rec["results"] = {**r, "validated": True}
    assert studies.fidelity_note(rec) == "OpenFOAM k-ω SST · ±10% (validated)"
    assert studies.error_band_label(rec) == "model error (validated)"
    # the empirical estimate's band is not a validated one either
    assert studies.error_band_label({"fidelity_class": "empirical"}) == \
        "model error (shape-class estimate)"


def test_sim_md_only_calls_the_built_in_band_validated():
    md = (Path(studies.__file__).parents[1] / "agent/builtin/sim.md").read_text()
    line = next(ln for ln in md.splitlines() if "(validated)\"" in ln)
    assert "built-in solver" in line
    assert "OpenFOAM" in md and "word for word" in md and "validation pending" in md


@pytest.mark.timeout(60)
async def test_missing_openfoam_falls_back_to_the_built_in_solver_and_says_so(tmp_path,
                                                                             monkeypatch):
    run = use(monkeypatch, FakeRunner("sphere_re2e4"))
    monkeypatch.setattr("mises.sim.cfd.openfoam_available",
                        lambda: (None, "no Docker or OrbStack"))
    # a 64 MB budget sends the built-in path to its quick empirical estimate
    s = studies.validate_settings("wind", {"solver": "openfoam", "memory_mb": 64})
    out = await studies._run_wind(_job(tmp_path, sphere(n=16) * 1.0), {}, s, _noop, 8192)
    assert run.calls == []
    assert out["fidelity_class"] == "empirical"
    assert "OpenFOAM was requested but is unavailable (no Docker or OrbStack)" in out["warnings"][0]


def test_band_is_the_target_until_a_benchmark_is_recorded(monkeypatch):
    from mises.sim.cfd import validation
    monkeypatch.setattr(validation, "OPENFOAM_VALIDATION", [])
    assert not openfoam_validated() and openfoam_error_band() == 0.10
    monkeypatch.setattr(validation, "OPENFOAM_VALIDATION",
                        [("sphere", 2e4, 3, 0.44, 0.47, -0.064),
                         ("flat plate", 1e4, 3, 1.31, 1.17, +0.12)])
    assert validation.openfoam_validated() and validation.openfoam_error_band() == 0.15
    assert validation.openfoam_validation_rows()[1]["body"] == "flat plate"


def test_recorded_real_benchmarks_are_both_within_the_target():
    # fix-bench: the real-solver rows are recorded only because BOTH are within 10%
    from mises.sim.cfd import validation
    rows = validation.openfoam_validation_rows()
    assert {r["body"] for r in rows} == {"sphere", "flat plate"}
    for r in rows:
        assert abs(r["cd"] - r["reference_cd"]) / r["reference_cd"] == pytest.approx(
            abs(r["error"]), abs=0.002)
        assert abs(r["error"]) < OPENFOAM_TARGET_BAND
    assert openfoam_validated() and openfoam_error_band() == 0.10


# ---- fix round 1: runner hardening

@pytest.mark.timeout(60)
async def test_cpus_are_clamped_to_the_docker_vm_and_leftovers_cleaned(tmp_path, monkeypatch):
    run = use(monkeypatch, FakeRunner("sphere_re2e4", ncpus=2))
    r = await openfoam.run_openfoam_study(sphere(n=16), settings(cpus=8), tmp_path, DOCKER)
    assert run.cleanups == 1
    assert "--cpus=2" in run.calls[0]
    assert "mpirun -np 2 " in (tmp_path / "openfoam_0/Allrun").read_text()
    assert r["mesh"]["cpus"] == 2
    # Reynolds, lRef and Aref share one reference length / frame (D, frontal area)
    assert r["reynolds_length_m"] == r["l_ref_m"] == pytest.approx(0.1, rel=0.01)
    assert r["reynolds"] == pytest.approx(1.225 * 10 * r["l_ref_m"] / 1.81e-5)   # default 10 m/s


async def test_a_container_that_will_not_stop_is_reported(tmp_path, monkeypatch):
    use(monkeypatch, FakeRunner(None, delay=30, kill_problem="the OpenFOAM container x could "
                                                             "not be stopped"))
    with pytest.raises(SimError) as e:
        await openfoam.run_openfoam_study(sphere(n=8), settings(timeout_s=5), tmp_path, DOCKER)
    assert e.value.code == "timeout" and "could not be stopped" in e.value.message


FAKE_DOCKER = """#!/bin/bash
here="$(cd "$(dirname "$0")" && pwd)"
echo "$*" >> "$here/calls.log"
case "$1" in
  run) exec /bin/sleep 30 ;;
  kill|rm) [ -f "$here/slow" ] && /bin/sleep 5; exit 0 ;;
  ps) printf 'abc123\\ndef456\\n' ;;
  info) echo 3 ;;
esac
"""


def _fake_docker(tmp_path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    p = d / "docker"
    p.write_text(FAKE_DOCKER)
    p.chmod(0o755)
    return p


@pytest.mark.timeout(60)
async def test_real_docker_runner_kills_then_removes_on_timeout(tmp_path):
    docker = _fake_docker(tmp_path)
    runner = openfoam.DockerRunner(str(docker))
    case = tmp_path / "job" / "openfoam_0"
    case.mkdir(parents=True)
    with pytest.raises(SimError) as e:
        await openfoam.run_case(case, runner, str(docker), cpus=1, iterations=10,
                                memory_mb=512, timeout_s=3, job_root=case.parent)
    assert e.value.code == "timeout" and "could not be stopped" not in e.value.message
    calls = (docker.parent / "calls.log").read_text().splitlines()
    name = calls[0].split("--name ")[1].split()[0]
    assert calls[0].startswith("run --rm --name mises-of-")
    assert calls[1:] == [f"kill {name}", f"rm -f {name}"]
    assert await runner.ncpu() == 3
    await runner.cleanup()
    assert (docker.parent / "calls.log").read_text().splitlines()[-2:] == [
        "ps -aq --filter label=mises.study=wind", "rm -f abc123 def456"]


@pytest.mark.timeout(60)
async def test_real_docker_runner_escalates_a_kill_that_hangs(tmp_path, monkeypatch):
    docker = _fake_docker(tmp_path)
    (docker.parent / "slow").write_text("")
    monkeypatch.setattr(openfoam, "KILL_TIMEOUT_S", 0.3)
    problem = await openfoam.DockerRunner(str(docker)).kill("mises-of-abc")
    assert problem and "could not be stopped" in problem and "docker rm -f mises-of-abc" in problem
    calls = (docker.parent / "calls.log").read_text().splitlines()
    assert calls == ["kill mises-of-abc", "rm -f mises-of-abc", "rm -f mises-of-abc"]


def test_container_outputs_are_read_no_follow_and_capped(tmp_path, monkeypatch):
    case = tmp_path / "case"
    shutil.copytree(FIX / "sphere_re2e4", case)
    coef = case / "postProcessing/forceCoeffs/0/coefficient.dat"
    secret = tmp_path / "secret.dat"
    secret.write_text(coef.read_text())
    coef.unlink()
    coef.symlink_to(secret)
    with pytest.raises(SimError) as e:
        openfoam.read_results(case)
    assert "unreadable" in e.value.message
    coef.unlink()
    coef.write_text(secret.read_text())
    monkeypatch.setattr(foam_parse, "TABLE_CAP", 1000)     # the table is now "oversized"
    with pytest.raises(SimError):
        openfoam.read_results(case)
    # a log symlinked at something huge is not followed
    log = case / "log.simpleFoam"
    log.unlink()
    log.symlink_to("/dev/zero")
    with pytest.raises(OSError):
        foam_parse.read_log_file(log)


def test_log_is_tailed_incrementally(tmp_path):
    log = tmp_path / "log.simpleFoam"
    log.write_text("banner\nTime = 1\n\nsmoothSolver: ...\nTime = 2\n")
    t = openfoam.LogTail(log)
    assert t.poll() == 2 and t.offset == log.stat().st_size
    with log.open("a") as f:
        f.write("x\nTime = 3")                       # the line is not finished yet
    assert t.poll() == 2
    with log.open("a") as f:
        f.write("\n\nTime = 4\n")
    assert t.poll() == 4
    big = "Time = 5\n" + "y" * (foam_parse.LOG_TAIL * 3) + "\nTime = 6\n"
    with log.open("a") as f:
        f.write(big)
    assert t.poll() == 6                              # only the last LOG_TAIL bytes read


# ---- real-solver benchmarks: ONLY in the user-approved QA step (the image must be pulled)

REAL = pytest.mark.skipif(os.environ.get("MISES_OPENFOAM_QA") != "1",
                          reason="real OpenFOAM benchmark: set MISES_OPENFOAM_QA=1 after the "
                                 "user approves pulling the image")


@REAL
@pytest.mark.openfoam_real
@pytest.mark.timeout(7200)
async def test_real_sphere_re2e4_within_10_percent(tmp_path):
    from mises.sim.cfd import openfoam_available
    docker, why = openfoam_available()
    assert docker, why
    r = await openfoam.run_openfoam_study(
        sphere(n=96), settings(speed_m_s=3, refinement=3, memory_mb=6144, timeout_s=7000),
        tmp_path, docker)
    print("sphere", r["cd"], r["steps"], r["converged"], r["mesh"]["cells_estimate"])
    assert abs(r["cd"] - 0.47) / 0.47 < 0.10, r["cd"]


@REAL
@pytest.mark.openfoam_real
@pytest.mark.timeout(7200)
async def test_real_flat_plate_within_10_percent(tmp_path):
    from mises.sim.cfd import openfoam_available
    docker, why = openfoam_available()
    assert docker, why
    r = await openfoam.run_openfoam_study(
        box(4, 100, 100), settings(speed_m_s=1.5, refinement=3, memory_mb=6144,
                                   timeout_s=7000), tmp_path, docker)
    print("plate", r["cd"], r["steps"], r["converged"], r["mesh"]["cells_estimate"])
    assert abs(r["cd"] - 1.17) / 1.17 < 0.10, r["cd"]
