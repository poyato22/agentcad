import asyncio
import json
import os
import stat

import httpx2
import pytest
import websockets

from mises.server.app import serve_async


async def test_live_events_ws_streams_run_events(rt, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("MISES_HOME", str(other))  # server.json must follow runtime.home
    port, token, handle = await serve_async(rt)
    try:
        info_path = rt.home / "server.json"
        info = json.loads(info_path.read_text())
        assert info == {"port": port, "token": token, "pid": os.getpid()}
        assert stat.S_IMODE(info_path.stat().st_mode) == 0o600
        assert not (other / "server.json").exists()
        assert not [p for p in rt.home.iterdir() if p.name.startswith(".server.json")]
        async with websockets.connect(f"ws://127.0.0.1:{port}/events?token={token}") as ws:
            async with httpx2.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                          headers={"Authorization": f"Bearer {token}"}) as http:
                r = await http.post("/runs", json={"agent": "designer", "prompt": "hi"})
                assert r.status_code == 200, r.text
                rid = r.json()["run_id"]
            types = []
            while True:
                ev = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert ev["run_id"] == rid
                types.append(ev["type"])
                if ev["type"] == "run_finished":
                    break
        assert types[0] == "run_started" and "run_finished" in types
        for url in [f"ws://127.0.0.1:{port}/events?token=wrong",
                    f"ws://127.0.0.1:{port}/events"]:
            async with websockets.connect(url) as ws:
                with pytest.raises(websockets.ConnectionClosed) as e:
                    await asyncio.wait_for(ws.recv(), 5)
            assert e.value.rcvd.code == 4401
    finally:
        await handle.stop()
    assert not (rt.home / "server.json").exists()
    with pytest.raises(httpx2.ConnectError):
        await httpx2.AsyncClient().get(f"http://127.0.0.1:{port}/health")


async def test_serve_async_rejects_host(rt):
    with pytest.raises(TypeError):
        await serve_async(rt, host="0.0.0.0")
