import stat
import sys
from pathlib import Path

import pytest

import mises.runtime as runtime_mod
from mises.print import tools as print_tools
from mises.profile import PrinterEntry, load_profile, save_profile
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime

FIXTURE = Path(__file__).parent / "fixtures" / "sample.gcode"
KEYS: dict[str, str] = {}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(h))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return h


@pytest.fixture
def rt(home):
    fake = FakeProvider([[TextDelta(text="hi"), Done(stop_reason="end_turn")]])
    return Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))


@pytest.fixture
def keychain(monkeypatch):
    """In-memory Keychain for printer API keys."""
    KEYS.clear()
    monkeypatch.setattr(print_tools, "keychain_get", lambda svc, acct: KEYS.get(f"{svc}/{acct}"))
    monkeypatch.setattr("mises.platform.keychain_get", lambda svc, acct: KEYS.get(f"{svc}/{acct}"))
    monkeypatch.setattr("mises.platform.keychain_set",
                        lambda svc, acct, v: KEYS.__setitem__(f"{svc}/{acct}", v))
    monkeypatch.setattr("mises.platform.keychain_delete",
                        lambda svc, acct: KEYS.pop(f"{svc}/{acct}", None) is not None)
    return KEYS


@pytest.fixture
def fake_slicer(tmp_path, monkeypatch):
    p = tmp_path / "fake_prusaslicer"
    p.write_text(f"#!{sys.executable}\nimport sys\nargs=sys.argv[1:]\n"
                 f"out=args[args.index('--output')+1]\nopen(out,'w').write(open({str(FIXTURE)!r}).read())\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MISES_PRUSASLICER", str(p))
    return p


def add_printer(rt, host: str, *, pid="mk4_bench", key="fake-key-abc", model="MK4"):
    prof = load_profile(rt.home)
    prof = prof.model_copy(update={"printers": [*prof.printers, PrinterEntry(
        id=pid, host=host, name="Bench MK4", model=model)]})
    save_profile(rt.home, prof)
    KEYS[f"mises/printer:{pid}"] = key


def make_part(rt, part_id="bracket"):
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "model.stl").write_bytes(b"solid x\nendsolid x\n")
    return d
