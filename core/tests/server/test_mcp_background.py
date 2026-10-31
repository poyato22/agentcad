"""Plan 9 B3 fix round 1: an MCP agent's (e.g. Claude Code's) sim_run returns at once and
the study outlives the HTTP request (the reviewer's probe, as a test)."""
import asyncio
import json
import time

import pytest

from mises import heavy, jobs
from mises.sim import tools as sim_tools
from mises.providers.fake import FakeProvider
from mises.sim import studies
from mises.types import SafetyClass

from .mcp_helpers import mcp_client, running

SETTINGS = {"load_cases": [{"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
                            "loads": [{"kind": "force", "face": "x-max", "newtons": 10,
                                       "direction": "-z"}]}]}


@pytest.fixture
def solver(monkeypatch):
    """A fake mesher + solver that takes `delay` seconds."""
    heavy.reset_for_tests()
    studies._sems.clear()
    jobs.reset_for_tests()
    state = {"delay": 0.3}

    async def dispatch(*a, **kw):
        await asyncio.sleep(state["delay"])
        return {"results": {"max_von_mises_mpa": 12.0, "max_displacement_mm": 0.2,
                            "safety_factor": 3.1, "error_band": 0.04}, "warnings": [],
                "mesh": {"fine": {"size_mm": 1.0}, "coarse": {"size_mm": 2.0}}}
    monkeypatch.setattr(studies, "_dispatch", dispatch)
    monkeypatch.setattr(sim_tools, "find_ccx", lambda: "/usr/bin/true")
    monkeypatch.setattr(studies, "ccx_version", lambda ccx: "2.21")
    yield state
    jobs.reset_for_tests()


def _part(home):
    d = home / "parts" / "beam"
    d.mkdir(parents=True, exist_ok=True)
    (d / "metrics.json").write_text(json.dumps({"volume_mm3": 1000.0}))
    (d / "model.step").write_text("ISO-10303-21; fake")


def _record(rt, sim_id):
    return json.loads((studies.find_sim(rt.home / "parts", sim_id) / "result.json").read_text())


async def test_an_mcp_agents_study_outlives_its_request(tmp_path, monkeypatch, solver):
    import mises.runtime as runtime_mod
    from mises.runtime import Runtime
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (FakeProvider([]), "m"))
    _part(rt.home)
    solver["delay"] = 1.0
    async with running(rt) as app:
        rid = rt.create_external_run("codex", provider="openai", model="gpt-x",
                                     allowed_classes={SafetyClass.READ,
                                                      SafetyClass.LOCAL_MUTATE})
        http, client = mcp_client(app, rid)
        async with http, client:
            t0 = time.monotonic()
            res = await client.call_tool("sim_run", {"type": "static", "part_id": "beam",
                                                     "settings": SETTINGS})
            assert time.monotonic() - t0 < 1.0
            body = json.loads(res.content[0].text)
        assert body["data"]["status"] == "running"
        sid = body["data"]["sim_id"]
        await asyncio.sleep(0.2)                    # the request and its client are gone
        assert sid in studies.active_ids()
        await asyncio.wait({jobs.get(sid).task}, timeout=10)
        assert _record(rt, sid)["status"] == "done"
