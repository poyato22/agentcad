"""The built-in parts directory: JSON files in ./data, one per category, each citing its
`sources`. Values a vendor would vary are marked `"note": "typical"` on the spec."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=1)
def builtin_items() -> tuple[dict, ...]:
    """Every built-in item, with `source: "builtin"` and the file's `sources` attached."""
    out: list[dict] = []
    for f in sorted(DATA.glob("*.json")):
        doc = json.loads(f.read_text())
        for it in doc["items"]:
            out.append({**it, "category": it.get("category") or doc["category"],
                        "source": "builtin", "url": None, "vendor": None,
                        "sources": doc.get("sources", []), "project_id": None})
    ids = [i["id"] for i in out]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate built-in catalog ids")
    return tuple(out)


def builtin_by_id() -> dict[str, dict]:
    return {i["id"]: i for i in builtin_items()}


def categories() -> list[str]:
    return sorted({i["category"] for i in builtin_items()})
