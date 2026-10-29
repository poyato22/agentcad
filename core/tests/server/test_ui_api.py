import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from mises.providers.base import Done, ToolCall
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.tools.registry import tool
from mises.types import SafetyClass, ToolOutput

from .conftest import AUTH, TOKEN

ORIGIN = "tauri://localhost"
METRICS = {"valid": True, "volume_mm3": 4000.0, "area_mm2": 2000.0, "mass_g": 10.8,
           "bbox": {"min": [0, 0, 0], "max": [40, 20, 5], "size": [40, 20, 5]},
           "center_of_mass": [20, 10, 2.5], "faces": 6, "edges": 12, "holes": 2, "solids": 1}


@pytest.fixture
def client(rt, monkeypatch):
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    with TestClient(create_app(rt, TOKEN)) as c:
        yield c


def write_part(rt, part_id="mount_plate", *, vars=True, glb=b"glTF-bytes"):
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True)
    if vars:
        (d / "vars.json").write_text(json.dumps({"thickness": 5.0}))
        (d / "metrics.json").write_text(json.dumps(METRICS))
        # a successful build also makes the part's tree row (Plan 5 C1), in the active build
        rt.store.register_part(part_id, rt.active_project_id())
    if glb is not None:
        (d / "model.glb").write_bytes(glb)
    return d


