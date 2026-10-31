"""The D28 design eval set (core/evals/design/prompts.json): 20 prompts, each class-labelled,
and the classifier agrees with every label. Also a check that the eval scripts import cleanly
(they are run by hand, never in CI)."""
import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

from mises.design_intensity import design_intensity

EVALS = Path(__file__).resolve().parents[1] / "evals" / "design"
SET = json.loads((EVALS / "prompts.json").read_text())["prompts"]


def test_the_set_has_twenty_prompts_across_all_classes():
    assert len(SET) == 20 and len({p["id"] for p in SET}) == 20
    counts = Counter(p["expect"] for p in SET)
    assert counts["design"] >= 8 and counts["mixed"] >= 3 and counts["engineering"] >= 5
    ids = {p["id"] for p in SET}
    assert {"humanoid", "robot_dog", "car", "kettle", "drone", "mold", "jet_nacelle"} <= ids


@pytest.mark.parametrize("item", SET, ids=[p["id"] for p in SET])
def test_classifier_matches_the_label(item):
    assert design_intensity(item["prompt"])["cls"] == item["expect"]


@pytest.mark.parametrize("script", ["run_eval.py", "preference_sheet.py", "humanoid_proof.py",
                                    "preview.py"])
def test_eval_scripts_import(script):
    spec = importlib.util.spec_from_file_location(script[:-3], EVALS / script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # module level only: no build, no model, no network
