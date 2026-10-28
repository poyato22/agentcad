"""Scripts written by weaker local models assume build123d names are already in scope."""
from pathlib import Path

import pytest

from mises.cad.runner import run_build

pytestmark = pytest.mark.slow

FAILING_LOCAL_MODEL = '''VARS = {"size": 10.0}

def build(v: dict) -> build123d:
    with BuildPart() as p:
        Box(v["size"], v["size"], v["size"])
    return p.part
'''


def mk(tmp_path: Path, script: str) -> Path:
    d = tmp_path / "p"
    d.mkdir()
    (d / "part.py").write_text(script)
    return d


async def test_annotation_naming_build123d_does_not_break(tmp_path):
    r = await run_build(mk(tmp_path, FAILING_LOCAL_MODEL), {"size": 10})
    assert r.ok, r.error
    assert r.metrics["volume_mm3"] == pytest.approx(1000)


async def test_bd_alias_and_math_preloaded(tmp_path):
    script = ('VARS = {}\ndef build(v):\n    r = math.sqrt(4.0)\n'
              '    return bd.Box(10, 10, r) + build123d.Box(1, 1, 1)\n')
    r = await run_build(mk(tmp_path, script), {})
    assert r.ok, r.error


async def test_undefined_name_gets_hint_and_part_line_numbers(tmp_path):
    script = 'VARS = {}\n\n\ndef build(v):\n    x = 1\n    return Bxo(10, 10, 10)\n'
    r = await run_build(mk(tmp_path, script), {})
    assert not r.ok
    assert "NameError" in r.error and "Bxo" in r.error
    assert 'part.py", line 6' in r.error and "Bxo(10, 10, 10)" in r.error
    assert "pre-imported" in r.error
    assert "importlib" not in r.error and "worker.py" not in r.error


async def test_missing_build_function_message(tmp_path):
    r = await run_build(mk(tmp_path, "VARS = {}\n"), {})
    assert not r.ok
    assert "def build(v: dict)" in r.error and "Part" in r.error


async def test_create_tool_description_example_actually_builds(tmp_path):
    from mises.cad.tools import EXAMPLE_SCRIPT, mirror_part_create

    assert EXAMPLE_SCRIPT in mirror_part_create.description
    r = await run_build(mk(tmp_path, EXAMPLE_SCRIPT), {})
    assert r.ok, r.error
    assert len(r.metrics["holes"]) == 1
