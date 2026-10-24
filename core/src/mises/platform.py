"""OS-specific helpers (macOS): data home, Keychain, sandbox-exec profile."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SECURITY = "/usr/bin/security"


def data_home() -> Path:
    env = os.environ.get("MISES_HOME")
    p = Path(env) if env else Path.home() / "Library" / "Application Support" / "Mises"
    p.mkdir(parents=True, exist_ok=True)
    return p


def keychain_get(service: str, account: str) -> str | None:
    r = subprocess.run([_SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.rstrip("\n")


def _q(v: str) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise ValueError("keychain value contains control characters")
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def keychain_set(service: str, account: str, secret: str) -> None:
    # Command goes via stdin (`security -i`) so the secret never appears in argv.
    cmd = f"add-generic-password -U -s {_q(service)} -a {_q(account)} -w {_q(secret)}\n"
    r = subprocess.run([_SECURITY, "-i"], input=cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"keychain_set failed for service {service!r} (exit {r.returncode})")


def _unsafe(s: str) -> bool:
    return any(c in s for c in '"\\') or any(ord(c) < 32 or ord(c) == 127 for c in s)


def sandbox_profile(write_dir: Path) -> str:
    d = str(Path(write_dir).resolve())
    if _unsafe(str(write_dir)) or _unsafe(d):
        raise ValueError("write_dir contains characters unsafe for a sandbox profile")
    return (
        "(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n"
        f'(allow file-write* (subpath "{d}") (literal "/dev/null"))\n'
    )
