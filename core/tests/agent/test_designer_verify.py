"""Bench fix: CADTestBench failures were mostly unchecked stated numbers (sizes, offsets,
implied volumes). The designer verifies every stated requirement, not a sample."""
from mises.agent.definitions import load_definitions


def test_designer_checks_every_stated_requirement():
    body = " ".join(load_definitions()["designer"].body.split())
    assert "Verify every requirement, not a sample" in body
    assert "work it out by hand and compare it with the measured volume" in body
    assert "report each requirement with its measured value" in body