def test_cors_preflight_without_token(client):
    r = client.options("/runs", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                                         "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN
    bad = client.options("/runs", headers={"Origin": "http://evil.example",
                                           "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


def test_cors_does_not_bypass_auth_on_real_requests(client):
    r = client.get("/runs", headers={"Origin": ORIGIN})
    assert r.status_code == 401
    ok = client.get("/runs", headers={**AUTH, "Origin": ORIGIN})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.parametrize("path", ["/status", "/agents", "/models", "/parts",
                                  "/parts/mount_plate/model.glb"])
def test_ui_routes_need_token(client, path):
    assert client.get(path).status_code == 401
    assert client.post("/runs/cancel_all").status_code == 401


def test_status_shape(client, rt):
    r = client.get("/status", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"core_version", "onshape", "onshape_calls_year", "onshape_cap_year",
                         "runs_running", "runs_queued", "approvals_pending", "data_dir",
                         "parallel"}
    assert body["data_dir"] == str(rt.home)
    assert body["onshape"] == "not_configured"
    assert body["onshape_cap_year"] == 2500
    assert body["onshape_calls_year"] == 0
    assert body["runs_running"] == 0 and body["approvals_pending"] == 0
    assert body["runs_queued"] == 0 and set(body["parallel"]) == {"limit", "auto", "reason"}
    assert isinstance(body["core_version"], str) and body["core_version"]


def test_agents_lists_builtins(client):
    items = client.get("/agents", headers=AUTH).json()
    assert {"designer", "planner", "verifier", "sim"} <= {a["name"] for a in items}
    # Plan 5 C1 enriched the shape with tools, builtin and hidden flags
    assert all(set(a) == {"name", "description", "model", "tools", "builtin", "hidden"}
               for a in items)
    assert all(a["builtin"] is True and a["hidden"] is False for a in items)


def test_models_never_leaks_keys(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.get("/models", headers=AUTH)
    assert "sk-test-secret" not in r.text
    body = r.json()
    assert body["default"].startswith("anthropic/")
    vendors = body["vendors"]
    keys = {v["vendor"]: v["key"] for v in vendors}
    assert keys["anthropic"] == "env" and keys["openai"] == "none"
    ollama = next(v for v in vendors if v["vendor"] == "ollama")
    assert ollama["key"] == "not_needed" and ollama["models"] == []
    assert {"vendor": "openai", "key": "none"} in vendors


def test_parts_lists_built_parts(client, rt):
    write_part(rt)
    write_part(rt, "aaa_empty", vars=False)
    (rt.home / "parts" / "stray_file").write_text("x")
    items = client.get("/parts", headers=AUTH).json()
    assert [p["part_id"] for p in items] == ["mount_plate"]
    p = items[0]
    assert p["vars"] == {"thickness": 5.0} and p["metrics"] == METRICS
    assert isinstance(p["mtime"], float)


def test_parts_empty_without_dir(client):
    assert client.get("/parts", headers=AUTH).json() == []


def test_glb_etag_and_304(client, rt):
    write_part(rt)
    r = client.get("/parts/mount_plate/model.glb", headers=AUTH)
    assert r.status_code == 200 and r.content == b"glTF-bytes"
    assert r.headers["content-type"] == "model/gltf-binary"
    etag = r.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')
    r2 = client.get("/parts/mount_plate/model.glb", headers={**AUTH, "If-None-Match": etag})
    assert r2.status_code == 304
    r3 = client.get("/parts/mount_plate/model.glb", headers={**AUTH, "If-None-Match": '"nope"'})
    assert r3.status_code == 200


def test_glb_rejects_traversal(client, rt):
    (rt.home / "secret").mkdir()
    (rt.home / "secret" / "model.glb").write_bytes(b"SECRET")
    for pid in ("..%2Fsecret", "Bad1", "..", "a", "x%00y"):
        r = client.get(f"/parts/{pid}/model.glb", headers=AUTH)
        assert r.status_code in (400, 404) and b"SECRET" not in r.content
    assert client.get("/parts/Bad1/model.glb", headers=AUTH).json()["error"] == "invalid_input"


def test_glb_rejects_symlink_escape(client, rt, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "model.glb").write_bytes(b"SECRET")
    (rt.home / "parts").mkdir()
    (rt.home / "parts" / "evil_part").symlink_to(outside, target_is_directory=True)
    d = write_part(rt, "linked_glb", glb=None)
    (d / "model.glb").symlink_to(outside / "model.glb")
    for pid in ("evil_part", "linked_glb"):
        r = client.get(f"/parts/{pid}/model.glb", headers=AUTH)
        assert r.status_code == 404 and b"SECRET" not in r.content
    assert [p["part_id"] for p in client.get("/parts", headers=AUTH).json()] == ["linked_glb"]


def test_glb_missing_404(client, rt):
    assert client.get("/parts/nothing_here/model.glb", headers=AUTH).status_code == 404
    write_part(rt, glb=None)
    r = client.get("/parts/mount_plate/model.glb", headers=AUTH)
    assert r.status_code == 404 and r.json()["error"] == "not_found"


class _In(BaseModel):
    pass


async def test_runtime_is_cancelling(home):
    fake = FakeProvider([[ToolCall(id="c1", name="slow_tool", input={}),
                          Done(stop_reason="tool_use")]])
    rt = Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))
    started = asyncio.Event()

    @tool("slow_tool", description="slow", safety=SafetyClass.READ)
    async def slow(ctx, args: _In) -> ToolOutput:
        started.set()
        await asyncio.sleep(30)
        return ToolOutput(summary="late")
    rt.registry.register(slow)
    rid = await rt.start_run("designer", "work")
    await asyncio.wait_for(started.wait(), 5)
    assert rt.is_live(rid) and not rt.is_cancelling(rid)
    rt.cancel(rid)
    assert rt.is_cancelling(rid)
    await asyncio.wait_for(rt.wait(rid), 5)
    assert not rt.is_live("nope") and not rt.is_cancelling("nope")


async def test_cancel_all_cancels_running_and_orphans(home):
    fake = FakeProvider([[ToolCall(id="c1", name="slow_tool", input={}),
                          Done(stop_reason="tool_use")]])
    rt = Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))
    started = asyncio.Event()

    @tool("slow_tool", description="slow", safety=SafetyClass.READ)
    async def slow(ctx, args: _In) -> ToolOutput:
        started.set()
        await asyncio.sleep(30)
        return ToolOutput(summary="late")
    rt.registry.register(slow)
    live = await rt.start_run("designer", "work")
    await asyncio.wait_for(started.wait(), 5)
    orphan = rt.store.create_run("designer", "fake", "m", "left behind by a crash").id
    assert rt.store.get_run(orphan).status == "running"

    transport = httpx.ASGITransport(app=create_app(rt, TOKEN))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/runs/cancel_all", headers=AUTH)
        assert r.status_code == 200
        assert sorted(r.json()["cancelled"]) == sorted([live, orphan])
        await asyncio.wait_for(rt.wait(live), 5)
        assert rt.store.get_run(live).status == "cancelled"
        assert rt.store.get_run(orphan).status == "cancelled"
        r2 = await c.post("/runs/cancel_all", headers=AUTH)
        assert r2.json() == {"cancelled": [], "failed": []}


