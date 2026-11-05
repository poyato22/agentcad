"""A tool name as a model wrote it -> the Mises tool it means, or the closest real names (P11 F3).

Models sometimes call a tool by a decorated or bare form of its name (`mcp__mises__x` in a
native run, `mises.x`, `Mirror-Part-Create`) or invent one (`loads_impact_placeholder`).
`resolve_tool_name` maps only the forms that name exactly one real tool once the decoration is
dropped, so the call then takes the normal Gate path (classes, scope, budget, approvals): it is
never a fuzzy guess. Anything else stays unknown; `closest_tools` lists what the model most
likely meant, for a recoverable tool error.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Iterable

# decorations a model puts in front of a Mises tool's name
_PREFIXES = ("mcp__mises__", "mises__", "mises.", "mises:", "mises/",
             "agentcad__", "agentcad.", "agentcad:", "agentcad/", "functions.")


def _norm(name: str) -> str:
    return re.sub(r"[\s\-.]+", "_", name.strip()).lower()


def resolve_tool_name(name: str, known: Iterable[str]) -> str | None:
    """The real tool `name` means: itself, or the one tool its undecorated form names
    exactly. None when it names no tool."""
    names = set(known)
    if name in names:
        return name
    base = name.strip()
    for p in _PREFIXES:
        if base.lower().startswith(p):
            base = base[len(p):]
            break
    n = _norm(base)
    return n if n in names else None


def closest_tools(name: str, known: Iterable[str], n: int = 5) -> list[str]:
    """Up to `n` real tool names the model most likely meant (best first): close spellings,
    then names sharing its leading words (`loads_impact_placeholder` -> `loads_impact`)."""
    names = sorted(set(known))
    base = _norm(name)
    for p in ("mcp__mises__", "mises__"):
        if base.startswith(p):
            base = base[len(p):]
    out = difflib.get_close_matches(base, names, n=n, cutoff=0.55)
    words = [w for w in base.split("_") if w]
    for k in range(len(words), 0, -1):        # the longest shared prefix of words first
        head = "_".join(words[:k])
        for t in names:
            if (t == head or t.startswith(head + "_")) and t not in out:
                out.append(t)
        if len(out) >= n:
            break
    if len(out) < n:                          # any name sharing a word
        for t in names:
            if t not in out and set(t.split("_")) & set(words) - {"get", "set", "part"}:
                out.append(t)
            if len(out) >= n:
                break
    return out[:n]


def unknown_tool_hint(name: str, known: Iterable[str], prefix: str = "") -> str:
    """The hint of a recoverable unknown-tool error: what to call instead."""
    near = closest_tools(name, known)
    listed = ", ".join(f"`{prefix}{t}`" for t in near)
    return (f"there is no tool named {name!r}; call only the tools you were given, by their "
            "exact names" + (f". Closest real tools: {listed}" if near else ""))
