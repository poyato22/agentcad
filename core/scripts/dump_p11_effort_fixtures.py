"""Write real server fixtures for Plan 11 F8 (the composer's Think control, the turn's effort).

Throwaway Runtime (temp MISES_HOME, a fake model that only says "ok"), driven through the real
routes:

  effort_get.json        GET /projects/{id}/effort -> {default, choices, levels}
  effort_put.json        PUT /projects/{id}/effort {default: "hard"}
  effort_bad.json        the 422 answer for a choice that does not exist
  chat_effort.json       POST /projects/{id}/chat {text, effort: "max"}
  chat_effort_steps.json GET /runs/{lead}/steps: the effort decision step (source user), then an
                         auto-picked one (an egg-drop challenge: max, with its reasons), each
                         just before its user message

    uv run python scripts/dump_p11_effort_fixtures.py --out tests/fixtures/ui/p11c
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def dump(out: Path) -> None:
    import mises.runtime as runtime_mod
    from fastapi.testclient import TestClient
    from mises.providers.base import Done, TextDelta
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app

    runtime_mod.keychain_get = lambda *a: None
    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-p11c-"))
    os.environ["MISES_HOME"] = str(home)
    os.environ["ANTHROPIC_API_KEY"] = "fixture-not-used"   # default model resolves natively

    class Ok:
        name = "anthropic"

        async def stream(self, req):
            async for ev in FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]]
                                         ).stream(req):
                yield ev

    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (Ok(), "claude-opus-5-5"))
    h = {"Authorization": "Bearer tok"}

    def write(name, data):
        (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def idle(rid):
        for _ in range(500):
            if rt.store.get_run(rid).status in ("done", "failed") and not rt.run_busy(rid):
                return
            time.sleep(0.02)
        raise RuntimeError("the run did not finish")

    with TestClient(create_app(rt, "tok")) as c:
        p = c.post("/projects", headers=h, json={"name": "Egg Drop"}).json()["id"]
        r = c.get(f"/projects/{p}/effort", headers=h)
        r.raise_for_status()
        write("effort_get.json", r.json())
        r = c.put(f"/projects/{p}/effort", headers=h, json={"default": "hard"})
        r.raise_for_status()
        write("effort_put.json", r.json())
        r = c.put(f"/projects/{p}/effort", headers=h, json={"default": "ultra"})
        assert r.status_code == 422, r.text
        write("effort_bad.json", {"status": r.status_code, "body": r.json()})
        c.put(f"/projects/{p}/effort", headers=h, json={"default": "auto"}).raise_for_status()
        r = c.post(f"/projects/{p}/chat", headers=h,
                   json={"text": "Make a lid for the box", "effort": "max"})
        r.raise_for_status()
        write("chat_effort.json", r.json())
        lead = r.json()["run_id"]
        idle(lead)
        c.post(f"/projects/{p}/chat", headers=h, json={
            "text": "for the egg drop challenge it keeps making the same boring cage but I "
                    "want something really interesting"}).raise_for_status()
        idle(lead)
        write("chat_effort_steps.json", c.get(f"/runs/{lead}/steps", headers=h).json())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    dump(a.out)
    print(f"wrote fixtures to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
