"""Find the user's installed `claude`, read its sign-in status, start Anthropic's sign-in flow.

Compliance: Mises runs the user's own binary unmodified. Sign-in happens only through
`claude auth login` (Anthropic's flow; it opens the browser). Status comes from
`claude auth status` (JSON; exit 0 = signed in), and only non-secret fields are kept. Mises never
reads, copies, stores, logs or transmits Claude tokens or the `~/.claude` credential files:
nothing here opens a file, the login's stdout is discarded unread, and its stderr tail is
redacted before it is kept.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

VERSION_TIMEOUT_S = 10
STATUS_TIMEOUT_S = 15
STDERR_TAIL_BYTES = 2048

# Documented install locations, after PATH (the app may start with a minimal PATH).
FALLBACK_DIRS: tuple[str, ...] = (
    str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin",
    str(Path.home() / ".claude" / "local"),
)

_TOKENISH = re.compile(
    r"sk-ant-[A-Za-z0-9_\-]+"                      # Anthropic keys / OAuth tokens
    r"|[A-Za-z0-9_\-\.+/=]{32,}"                   # any long opaque run (JWTs, codes, hashes)
)


def redact(text: str) -> str:
    """Replace token-looking strings with [redacted]."""
    return _TOKENISH.sub("[redacted]", text)


@dataclass(frozen=True)
class ClaudeInfo:
    path: str | None
    version: str | None


@dataclass(frozen=True)
class AuthStatus:
    signed_in: bool
    auth_method: str
    email: str | None
    config_dir: str | None
    raw_ok: bool


NOT_SIGNED_IN = AuthStatus(signed_in=False, auth_method="none", email=None, config_dir=None,
                           raw_ok=False)


def _version(path: str) -> str | None:
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True,
                           timeout=VERSION_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    m = re.match(r"\s*v?(\d+(?:\.\d+)+\S*)", r.stdout)
    return m.group(1) if m else r.stdout.strip().split()[0]


def find_claude() -> ClaudeInfo:
    """The first executable `claude` on PATH, then in FALLBACK_DIRS; its `--version`."""
    candidates = []
    found = shutil.which("claude")
    if found:
        candidates.append(found)
    candidates += [os.path.join(d, "claude") for d in FALLBACK_DIRS]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            v = _version(c)
            if v is not None:
                return ClaudeInfo(path=c, version=v)
    return ClaudeInfo(path=None, version=None)


def _str_or_none(v) -> str | None:
    return v if isinstance(v, str) and v else None


def parse_status(returncode: int, stdout: str) -> AuthStatus:
    try:
        data = json.loads(stdout)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError:
        return NOT_SIGNED_IN
    method = _str_or_none(data.get("authMethod")) or "none"
    signed_in = returncode == 0 and data.get("loggedIn", True) is True and method != "none"
    return AuthStatus(signed_in=signed_in, auth_method=method,
                      email=_str_or_none(data.get("email")),
                      config_dir=_str_or_none(data.get("configDirectory")), raw_ok=True)


async def auth_status(info: ClaudeInfo) -> AuthStatus:
    """Run `claude auth status` (JSON by default; exit 0 = signed in, 1 = not)."""
    if not info.path:
        return NOT_SIGNED_IN
    try:
        proc = await asyncio.create_subprocess_exec(
            info.path, "auth", "status", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)
    except OSError:
        return NOT_SIGNED_IN
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), STATUS_TIMEOUT_S)
    except asyncio.TimeoutError:
        return NOT_SIGNED_IN
    finally:
        if proc.returncode is None:      # timed out or our caller was cancelled
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            await asyncio.shield(proc.wait())
    return parse_status(proc.returncode, out.decode("utf-8", "replace"))


@dataclass
class LoginHandle:
    """A running `claude auth login`. Its stdout goes to /dev/null; only a redacted stderr tail
    is kept. `wait()` waits for the flow to exit and re-checks `claude auth status`."""
    info: ClaudeInfo
    proc: asyncio.subprocess.Process
    returncode: int | None = None
    stderr_tail: str = ""
    status: AuthStatus | None = None
    _tail: bytearray = field(default_factory=bytearray, repr=False)
    _pump: asyncio.Task | None = field(default=None, repr=False)

    async def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        while chunk := await self.proc.stderr.read(4096):
            self._tail += chunk
            del self._tail[:-STDERR_TAIL_BYTES * 2]
        self.stderr_tail = redact(self._tail.decode("utf-8", "replace"))[-STDERR_TAIL_BYTES:]
        self._tail.clear()

    @property
    def done(self) -> bool:
        return self.returncode is not None

    async def wait(self, timeout: float | None = None) -> AuthStatus:
        try:
            await asyncio.wait_for(self.proc.wait(), timeout)
        except asyncio.TimeoutError:
            await self.cancel()
        if self._pump is not None:
            await self._pump
        self.returncode = self.proc.returncode
        self.status = await auth_status(self.info)
        return self.status

    async def cancel(self) -> None:
        if self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.proc.wait(), 2)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
                await self.proc.wait()
        self.returncode = self.proc.returncode


async def start_login(info: ClaudeInfo, *, console: bool = False) -> LoginHandle:
    """Start `claude auth login [--console]`: Anthropic's own flow, which opens the browser."""
    if not info.path:
        raise FileNotFoundError("claude is not installed")
    argv = [info.path, "auth", "login", *(["--console"] if console else [])]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE, start_new_session=True)
    h = LoginHandle(info=info, proc=proc)
    h._pump = asyncio.create_task(h._read_stderr())
    return h
