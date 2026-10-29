import pytest
from pydantic import ValidationError

from mises.engineering.spec import LoadCase, Requirement, Spec, evaluate_spec


def drone_spec() -> Spec:
    return Spec(
        goal="5 inch drone frame",
        load_cases=[
            LoadCase(id="hover_thrust", name="hover", fix="bottom", load="top",
                     newtons=40, direction="-z"),
            LoadCase(id="crash_drop", name="crash drop", fix="bottom", load="top",
                     newtons=120, direction="-z", estimate=True),
        ],
        requirements=[
            Requirement(id="mass_budget", kind="max_mass_g", target=120, priority=5,
                        source="user"),
            Requirement(id="arm_safety", kind="min_safety_factor", target=2.0,
                        load_case_id="crash_drop", priority=4),
            Requirement(id="frame_envelope", kind="envelope_mm", target=[220, 220, 40],
                        priority=2),
        ],
    )


def test_spec_validates():
    s = drone_spec()
    assert len(s.requirements) == 3 and len(s.load_cases) == 2


def test_unknown_load_case_rejected():
    with pytest.raises(ValidationError):
        Spec(requirements=[Requirement(id="x_sf", kind="min_safety_factor", target=2,
                                       load_case_id="ghost")])


def test_duplicate_requirement_ids_rejected():
    with pytest.raises(ValidationError):
        Spec(requirements=[Requirement(id="mass_budget", kind="max_mass_g", target=10),
                           Requirement(id="mass_budget", kind="max_mass_g", target=20)])


def test_numeric_target_must_be_positive():
    with pytest.raises(ValidationError):
        Requirement(id="mass_budget", kind="max_mass_g", target=-1)


def test_safety_factor_needs_a_load_case():
    with pytest.raises(ValidationError):
        Requirement(id="sf_check", kind="min_safety_factor", target=2)


def test_name_must_not_end_in_number():
    with pytest.raises(ValidationError):
        Requirement(id="mass_budget1", kind="max_mass_g", target=10)


def test_envelope_target_shape():
    with pytest.raises(ValidationError):
        Requirement(id="env", kind="envelope_mm", target=[1, 2])


def test_evaluate_pass_warn_fail_and_not_checked():
    s = drone_spec()
    sim = {"crash_drop": {"safety_factor": 2.4, "max_displacement_mm": 0.5}}

    ok = evaluate_spec(s, {"mass_g": 118.0, "bbox": {"size": [210, 210, 38]}}, sim)
    by = {r["id"]: r for r in ok["requirements"]}
    assert by["mass_budget"]["status"] == "pass"
    assert by["arm_safety"]["status"] == "pass"
    assert by["frame_envelope"]["status"] == "pass"
    assert ok["overall"] == "pass"

    warn = evaluate_spec(s, {"mass_g": 123.0, "bbox": {"size": [210, 210, 38]}}, sim)
    assert {r["id"]: r["status"] for r in warn["requirements"]}["mass_budget"] == "warn"
    assert warn["overall"] == "warn"

    fail = evaluate_spec(s, {"mass_g": 140.0, "bbox": {"size": [210, 210, 38]}},
                         {"crash_drop": {"safety_factor": 1.4}})
    statuses = {r["id"]: r["status"] for r in fail["requirements"]}
    assert statuses["mass_budget"] == "fail" and statuses["arm_safety"] == "fail"
    assert fail["overall"] == "fail"

    # no stress study yet: the safety-factor requirement is not_checked, never a silent pass
    none = evaluate_spec(s, {"mass_g": 100.0, "bbox": {"size": [210, 210, 38]}}, {})
    assert {r["id"]: r["status"] for r in none["requirements"]}["arm_safety"] == "not_checked"
