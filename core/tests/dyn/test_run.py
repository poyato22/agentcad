"""The dynamics job runs in the sandboxed child process (Plan 10 C3)."""
import pytest

from mises.dyn import run
from mises.types import MisesError

from .test_engine import arm_model

pytestmark = pytest.mark.slow


async def test_sandboxed_worker_runs_the_arm_exactly(tmp_path):
    out = await run.run_job(tmp_path, {"task": "torque", "model": arm_model(),
                                       "trajectory": {"kind": "hold", "duration_s": 0.1}})
    by = {j["joint"]: j for j in out["joints"]}
    assert by["j1"]["peak_inverse"] == pytest.approx(1.9865, abs=1e-4)
    assert not list((tmp_path / "dyn_tmp").iterdir())      # the job dir is cleaned up


async def test_a_bad_model_fails_in_the_child_not_the_server(tmp_path):
    bad = arm_model()
    bad["bodies"][1]["parent"] = "nope"
    with pytest.raises(MisesError) as e:
        await run.run_job(tmp_path, {"task": "build", "model": bad})
    assert "dynamics failed" in e.value.message


def test_results_are_kept_per_build(tmp_path):
    rid = run.save_result(tmp_path, "b1", "torque", {"summary": "x"})
    assert run.load_result(tmp_path, "b1", rid)["summary"] == "x"
    assert run.load_result(tmp_path, "b1", "../x") is None
