import asyncio

import pytest

from mises.profile import PrinterEntry, Profile
from mises.types import MisesError

from .conftest import add_printer, make_part
from .fake_prusalink import FakePrusaLink


def send_args(rt, sid, printer_id="mk4_bench"):
    from mises.print.tools import read_slice
    rec = read_slice(rt.home, sid)
    return {"slice_id": sid, "printer_id": printer_id, "gcode_sha256": rec["gcode_sha256"],
            "file_name": rec["file_name"]}


async def wait_call(rt, call, timeout=10):
    await asyncio.wait_for(asyncio.shield(call.task), timeout)
    return call


async def pending(rt, n=1, timeout=5):
    for _ in range(int(timeout / 0.02)):
        p = rt.store.pending_approvals()
        if len(p) >= n:
            return p
        await asyncio.sleep(0.02)
    raise AssertionError("no approval card appeared")


def test_profile_printers_validation():
    Profile(printers=[PrinterEntry(id="mk4_a", host="192.168.1.20", name="A")])
    with pytest.raises(ValueError):
        PrinterEntry(id="Bad Id", host="x.local", name="A")
    with pytest.raises(ValueError):
        PrinterEntry(id="ok_id", host="http://evil/x", name="A")
    with pytest.raises(ValueError):
        PrinterEntry(id="ok_id", host="a.local:99999", name="A")
    with pytest.raises(ValueError):
        Profile(printers=[PrinterEntry(id="dup_id", host="a", name="A")] * 2)


async def test_printers_list_has_no_keys(rt, keychain):
    add_printer(rt, "10.0.0.5")
    call = rt.ui_tool_call("print_printers_list", {})
    await wait_call(rt, call)
    assert call.status == "done"
    assert call.output["data"]["printers"][0]["id"] == "mk4_bench"
    assert "fake-key-abc" not in str(call.output)


async def test_slice_estimate_and_preview(rt, keychain, fake_slicer):
    add_printer(rt, "10.0.0.5")
    make_part(rt)
    call = rt.ui_tool_call("print_slice", {"part_id": "bracket", "printer_id": "mk4_bench",
                                           "material": "pla"})
    await wait_call(rt, call)
    assert call.status == "done", call.error
    data = call.output["data"]
    assert data["estimate"]["filament_g"] == 7.51 and data["estimate"]["time_s"] == 93802
    assert data["material"] == "PLA" and data["preview"] is True
    assert (rt.home / "slices" / data["slice_id"] / "preview.png").is_file()


async def test_slice_without_slicer_gives_install_hint(rt, keychain, monkeypatch, tmp_path):
    from mises.print import slicer
    monkeypatch.delenv("MISES_PRUSASLICER", raising=False)
    monkeypatch.setattr(slicer, "APP_PATH", tmp_path / "nope")
    monkeypatch.setattr("shutil.which", lambda *_: None)
    add_printer(rt, "10.0.0.5")
    make_part(rt)
    call = rt.ui_tool_call("print_slice", {"part_id": "bracket", "printer_id": "mk4_bench"})
    await wait_call(rt, call)
    assert call.status == "failed"
    assert call.error["hint"] == "install it with: brew install --cask prusaslicer"


async def _slice(rt):
    make_part(rt)
    c = rt.ui_tool_call("print_slice", {"part_id": "bracket", "printer_id": "mk4_bench"})
    await wait_call(rt, c)
    assert c.status == "done", c.error
    return c.output["data"]["slice_id"]


async def test_send_needs_approval_then_uploads_and_starts(rt, keychain, fake_slicer):
    with FakePrusaLink("IDLE") as fake:
        add_printer(rt, fake.host)
        sid = await _slice(rt)
        call = rt.ui_tool_call("print_send", send_args(rt, sid))
        (appr,) = await pending(rt)
        assert fake.uploads == []  # nothing before approval
        assert appr.action == "print_send" and appr.payload["slice_id"] == sid
        card = appr.deltas
        assert card["material"] == "PLA" and card["filament_g"] == 7.51
        assert card["cost_usd"] > 0 and card["printer"]["state"] == "IDLE"
        assert card["preview_available"] is True
        rt.approvals.decide(appr.id, appr.payload_hash, True, decided_by="local-user")
        await wait_call(rt, call)
        assert call.status == "done", call.error
        assert fake.uploads[0]["print_after"] == "?1"
        assert fake.uploads[0]["path"] == "/api/v1/files/usb/mises_bracket.gcode"


