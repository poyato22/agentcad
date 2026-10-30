"""recipes_list / recipe_doc: on-demand docs for the mises_shapes recipe library (Plan 8 G2).

The recipes run only inside the sandboxed build worker (cad/mises_shapes.py, pre-imported as
`ms`). The host never imports that module: it reads its source with `ast`, so listing the docs
loads neither build123d nor any recipe code into the server process."""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput

SHAPES_FILE = Path(__file__).resolve().parent / "mises_shapes.py"

USAGE = ("Call recipes from build(v) as `ms.<name>(...)` (also `mises_shapes`); they are "
         "pre-imported in every part script. Units mm; aircraft recipes use X aft, Y span, Z up.")


def _signature(fn: ast.FunctionDef) -> str:
    a = fn.args
    parts: list[str] = []
    pos = a.posonlyargs + a.args
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    for arg, d in zip(pos, defaults):
        parts.append(arg.arg if d is None else f"{arg.arg}={ast.unparse(d)}")
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    for arg, d in zip(a.kwonlyargs, a.kw_defaults):
        parts.append(arg.arg if d is None else f"{arg.arg}={ast.unparse(d)}")
    return f"{fn.name}({', '.join(parts)})"


@lru_cache(maxsize=1)
def _catalog() -> tuple[tuple[str, str, str, str], ...]:
    """(name, signature, one-line summary, full docstring) per public recipe, in __all__ order."""
    tree = ast.parse(SHAPES_FILE.read_text())
    names: list[str] = []
    fns: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            names = list(ast.literal_eval(node.value))
        elif isinstance(node, ast.FunctionDef):
            fns[node.name] = node
    out = []
    for n in names:
        fn = fns[n]
        doc = ast.get_docstring(fn) or ""
        out.append((n, _signature(fn), doc.split("\n", 1)[0].strip(), doc))
    return tuple(out)


def recipe_names() -> list[str]:
    return [c[0] for c in _catalog()]


def recipe_index() -> str:
    """The one-line-per-recipe index the designer and engineer prompts carry."""
    return "\n".join(f"- `ms.{name}`: {summary}" for name, _sig, summary, _doc in _catalog())


PROMPT_BEGIN = "<!-- mises-shapes:begin (generated: recipes.prompt_section) -->"
PROMPT_END = "<!-- mises-shapes:end -->"


def prompt_section() -> str:
    """The delimited block the designer and engineer prompts carry (tests keep them in sync)."""
    return "\n".join([
        PROMPT_BEGIN,
        "## Curved and organic shapes: use the recipes",
        "Never approximate organic or aero shapes (wings, fuselages, flying wings, hulls, "
        "ducts, handles, ergonomic shells) with ellipsoids, spheres, boxes or cylinders. Build "
        "them from lofts, sweeps and spline surfaces with the `mises_shapes` recipes, "
        "pre-imported in every part script as `ms` (e.g. `return ms.blended_wing_body(span=524)`"
        "). Read `recipe_doc(name)` before first use; `recipes_list` lists them. A recipe checks "
        "its result and raises ValueError with the reason: fix the arguments, do not fall back "
        "to primitives.",
        recipe_index(),
        PROMPT_END,
    ])


def recipe_doc_text(name: str) -> str:
    for n, sig, _summary, doc in _catalog():
        if n == name:
            return f"ms.{sig}\n\n{doc}"
    raise MisesError(ErrorCode.NOT_FOUND, f"no recipe {name!r}",
                     hint="call recipes_list for the names")


class RecipesListArgs(BaseModel):
    pass


class RecipeDocArgs(BaseModel):
    name: str = Field(description="recipe name from recipes_list, e.g. blended_wing_body")


@tool("recipes_list", safety=SafetyClass.READ,
      description="List the curved-geometry recipes (airfoils, wings, blended-wing-body, "
                  "fuselage, sweeps, lofts, spline surfaces, fillets, enclosures, standoffs, "
                  "vents, cable paths) pre-imported in part scripts as `ms`, one line each "
                  "with its signature. Read recipe_doc(name) before using one.")
async def recipes_list(ctx: ToolContext, args: RecipesListArgs) -> ToolOutput:
    rows = [{"name": n, "signature": f"ms.{sig}", "summary": summary}
            for n, sig, summary, _doc in _catalog()]
    return ToolOutput(summary=f"{len(rows)} recipes. {USAGE}",
                      data={"usage": USAGE, "recipes": rows}, step_kind=StepKind.TOOL_RESULT)


@tool("recipe_doc", safety=SafetyClass.READ,
      description="Full documentation of one mises_shapes recipe: signature, defaults, "
                  "coordinate conventions, what it returns and how it fails.")
async def recipe_doc(ctx: ToolContext, args: RecipeDocArgs) -> ToolOutput:
    text = recipe_doc_text(args.name.strip().removeprefix("ms.").removeprefix("mises_shapes."))
    return ToolOutput(summary=text.split("\n", 1)[0], data={"name": args.name, "doc": text,
                                                            "usage": USAGE})


def register_recipe_tools(registry: ToolRegistry) -> None:
    for t in (recipes_list, recipe_doc):
        registry.register(t)
