"""Review fix round (G-C): no free text in generated source, deltas held to the field ranges,
the filter radius capped in voxels, the new part id scope-checked, per-variant verdicts as
each finishes, memory-aware parallelism."""
import ast
import asyncio

import pytest
from pydantic import ValidationError

from mises.topopt import accept as accept_mod
from mises.topopt import jobs, tools
from mises.topopt.spec import AcceptArgs, TopoptGenerateArgs, TopoptRunArgs
from mises.types import ErrorCode, MisesError

from .test_jobs import BASE, _ctx, _part, _settle, fake  # noqa: F401 (fixture)

HOSTILE = '"""\nVARS={"injected":1}\nimport os\n"""'


def _body(src: str) -> list[str]:
    return [type(n).__name__ + (f":{n.targets[0].id}" if isinstance(n, ast.Assign) else "")
            for n in ast.parse(src).body]


def test_a_hostile_label_stays_data_in_the_generated_script():
    src = accept_mod.script("to_0123456789ab", "v0", "bracket", HOSTILE)
    tree = ast.parse(src)
    assert _body(src) == ["Expr", "Assign:GENERATIVE", "Assign:VARS", "FunctionDef"]
    gen = ast.literal_eval(tree.body[1].value)
    assert gen["name"] == HOSTILE and ast.literal_eval(tree.body[2].value) == {}
    assert "injected" not in ast.get_docstring(tree)


def test_variant_names_are_short_plain_labels():
    with pytest.raises(ValidationError):
        TopoptGenerateArgs(**BASE, variants=[{"name": HOSTILE}])
    with pytest.raises(ValidationError):
        TopoptGenerateArgs(**BASE, variants=[{"name": "x" * 41}])
    ok = TopoptGenerateArgs(**BASE, variants=[{"name": "light print-ready v2"}])
    assert ok.variants == [{"name": "light print-ready v2"}]


@pytest.mark.parametrize("delta", [
    {"filter_radius_mm": 1e6}, {"filter_radius_mm": 0}, {"resolution_mm": -1},
    {"resolution_mm": 0.01}, {"max_iter": 10**9}, {"max_iter": 1}, {"volume_fraction": 0.95},
    {"manufacturing": {"process": "laser"}}, {"penal": 4}])
def test_deltas_are_held_to_the_top_level_ranges(delta):
    with pytest.raises(ValidationError):
        TopoptGenerateArgs(**BASE, variants=[delta])


@pytest.mark.parametrize("sweep", [{"filter_radius_mm": [2, 1e6]}, {"resolution_mm": [0.5, -1]},
                                   {"volume_fraction": [0.2, 0.9]}])
def test_sweep_values_are_held_to_the_ranges(sweep):
    with pytest.raises(ValidationError):
        TopoptGenerateArgs(**BASE, sweep=sweep)


def test_a_delta_keeps_the_runs_manufacturing_unless_it_names_it():
    a = TopoptGenerateArgs(**BASE, manufacturing={"process": "print_3d"},
                           variants=[{"manufacturing": {"symmetry": ["y"]}}])
    assert a.variants == [{"manufacturing": {"symmetry": ["y"]}}]


def test_a_wide_filter_coarsens_the_voxels_instead_of_exploding(tmp_path):
    _part(tmp_path)
    ctx = _ctx(tmp_path)
    p = jobs.plan(ctx, TopoptRunArgs(**BASE, resolution_mm=0.5, manufacturing={
        "min_member_mm": 20}))
    v = p["variants"][0]
    assert v["spec"]["filter_radius"] <= jobs.MAX_FILTER_VOXELS
    assert v["spec"]["h"] == pytest.approx(10 / jobs.MAX_FILTER_VOXELS, abs=0.01)
    assert any("filter radius" in w for w in v["warnings"])
    p = jobs.plan(ctx, TopoptRunArgs(**BASE, resolution_mm=1.0, filter_radius_mm=100))
    assert p["variants"][0]["spec"]["filter_radius"] <= jobs.MAX_FILTER_VOXELS


