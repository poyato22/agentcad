import pytest

from mises.engineering.interview import MAX_QUESTIONS, build_interview, detect_domain


def test_drone_interview_small_with_options():
    iv = build_interview("build me a 5 inch drone")
    assert iv.domain == "drone"
    assert 1 <= len(iv.questions) <= MAX_QUESTIONS
    for q in iv.questions:
        assert q.options, f"{q.id} has no options"
        assert q.allow_free
    ids = {q.id for q in iv.questions}
    assert {"use", "size_class", "fc_mount", "priorities"} <= ids
    assert any(q.kind == "rank" for q in iv.questions)


def test_generic_interview_small():
    iv = build_interview("design me a shelf bracket")
    assert iv.domain == "generic"
    assert 1 <= len(iv.questions) <= MAX_QUESTIONS
    assert any(q.kind == "rank" for q in iv.questions)


def test_batches_are_two_or_three():
    iv = build_interview("quadcopter frame")
    assert iv.domain == "drone"
    for batch in iv.batches:
        assert 1 <= len(batch) <= 3


def test_detect_domain():
    assert detect_domain("an FPV quad") == "drone"
    assert detect_domain("a phone stand") == "generic"


@pytest.mark.parametrize("goal,domain", [
    ("build me a 5 inch drone", "drone"),
    ("a quadcopter frame", "drone"),
    ("an FPV quad", "drone"),
    ("a multirotor", "drone"),
    # D26: "quad" inside a word must not read as a quadcopter, and legged words win
    ("a quadruped robot dog", "legged_robot"),
    ("a robotic dog", "legged_robot"),
    ("a humanoid robot", "legged_robot"),
    ("a hexapod walker", "legged_robot"),
    ("a bipedal robot", "legged_robot"),
    ("a legged rover", "legged_robot"),
    ("a squad car", "generic"),          # "squad" is not "quad"
    ("a phone stand", "generic"),
    ("a shelf bracket", "generic"),
])
def test_detect_domain_parametrized(goal, domain):
    assert detect_domain(goal) == domain


def test_legged_robot_interview_has_no_drone_leak():
    # D26: a robotic dog must never surface the drone interview (prop size, FPV, flight stack).
    iv = build_interview("a quadruped robot dog")
    assert iv.domain == "legged_robot"
    assert 1 <= len(iv.questions) <= MAX_QUESTIONS
    for q in iv.questions:
        assert q.options and q.allow_free
    blob = iv.model_dump_json().lower()
    for leak in ("drone", "quadcopter", "propeller", "fpv", "flight-controller", "multirotor"):
        assert leak not in blob, f"{leak!r} leaked into the legged-robot interview"
    # it asks the legged-robot questions the brief calls for
    ids = {q.id for q in iv.questions}
    assert {"use", "gait_speed", "legs_dof", "aesthetic"} <= ids
