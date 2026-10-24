import pytest
from pydantic import BaseModel

from mises.tools.registry import ToolRegistry, ToolDef, tool
from mises.types import MisesError, ErrorCode, SafetyClass, ToolOutput


class Args(BaseModel):
    x: int
    inner: "Inner | None" = None


class Inner(BaseModel):
    y: str = "a"


Args.model_rebuild()


@tool("echo_tool", description="echo", safety=SafetyClass.READ)
async def echo(ctx, args: Args) -> ToolOutput:
    return ToolOutput(summary=str(args.x))


def test_tool_decorator_infers_model():
    assert isinstance(echo, ToolDef)
    assert echo.input_model is Args and echo.name == "echo_tool"
    assert echo.safety == SafetyClass.READ


def test_default_safety_fails_closed():
    @tool("t_default", description="d")
    async def f(ctx, args: Args) -> ToolOutput:
        return ToolOutput(summary="")
    assert f.safety == SafetyClass.IRREVERSIBLE


def test_bad_name_rejected():
    reg = ToolRegistry()
    bad = ToolDef(name="onshape.propose", description="", input_model=Args,
                  safety=SafetyClass.READ, handler=echo.handler)
    with pytest.raises(ValueError):
        reg.register(bad)


def test_duplicate_rejected_and_get_missing():
    reg = ToolRegistry()
    reg.register(echo)
    with pytest.raises(ValueError):
        reg.register(echo)
    with pytest.raises(MisesError) as e:
        reg.get("nope")
    assert e.value.code == ErrorCode.NOT_FOUND


def test_list_filters():
    reg = ToolRegistry()
    reg.register(echo)
    assert reg.list() == [echo]
    assert reg.list(names=["other"]) == []
    assert reg.list(classes={SafetyClass.IRREVERSIBLE}) == []
    assert reg.list(names=["echo_tool"], classes={SafetyClass.READ}) == [echo]


def test_schema_has_properties():
    s = ToolRegistry().schema(echo)
    assert s["name"] == "echo_tool" and s["description"] == "echo"
    assert "x" in s["input_schema"]["properties"]
    assert "$defs" not in s["input_schema"] and "$ref" not in str(s["input_schema"])
