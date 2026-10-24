from io import StringIO

import pytest
from rich.console import Console

import mises.runtime as runtime_mod
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.types import ErrorCode, MisesError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)


@pytest.fixture
def out():
    return StringIO()


@pytest.fixture
def console(out):
    return Console(file=out, force_terminal=False, width=100)


def make_runtime(tmp_path, turns):
    fake = FakeProvider(turns)

    def resolver(ref):
        if ref == "bogus":
            raise MisesError(ErrorCode.INVALID_INPUT, "unknown vendor or empty model in 'bogus'")
        return fake, "fake-model"
    return Runtime(tmp_path / "home", provider_resolver=resolver), fake


class Lines:
    """Scripted read_line: pops answers in order and records the prompts it was shown."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    async def __call__(self, prompt, default=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)
