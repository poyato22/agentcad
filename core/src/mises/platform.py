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


def sandboxed_command(argv: list[str], write_dir: Path) -> list[str]:
    """Prefix argv with sandbox-exec so it can only write under write_dir and has no network."""
    return ["/usr/bin/sandbox-exec", "-p", sandbox_profile(write_dir), *argv]


SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def agent_sandbox_profile(home: Path, workdir: Path) -> str:
    """sandbox-exec profile for an external (ACP) agent and everything it spawns.

    Everything is allowed (agents need their own config, the network, npx caches, ...) except
    reading or writing the Mises home: server.json (admin token), mises.db* (approvals, ledger),
    other runs and parts. The run's own workdir inside the home stays readable and writable.
    Metadata of the home and runs/ directories stays readable so path lookups of the workdir
    (getcwd, realpath) work; their contents do not.
    """
    h = Path(home).resolve()
    w = Path(workdir).resolve()
    if any(_unsafe(str(x)) for x in (home, workdir, h, w)):
        raise ValueError("path contains characters unsafe for a sandbox profile")
    if not w.is_relative_to(h) or w == h:
        raise ValueError("the agent workdir must be inside the Mises home")
    lookups = " ".join(f'(literal "{d}")' for d in [h, *w.parents] if d.is_relative_to(h))
    return (
        "(version 1)\n(allow default)\n"
        f'(deny file-read* file-write* (subpath "{h}"))\n'
        f"(allow file-read-metadata {lookups})\n"
        f'(allow file-read* file-write* (subpath "{w}"))\n'
    )


def agent_sandboxed_command(argv: list[str], home: Path, workdir: Path) -> list[str]:
    """Prefix argv with sandbox-exec under agent_sandbox_profile(home, workdir)."""
    if not os.path.exists(SANDBOX_EXEC):
        raise RuntimeError(f"{SANDBOX_EXEC} is missing; external agents cannot be sandboxed")
    return [SANDBOX_EXEC, "-p", agent_sandbox_profile(home, workdir), *argv]
