"""Agent-built simulator plugins (plan 2026-11-06 S4 S5 S7 S8 S9): the builder writes only in
the plugin folder; an independent static review refuses network / process / file / dynamic
code; the code runs only in the sandboxed worker, which refuses escape attempts even when
the review is bypassed; the plugin stays disabled until its validation cases (known answers)
pass; enabled plugins are listed with fidelity agent_built and their evidence."""
import asyncio
import math
import shutil

import pytest

from mises.sim import plugins as pl
from mises.sim.requests import SimRequest, SimRequests
from mises.store import Store
from mises.types import MisesError

pytestmark = pytest.mark.skipif(shutil.which("sandbox-exec") is None and
                                not __import__("os").path.exists("/usr/bin/sandbox-exec"),
                                reason="macOS sandbox-exec needed")

# projectile range on flat ground, no drag: R = v^2 sin(2 theta) / g (closed form)
GOOD = """import math

def solve(inputs):
    v, th = inputs["speed"], math.radians(inputs["angle"])
    return {"range": v * v * math.sin(2 * th) / 9.80665}
"""
WRONG = GOOD.replace("9.80665", "4.9")


def _req(**kw):
    base = dict(
        name="projectile_range", computes="range of a drag-free projectile on flat ground",
        inputs=[{"name": "speed", "unit": "m/s"}, {"name": "angle", "unit": "deg"}],
        outputs=[{"name": "range", "unit": "m"}],
        why="the launcher's 'lands within 10 m' claim is unverified",
        method="closed-form ballistic range", fidelity_class="analytic", complexity="small",
        validation_cases=[
            {"name": "45 deg", "inputs": {"speed": 10, "angle": 45},
             "expected": {"range": 100 / 9.80665}, "tolerance_pct": 0.5,
             "source": "R = v^2 sin 2theta / g (closed form)"},
            {"name": "30 deg", "inputs": {"speed": 20, "angle": 30},
             "expected": {"range": 400 * math.sin(math.radians(60)) / 9.80665},
             "tolerance_pct": 0.5, "source": "closed form"}])
    return SimRequest.model_validate({**base, **kw})


def _building(tmp_path):
    s = Store(tmp_path / "mises.db")
    home = tmp_path / "home"
    home.mkdir()
    reqs = SimRequests(s)
    rec, _ = reqs.file(_req(), run_id=None, project_id="p1", existing=[])
    reqs.decide(rec["id"], "accept", decided_by="user")
    reqs.start_build(rec["id"], "builder-root")
    return s, home, rec["id"]


def test_builder_writes_only_inside_its_folder(tmp_path):
    home = tmp_path / "home"
    for bad in ("../x.py", "/etc/x.py", "sub/deep/x.py", "x.sh", ".hidden.py",
                "manifest.json"):
        with pytest.raises(MisesError):
            pl.write_file(home, "projectile_range", bad, "x = 1\n")
    p = pl.write_file(home, "projectile_range", "tests/test_x.py", "x = 1\n")
    assert p.parent.parent == home / "simulators" / "projectile_range"


@pytest.mark.parametrize("src, what", [
    ("import socket\ndef solve(i):\n    return {}\n", "import socket"),
    ("import subprocess\ndef solve(i):\n    return {}\n", "import subprocess"),
    ("import os\ndef solve(i):\n    return {}\n", "import os"),
    ("def solve(i):\n    open('/tmp/x', 'w')\n    return {}\n", "open"),
    ("def solve(i):\n    return {}.__class__.__bases__\n", "dunder"),
    ("def solve(i):\n    return eval('1')\n", "eval"),
])
def test_review_refuses_escape_code(tmp_path, src, what):
    d = tmp_path / "p"
    d.mkdir()
    (d / "solver.py").write_text(src)
    r = pl.review(d)
    assert not r["passed"] and any(what in f for f in r["findings"])


def test_review_passes_plain_numeric_code(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "solver.py").write_text(GOOD)
    assert pl.review(d)["passed"]


def test_sandbox_refuses_escape_even_without_the_review(tmp_path):
    """The review bypassed: the worker itself must stop network, outside writes, reads of the
    user's files and process spawning."""
    home = tmp_path / "home"
    outside = tmp_path / "outside.txt"
    secret = pl.Path.home() / ".mises_sandbox_probe_does_not_matter"
    pl.write_file(home, "escape_probe", "solver.py", f"""
import socket, os, subprocess
def solve(inputs):
    out = {{}}
    try:
        socket.create_connection(("1.1.1.1", 80), timeout=2); out["net"] = 1.0
    except Exception:
        out["net"] = 0.0
    try:
        open({str(outside)!r}, "w").write("x"); out["write"] = 1.0
    except Exception:
        out["write"] = 0.0
    try:
        os.listdir({str(pl.Path.home() / "Documents")!r}); out["read_home"] = 1.0
    except Exception:
        out["read_home"] = 0.0
    try:
        subprocess.run(["/bin/echo", "hi"], check=True, capture_output=True); out["spawn"] = 1.0
    except Exception:
        out["spawn"] = 0.0
    return out
""")
    res = asyncio.run(pl.run_cases(home, "escape_probe", [{"name": "probe", "inputs": {}}]))
    assert res[0].get("outputs") == {"net": 0.0, "write": 0.0, "read_home": 0.0, "spawn": 0.0}, res
    assert not outside.exists() and not secret.exists()


