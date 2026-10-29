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
