from mises.types import Message, TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock, MisesError, ErrorCode


def test_error_to_dict():
    assert MisesError(ErrorCode.TRANSIENT, "x").to_dict()["retryable"] is True
    d = MisesError(ErrorCode.INVALID_INPUT, "x", hint="h").to_dict()
    assert d["retryable"] is False and d["error"] == "invalid_input" and d["hint"] == "h"


def test_message_roundtrip():
    m = Message(role="assistant", content=[
        TextBlock(text="hi"), ImageBlock(media_type="image/png", data_b64="AA=="),
        ToolUseBlock(id="1", name="t", input={"a": 1}),
        ToolResultBlock(tool_use_id="1", content="ok")])
    assert Message.model_validate_json(m.model_dump_json()) == m
