import json
import logging

from starlette.testclient import TestClient

from mises.error_log import ErrorLog, install_log_handler, scrub
from mises.events import Event
from mises.server.app import create_app

from .conftest import AUTH, TOKEN


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def lines(log):
    return [json.loads(x) for x in log.path.read_text().splitlines()]


def test_capture_writes_one_jsonl_entry_with_the_fields(tmp_path):
    log = ErrorLog(tmp_path / "logs")
    log.record("tool", "part_create failed", code="tool_failed", hint="check the sketch",
               run_id="r1", tool="part_create", build_id="b1", stack="Traceback ...")
    [e] = lines(log)
    assert log.path == tmp_path / "logs" / "errors.jsonl"
    assert e["source"] == "tool" and e["code"] == "tool_failed" and e["message"] == "part_create failed"
    assert e["hint"] == "check the sketch" and e["run_id"] == "r1" and e["tool"] == "part_create"
    assert e["build_id"] == "b1" and e["stack"] == "Traceback ..." and e["count"] == 1
    assert e["ts"] and e["version"] and e["fp"]


def test_identical_repeats_are_deduplicated_with_a_count(tmp_path):
    clock = Clock()
    log = ErrorLog(tmp_path, clock=clock)
    for _ in range(5):
        clock.t += 1
        log.record("app", "boom", code="x")
    log.record("app", "other")
    rows = log.recent()
    assert [r["message"] for r in rows] == ["other", "boom"]
    assert rows[1]["count"] == 4          # power-of-2 flush; the in-memory count is 5
    assert len(lines(log)) < 6
    clock.t += 120                          # after the window the next one writes the full count
    log.record("app", "boom", code="x")
    assert log.recent(source="app")[0]["count"] == 6


def test_rotation_by_size_keeps_a_bounded_number_of_files(tmp_path):
    log = ErrorLog(tmp_path, max_bytes=2000, keep=2)
    for i in range(60):
        log.record("core", f"error number {i} " + "x" * 50)
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["errors.1.jsonl", "errors.2.jsonl", "errors.jsonl"]
    assert all(p.stat().st_size <= 2000 for p in tmp_path.iterdir())
    assert log.recent(limit=1)[0]["message"].startswith("error number 59")


def test_scrub_removes_secrets():
    secret = "adm1n-t0ken"
    raw = (f"token {secret} failed; Authorization: Bearer abc.def.ghi\n"
           'api_key=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA {"password": "hunter2"} '
           "x-api-key: zzz OPENAI sk-proj-abcdefghijklmnop https://user:pw@host/x "
           "ghp_abcdefghijklmnopqrstuv " + "Q" * 40)
    out = scrub(raw, [secret])
    for s in (secret, "abc.def.ghi", "sk-ant", "hunter2", "zzz", "sk-proj", "user:pw",
              "ghp_", "Q" * 40):
        assert s not in out, s
    assert "/Users/me/a-very-long-folder-name-that-is-not-a-secret/x.py" in scrub(
        "at /Users/me/a-very-long-folder-name-that-is-not-a-secret/x.py")


def test_record_scrubs_every_field_and_truncates(tmp_path):
    log = ErrorLog(tmp_path, secrets=lambda: ["s3cr3t-value"])
    log.record("app", "s3cr3t-value " + "m" * 5000, hint="Bearer s3cr3t-value",
               stack="Authorization: Bearer xyzxyzxyz")
    text = log.path.read_text()
    assert "s3cr3t" not in text and "xyzxyzxyz" not in text
    assert len(lines(log)[0]["message"]) < 2100


def test_event_tap_logs_failed_tools_runs_and_studies(tmp_path):
    log = ErrorLog(tmp_path, build_of=lambda rid: "b9")
    log.observe_event(Event(type="tool_result", run_id="r1",
                            data={"ok": False, "name": "part_create", "error": "tool_failed",
                                  "message": "bad sketch", "hint": "fix it"}))
    log.observe_event(Event(type="tool_result", run_id="r1", data={"ok": True, "name": "x"}))
    log.observe_event(Event(type="run_finished", run_id="r1",
                            data={"status": "failed", "stop_reason": "error", "error": "crashed"}))
    log.observe_event(Event(type="sim_finished", run_id="r1",
                            data={"status": "failed", "kind": "fea", "summary": "mesh failed"}))
    rows = {r["source"]: r for r in log.recent()}
    assert set(rows) == {"tool", "agent", "study"}
    assert rows["tool"]["tool"] == "part_create" and rows["tool"]["build_id"] == "b9"
    assert rows["agent"]["message"] == "crashed" and rows["study"]["message"] == "mesh failed"


def test_logging_handler_captures_core_exceptions_with_stack(tmp_path):
    log = ErrorLog(tmp_path)
    install_log_handler(log)
    try:
        raise ValueError("kaput")
    except ValueError:
        logging.getLogger("mises.something").exception("the thing failed")
    [e] = log.recent()
    assert e["source"] == "core" and e["message"] == "the thing failed" and "kaput" in e["stack"]


def test_routes_report_and_list_admin_only(rt):  # noqa: F811
    with TestClient(create_app(rt, TOKEN, mcp_token="mcp-tok-456"), base_url="http://127.0.0.1") as c:
        r = c.post("/logs/errors", headers=AUTH, json={"message": f"red text {TOKEN}", "code": "network",
                                                       "where": "role=alert"})
        assert r.status_code == 200
        body = c.get("/logs/errors", headers=AUTH).json()
        assert body["path"].endswith("logs/errors.jsonl")
        assert body["entries"][0]["message"] == "red text [redacted]"
        assert c.get("/logs/errors", headers={"Authorization": "Bearer mcp-tok-456"}).status_code == 403
        assert c.get("/logs/errors").status_code == 401
    assert TOKEN not in rt.error_log.path.read_text()
