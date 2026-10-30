"""recipes_list / recipe_doc tools, the prompt index, and `ms` in the sandboxed worker (G2)."""
from pathlib import Path

import pytest

from mises.agent.definitions import load_definitions
from mises.cad import mises_shapes as ms
from mises.cad import recipes
from mises.cad.runner import run_build
from mises.tools.registry import ToolRegistry
from mises.types import MisesError, SafetyClass

BUILTIN = Path(recipes.__file__).resolve().parents[1] / "agent" / "builtin"


async def test_recipes_list_names_every_recipe_with_a_signature():
    out = await recipes.recipes_list.handler(None, recipes.RecipesListArgs())
    rows = out.data["recipes"]
    assert [r["name"] for r in rows] == list(ms.__all__)
    bwb = next(r for r in rows if r["name"] == "blended_wing_body")
    assert bwb["signature"].startswith("ms.blended_wing_body(span=524.0")
    assert "B-2" in bwb["summary"]


async def test_recipe_doc_returns_full_doc_and_rejects_unknown():
    out = await recipes.recipe_doc.handler(None, recipes.RecipeDocArgs(name="ms.enclosure"))
    assert out.data["doc"].startswith("ms.enclosure(outer=(80.0, 60.0, 30.0), wall=2.0")
    assert "snap_fits" in out.data["doc"] and "EnclosureParts" in out.data["doc"]
    with pytest.raises(MisesError) as e:
        await recipes.recipe_doc.handler(None, recipes.RecipeDocArgs(name="ellipsoid"))
    assert e.value.code == "not_found"


def test_tools_are_read_only_and_registered_with_the_mirror_tools():
    from mises.cad.tools import register_mirror_tools

    reg = ToolRegistry()
    register_mirror_tools(reg)
    for name in ("recipes_list", "recipe_doc"):
        assert reg.get(name).safety == SafetyClass.READ


def test_index_comes_from_source_without_importing_build123d_on_the_host():
    # every public recipe has a one-line docstring summary for the prompt index
    for name, _sig, summary, _doc in recipes._catalog():
        assert summary and len(summary) < 120, name
    assert recipes.recipe_index().count("\n") == len(ms.__all__) - 1


@pytest.mark.parametrize("agent", ["designer", "engineer"])
def test_prompts_carry_the_generated_index_and_the_no_ellipsoid_rule(agent):
    body = (BUILTIN / f"{agent}.md").read_text()
    assert body.count(recipes.PROMPT_BEGIN) == 1 and body.count(recipes.PROMPT_END) == 1
    start = body.index(recipes.PROMPT_BEGIN)
    block = body[start:body.index(recipes.PROMPT_END) + len(recipes.PROMPT_END)]
    assert block == recipes.prompt_section(), "regenerate with recipes.prompt_section()"
    assert "Never approximate organic or aero shapes" in block
    assert "ellipsoids" in block and "boxes" in block
    defn = load_definitions([]).get(agent)
    assert recipes.prompt_section() in defn.body


@pytest.mark.slow
async def test_ms_is_preimported_in_the_sandboxed_worker(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "part.py").write_text(
        "import mises_shapes\n"
        "VARS = {'span': 300.0}\n"
        "def build(v):\n"
        "    assert mises_shapes is ms\n"
        "    return ms.wing(span=v['span'], root_chord=80, tip_chord=40)\n")
    r = await run_build(d, {"span": 300.0})
    assert r.ok, r.error
    size = r.metrics["bbox"]["size"]
    assert size[1] == pytest.approx(300, rel=1e-3)
    assert r.metrics["valid"]


@pytest.mark.slow
async def test_recipe_error_reaches_the_agent_as_a_plain_reason(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "part.py").write_text("def build(v):\n"
                               "    return ms.blended_wing_body(notches=(0.5, 0.4, 0.6))\n")
    r = await run_build(d, {})
    assert not r.ok
    assert "notches must rise" in r.error and 'part.py", line 2' in r.error
