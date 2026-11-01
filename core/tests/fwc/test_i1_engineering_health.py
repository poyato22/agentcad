"""FWC I1: engineering_checks reads assembly_health's nested report, so an overlapping pair
fails the done gate instead of being overwritten with "pass"."""
from types import SimpleNamespace

from mises.assembly.model import AssemblyStore
from mises.engineering import eng_tools as et
from mises.engineering import pipeline
from mises.types import ToolOutput

OVERLAP = {"ok": False, "summary": "assembly health: 1 overlapping pair(s)",
           "overlaps": [{"a": "arm", "b": "base", "a_id": "x", "b_id": "y",
                         "volume_mm3": 30.0}], "stray": [], "floating": [],
           "extents": None, "overlap_method": "occt"}
CLEAN = {**OVERLAP, "ok": True, "summary": "assembly health: clean", "overlaps": []}


class _Gate:
    def __init__(self, report):
        self.report, self.calls = report, []
        self.registry = SimpleNamespace(list=lambda names: names)

    async def call(self, run_id, name, args, **kw):
        self.calls.append((name, args))
        return ToolOutput(summary=self.report["summary"],
                          data={"project_id": "p", "assembly_health": self.report})


def _ctx(tmp_path, gate):
    return SimpleNamespace(workdir=tmp_path, run_id="r1", call_id="c1", store=None,
                           services={"gate": gate,
                                     "assembly": AssemblyStore(tmp_path / "a.db")})


async def test_an_overlapping_pair_is_recorded_as_a_fail(tmp_path):
    gate = _Gate(OVERLAP)
    ctx = _ctx(tmp_path, gate)
    asm = ctx.services["assembly"].get("p")
    await et._health(ctx, "p", asm)
    row = pipeline.load(tmp_path, "p")["assembly_health"]
    assert row["status"] == "fail" and row["margins"] == {"issues": 1}
    # the nested call never renders: its images are discarded here
    assert gate.calls == [("assembly_health", {"project_id": "p", "render": False})]


async def test_a_clean_report_passes(tmp_path):
    ctx = _ctx(tmp_path, _Gate(CLEAN))
    await et._health(ctx, "p", ctx.services["assembly"].get("p"))
    assert pipeline.load(tmp_path, "p")["assembly_health"]["status"] == "pass"


def test_health_verdict_never_passes_a_missing_report():
    assert et.health_verdict(True, {}) == ("fail", 1)
    assert et.health_verdict(True, {"assembly_health": {**CLEAN, "floating": [{}]}}) == \
        ("fail", 1)
    assert et.health_verdict(True, {"assembly_health": CLEAN}) == ("pass", 0)
