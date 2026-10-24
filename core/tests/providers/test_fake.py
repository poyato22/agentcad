from mises.providers.base import Done, ModelRequest, TextDelta, ToolCall
from mises.providers.fake import FakeProvider
from mises.types import Message, TextBlock


def _req():
    return ModelRequest(model="m", system="s",
                        messages=[Message(role="user", content=[TextBlock(text="hi")])])


async def test_fake_scripts_turns():
    p = FakeProvider([[TextDelta(text="a")],
                      [ToolCall(id="1", name="t", input={}), Done(stop_reason="tool_use")]])
    r = _req()
    one = [e async for e in p.stream(r)]
    two = [e async for e in p.stream(r)]
    assert one == [TextDelta(text="a"), Done(stop_reason="end_turn")]
    assert two[-1] == Done(stop_reason="tool_use") and len(two) == 2
    assert p.requests == [r, r] and p.name == "fake"
