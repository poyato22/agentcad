"""Final review M5: warm measure workers never exceed MAX_WARM, even when several parts warm
at once (each start reserves its slot before the up-to-60 s wait for "ready")."""
import asyncio
from types import SimpleNamespace

from mises import measure as M


async def test_concurrent_warm_starts_stay_within_max_warm(monkeypatch, tmp_path):
    peak = 0

    async def spawn(part_dir, mtime_ns):
        nonlocal peak
        peak = max(peak, len(M._warm) + len(M._starting))
        await asyncio.sleep(0.05)                      # the worker loading its shape
        w = SimpleNamespace(part_dir=part_dir, mtime_ns=mtime_ns)
        M._warm[str(part_dir)] = w
        return w

    async def close(w):
        M._warm.pop(str(w.part_dir), None)

    monkeypatch.setattr(M, "_spawn", spawn)
    monkeypatch.setattr(M, "_close", close)
    try:
        await asyncio.gather(*(M._start(tmp_path / f"p{i}", 1) for i in range(5)))
        assert peak <= M.MAX_WARM and len(M._warm) <= M.MAX_WARM and not M._starting
    finally:
        M._warm.clear()
