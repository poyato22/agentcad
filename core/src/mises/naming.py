"""Naming rules: agent-created names must not end in a number."""
from __future__ import annotations

import re

from .types import ErrorCode, MisesError


def check_name(name: str) -> None:
    if not name or not name.strip():
        raise MisesError(ErrorCode.INVALID_INPUT, "name must not be empty",
                         hint="use a descriptive name like 'gusset_upright'")
    if re.search(r"\d+\s*$", name):
        raise MisesError(ErrorCode.INVALID_INPUT, f"name {name!r} must not end in a number",
                         hint="name by purpose, e.g. 'gusset_upright' instead of 'Gusset1'")


def display_name(feature_type: str, name: str, params: dict | None = None) -> str:
    if not re.search(r"\d+\s*$", name):
        return name
    purpose = "unnamed"
    for k in ("purpose", "name", "label"):
        v = (params or {}).get(k)
        if isinstance(v, str) and v.strip():
            purpose = v.strip()
            break
    return f"{feature_type} · {purpose}"
