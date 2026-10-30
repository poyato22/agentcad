"""The shape-complexity routing heuristic (G-R6)."""
from mises import shape_complexity as sc


def test_b2_bomber_is_high():
    est = sc.estimate("model a B-2 bomber, ~1:100 scale")
    assert est["level"] == "high"
    assert est["vision_recommended"] is True
    assert "bomber" in est["signals"]


def test_organic_wing_loft_is_high():
    est = sc.estimate("design a wing with a NACA airfoil lofted along a curved planform")
    assert est["level"] == "high"
    assert est["score"] >= 0.6


def test_simple_box_is_low():
    est = sc.estimate("make a 40x20x10 box with two 4mm holes")
    assert est["level"] == "low"
    assert est["vision_recommended"] is False


def test_enclosure_with_internals_is_at_least_medium():
    est = sc.estimate("a vented case enclosure to pack the Raspberry Pi and a fan inside")
    assert est["level"] in ("medium", "high")
    assert est["vision_recommended"] is True


def test_empty_prompt_is_low():
    est = sc.estimate("")
    assert est["level"] == "low"
    assert est["signals"] == []
