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


def keychain_set(service: str, account: str, secret: str) -> None:
    subprocess.run([_SECURITY, "add-generic-password", "-U", "-s", service, "-a", account,
                    "-w", secret], capture_output=True, text=True, check=True)


def sandbox_profile(write_dir: Path) -> str:
    return (
        "(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n"
        f'(allow file-write* (subpath "{write_dir}") (subpath "/private/var/folders") '
        '(literal "/dev/null"))\n'
    )
