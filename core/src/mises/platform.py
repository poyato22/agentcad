"""OS-specific helpers (macOS): data home, Keychain, sandbox-exec profile."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SECURITY = "/usr/bin/security"
_SYSCTL = "/usr/sbin/sysctl"


def sysctl(name: str) -> str | None:
    """`sysctl -n <name>` (e.g. hw.memsize); None if the key is unknown or sysctl fails."""
    try:
        r = subprocess.run([_SYSCTL, "-n", name], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


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


def keychain_delete(service: str, account: str) -> bool:
    """Remove the item; True if one was removed, False if there was none (exit 44)."""
    r = subprocess.run([_SECURITY, "delete-generic-password", "-s", service, "-a", account],
                       capture_output=True, text=True)
    if r.returncode == 0:
        return True
    if r.returncode == 44:
        return False
    raise RuntimeError(f"keychain_delete failed for service {service!r} (exit {r.returncode})")


def _unsafe(s: str) -> bool:
    return any(c in s for c in '"\\') or any(ord(c) < 32 or ord(c) == 127 for c in s)


_SANDBOX_HOME: Path | None = None


def register_sandbox_home(home: Path | None) -> None:
    """The Runtime registers its home here so every sandboxed worker (`sandboxed_command`) denies
    reads of it. Left unset — no Runtime, as in a bare unit test of run_build — the profile adds
    no home restriction and nothing ever touches the real `data_home()`."""
    global _SANDBOX_HOME
    _SANDBOX_HOME = Path(home).resolve() if home is not None else None


def sandbox_profile(write_dir: Path, *, home: Path | None = None,
                    read_dirs: "tuple[Path, ...] | list[Path]" = ()) -> str:
    """No network, writes only under write_dir, and no new processes: `process-fork` covers
    fork, vfork and posix_spawn, so nothing the sandboxed program starts can outlive it (a
    `fork(); setsid()` daemon would otherwise keep writing into its dir after the host checked
    it). The CAD worker, the drawing worker, gmsh and CalculiX all run in one process.

    When `home` is given the Mises home is also unreadable: server.json holds the admin token,
    mises.db* the approvals and ledger, and runs/ the other runs' policies and workdirs, so a
    part script that read them could carry a secret back in its error text. Only the job's own
    write_dir and the parts tree (geometry scripts and metrics, never secrets) are re-opened; the
    home's and the write_dir's own ancestor dirs keep metadata so the program can still reach its
    write_dir. The stdlib/venv and everything outside the home stay readable."""
    d = str(Path(write_dir).resolve())
    if _unsafe(str(write_dir)) or _unsafe(d):
        raise ValueError("write_dir contains characters unsafe for a sandbox profile")
    extra = []
    for rd in read_dirs:
        rp = str(Path(rd).resolve())
        if _unsafe(str(rd)) or _unsafe(rp):
            raise ValueError("read_dir contains characters unsafe for a sandbox profile")
        extra.append(rp)
    reads = ""
    if home is not None:
        h = Path(home).resolve()
        if _unsafe(str(h)):
            raise ValueError("home contains characters unsafe for a sandbox profile")
        meta_of = [h, *Path(d).parents]
        for rp in extra:
            meta_of += list(Path(rp).parents)
        lookups = " ".join(f'(literal "{p}")' for p in dict.fromkeys(meta_of)
                           if p.is_relative_to(h))
        reads = (
            f'(deny file-read* (subpath "{h}"))\n'
            f'(allow file-read* (subpath "{h / "parts"}"))\n'
            f'(allow file-read* (subpath "{d}"))\n'
        )
        for rp in extra:
            reads += f'(allow file-read* (subpath "{rp}"))\n'
        if lookups:
            reads += f"(allow file-read-metadata {lookups})\n"
    return (
        "(version 1)\n(allow default)\n(deny network*)\n(deny process-fork)\n(deny file-write*)\n"
        f'(allow file-write* (subpath "{d}") (literal "/dev/null"))\n'
        + reads
    )


def sandboxed_command(argv: list[str], write_dir: Path, *, home: Path | None = None,
                      read_dirs: "tuple[Path, ...] | list[Path]" = ()) -> list[str]:
    """Prefix argv with sandbox-exec so it can only write under write_dir, read the Mises home
    only through write_dir, the parts tree and any explicit read_dirs (e.g. a sim's job root,
    which holds the model.step the mesher reads while writing into a level subdir), has no
    network and cannot start other processes. `home` defaults to the Runtime-registered home
    (see register_sandbox_home)."""
    home = home if home is not None else _SANDBOX_HOME
    return ["/usr/bin/sandbox-exec", "-p",
            sandbox_profile(write_dir, home=home, read_dirs=read_dirs), *argv]


def _process_table() -> list[tuple[int, int, int]]:
    """(pid, ppid, pgid) of every process, from one `ps` snapshot."""
    try:
        r = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,pgid="], capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(x.isdigit() for x in parts):
            rows.append((int(parts[0]), int(parts[1]), int(parts[2])))
    return rows


def kill_process_tree(pid: int) -> list[int]:
    """Backstop after a sandboxed run: SIGKILL every process still in its process group (the
    runner starts it with start_new_session, so pgid == pid) or descended from it. macOS does
    not expose session ids, so a process that called setsid() AND lost its parent cannot be
    found here — the real control is the profile's `(deny process-fork)`, under which no such
    process can exist. Returns the pids it had to kill."""
    import signal
    table = _process_table()
    found = {p for p, _pp, g in table if g == pid}
    children: dict[int, list[int]] = {}
    for p, pp, _g in table:
        children.setdefault(pp, []).append(p)
    frontier = [pid, *found]
    while frontier:
        cur = frontier.pop()
        for c in children.get(cur, []):
            if c not in found:
                found.add(c)
                frontier.append(c)
    found.discard(os.getpid())
    killed = []
    for p in sorted(found):
        try:
            os.kill(p, signal.SIGKILL)
            killed.append(p)
        except (ProcessLookupError, PermissionError):
            pass
    return killed


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
        raise ValueError("the agent workdir must be inside the AgentCAD home")
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


def claude_sandbox_profile(home: Path, workdir: Path, claude_config_dir: Path,
                           *, binary_dir: Path | None = None) -> str:
    """sandbox-exec profile for the user's Claude Code (`claude -p`) and everything it spawns.

    agent_sandbox_profile (the Mises home is unreadable except the run's workdir; network and
    the rest of the Mac stay allowed), plus explicit allows for Claude Code's own config dir
    (read/write: sessions, settings; from `claude auth status` configDirectory, default
    ~/.claude) and the binary's install dir (read). Neither may overlap the Mises home, or the
    allow would re-open server.json / mises.db / other runs.
    """
    base = agent_sandbox_profile(home, workdir)
    h = Path(home).resolve()
    extra = []
    for path, rule in ((claude_config_dir, "file-read* file-write*"), (binary_dir, "file-read*")):
        if path is None:
            continue
        r = Path(path).expanduser().resolve()
        if _unsafe(str(path)) or _unsafe(str(r)):
            raise ValueError("path contains characters unsafe for a sandbox profile")
        if h.is_relative_to(r) or r.is_relative_to(h):
            raise ValueError(f"{r} overlaps the AgentCAD home; refusing to allow it")
        extra.append(f'(allow {rule} (subpath "{r}"))\n')
    return base + "".join(extra)


def claude_sandboxed_command(argv: list[str], home: Path, workdir: Path, claude_config_dir: Path,
                             *, binary_dir: Path | None = None) -> list[str]:
    """Prefix argv with sandbox-exec under claude_sandbox_profile; refuses without sandbox-exec."""
    if not os.path.exists(SANDBOX_EXEC):
        raise RuntimeError(f"{SANDBOX_EXEC} is missing; Claude Code cannot be sandboxed")
    return [SANDBOX_EXEC, "-p", claude_sandbox_profile(home, workdir, claude_config_dir,
                                                       binary_dir=binary_dir), *argv]