async def test_accept_scope_checks_the_new_part_id(tmp_path, fake):  # noqa: F811
    _part(tmp_path)
    out = await tools.topopt_run.handler(_ctx(tmp_path), TopoptRunArgs(**BASE))
    tid = out.data["topopt_id"]
    await _settle(tid)
    narrow = _ctx(tmp_path, scope=("bracket",))
    with pytest.raises(MisesError) as e:
        await tools.topopt_accept.handler(narrow, AcceptArgs(topopt_id=tid, part_id="bracket"))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE


async def test_each_variant_is_marked_verified_as_it_finishes(tmp_path, fake):  # noqa: F811
    _part(tmp_path)
    ctx = _ctx(tmp_path)
    out = await tools.topopt_generate.handler(ctx, TopoptGenerateArgs(
        **BASE, sweep={"volume_fraction": [0.2, 0.3]}))
    tid = out.data["topopt_id"]
    seen = None
    for _ in range(400):
        rec = jobs.read_record(tmp_path, tid)
        if rec["status"] == "running" and any(v["status"] == "done" for v in rec["variants"]):
            seen = rec
            break
        await asyncio.sleep(0.02)
    await _settle(tid)
    if seen is not None:   # caught mid-run: the finished one already carries its verdict
        done = [v for v in seen["variants"] if v["status"] == "done"]
        assert all(v["verified"] and v["passes"] for v in done)


async def test_finish_notes_the_starting_agent_through_b3s_deliver_note(monkeypatch):
    """The B3 merge wired the hook: a finished run is a sim_finished-style note to the agent that
    started it; a run it already waited on, or one stopped with its agent, sends none; a run
    the user stopped on its own rides along with the next note (defer)."""
    from mises import jobs as bg
    from mises.orchestrator import lead
    bg.reset_for_tests()
    rec = {"topopt_id": "to_0123456789ab", "run_id": "r1", "status": "done", "summary": "ok",
           "part_id": "bracket"}
    sent = []

    async def deliver(rt, run_id, text, meta, defer=False):
        sent.append((run_id, text, meta, defer))
    monkeypatch.setattr(lead, "deliver_note", deliver)
    assert jobs.note_lead(object(), rec) is True
    await asyncio.sleep(0)
    run_id, text, meta, defer = sent[0]
    assert run_id == "r1" and "(to_0123456789ab) finished" in text and not defer
    assert text.startswith("[AgentCAD system note, not from the user]") and '(data): "ok"' in text
    assert meta == {"kind": "sim_finished", "job": "topopt", "id": "to_0123456789ab",
                    "topopt_id": "to_0123456789ab", "status": "done"}
    assert jobs.note_lead(None, rec) is False
    # read with topopt_wait / stopped with its agent: no note
    job = bg.register(bg.Job(id="to_0123456789ab", kind="topopt", run_id="r1"))
    job.consumed = True
    assert jobs.note_lead(object(), rec) is False
    job.consumed, job.stopped = False, True
    assert jobs.note_lead(object(), {**rec, "status": "cancelled"}) is False
    # the user stopped this one run: a deferred note
    job.stopped, job.defer = False, True
    assert jobs.note_lead(object(), {**rec, "status": "cancelled"}) is True
    await asyncio.sleep(0)
    assert sent[-1][3] is True and "cancelled by the user" in sent[-1][1]
    bg.reset_for_tests()


def test_parallel_variants_fit_memory_and_leave_heavy_slots(monkeypatch):
    from mises import hardware, heavy
    monkeypatch.setattr(heavy, "heavy_limit", lambda *a: 6)
    monkeypatch.setattr(hardware, "usable_memory_gb", lambda *a: 18.0)
    assert jobs.parallel_variants() == 3
    monkeypatch.setattr(hardware, "usable_memory_gb", lambda *a: 6.0)
    assert jobs.parallel_variants() == 1
    monkeypatch.setattr(hardware, "usable_memory_gb", lambda *a: 64.0)
    monkeypatch.setattr(heavy, "heavy_limit", lambda *a: 3)
    assert jobs.parallel_variants() == 1