@pytest.mark.parametrize("state", ["ERROR", "BUSY", "PRINTING", "ATTENTION"])
async def test_send_refused_for_bad_printer_state_before_any_card(rt, keychain, fake_slicer, state):
    with FakePrusaLink("IDLE") as fake:
        add_printer(rt, fake.host)
        sid = await _slice(rt)
        fake.state = state
        call = rt.ui_tool_call("print_send", send_args(rt, sid))
        await wait_call(rt, call)
        assert call.status == "failed" and call.error["error"] == "denied"
        assert state in call.error["message"]
        assert rt.store.pending_approvals() == [] and fake.uploads == []


async def test_printer_errors_after_approval_still_refused(rt, keychain, fake_slicer):
    with FakePrusaLink("IDLE") as fake:
        add_printer(rt, fake.host)
        sid = await _slice(rt)
        call = rt.ui_tool_call("print_send", send_args(rt, sid))
        (appr,) = await pending(rt)
        fake.state = "ERROR"  # changed while the card was open
        rt.approvals.decide(appr.id, appr.payload_hash, True, decided_by="local-user")
        await wait_call(rt, call)
        assert call.status == "failed" and "ERROR" in call.error["message"]
        assert fake.uploads == []


async def test_rejected_send_uploads_nothing(rt, keychain, fake_slicer):
    with FakePrusaLink("IDLE") as fake:
        add_printer(rt, fake.host)
        sid = await _slice(rt)
        call = rt.ui_tool_call("print_send", send_args(rt, sid))
        (appr,) = await pending(rt)
        rt.approvals.decide(appr.id, appr.payload_hash, False, decided_by="local-user")
        await wait_call(rt, call)
        assert call.status == "failed" and fake.uploads == []


async def test_send_unknown_slice_or_printer(rt, keychain):
    add_printer(rt, "10.0.0.5")
    extra = {"gcode_sha256": "0" * 64, "file_name": "x.gcode"}
    call = rt.ui_tool_call("print_send", {"slice_id": "slice_0123456789",
                                          "printer_id": "mk4_bench", **extra})
    await wait_call(rt, call)
    assert call.error["error"] == "not_found"
    call = rt.ui_tool_call("print_send", {"slice_id": "../../etc", "printer_id": "mk4_bench", **extra})
    await wait_call(rt, call)
    assert call.error["error"] == "invalid_input"


async def test_card_shows_hash_name_and_slice_model(rt, keychain, fake_slicer):
    with FakePrusaLink("IDLE") as fake:
        add_printer(rt, fake.host)
        sid = await _slice(rt)
        args = send_args(rt, sid)
        rt.ui_tool_call("print_send", args)
        (appr,) = await pending(rt)
        assert appr.payload["gcode_sha256"] == args["gcode_sha256"]
        assert appr.payload["file_name"] == "mises_bracket.gcode"
        assert appr.deltas["gcode_sha256_short"] == args["gcode_sha256"][:12]
        assert appr.deltas["file_name"] == "mises_bracket.gcode"
        assert appr.deltas["slice_model"] == "MK4" and appr.deltas["printer"]["model"] == "MK4"


async def test_reslice_after_card_refuses_send(rt, keychain, fake_slicer):
    with FakePrusaLink("IDLE") as fake:
        add_printer(rt, fake.host)
        sid = await _slice(rt)
        call = rt.ui_tool_call("print_send", send_args(rt, sid))
        (appr,) = await pending(rt)
        # the G-code on disk changes after the card was shown (re-slice / tamper)
        (rt.home / "slices" / sid / "model.gcode").write_text("; other job\nG28\n")
        rt.approvals.decide(appr.id, appr.payload_hash, True, decided_by="local-user")
        await wait_call(rt, call)
        assert call.status == "failed" and call.error["error"] == "needs_approval"
        assert fake.uploads == []


