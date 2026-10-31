"""Plan 9 G-B fix round 1 (review I2 / I3 / I4): classifier false positives, the user's plain
cues and override, follow-up rules, and no archetype or brand leaks in the prompt block."""
import pytest

from mises import design_intensity as di


@pytest.mark.parametrize("prompt", [
    "A phone mount bracket for my car dashboard", "A toy car wheel axle",
    "A kettle lid hinge pin", "A watch strap pin", "A lamp bracket",
    "A helmet mount for a GoPro", "Spot weld this steel bracket", "Spot face the M6 holes",
    "A fixture for N52 neo magnets", "Plot the characteristic curve of a spring",
    "A bottleneck-free hydraulic manifold", "A shell-and-tube heat exchanger",
    "an adapter for a camera body", "a bolt for a chair"])
def test_functional_parts_and_lookalike_words_are_not_design(prompt):
    dc = di.design_intensity(prompt)
    assert dc["cls"] == "engineering", dc
    assert dc["archetype"] is None


def test_explicit_styling_lets_the_product_win_over_the_part():
    assert di.design_intensity("a sleek sculpted lamp bracket")["cls"] == "design"
    # a functional noun outside the head phrase does not demote the product
    dc = di.design_intensity("a humanoid robot with a camera mount on the head")
    assert dc["cls"] == "design" and dc["archetype"] == "humanoid"


@pytest.mark.parametrize("prompt", [
    "a simple plain humanoid robot", "a robot dog, just functional, no styling",
    "a car body, I don't care how it looks"])
def test_plain_cues_lower_the_class(prompt):
    dc = di.design_intensity(prompt)
    assert dc["cls"] == "engineering" and any("plain geometry" in r for r in dc["reasons"])
    assert di.combine(dc, 0.95) is dc          # a model score cannot overrule the user


def test_follow_up_rises_on_signal_falls_on_plain_and_ignores_functional_asks():
    eng = di.design_intensity("a camera bracket")
    up = di.follow_up(eng, "now make it a sleek humanoid robot head")
    assert up["cls"] == "design"
    assert di.follow_up(up, "add a phone mount") is up
    assert di.follow_up(up, "spot face the holes") is up
    down = di.follow_up(up, "actually keep it simple and just functional")
    assert down["cls"] == "engineering"
    user = di.user_override("design")
    assert di.follow_up(user, "keep it plain") is user
    assert di.strongest(di.design_intensity("a bracket"), user) is user


def test_user_override_shape():
    dc = di.user_override("mixed")
    assert dc == {"score": 0.5, "cls": "mixed", "per_part": {}, "source": "user",
                  "reasons": ["set by the user on this build"], "archetype": None}
    with pytest.raises(ValueError):
        di.user_override("pretty")


_ARCH_WORDS = {"humanoid": ("humanoid", "head height", "shoulder:hip"),
               "quadruped": ("quadruped", "body length to leg"),
               "car": ("car_body", "wheel diameter", "wheelbase")}


@pytest.mark.parametrize("prompt, own", [
    ("a sleek kettle", None), ("a jet engine", None),
    ("build me a robotic dog", "quadruped"), ("design a car body", "car"),
    ("a humanoid robot", "humanoid")])
def test_prompt_block_names_no_other_archetype(prompt, own):
    """Review I4: a kettle build never hears about heads or wheels; each archetype hears only
    its own recipe and proportion units; no brand names anywhere."""
    text = di.prompt_section(di.design_intensity(prompt)).lower()
    assert text
    for arch, words in _ARCH_WORDS.items():
        for w in words:
            if arch == own:
                assert w in text, (arch, w)
            else:
                assert w not in text, (arch, w)
    for brand in ("optimus", " neo", "spot", "go2", "tesla", "porsche"):
        assert brand not in text
    if own is None:
        for generic in ("torso", "legs", "the head"):
            assert generic not in text


def test_recipe_index_names_no_brands():
    from mises.cad import recipes
    idx = recipes.recipe_index().lower()
    for brand in ("optimus", " neo", "spot", "go2"):
        assert brand not in idx
