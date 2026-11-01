"""Write real server fixtures for Plan 10 C1's new routes and fields (app tasks A2/A3).

Throwaway Runtime (temp MISES_HOME, a fake model that only says "ok"), driven through the real
routes:

  chat.json / chat_followup.json   POST /projects/{id}/chat (first message makes the lead; the
                                   next goes to the same lead) — same shape as POST /runs
  queued_cancel.json               POST /runs/{id}/queued/{mid}/cancel -> {ok: true}
  queued_cancel_events.jsonl       the message_dropped event (reason "cancelled")
  retry.json                       POST /runs/{id}/retry
  message_edit_steps.json          GET /runs/{id}/steps after POST /runs/{id}/message {edits}
  error_step_tls_steps.json        GET /runs/{id}/steps of a Claude Code run whose turn failed with
                                   a TLS error: the error step carries kind / cause / hint

    uv run python scripts/dump_p10_c1_fixtures.py --out tests/fixtures/ui/p10
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path


async def dump(out: Path) -> None:
    import mises.runtime as runtime_mod
    from fastapi.testclient import TestClient
    from mises.claude_code import ClaudeCodeSession
    from mises.claude_code.detect import ClaudeInfo
    from mises.providers.base import Done, TextDelta
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app

    runtime_mod.keychain_get = lambda *a: None
    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-p10-"))
    os.environ["MISES_HOME"] = str(home)
    os.environ["ANTHROPIC_API_KEY"] = "fixture-not-used"   # default model resolves natively

    class Ok:
        name = "fake"

        async def stream(self, req):
            async for ev in FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]]
                                         ).stream(req):
                yield ev

    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (Ok(), "fake-model"))
    h = {"Authorization": "Bearer tok"}

    def write(name, data):
        (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    with TestClient(create_app(rt, "tok")) as c:
        def ok(r):
            r.raise_for_status()
            return r.json()

        def wait(rid):
            for _ in range(400):
                if c.get(f"/runs/{rid}", headers=h).json()["status"] != "running":
                    return
                time.sleep(0.02)

        pid = ok(c.post("/projects", headers=h, json={"name": "Robot arm"}))["id"]
        first = ok(c.post(f"/projects/{pid}/chat", headers=h, json={"text": "Build a robot arm"}))
        write("chat.json", first)
        lead = first["run_id"]
        wait(lead)
        write("chat_followup.json",
              ok(c.post(f"/projects/{pid}/chat", headers=h, json={"text": "add a gripper"})))
        wait(lead)

        # a queued message, cancelled by the app
        rt.lead_queues[lead] = [{"message_id": "msg_0123456789ab", "text": "make it blue",
                                 "shown": "make it blue", "log": None}]
        sub = rt.events.subscribe(lead)
        write("queued_cancel.json",
              ok(c.post(f"/runs/{lead}/queued/msg_0123456789ab/cancel", headers=h)))
        evs = []
        while not sub.queue.empty():
            e = sub.queue.get_nowait()
            if e.type == "message_dropped":
                evs.append(e.model_dump(mode="json"))
        (out / "queued_cancel_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs))

        steps = ok(c.get(f"/runs/{lead}/steps", headers=h))
        edited = next(s["id"] for s in steps if s["payload"].get("role") == "user")
        ok(c.post(f"/runs/{lead}/message", headers=h,
                  json={"text": "Build a robot arm, 6 axes", "edits": str(edited)}))
        wait(lead)
        write("message_edit_steps.json", ok(c.get(f"/runs/{lead}/steps", headers=h)))

        write("retry.json", ok(c.post(f"/runs/{lead}/retry", headers=h)))
        wait(lead)

        # a Claude Code turn that failed on a TLS-intercepting proxy
        rid = rt.create_external_run("engineer", provider="claude-code", model="sonnet",
                                     project_id=pid)
        s = ClaudeCodeSession(rt, rid, model_alias="sonnet", info=ClaudeInfo(None, None),
                              mcp_url="http://127.0.0.1:1/mcp", mcp_token="t")
        s._init_ok = True
        s._on_result({"type": "result", "is_error": True, "usage": {},
                      "result": "API Error: Connection error. (cause: unable to get local "
                                "issuer certificate, code UNABLE_TO_GET_ISSUER_CERT_LOCALLY)"})
        write("error_step_tls_steps.json", ok(c.get(f"/runs/{rid}/steps", headers=h)))
    print(f"wrote p10 C1 fixtures to {out} (project={pid!r}, lead={lead})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(dump(a.out))


if __name__ == "__main__":
    sys.exit(main())
