"""This Mac's specs (sysctl, through platform.py) and how many agents it should run at once.

auto_parallel(model_ref):
- cloud models and the user's Claude Code: min(4, max(1, perf_cores // 2)); the Mac only runs
  the CAD kernel and tool calls, so performance cores are the limit.
- OpenRouter free models: 1 (the account allows 20 requests/min across all free models).
- local models (Ollama, LM Studio): 1, or 2 when the memory left for models holds two copies
  of the model's weights. Memory left for models is the RAM minus MEM_RESERVE_GB for macOS,
  Mises and the CAD kernel (24 GB -> 19 GB; the model research budgets 16-18 GB of a 24 GB Mac
  for weights + KV cache, so a 17 GB model is one at a time and a 6.6 GB one two).
"""
from __future__ import annotations

import functools
import math
import os
from typing import Literal, TypedDict

from . import platform

MEM_RESERVE_GB = 5
CLOUD_MAX = 4
LOCAL_VENDORS = ("ollama", "lmstudio")
# A local model needs more than its raw weights to run: KV cache and runtime headroom. We
# budget need = size x FIT_FACTOR everywhere (admission, the "two copies" check, the
# recommended-model fit) so a model is never admitted on raw weights alone. (For a fixed
# context this is roughly size + the KV estimate for num_ctx 16384.)
FIT_FACTOR = 1.2

ModelKind = Literal["cloud", "free", "local"]


class Machine(TypedDict):
    cpu_cores: int
    perf_cores: int
    ram_gb: int
    chip: str


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


@functools.lru_cache(maxsize=1)
def machine() -> Machine:
    cpu = _int(platform.sysctl("hw.ncpu")) or os.cpu_count() or 1
    perf = _int(platform.sysctl("hw.perflevel0.physicalcpu")) or cpu
    mem = _int(platform.sysctl("hw.memsize")) or 0
    brand = platform.sysctl("machdep.cpu.brand_string") or ""
    chip = brand.removeprefix("Apple ").strip() or "this Mac"
    return {"cpu_cores": cpu, "perf_cores": perf, "ram_gb": round(mem / 2**30),
            "chip": chip}


def usable_memory_gb(m: Machine | None = None) -> float:
    m = m or machine()
    return max(0.0, float(m["ram_gb"] - MEM_RESERVE_GB))


def model_need_gb(size_gb: float) -> float:
    """Memory a local model of `size_gb` raw weights needs to run (weights + KV + headroom)."""
    return size_gb * FIT_FACTOR


def _price(v) -> float | None:
    try:
        f = float(str(v).strip())
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def is_free_model(model_ref: str, pricing: dict | None = None) -> bool:
    """The one "free model" predicate: an OpenRouter model that is zero-priced, or whose id
    ends in `:free`. Shared by model_kind, the registry's 20/min limiter, and the live model
    list (which passes OpenRouter's `pricing`). Free models get the 20/min group cap and the
    small-model tool filtering."""
    vendor, _, model = model_ref.partition("/")
    if vendor != "openrouter":
        return False
    if isinstance(pricing, dict) and pricing:
        if all(_price(pricing.get(k)) == 0.0 for k in ("prompt", "completion")):
            return True
    return model.endswith(":free")


def model_kind(model_ref: str) -> ModelKind:
    vendor, _, model = model_ref.partition("/")
    if vendor in LOCAL_VENDORS:
        return "local"
    if is_free_model(model_ref):
        return "free"
    return "cloud"


def auto_parallel(model_ref: str, *, machine_info: Machine | None = None,
                  model_size_gb: float | None = None) -> int:
    m = machine_info or machine()
    kind = model_kind(model_ref)
    if kind == "free":
        return 1
    if kind == "local":
        if (model_size_gb and model_size_gb > 0
                and usable_memory_gb(m) >= 2 * model_need_gb(model_size_gb)):
            return 2
        return 1
    return min(CLOUD_MAX, max(1, m["perf_cores"] // 2))


def _gb(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def parallel_reason(model_ref: str, *, machine_info: Machine | None = None,
                    model_size_gb: float | None = None, limit: int | None = None) -> str:
    """One line for the UI, e.g. "M5 Pro · 24 GB · cloud model → 3"."""
    m = machine_info or machine()
    n = limit if limit is not None else auto_parallel(model_ref, machine_info=m,
                                                      model_size_gb=model_size_gb)
    kind = model_kind(model_ref)
    if kind == "free":
        what = "OpenRouter free model (20 requests/min)"
    elif kind == "local":
        what = f"local model ({_gb(model_size_gb)} GB)" if model_size_gb else "local model"
    else:
        what = "cloud model"
    ram = f"{m['ram_gb']} GB" if m["ram_gb"] else "unknown memory"
    line = f"{m['chip']} · {ram} · {what} → {n}"
    if kind == "local":
        line += f" · local models share {_gb(usable_memory_gb(m))} GB"
    return line