async def test_stale_hash_in_request_is_refused_before_card(rt, keychain, fake_slicer):
    with FakePrusaLink("IDLE") as fake:
        add_printer(rt, fake.host)
        sid = await _slice(rt)
        args = send_args(rt, sid)
        # a second slice of the same part gets its own id; the old request's hash is not valid
        # for a rewritten record
        from mises.print.tools import read_slice, write_slice
        rec = read_slice(rt.home, sid)
        write_slice(rt.home, sid, {**rec, "gcode_sha256": "f" * 64})
        call = rt.ui_tool_call("print_send", args)
        await wait_call(rt, call)
        assert call.error["error"] == "needs_approval"
        assert rt.store.pending_approvals() == [] and fake.uploads == []


async def test_send_refused_when_slice_model_differs_from_printer(rt, keychain, fake_slicer):
    with FakePrusaLink("IDLE") as fake:
        add_printer(rt, fake.host)
        sid = await _slice(rt)
        add_printer(rt, fake.host, pid="mini_bench", model="MINI")
        call = rt.ui_tool_call("print_send", {**send_args(rt, sid), "printer_id": "mini_bench"})
        await wait_call(rt, call)
        assert call.status == "failed" and call.error["error"] == "denied"
        assert "MK4" in call.error["message"] and "MINI" in call.error["message"]
        assert rt.store.pending_approvals() == [] and fake.uploads == []


async def test_agents_cannot_choose_slice_id(rt, keychain, fake_slicer):
    from mises.print.tools import SliceArgs
    assert "slice_id" not in SliceArgs.model_fields
    add_printer(rt, "10.0.0.5")
    make_part(rt)
    a = rt.ui_tool_call("print_slice", {"part_id": "bracket", "printer_id": "mk4_bench"})
    b = rt.ui_tool_call("print_slice", {"part_id": "bracket", "printer_id": "mk4_bench"})
    await wait_call(rt, a)
    await wait_call(rt, b)
    assert a.output["data"]["slice_id"] != b.output["data"]["slice_id"]


async def test_ui_calls_share_one_run_and_cancel_is_per_call(rt, keychain, fake_slicer):
    with FakePrusaLink("IDLE") as fake:
        add_printer(rt, fake.host)
        sid = await _slice(rt)
        c1 = rt.ui_tool_call("print_send", send_args(rt, sid))
        (appr1,) = await pending(rt, 1)
        # a second send for a different slice, so its card has a different payload
        sid2 = await _slice_again(rt)
        c2 = rt.ui_tool_call("print_send", send_args(rt, sid2))
        await pending(rt, 2)
        assert c1.run_id == c2.run_id
        assert rt.store.get_run(c1.run_id).provider == "ui"
        rt.cancel_ui_call(c1.call_id)
        await wait_call(rt, c1)
        assert c1.status == "cancelled" and c2.status == "running"
        left = rt.store.pending_approvals()
        assert [a.payload["slice_id"] for a in left] == [sid2]
        assert rt.store.get_run(c2.run_id).status == "running"
        rt.approvals.decide(left[0].id, left[0].payload_hash, True, decided_by="local-user")
        await wait_call(rt, c2)
        assert c2.status == "done" and len(fake.uploads) == 1


async def _slice_again(rt):
    c = rt.ui_tool_call("print_slice", {"part_id": "bracket", "printer_id": "mk4_bench"})
    await wait_call(rt, c)
    assert c.status == "done", c.error
    return c.output["data"]["slice_id"]


async def test_ui_run_closed_by_aclose_and_hidden_from_runs(rt, keychain):
    add_printer(rt, "10.0.0.5")
    call = rt.ui_tool_call("print_printers_list", {})
    await wait_call(rt, call)
    assert rt.store.get_run(call.run_id).status == "running"  # not finished per call
    await rt.aclose()
    assert rt.store.get_run(call.run_id).status == "done"
