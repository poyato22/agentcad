"""A unit-slip check on a freshly built part: when the part's sizes are a unit factor (x1000,
x25.4, x10, or their inverses) of the numbers in the run's request and none of them matches a
number as given, the build summary says so. The rules say to use the user's numbers as given
(bench: CADTestBench 00995759, "0.75 units ... 1 unit = 1 mm" built as 750 mm).

The request text is untrusted data: only its numbers are read."""
from __future__ import annotations

import re

_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?|\.\d+)(?![\w.])")
FACTORS = (1000.0, 25.4, 10.0, 0.001, 1 / 25.4, 0.1)
TOL = 0.02


def numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text or ""):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if v > 0:
            out.append(v)
    return out


def _near(a: float, b: float) -> bool:
    return b > 0 and abs(a - b) <= TOL * b


def unit_slip(request: str, bbox_size: list[float]) -> float | None:
    """The factor the part's sizes are off by, or None. Needs at least two bbox sizes that are
    the same factor of stated numbers, and no bbox size equal to a stated number."""
    nums = numbers(request)
    sizes = [s for s in bbox_size if s > 1e-9]
    if len(nums) < 2 or len(sizes) < 2:
        return None
    if any(_near(s, n) for s in sizes for n in nums):
        return None
    for k in FACTORS:
        hits = sum(any(_near(s, k * n) for n in nums) for s in sizes)
        if hits >= 2:
            return k
    return None


def note(request: str, bbox_size: list[float]) -> str:
    k = unit_slip(request, bbox_size)
    if k is None:
        return ""
    f = f"{k:g}" if k >= 1 else f"1/{1 / k:g}"
    return (f"; WARNING: the part's sizes are {f} times the numbers in the request and none "
            "matches a number as given. Use the user's numbers as given unless they plainly "
            "mean another unit; if you converted, say so")
