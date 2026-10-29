"""Host-side file access to part dirs that a sandboxed build cannot redirect.

The build sandbox may write anywhere inside its part dir, which includes creating symlinks and
hard links there that point OUTSIDE it. The host (this un-sandboxed process) must therefore never
follow a link it finds in a part dir: a `part.py -> ~/.zshrc` left by a hostile script would turn
the host's next write of part.py into an arbitrary write, and a read into an exfiltration.

Every host read and write in a part dir goes through here:
- the dir itself is opened with O_NOFOLLOW (a part dir replaced by a symlink is refused);
- files are opened relative to that dir fd with O_NOFOLLOW and must be regular files with a
  single link (st_nlink == 1);
- writes go to a fresh temp name (O_CREAT|O_EXCL|O_NOFOLLOW) and are renamed over the target,
  which replaces a link rather than writing through it;
- `scrub()` removes every symlink, extra hard link and special file a build left behind.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path

from ..types import ErrorCode, MisesError

_FLAGS_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK


class UnsafePath(MisesError):
    def __init__(self, msg: str):
        super().__init__(ErrorCode.TOOL_FAILED, msg,
                         hint="the part dir held a link or special file; it was refused")


def real_dir(d: Path) -> Path:
    """The part dir with its PARENT resolved (a run reaches parts through its own symlink, which
    the host made), but its own last component left unresolved, so a link there is caught."""
    d = Path(d)
    return d.parent.resolve() / d.name


def open_dir(d: Path) -> int:
    try:
        return os.open(real_dir(d), _FLAGS_DIR)
    except OSError as e:
        raise UnsafePath(f"{Path(d).name}: not a plain directory ({e.strerror})") from None


def ensure_dir(d: Path) -> None:
    """mkdir -p for a part (or meta) dir, refusing a link in its place."""
    p = real_dir(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(p)
    except FileExistsError:
        pass
    os.close(open_dir(p))


def _check_regular(fd: int, name: str) -> os.stat_result:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise UnsafePath(f"{name}: not a plain file")
    return st


def read_bytes(d: Path, name: str) -> bytes | None:
    """The file's bytes, None if it does not exist; UnsafePath for a link or special file."""
    dfd = open_dir(d)
    try:
        try:
            # O_NONBLOCK: opening a FIFO must not block the event loop; it is refused below
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise UnsafePath(f"{name}: refused ({e.strerror})") from None
        try:
            _check_regular(fd, name)
            os.set_blocking(fd, True)           # a regular file: read it normally
            chunks = []
            while chunk := os.read(fd, 1 << 20):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def read_text(d: Path, name: str) -> str | None:
    b = read_bytes(d, name)
    return None if b is None else b.decode("utf-8", "replace")


def read_json(d: Path, name: str):
    t = read_text(d, name)
    return None if t is None else json.loads(t)


def exists(d: Path, name: str) -> bool:
    try:
        os.lstat(real_dir(d) / name)
    except OSError:
        return False
    return True


def write_bytes(d: Path, name: str, data: bytes) -> None:
    """Atomically replace `name` in dir `d` without following any link: a fresh temp file is
    created exclusively, then renamed over the target (replacing a link, never writing through
    it)."""
    dfd = open_dir(d)
    tmp = f".mises-tmp-{secrets.token_hex(8)}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
        try:
            st = os.lstat(name, dir_fd=dfd)
            if stat.S_ISDIR(st.st_mode):
                raise UnsafePath(f"{name}: is a directory")
        except FileNotFoundError:
            pass
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise
    finally:
        os.close(dfd)


def write_text(d: Path, name: str, text: str) -> None:
    write_bytes(d, name, text.encode("utf-8"))


def copy_out(d: Path, name: str, dest_dir: Path, dest_name: str | None = None) -> Path | None:
    """Copy a part file (read safely, as one open) into a host-chosen dir and return the copy's
    path — what to hand to later consumers instead of a path into the part dir, which a check
    could not keep honest until they open it. None if the file does not exist."""
    data = read_bytes(d, name)
    if data is None:
        return None
    ensure_dir(dest_dir)
    write_bytes(dest_dir, dest_name or name, data)
    return real_dir(dest_dir) / (dest_name or name)


def unlink(d: Path, name: str) -> None:
    """Remove `name` (a link is removed itself, never its target)."""
    dfd = open_dir(d)
    try:
        os.unlink(name, dir_fd=dfd)
    except FileNotFoundError:
        pass
    finally:
        os.close(dfd)


def scrub(d: Path, *, max_entries: int = 100_000) -> list[str]:
    """Remove every symlink, multiply-linked file and special file under `d` (recursively,
    without following links). Returns the relative paths removed; [] means the dir is clean."""
    bad: list[str] = []
    root = real_dir(d)
    if os.path.islink(root):
        raise UnsafePath(f"{Path(d).name}: the part dir itself is a link")
    stack, seen = [root], 0
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for e in entries:
            seen += 1
            if seen > max_entries:
                raise UnsafePath(f"{Path(d).name}: too many files")
            st = e.stat(follow_symlinks=False)
            rel = os.path.relpath(e.path, root)
            if stat.S_ISDIR(st.st_mode):
                stack.append(Path(e.path))
            elif not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                bad.append(rel)
                try:
                    os.unlink(e.path)          # removes the link / name, never the target
                except OSError:
                    pass
    return sorted(bad)
