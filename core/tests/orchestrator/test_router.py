"""The router over a prompt corpus: every prompt names the agent it must reach (Plan 7 R8)."""
import pytest

from mises.orchestrator.router import (ROUTER_ASK_BELOW, Attachment, Routed, route,
                                       route_rules)

ALL = {"engineer", "designer", "sim", "verifier", "planner", "drawing-import"}

# (prompt, attachments, expected agent)
CORPUS = [
    # system builds -> engineer
    ("Build me a drone", [], "engineer"),
    ("Make a robotic arm that can lift a soda can", [], "engineer"),
    ("Design a 5 inch FPV quadcopter frame", [], "engineer"),
    ("I need a pan-tilt gimbal for a small camera", [], "engineer"),
    ("Create a two-stage gearbox with a 10:1 ratio", [], "engineer"),
    ("design a rover chassis with four wheels", [], "engineer"),
    ("build a hexapod walker", [], "engineer"),
    ("make an assembly of a hinge mechanism with a lid", [], "engineer"),
    ("Design a lightweight bracket that holds 50 N with a safety factor of 2", [], "engineer"),
    # single parts -> designer
    ("Make a 40x20x5 mm plate with four M3 holes", [], "designer"),
    ("Create a bracket", [], "designer"),
    ("model a 10 mm spacer with a 3.2 mm hole", [], "designer"),
    ("design a knob for a 6 mm shaft", [], "designer"),
    ("Add a 2 mm fillet to the box", [], "designer"),
    ("I want a motor mount plate for a NEMA 17", [], "designer"),
    ("generate a cylinder 30 mm tall, 12 mm diameter", [], "designer"),
    ("a 25 mm standoff", [], "designer"),
    ("give me a simple enclosure lid with a snap clip", [], "designer"),
    # test / analyze -> sim
    ("Run a stress analysis on the bracket", [], "sim"),
    ("test the frame under a 20 N load", [], "sim"),
    ("analyze the natural frequencies of the arm", [], "sim"),
    ("What's the drag on this at 15 m/s wind?", [], "sim"),
    ("simulate a 1 m drop test", [], "sim"),
    ("check the stress at the fixture", [], "sim"),
    ("how strong is it?", [], "sim"),
    ("will it break if I hang 5 kg from it", [], "sim"),
    ("I want to test the bracket under 50 N", [], "sim"),
    ("I need to know if the plate will bend under 200 N", [], "sim"),
    ("simulate the arm under a 2 kg load", [], "sim"),
    ("analyze the bracket under 100 N", [], "sim"),
    ("test the mount under load", [], "sim"),
    ("I need the frame tested under a 30 kN load", [], "sim"),
    # check / review -> verifier
    ("Check the hole diameters", [], "verifier"),
    ("review the design against the spec", [], "verifier"),
    ("verify that the bounding box is 40 by 20", [], "verifier"),
    ("Please double-check my changes", [], "verifier"),
    ("audit the part names", [], "verifier"),
    # drawing attachments -> drawing-import
    ("Make this part", [{"name": "bracket.pdf"}], "drawing-import"),
    ("", [{"name": "scan.PNG"}], "drawing-import"),
    ("build it from the drawing", [{"name": "plate.dxf"}], "drawing-import"),
    ("here you go", [{"name": "x", "mime": "image/jpeg"}], "drawing-import"),
    # ambiguous -> the default agent, with low confidence
    ("hi", [], "engineer"),
    ("make it better", [], "engineer"),
    ("what can you do?", [], "engineer"),
    ("thanks!", [], "engineer"),
]


def test_corpus_is_big_enough():
    assert len(CORPUS) >= 30
    assert any(a for _, a, _ in CORPUS)
    assert sum(1 for p, _, e in CORPUS if route_rules(p, known=ALL).confidence
               < ROUTER_ASK_BELOW) >= 3


@pytest.mark.parametrize("prompt,atts,expected", CORPUS)
def test_corpus(prompt, atts, expected):
    r = route_rules(prompt, attachments=atts, known=ALL)
    assert r.agent == expected, (prompt, r)
    assert r.reason and 0 <= r.confidence <= 1


@pytest.mark.parametrize("prompt", ["hi", "make it better", "what can you do?", "thanks!"])
def test_ambiguous_prompts_are_low_confidence(prompt):
    assert route_rules(prompt, known=ALL).confidence < ROUTER_ASK_BELOW


def test_ambiguous_uses_the_profile_default():
    assert route_rules("hello there", known=ALL, default="designer").agent == "designer"


def test_unknown_default_falls_back_to_engineer():
    assert route_rules("hello", known=ALL, default="nobody").agent == "engineer"


def test_drawing_without_drawing_agent_falls_back_to_designer():
    r = route_rules("make it", attachments=[Attachment(name="a.pdf")],
                    known=ALL - {"drawing-import"})
    assert r.agent == "designer" and "drawing" in r.reason


def test_non_drawing_attachment_is_ignored():
    r = route_rules("Build me a drone", attachments=[{"name": "notes.txt"}], known=ALL)
    assert r.agent == "engineer"


def test_routing_to_an_undefined_agent_falls_back():
    r = route_rules("run a stress analysis", known={"engineer", "designer"})
    assert r.agent == "engineer"


def test_reasons_are_short_labels():
    assert route_rules("Build me a drone", known=ALL).reason == "system build"
    assert route_rules("Create a bracket", known=ALL).reason == "single part"
    assert route_rules("Run a stress analysis", known=ALL).reason == "test / analyze"
    assert route_rules("Check the holes", known=ALL).reason == "check / review"


async def test_classifier_only_asked_when_unsure():
    asked = []

    async def clf(prompt, agents):
        asked.append(prompt)
        return Routed(agent="sim", reason="model: analysis", confidence=0.7)

    sure = await route("Build me a drone", known=ALL, classifier=clf)
    assert sure.agent == "engineer" and asked == []
    unsure = await route("make it better", known=ALL, classifier=clf)
    assert unsure.agent == "sim" and asked == ["make it better"]


async def test_classifier_failure_or_unknown_agent_keeps_rules():
    async def boom(prompt, agents):
        raise RuntimeError("offline")

    async def bogus(prompt, agents):
        return Routed(agent="hacker", reason="x", confidence=1)

    assert (await route("hi", known=ALL, classifier=boom)).agent == "engineer"
    assert (await route("hi", known=ALL, classifier=bogus)).agent == "engineer"
