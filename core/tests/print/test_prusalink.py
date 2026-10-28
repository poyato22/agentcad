import pytest

from mises.print.prusalink import SENDABLE_STATES, PrusaLinkClient
from mises.types import ErrorCode, MisesError

from .fake_prusalink import API_KEY, FakePrusaLink


async def test_status_idle_and_printing():
    with FakePrusaLink("IDLE") as fake:
        c = PrusaLinkClient(fake.host, API_KEY)
        st = await c.status()
        assert st.state == "IDLE" and st.sendable
        assert st.temp_nozzle == 215.0 and st.job is None
        fake.state = "PRINTING"
        st = await c.status()
        assert st.state == "PRINTING" and not st.sendable
        assert st.job["progress"] == 42.0


@pytest.mark.parametrize("state,ok", [("IDLE", True), ("READY", True), ("FINISHED", True),
                                      ("STOPPED", True), ("ERROR", False), ("ATTENTION", False),
                                      ("BUSY", False), ("PRINTING", False), ("PAUSED", False),
                                      ("WEIRD", False)])
async def test_sendable_states(state, ok):
    with FakePrusaLink(state) as fake:
        assert (await PrusaLinkClient(fake.host, API_KEY).status()).sendable is ok
    assert ("ERROR" not in SENDABLE_STATES) and ("BUSY" not in SENDABLE_STATES)


async def test_upload_sends_headers_and_body(tmp_path):
    f = tmp_path / "a.gcode"
    f.write_bytes(b"G28\nG1 X1\n")
    with FakePrusaLink("IDLE") as fake:
        await PrusaLinkClient(fake.host, API_KEY).upload(f, "mises_bracket.gcode", print_after=True)
        up = fake.uploads[0]
    assert up["path"] == "/api/v1/files/usb/mises_bracket.gcode"
    assert up["data"] == b"G28\nG1 X1\n"
    assert up["print_after"] == "?1" and up["overwrite"] == "?1"
    assert up["ctype"] == "application/octet-stream"


async def test_bad_key_is_denied_and_never_echoed():
    with FakePrusaLink("IDLE") as fake:
        with pytest.raises(MisesError) as e:
            await PrusaLinkClient(fake.host, "wrong-secret-key").status()
    assert e.value.code == ErrorCode.DENIED
    assert "wrong-secret-key" not in e.value.message + (e.value.hint or "")


async def test_unreachable_is_transient():
    with FakePrusaLink() as fake:
        host = fake.host
    with pytest.raises(MisesError) as e:
        await PrusaLinkClient(host, API_KEY, timeout=2).status()
    assert e.value.code == ErrorCode.TRANSIENT


async def test_upload_conflict(tmp_path):
    f = tmp_path / "a.gcode"
    f.write_bytes(b"x")
    with FakePrusaLink("IDLE") as fake:
        fake.upload_status = 409
        with pytest.raises(MisesError, match="409|conflict|busy"):
            await PrusaLinkClient(fake.host, API_KEY).upload(f, "a.gcode", print_after=True)


async def test_upload_timeout_after_send_is_unknown_outcome_not_unreachable():
    import socket
    import threading
    import time

    import mises.print.prusalink as pl
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def accept():  # accepts the connection, reads, never answers
        c, _ = srv.accept()
        time.sleep(3)
        c.close()
    threading.Thread(target=accept, daemon=True).start()
    assert pl.UPLOAD_READ_TIMEOUT_S == 120.0
    old, pl.UPLOAD_READ_TIMEOUT_S = pl.UPLOAD_READ_TIMEOUT_S, 0.5
    try:
        with pytest.raises(MisesError) as e:
            await PrusaLinkClient(f"127.0.0.1:{srv.getsockname()[1]}", API_KEY).upload_bytes(
                b"G28\n", "a.gcode", print_after=True)
    finally:
        pl.UPLOAD_READ_TIMEOUT_S = old
        srv.close()
    assert e.value.code == ErrorCode.TOOL_FAILED
    assert "unknown outcome" in e.value.message and "check the printer" in e.value.hint
