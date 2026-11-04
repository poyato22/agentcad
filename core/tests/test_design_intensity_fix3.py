"""Plan 9 G-B fix round 3 (re-review 2, I2): the head object of the request decides. Every other
noun only modifies it ("a car seat gap filler" is a filler), so a design-heavy product named as
a modifier never makes the build design work. Design needs a design-heavy head object (a robot,
a dog, a kettle, a car body, a lamp, a chair, a drone shell...) or explicit styling words;
anything else is engineering by default (the user raises it with the Design focus chip)."""
import pytest

from mises import design_intensity as di

# the reviewer's four misses, then the coordinator's examples
REVIEW = ["a car seat gap filler", "a water bottle cage for my bike", "a mouse bungee for my desk",
          "a keyboard wrist rest"]
# 24 more everyday functional parts whose head noun is in no word list; most name a
# design-heavy product as a modifier
UNLISTED = [
    "a cat door flap", "a robot vacuum ramp for my doorstep", "a kettle descaler scoop",
    "a humanoid robot servo horn", "a car roof rack crossbar", "a motorcycle chain guard",
    "a desk lamp counterweight", "a sports car model display base", "a coffee machine drip tray grate",
    "a robot arm cable chain link", "a headphone cable organizer", "a speaker grille frame for my car",
    "a phone case for an iPhone 15", "a replacement battery cover for my TV remote",
    "a chair leg glide", "a dog bowl stand", "a drone battery strap", "a toy car axle",
    "a lamp shade clip", "a camera body cap", "a car cup holder insert", "a perfume bottle cap insert",
    "a robot dog charging dock", "a keyboard tenting kit", "a 5 mm spacer for a sports car mirror",
    "a watch strap pin", "a bike light mount", "a phone cover", "a keyboard dust cover",
]


@pytest.mark.parametrize("prompt", REVIEW + UNLISTED)
def test_a_functional_head_object_is_engineering(prompt):
    dc = di.design_intensity(prompt)
    assert dc["cls"] == "engineering", (prompt, dc)
    assert dc["archetype"] is None


@pytest.mark.parametrize("prompt, head", [
    ("a car seat gap filler", "filler"), ("a water bottle cage for my bike", "cage"),
    ("a mouse bungee for my desk", "bungee"), ("a keyboard wrist rest", "rest"),
    ("a 3d model of a humanoid robot", "robot"), ("a 5 inch FPV drone frame", "frame"),
    ("build me a robotic dog about 70 cm long", "dog"), ("a set of gears", "gear"),
])
def test_the_head_object_is_the_last_noun_of_the_first_real_noun_phrase(prompt, head):
    got, _hit, _how = di.head_object(" ".join(prompt.lower().split()))
    assert got.rstrip("s") == head.rstrip("s")


@pytest.mark.parametrize("prompt, arch", [
    ("a robot dog", "quadruped"), ("a humanoid robot", "humanoid"),
    ("a 3d printable humanoid robot", "humanoid"), ("a delivery robot", "robot"),
    ("a companion robot", "robot"), ("an electric kettle", "consumer_product"),
    ("a desk lamp", "consumer_product"), ("an office chair", "consumer_product"),
    ("a car body", "car"), ("a car hood", "car"), ("a robot dog shell", "quadruped"),
    ("a mouse shell for a right hand", "consumer_product"), ("a drone shell", "drone"),
    ("a bluetooth speaker", "consumer_product"), ("a robot vacuum", "consumer_product"),
    ("the humanoid robot covers", "humanoid"), ("a car door panel", "car"),
])
def test_a_design_heavy_head_object_is_design(prompt, arch):
    dc = di.design_intensity(prompt)
    assert dc["cls"] == "design" and dc["archetype"] == arch, (prompt, dc)


@pytest.mark.parametrize("prompt, cls", [
    ("a 5 inch FPV drone frame", "mixed"), ("a robot arm", "mixed"), ("a jet engine", "mixed"),
    ("an enclosure", "engineering"), ("a gearbox housing", "engineering"),
])
def test_structures_and_bare_enclosures(prompt, cls):
    assert di.design_intensity(prompt)["cls"] == cls


def test_styling_words_still_make_it_design_work():
    # the user asked for looks: an accessory becomes product design
    assert di.design_intensity("a sleek phone case")["cls"] == "design"
    assert di.design_intensity("a beautiful sculpted keyboard wrist rest")["cls"] != "engineering"


def test_an_unknown_head_says_so_and_points_at_the_chip():
    dc = di.design_intensity("a car seat gap filler")
    assert any("filler" in r and "Design focus" in r for r in dc["reasons"])


@pytest.mark.parametrize("prompt", [
    # DRONE demo: an em dash ends the head phrase (it was read as "... cinewhoop all-up weight")
    "Design a sub-100 g 3-inch FPV cinewhoop — all-up weight with the battery under 100 g",
    "a 3 inch cinewhoop – light and stiff", "a tiny whoop", "a 2.5 inch toothpick quad",
])
def test_a_whoop_is_a_drone_and_a_dash_ends_the_head_phrase(prompt):
    dc = di.design_intensity(prompt)
    assert dc["archetype"] == "drone" and dc["cls"] == "mixed", (prompt, dc)
