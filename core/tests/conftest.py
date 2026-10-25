import pytest

from mises.claude_code import detect

REAL_FIND_CLAUDE = detect.find_claude


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch):
    """Never run the user's real `claude` from tests: code that looks it up through the module
    (Runtime, routes, CLI) sees it as not installed unless the test asks for the fake one
    (tests/claude_code/conftest.py `claude_bin` puts the real lookup back, with a fake on PATH)."""
    monkeypatch.setattr(detect, "find_claude", lambda: detect.ClaudeInfo(path=None, version=None))
