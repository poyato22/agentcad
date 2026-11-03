"""G4: a part with no look of its own shows its material's or the approved concept's (the
user's ornicopter: materials set, CMF approved, every part grey)."""
from mises.looks import default_look, material_look

DRAGONFLY = [{"name": "Teal PLA", "color": "#12a6a0", "finish": "matte"},
             {"name": "Translucent amber film", "color": "#f2b84b", "finish": "gloss"}]


def test_materials_with_a_look_of_their_own():
    assert material_look("Al 6061-T6")["preset"] == "brushed_aluminum"
    assert material_look("Brass C360")["color"] == "#c9a24a"
    assert material_look("CF plate")["color"] == "#1e2024"
    assert material_look("PA6-CF")["color"] == "#1e2024"
    assert material_look("TPU 95A")["preset"] == "rubber"
    assert material_look("PETG") is None and material_look(None) is None


def test_concept_cmf_by_role():
    body = default_look("fuselage_top", "fuselage_top", None, DRAGONFLY)
    assert body["color"] == "#12a6a0" and body["preset"] == "matte_plastic"
    film = default_look("wing_film_left", "wing_film_left", None, DRAGONFLY)
    assert film["color"] == "#f2b84b" and film["opacity"] == 0.6
    # a material's own look wins over the concept's body colour
    assert default_look("coreless_motor", "motor", "Al 6061-T6", DRAGONFLY)["metalness"] == 1
    assert default_look("crank_gear", "gear", "PA12", DRAGONFLY)["color"] == "#12a6a0"
    # bought electronics read as what they are
    assert default_look("battery_lipo", "battery", None, DRAGONFLY)["color"] == "#2c3e5c"


def test_no_concept_and_a_plastic_gets_a_stable_part_colour():
    # the user saw an all-grey assembly next to a coloured agent render: every unstyled part
    # now shows a stable muted colour of its own, the same in the app and in renders
    from mises.looks import PART_PALETTE
    look = default_look("bracket", "bracket", "PLA", [])
    assert look["color"] in PART_PALETTE and look == default_look("bracket", None, None, [])


def test_renders_use_the_default_look_and_a_real_appearance_wins():
    """Home covers, part thumbnails and agent renders (appearance.render_params) show what the
    app shows: the default look for a part nobody styled, the appearance when it is set."""
    from types import SimpleNamespace as NS
    from mises.appearance import render_params
    motor = NS(id="coreless_motor", label="motor", material="Al 6061-T6", appearance=None,
               color=None, kind="part", project_id=None)
    assert render_params(motor)["metalness"] == 1 and render_params(motor)["color"] == "#c4c8cc"
    red = NS(**{**vars(motor), "appearance": {"preset": "anodized", "color": "#d23c2a"}})
    assert render_params(red)["color"] == "#d23c2a"
    plain = NS(**{**vars(motor), "material": "PLA"})
    from mises.looks import PART_PALETTE
    assert render_params(plain)["color"] in PART_PALETTE
