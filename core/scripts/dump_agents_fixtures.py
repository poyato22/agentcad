"""Write real-core fixtures for the app's Plan 7 contract tests (U3a against U3c): routed
POST /runs answers (a new lead, a follow-up, and — on a core that queues — a queued message and
the 429 of a full queue), GET /projects/{id}/agents, the agent_announce events and the stored
DECISION steps they leave, the message_queued / message_delivered events, and the
assembly_animate event of a single joint and of a sequence.

    uv run python scripts/dump_agents_fixtures.py --out tests/fixtures/ui/plan7

Deterministic: a scripted provider per agent role, no network, a throwaway MISES_HOME.
"""
import argparse
import asyncio
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

TOKEN = "fixture-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _write(out: Path, name: str, data) -> None:
    (out / name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _jsonl(out: Path, name: str, events) -> None:
    (out / name).write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in events))


def _ev(e) -> dict:
    return {"type": e.type, "run_id": e.run_id, "data": e.data, "ts": e.ts}


def _wait(client, rid, n=400):
    for _ in range(n):
        r = client.get(f"/runs/{rid}", headers=AUTH).json()
        if r["status"] in ("done", "failed", "cancelled"):
            time.sleep(0.1)
            return r
        time.sleep(0.02)
    raise SystemExit(f"run {rid} did not finish")


def dump_runs(out: Path) -> None:
    import mises.runtime as runtime_mod
    from fastapi.testclient import TestClient
    from mises.providers.base import Done, TextDelta, ToolCall
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app
    from mises.orchestrator import lead as lead_mod

    runtime_mod.keychain_get = lambda *a: None
    hold = threading.Event()          # the lead's 3rd turn waits here: "busy" for the queue

    def text(t):
        return [TextDelta(text=t), Done(stop_reason="end_turn")]

    spawn = [ToolCall(id="s1", name="agent_spawn", input={
        "agent": "designer", "task": "Design motor mount",
        "prompt": "Make a 30x30x4 mm motor mount plate."}), Done(stop_reason="tool_use")]

    class Held(FakeProvider):
        async def stream(self, req):
            if self.calls_made >= 3:
                while not hold.is_set():
                    await asyncio.sleep(0.02)
            self.calls_made += 1
            async for ev in super().stream(req):
                yield ev

    class Roles:
        name = "fake"

        def __init__(self, **roles):
            self.roles = roles

        async def stream(self, req):
            role = req.system.split("# Role: ", 1)[1].split()[0]
            async for ev in self.roles[role].stream(req):
                yield ev

    engineer = Held([spawn, text("mount done"), text("frame looks fine"),
                     text("working on it"), text("queued one handled")])
    engineer.calls_made = 0
    prov = Roles(engineer=engineer, designer=FakeProvider([text("built the mount")]),
                 verifier=FakeProvider([text("ok")]))
    home = Path(tempfile.mkdtemp(prefix="mises-plan7-"))
    os.environ["MISES_HOME"] = str(home)
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (prov, "fake-model"))
    sub = rt.events.subscribe()
    with TestClient(create_app(rt, TOKEN)) as client:
        pid = rt.active_project_id()
        _write(out, "project_agents_empty.json", client.get(f"/projects/{pid}/agents", headers=AUTH).json())
        first = client.post("/runs", headers=AUTH, json={"prompt": "Build me a drone"})
        _write(out, "run_routed_new.json", first.json())
        lead = first.json()["run_id"]
        _wait(client, lead)
        other = client.post("/runs", headers=AUTH, json={"prompt": "check", "agent": "verifier"}).json()
        _wait(client, other["run_id"])
        follow = client.post("/runs", headers=AUTH, json={"prompt": "now test the frame for stress"})
        _write(out, "run_routed_followup.json", follow.json())
        _wait(client, lead)
        queues = hasattr(lead_mod, "QUEUE_MAX")
        if queues:
            # the lead's next turn is held: a second prompt queues behind it
            busy = client.post("/runs", headers=AUTH, json={"prompt": "add a landing gear"})
            for _ in range(200):
                if client.get(f"/runs/{lead}", headers=AUTH).json()["status"] == "running":
                    break
                time.sleep(0.02)
            q = client.post("/runs", headers=AUTH, json={"prompt": "and paint it red"})
            _write(out, "run_routed_queued.json", q.json())
            saved = lead_mod.QUEUE_MAX
            lead_mod.QUEUE_MAX = 1
            full = client.post("/runs", headers=AUTH, json={"prompt": "one more"})
            lead_mod.QUEUE_MAX = saved
            _write(out, "queue_full.json", {"status": full.status_code, "body": full.json()})
            hold.set()
            _wait(client, busy.json()["run_id"])
            for _ in range(200):          # the queued message is delivered at the turn boundary
                steps = client.get(f"/runs/{lead}/steps?after_id=0", headers=AUTH).json()
                if any("queued_message_delivered" in s["payload"] for s in steps):
                    break
                time.sleep(0.02)
            _wait(client, lead)
        else:
            hold.set()
        _write(out, "project_agents.json", client.get(f"/projects/{pid}/agents", headers=AUTH).json())
        _write(out, "run_lead.json", client.get(f"/runs/{lead}", headers=AUTH).json())
        steps = client.get(f"/runs/{lead}/steps?after_id=0", headers=AUTH).json()
        _write(out, "lead_steps.json", steps)
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    keep = ("agent_announce", "message_queued", "message_delivered", "message_dropped")
    _jsonl(out, "agent_events.jsonl", [_ev(e) for e in events if e.type in keep])


async def dump_animate(out: Path) -> None:
    from mises.assembly.model import AssemblyStore
    from mises.assembly.tools import (AddJointArgs, AddPartArgs, AnimateArgs, assembly_add_joint,
                                      assembly_add_part, assembly_animate)
    from mises.events import EventBus

    tmp = Path(tempfile.mkdtemp(prefix="mises-anim-"))
    ctx = SimpleNamespace(workdir=tmp, events=EventBus(), services={"assembly": AssemblyStore(tmp / "a.db")},
                          run_id="run_fixture")
    a = (await assembly_add_part.handler(ctx, AddPartArgs(project_id="p", part_id="base", place={}))).data["instance"]["id"]
    r = await assembly_add_part.handler(ctx, AddPartArgs(project_id="p", part_id="arm", place={}))
    b = r.data["instance"]["id"]
    ctx.services["assembly"].delete_joint("p", r.data["joint"]["id"])
    fr = lambda iid: {"instance_id": iid, "frame": {"origin": [0, 0, 0], "axis": [0, 0, 1]}}  # noqa: E731
    jid = (await assembly_add_joint.handler(ctx, AddJointArgs(project_id="p", kind="revolute", a=fr(a), b=fr(b)))).data["joint"]["id"]
    sub = ctx.events.subscribe()
    await assembly_animate.handler(ctx, AnimateArgs.model_validate(
        {"project_id": "p", "joint_id": jid, "from": 0, "to": 90, "duration_s": 3, "loop": True}))
    await assembly_animate.handler(ctx, AnimateArgs.model_validate(
        {"project_id": "p", "sequence": [{"joint_id": jid, "to": 45, "duration_s": 1},
                                         {"joint_id": jid, "to": 0, "duration_s": 1}]}))
    evs = []
    while not sub.queue.empty():
        evs.append(_ev(sub.queue.get_nowait()))
    _jsonl(out, "animate_events.jsonl", evs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    dump_runs(args.out)
    asyncio.run(dump_animate(args.out))
    print(f"wrote Plan 7 fixtures to {args.out}")


if __name__ == "__main__":
    main()