def test_validation_passes_and_enables_with_evidence(tmp_path):
    s, home, rid = _building(tmp_path)
    pl.write_file(home, "projectile_range", "solver.py", GOOD)
    plugins = pl.SimPlugins(s, home)
    p = asyncio.run(plugins.validate(rid))
    assert p["enabled"] and p["validation"]["passed"]
    assert p["validation"]["label"] == "agent-built simulator, validated against 2 cases"
    assert all(e["pass"] for e in p["validation"]["evidence"])
    assert SimRequests(s).get(rid)["status"] == "built"
    out = asyncio.run(plugins.run("projectile_range", {"speed": 10, "angle": 45}))
    assert out["fidelity"] == "agent_built" and out["units"] == {"range": "m"}
    assert out["outputs"]["range"] == pytest.approx(100 / 9.80665, rel=1e-9)
    reg = plugins.registry()
    assert reg[0]["id"] == "projectile_range" and reg[0]["available"]
    assert reg[0]["version"] == 1 and "validated against 2" in reg[0]["validation"]


def test_failed_validation_keeps_the_plugin_disabled(tmp_path):
    s, home, rid = _building(tmp_path)
    pl.write_file(home, "projectile_range", "solver.py", WRONG)
    plugins = pl.SimPlugins(s, home)
    p = asyncio.run(plugins.validate(rid))
    assert not p["enabled"] and "validation failed: 0 of 2" in p["why_disabled"]
    assert SimRequests(s).get(rid)["status"] == "building"
    with pytest.raises(MisesError, match="not enabled"):
        asyncio.run(plugins.run("projectile_range", {"speed": 1, "angle": 1}))
    with pytest.raises(MisesError, match="stays off"):
        plugins.set_enabled("projectile_range", True)
    assert plugins.registry()[0]["available"] is False


def test_review_failure_never_runs_the_code(tmp_path, monkeypatch):
    s, home, rid = _building(tmp_path)
    pl.write_file(home, "projectile_range", "solver.py", "import socket\n" + GOOD)
    ran = []
    monkeypatch.setattr(pl, "run_cases", lambda *a, **k: ran.append(1))
    p = asyncio.run(pl.SimPlugins(s, home).validate(rid))
    assert not p["enabled"] and p["why_disabled"].startswith("review refused") and not ran


def test_builder_tools_start_pause_and_list(tmp_path, monkeypatch):
    """Through the tools: the builder's first write starts the build (its run's usage is the
    spend), a paused budget stops writes, and a validated plugin shows in sim_types_list."""
    from mises.runtime import Runtime
    from mises.sim import request_tools as rt_tools
    from mises.sim.tools import sim_types_list, TypesArgs
    from mises.tools.registry import ToolContext
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    rt = Runtime(tmp_path / "home")
    reqs = SimRequests(rt.store)
    rec, _ = reqs.file(_req(), run_id=None, project_id=None, existing=[])
    reqs.decide(rec["id"], "accept", decided_by="user")
    builder = rt.store.create_run("sim-builder", "anthropic", "claude-opus-4-5", "b").id
    ctx = ToolContext(run_id=builder, user="u", store=rt.store, events=rt.events,
                      workdir=tmp_path, policy=None, call_id="c", services={"runtime": rt})
    w = rt_tools.WriteArgs(request_id=rec["id"], path="solver.py", content=GOOD)
    asyncio.run(rt_tools.simulator_write_file.handler(ctx, w))
    assert reqs.get(rec["id"])["status"] == "building"
    out = asyncio.run(rt_tools.simulator_validate.handler(
        ctx, rt_tools.StatusArgs(request_id=rec["id"])))
    assert out.data["enabled"]
    listed = asyncio.run(sim_types_list.handler(ctx, TypesArgs()))
    assert [t["id"] for t in listed.data["agent_built"]] == ["projectile_range"]
    assert "validated against 2 cases" in listed.summary
    # a second build over budget: writes stop at the pause
    rec2, _ = reqs.file(_req(name="projectile_range_drag"), run_id=None, project_id=None,
                        existing=[])
    reqs.decide(rec2["id"], "accept", decided_by="user")
    b2 = rt.store.create_run("sim-builder", "anthropic", "claude-opus-4-5", "b").id
    ctx2 = ToolContext(run_id=b2, user="u", store=rt.store, events=rt.events,
                       workdir=tmp_path, policy=None, call_id="c", services={"runtime": rt})
    asyncio.run(rt_tools.simulator_write_file.handler(
        ctx2, rt_tools.WriteArgs(request_id=rec2["id"], path="solver.py", content=GOOD)))
    rt.store.add_usage(b2, input_tokens=1, output_tokens=1,
                       usd=rec2["estimate"]["budget_pause_usd"] + 0.01)
    with pytest.raises(MisesError, match="budget"):
        asyncio.run(rt_tools.simulator_write_file.handler(
            ctx2, rt_tools.WriteArgs(request_id=rec2["id"], path="solver.py", content=GOOD)))
    assert reqs.get(rec2["id"])["status"] == "paused_budget"


def test_validate_needs_an_accepted_build(tmp_path):
    s = Store(tmp_path / "mises.db")
    rec, _ = SimRequests(s).file(_req(), run_id=None, project_id="p1", existing=[])
    with pytest.raises(MisesError, match="pending, not building"):
        asyncio.run(pl.SimPlugins(s, tmp_path).validate(rec["id"]))
