"""Shared bits of the benchmark harness: paths, the task record, the usage gauge."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent.parent

#: datasets and run outputs live outside the repo (never commit datasets)
CACHE = Path(os.environ.get("MISES_BENCH_CACHE", "/tmp/mises-bench")).expanduser()

#: the read-only usage helper the operator pointed us at (it reads its own token itself)
USAGE_HELPER = Path("~/.claude/cmux-limit-watcher/bin").expanduser()

#: text added to every prompt for both systems, so they share one output contract
UNITS_NOTE = ("Units are millimetres: use every number in the description exactly as given "
              "(1 unit = 1 mm).")


@dataclass
class Task:
    suite: str                        # cadbench | cadtestbench | functional
    id: str
    prompt: str
    images: list[str] = field(default_factory=list)   # paths to PNGs shown with the prompt
    meta: dict = field(default_factory=dict)          # family, label, gt paths, checker ...

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Task":
        return Task(**d)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


_HELPER = None
_LAST: dict = {"t": 0.0, "v": None}
USAGE_EVERY_S = 300.0


def weekly_usage() -> dict | None:
    """The account's 7-day / 5-hour utilisation (percent) via the cmux-limit-watcher helper.
    Read only, sampled at most every 5 minutes (the usage API rate-limits); the last sample is
    reused in between. Shared with every other session on the account."""
    global _HELPER
    now = time.time()
    if _LAST["v"] is not None and now - _LAST["t"] < USAGE_EVERY_S:
        return _LAST["v"]
    try:
        if _HELPER is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("bench_cmuxlimit",
                                                          USAGE_HELPER / "cmuxlimit.py")
            _HELPER = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_HELPER)
        d = _HELPER.fetch_usage() or {}
        sd, fh = d.get("seven_day") or {}, d.get("five_hour") or {}
        v = {"t": now, "seven_day": sd.get("utilization"),
             "seven_day_resets": sd.get("resets_at"), "five_hour": fh.get("utilization")}
    except Exception as e:  # noqa: BLE001
        v = {"t": now, "error": str(e)}
    _LAST.update(t=now, v=v)
    return v
