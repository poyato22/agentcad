import time

import pytest
from fastapi.testclient import TestClient

from mises.print import slicer
from mises.server.app import create_app

from .conftest import KEYS, make_part
from .fake_prusalink import API_KEY, FakePrusaLink

TOKEN = "t-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(rt, keychain):
    with TestClient(create_app(rt, TOKEN)) as c:
        yield c


def add(client, host, **kw):
    body = {"name": "Bench MK4", "host": host, "api_key": API_KEY, **kw}
    return client.post("/printers", json=body, headers=AUTH)


def poll(client, path, field="status", until=("done", "failed", "cancelled"), timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(path, headers=AUTH).json()
        if r[field] in until:
            return r
        time.sleep(0.05)
    raise AssertionError(f"{path} still {r}")


@pytest.mark.parametrize("method,path", [("get", "/printers"), ("post", "/printers"),
                                         ("get", "/print/capabilities"), ("post", "/slices"),
                                         ("post", "/prints"), ("get", "/slices/slice_0123456789")])
def test_needs_admin_token(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_printer_crud_and_key_goes_to_keychain_only(client, rt):
    r = add(client, "192.168.1.20")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"id": "bench_mk4", "kind": "prusalink", "host": "192.168.1.20",
                    "name": "Bench MK4", "model": "MK4", "has_key": True}
    assert KEYS["mises/printer:bench_mk4"] == API_KEY
    assert API_KEY not in r.text
    assert API_KEY not in (rt.home / "profile.json").read_text()
    assert client.get("/printers", headers=AUTH).json() == [body]
    assert API_KEY not in client.get("/profile", headers=AUTH).text
    assert add(client, "192.168.1.21").status_code == 400  # same id
    d = client.delete("/printers/bench_mk4", headers=AUTH)
    assert d.status_code == 200
    assert client.get("/printers", headers=AUTH).json() == []
    assert "mises/printer:bench_mk4" not in KEYS


def test_printer_validation_never_echoes_key(client):
    r = client.post("/printers", json={"name": "x", "host": "http://bad/", "api_key": "sekret-key"},
                    headers=AUTH)
    assert r.status_code == 400 and "sekret-key" not in r.text
    r = client.post("/printers", json={"name": "x", "host": "a.local", "api_key": ""}, headers=AUTH)
    assert r.status_code == 400
    r = client.post("/printers", json={"name": "x", "host": "a.local", "api_key": "k",
                                       "kind": "bambu"}, headers=AUTH)
    assert r.status_code == 400 and "coming later" in r.json()["message"]


def test_capabilities(client, monkeypatch, tmp_path):
    monkeypatch.delenv("MISES_PRUSASLICER", raising=False)
    monkeypatch.setattr(slicer, "APP_PATH", tmp_path / "nope")
    monkeypatch.setattr("shutil.which", lambda *_: None)
    c = client.get("/print/capabilities", headers=AUTH).json()
    assert c["slicer"] == {"installed": False, "path": None,
                           "install_command": "brew install --cask prusaslicer"}
    kinds = {k["kind"]: k for k in c["kinds"]}
    assert kinds["prusalink"]["available"] is True
    assert kinds["bambu"]["available"] is False and kinds["bambu"]["note"] == "coming later"
    assert c["models"] == ["MK4", "MINI"]
    assert {m["name"] for m in c["materials"]} >= {"PLA", "PETG"}
    assert set(c["materials"][0]) == {"name", "density_g_cm3", "usd_per_kg"}


def test_status_route(client):
    with FakePrusaLink("ERROR") as fake:
        add(client, fake.host)
        s = client.get("/printers/bench_mk4/status", headers=AUTH).json()
        assert s["reachable"] is True and s["state"] == "ERROR" and s["sendable"] is False
        fake.state = "IDLE"
        assert client.get("/printers/bench_mk4/status", headers=AUTH).json()["sendable"] is True
    s = client.get("/printers/bench_mk4/status", headers=AUTH).json()  # server gone
    assert s["reachable"] is False and s["state"] is None and s["error"]
    assert client.get("/printers/nope_x/status", headers=AUTH).status_code == 404


def test_slice_flow_and_preview(client, rt, fake_slicer):
    add(client, "10.0.0.9")
    make_part(rt)
    r = client.post("/slices", json={"part_id": "bracket", "printer_id": "bench_mk4",
                                     "material": "PETG"}, headers=AUTH)
    assert r.status_code == 200, r.text
    sid = r.json()["slice_id"]
    assert r.json()["status"] == "running"
    s = poll(client, f"/slices/{sid}")
    assert s["status"] == "done", s
    assert s["estimate"] == {"time_s": 93802, "time_text": "1d 2h 3m 22s", "filament_mm": 2519.58,
                             "filament_g": 7.51, "cost_usd": round(7.51 / 1000 * 25.0, 4)}
    assert s["material"] == "PETG" and s["part_id"] == "bracket" and s["preview"] is True
    png = client.get(f"/slices/{sid}/preview.png", headers=AUTH)
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert png.content.startswith(b"\x89PNG")
    assert client.get("/slices/slice_ffffffffff", headers=AUTH).status_code == 404
    assert client.get("/slices/../x", headers=AUTH).status_code in (400, 404)


def test_slice_without_slicer_is_424_with_install_hint(client, rt, monkeypatch, tmp_path):
    monkeypatch.delenv("MISES_PRUSASLICER", raising=False)
    monkeypatch.setattr(slicer, "APP_PATH", tmp_path / "nope")
    monkeypatch.setattr("shutil.which", lambda *_: None)
    add(client, "10.0.0.9")
    make_part(rt)
    r = client.post("/slices", json={"part_id": "bracket", "printer_id": "bench_mk4"}, headers=AUTH)
    assert r.status_code == 424
    assert r.json()["hint"] == "install it with: brew install --cask prusaslicer"


def test_slice_bad_inputs(client, rt, fake_slicer):
    add(client, "10.0.0.9")
    assert client.post("/slices", json={"part_id": "ghost", "printer_id": "bench_mk4"},
                       headers=AUTH).status_code == 404
    make_part(rt)
    assert client.post("/slices", json={"part_id": "bracket", "printer_id": "nope_x"},
                       headers=AUTH).status_code == 404
    assert client.post("/slices", json={"part_id": "bracket", "printer_id": "bench_mk4",
                                        "material": "gold"}, headers=AUTH).status_code == 400


def test_print_flow_needs_card_and_refuses_error_state(client, rt, fake_slicer):
    with FakePrusaLink("IDLE") as fake:
        add(client, fake.host)
        make_part(rt)
        sid = client.post("/slices", json={"part_id": "bracket", "printer_id": "bench_mk4"},
                          headers=AUTH).json()["slice_id"]
        assert poll(client, f"/slices/{sid}")["status"] == "done"

        fake.state = "ERROR"
        r = client.post("/prints", json={"slice_id": sid, "printer_id": "bench_mk4"}, headers=AUTH)
        assert r.status_code == 409 and "ERROR" in r.json()["message"]
        assert client.get("/approvals", headers=AUTH).json() == [] and fake.uploads == []

        fake.state = "IDLE"
        r = client.post("/prints", json={"slice_id": sid, "printer_id": "bench_mk4"}, headers=AUTH)
        assert r.status_code == 200, r.text
        call_id = r.json()["call_id"]
        end = time.time() + 5
        while time.time() < end and not (cards := client.get("/approvals", headers=AUTH).json()):
            time.sleep(0.05)
        assert len(cards) == 1 and fake.uploads == []
        card = cards[0]
        assert card["action"] == "print_send"
        assert card["deltas"]["material"] == "PLA" and card["deltas"]["printer"]["state"] == "IDLE"
        assert client.get(f"/prints/{call_id}", headers=AUTH).json()["status"] == "running"
        d = client.post(f"/approvals/{card['id']}/decide", headers=AUTH,
                        json={"payload_hash": card["payload_hash"], "approve": True})
        assert d.status_code == 200
        done = poll(client, f"/prints/{call_id}")
        assert done["status"] == "done" and fake.uploads[0]["print_after"] == "?1"


def test_ui_run_hidden_from_runs_list_unless_asked(client, rt, fake_slicer):
    add(client, "10.0.0.9")
    make_part(rt)
    sid = client.post("/slices", json={"part_id": "bracket", "printer_id": "bench_mk4"},
                      headers=AUTH).json()["slice_id"]
    poll(client, f"/slices/{sid}")
    assert [r for r in client.get("/runs", headers=AUTH).json() if r["provider"] == "ui"] == []
    ui = [r for r in client.get("/runs?include_ui=1", headers=AUTH).json() if r["provider"] == "ui"]
    assert len(ui) == 1 and ui[0]["agent"] == "you"


def test_print_refused_for_model_mismatch(client, rt, fake_slicer):
    with FakePrusaLink("IDLE") as fake:
        add(client, fake.host)
        add(client, fake.host, name="Mini", model="MINI")
        make_part(rt)
        sid = client.post("/slices", json={"part_id": "bracket", "printer_id": "bench_mk4"},
                          headers=AUTH).json()["slice_id"]
        poll(client, f"/slices/{sid}")
        r = client.post("/prints", json={"slice_id": sid, "printer_id": "mini"}, headers=AUTH)
        assert r.status_code == 403 and "MK4" in r.json()["message"]
        assert client.get("/approvals", headers=AUTH).json() == []


def test_cancel_running_slice(client, rt, monkeypatch, tmp_path):
    import stat
    import sys
    p = tmp_path / "hang_slicer"
    p.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MISES_PRUSASLICER", str(p))
    add(client, "10.0.0.9")
    make_part(rt)
    sid = client.post("/slices", json={"part_id": "bracket", "printer_id": "bench_mk4"},
                      headers=AUTH).json()["slice_id"]
    time.sleep(0.5)
    assert client.post(f"/slices/{sid}/cancel", headers=AUTH).status_code == 200
    s = poll(client, f"/slices/{sid}")
    assert s["status"] == "cancelled"