@pytest.mark.slow
@pytest.mark.timeout(240)
def test_dump_ui_fixtures(tmp_path):
    core = Path(__file__).resolve().parents[2]
    out = tmp_path / "ui"
    subprocess.run([sys.executable, str(core / "scripts" / "dump_ui_fixtures.py"),
                    "--out", str(out)], check=True, cwd=core, timeout=240)
    for n in ("runs.json", "steps.json", "approvals.json", "events.jsonl", "parts.json",
              "agents.json", "models.json", "status.json", "mount_plate.glb",
              # Plan 3 routes
              "profile.json", "profile_put.json", "keys.json", "setup.json", "setup_complete.json",
              "claude_code_status.json", "claude_code_login.json", "claude_code_logout.json",
              "local_test.json", "onshape_test.json", "context_put.json",
              # Plan 4 routes (a fixed Mac and Ollama stand-in: deterministic, no network)
              "local_status.json", "local_start.json", "local_pull_cancel.json"):
        assert (out / n).is_file(), n
    # the stand-in Claude Code is signed in; nothing secret-looking is in any answer
    models = json.loads((out / "models.json").read_text())
    cc = next(v for v in models["vendors"] if v["vendor"] == "claude-code")
    assert (cc["key"], cc["models"]) == ("signed_in", ["sonnet", "opus", "haiku", "fable"])
    assert [e["model"] for e in models["recommended"]] == [
        "claude-code/opus", "anthropic/claude-opus-5-5", "ollama/gpt-oss:20b",
        "openrouter/qwen/qwen3-coder:free"]
    local = json.loads((out / "local_status.json").read_text())
    assert (local["running"], [m["name"] for m in local["models"]]) == (True, ["gpt-oss:20b"])
    assert json.loads((out / "context_put.json").read_text())["selected_part_id"] == "mount_plate"
    for f in out.glob("*.json"):
        assert "sk-ant" not in f.read_text() and "secret" not in f.read_text().lower(), f.name
    approvals = json.loads((out / "approvals.json").read_text())
    assert len(approvals) == 1 and len(approvals[0]["payload_hash"]) == 64
    int(approvals[0]["payload_hash"], 16)
    steps = json.loads((out / "steps.json").read_text())
    diffs = [s for s in steps if s["kind"] == "diff"]
    assert diffs and "delta" in diffs[0]["payload"]["output"]["data"]
    assert (out / "mount_plate.glb").read_bytes()[:4] == b"glTF"


async def test_cancel_all_runs_external_cancel_hooks_and_is_best_effort(home, monkeypatch):
    # M8: an external run's owner hook (AcpSession.cancel) runs; T1: one failing run does not
    # stop the others and is reported under "failed"
    rt = Runtime(home, provider_resolver=lambda ref: (FakeProvider([]), "m"))
    ext = rt.create_external_run("claude", provider="acp", model="claude")
    hooked = []

    async def hook():
        hooked.append(ext)
    rt.register_cancel_hook(ext, hook)
    bad = rt.create_external_run("codex", provider="acp", model="codex")
    good = rt.create_external_run("gemini", provider="acp", model="gemini")
    real_cancel = rt.cancel

    def cancel(run_id):
        if run_id == bad:
            raise RuntimeError("boom")
        real_cancel(run_id)
    monkeypatch.setattr(rt, "cancel", cancel)
    transport = httpx.ASGITransport(app=create_app(rt, TOKEN))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/runs/cancel_all", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["cancelled"]) == sorted([ext, good])
    assert body["failed"] == [{"run_id": bad, "error": "RuntimeError: boom"}]
    await asyncio.sleep(0.05)
    assert hooked == [ext]
    assert rt.store.get_run(good).status == "cancelled"


def test_cors_allows_if_none_match_and_exposes_etag(client, rt):
    # T1 follow-up: the webview revalidates model.glb with If-None-Match and reads the ETag
    r = client.options("/parts/mount_plate/model.glb",
                       headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                                "Access-Control-Request-Headers": "authorization, if-none-match"})
    assert r.status_code == 200
    assert "if-none-match" in r.headers["access-control-allow-headers"].lower()
    write_part(rt)
    ok = client.get("/parts/mount_plate/model.glb", headers={**AUTH, "Origin": ORIGIN})
    assert ok.status_code == 200
    assert "etag" in ok.headers["access-control-expose-headers"].lower()


def test_models_keychain_lookups_run_off_the_event_loop(client, monkeypatch):
    # T1 follow-up: keychain_get spawns `security`; it must not block the event loop
    import asyncio as _asyncio
    on_loop = []

    def kc(*a):
        try:
            _asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return None
    monkeypatch.setattr("mises.platform.keychain_get", kc)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert client.get("/models", headers=AUTH).status_code == 200
    assert on_loop and not any(on_loop)
