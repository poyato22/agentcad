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


class Titled(BaseModel):
    title: str = "a property named title stays"
    inner: Inner | None = None


@tool("titled_tool", description="t", safety=SafetyClass.READ)
async def titled(ctx, args: Titled) -> ToolOutput:
    return ToolOutput(summary="")


def test_schema_drops_pydantic_titles_but_keeps_a_property_named_title():
    # bench cost fix: the generated "title" labels repeat every field name in every tool's
    # schema (about 26k characters over the registry) and carry no meaning for the model
    s = ToolRegistry().schema(titled)["input_schema"]
    assert "title" in s["properties"] and "inner" in s["properties"]

    def titles(node, key=None):
        if isinstance(node, dict):
            return sum((k == "title" and key != "properties") + titles(v, k)
                       for k, v in node.items())
        if isinstance(node, list):
            return sum(titles(v) for v in node)
        return 0
    assert titles(s) == 0
    assert "y" in str(s)          # the nested model is still inlined


def test_schema_has_properties():
    s = ToolRegistry().schema(echo)
    assert s["name"] == "echo_tool" and s["description"] == "echo"
    assert "x" in s["input_schema"]["properties"]
    assert "$defs" not in s["input_schema"] and "$ref" not in str(s["input_schema"])
