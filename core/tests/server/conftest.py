import httpx
import pytest

import mises.runtime as runtime_mod
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(h))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return h


@pytest.fixture
def fake():
    return FakeProvider([[TextDelta(text="hello"), Done(stop_reason="end_turn")]])


@pytest.fixture
def rt(home, fake):
    return Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))


@pytest.fixture(autouse=True)
def _no_real_model_lists(monkeypatch):
    """/models must never reach Ollama, LM Studio or OpenRouter from the test suite."""
    import mises.server.model_lists as ml

    monkeypatch.setattr(ml, "TRANSPORT", httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("offline in tests"))))
