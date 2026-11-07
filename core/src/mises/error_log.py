"""The error log: every error the user can see (error cards, red text, failed tool steps,
failed runs and studies, toasts) plus uncaught core and app exceptions, appended to one JSONL
file, `<MISES_HOME>/logs/errors.jsonl`, so they can be worked on later.

One line per occurrence group: identical repeats (same source, code, message, tool and build)
within REPEAT_WINDOW_S bump an in-memory count and are re-written at most once per window with
the running count; readers collapse lines by `fp`, keeping the last. The file rotates by size
(errors.jsonl -> errors.1.jsonl ... errors.{KEEP}.jsonl).

Secrets never reach the file: every string goes through `scrub` (the admin and MCP tokens,
Authorization headers, bearer tokens, key=value credentials, provider key shapes and any long
opaque token run, via claude_code.detect.redact). Messages are truncated; web page text in a
message stays data, it is never interpreted."""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
import subprocess
import threading
import time
import traceback
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Iterable

from .claude_code.detect import redact as _redact_tokenish

SOURCES = ("app", "core", "agent", "tool", "study")
MAX_BYTES = 2 * 1024 * 1024
KEEP = 3
MAX_MESSAGE = 2000
MAX_HINT = 1000
MAX_STACK = 8000
REPEAT_WINDOW_S = 60.0

_SECRET_PATTERNS = [
    # Authorization / Proxy-Authorization / Cookie headers: the whole value
    (re.compile(r"(?im)\b((?:proxy-)?authorization|cookie|set-cookie|x-api-key)\b(\s*[:=]\s*)[^\r\n]+"),
     r"\1\2[redacted]"),
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9_\-.+/=~]{8,}"), r"\1 [redacted]"),
    # key=value / "key": "value" credentials
    (re.compile(r"""(?i)(["']?\b[\w-]*(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|"""
                r"""credential|client[_-]?secret|private[_-]?key)[\w-]*["']?\s*[:=]\s*)(["']?)[^\s"',}&]+"""),
     r"\1\2[redacted]"),
    # provider key shapes, even short ones
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{12,}"), "[redacted]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat|xox[abprs]|AIza)[A-Za-z0-9_\-]{12,}"), "[redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
     "[redacted private key]"),
    # secrets in URLs: user:pass@ and ?token= style query params
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[redacted]@"),
]


def scrub(text: Any, secrets: Iterable[str] = (), limit: int | None = None) -> str | None:
    """`text` with secrets replaced by [redacted], truncated to `limit` characters."""
    if text is None:
        return None
    s = str(text)
    for sec in sorted((x for x in secrets if x), key=len, reverse=True):
        s = s.replace(sec, "[redacted]")
    for pat, rep in _SECRET_PATTERNS:
        s = pat.sub(rep, s)
    s = _redact_tokenish(s)
    if limit is not None and len(s) > limit:
        s = s[:limit] + f"… [{len(s) - limit} more characters]"
    return s


@functools.cache
def app_version() -> str:
    """The core's package version plus the git sha of the checkout, when there is one."""
    try:
        v = metadata.version("mises")
    except metadata.PackageNotFoundError:
        v = "dev"
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent,
                           capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            v += f"+{r.stdout.strip()}"
    except (OSError, subprocess.SubprocessError):
        pass
    return v


class ErrorLog:
    def __init__(self, folder: Path, *, secrets: Callable[[], Iterable[str]] | None = None,
                 max_bytes: int = MAX_BYTES, keep: int = KEEP, clock: Callable[[], float] = time.time,
                 build_of: Callable[[str], str | None] | None = None):
        self.folder = Path(folder)
        self.path = self.folder / "errors.jsonl"
        self._secrets = secrets or (lambda: ())
        self.max_bytes = max_bytes
        self.keep = keep
        self._clock = clock
        self._build_of = build_of
        self._lock = threading.Lock()
        self._seen: dict[str, dict] = {}   # fp -> {"entry": ..., "written": ts}

    # ---- writing
    def record(self, source: str, message: Any, *, code: Any = None, hint: Any = None,
               stack: Any = None, build_id: str | None = None, run_id: str | None = None,
               tool: str | None = None, version: str | None = None, extra: dict | None = None) -> dict | None:
        """Append one error (or bump its repeat count). Never raises: logging an error must not
        cause another one."""
        try:
            return self._record(source, message, code=code, hint=hint, stack=stack, build_id=build_id,
                                run_id=run_id, tool=tool, version=version, extra=extra)
        except Exception:  # noqa: BLE001
            return None

    def _record(self, source, message, *, code, hint, stack, build_id, run_id, tool, version, extra):
        secrets = list(self._secrets() or ())
        if build_id is None and run_id and self._build_of is not None:
            try:
                build_id = self._build_of(run_id)
            except Exception:  # noqa: BLE001
                build_id = None
        src = source if source in SOURCES else "app"
        msg = scrub(message, secrets, MAX_MESSAGE) or ""
        entry = {
            "ts": None, "first_ts": None, "count": 1, "source": src,
            "code": scrub(code, secrets, 100), "message": msg,
            "hint": scrub(hint, secrets, MAX_HINT),
            "stack": scrub(stack, secrets, MAX_STACK),
            "build_id": scrub(build_id, secrets, 200), "run_id": scrub(run_id, secrets, 200),
            "tool": scrub(tool, secrets, 200),
            "version": scrub(version, secrets, 100) or app_version(),
        }
        if extra:
            entry["extra"] = {str(k)[:50]: scrub(v, secrets, 500) for k, v in list(extra.items())[:10]}
        key = "\x1f".join(str(entry[k] or "") for k in ("source", "code", "message", "tool", "build_id"))
        fp = hashlib.sha1(key.encode()).hexdigest()[:16]
        now = self._clock()
        with self._lock:
            seen = self._seen.get(fp)
            if seen is not None and now - seen["entry"]["ts"] < REPEAT_WINDOW_S:
                e = seen["entry"]
                e["count"] += 1
                e["ts"] = now
                if now - seen["written"] < REPEAT_WINDOW_S and e["count"] & (e["count"] - 1):
                    return e          # repeat inside the window: count only (power-of-2 counts flush)
                seen["written"] = now
                self._append(e)
                return e
            entry.update(ts=now, first_ts=now, fp=fp)
            if seen is not None:      # the same error again after a quiet spell: keep counting
                entry["count"] = seen["entry"]["count"] + 1
                entry["first_ts"] = seen["entry"]["first_ts"]
            self._seen[fp] = {"entry": entry, "written": now}
            if len(self._seen) > 2000:
                self._seen.pop(next(iter(self._seen)))
            self._append(entry)
            return entry

    def _append(self, entry: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        try:
            if self.path.stat().st_size + len(line) > self.max_bytes:
                self._rotate()
        except FileNotFoundError:
            pass
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)

    def _rotate(self) -> None:
        oldest = self.folder / f"errors.{self.keep}.jsonl"
        oldest.unlink(missing_ok=True)
        for i in range(self.keep - 1, 0, -1):
            p = self.folder / f"errors.{i}.jsonl"
            if p.exists():
                p.rename(self.folder / f"errors.{i + 1}.jsonl")
        self.path.rename(self.folder / "errors.1.jsonl")

    def record_exception(self, source: str, exc: BaseException, **kw) -> dict | None:
        code = getattr(exc, "code", None) or type(exc).__name__
        stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return self.record(source, getattr(exc, "message", None) or str(exc) or type(exc).__name__,
                           code=str(code), hint=getattr(exc, "hint", None), stack=stack, **kw)

    # ---- reading
    def recent(self, limit: int = 200, source: str | None = None, query: str | None = None) -> list[dict]:
        """The newest entries first, repeats collapsed (the last line of each fp wins)."""
        by_fp: dict[str, dict] = {}
        files = [self.folder / f"errors.{i}.jsonl" for i in range(self.keep, 0, -1)] + [self.path]
        for p in files:
            try:
                text = p.read_text(encoding="utf-8")
            except (FileNotFoundError, OSError):
                continue
            for raw in text.splitlines():
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    by_fp.pop(e.get("fp") or raw, None)
                    by_fp[e.get("fp") or raw] = e
        rows = list(reversed(by_fp.values()))
        if source:
            rows = [e for e in rows if e.get("source") == source]
        if query:
            q = query.lower()
            rows = [e for e in rows if q in " ".join(str(e.get(k) or "") for k in
                                                     ("message", "hint", "code", "tool")).lower()]
        return rows[:max(0, limit)]

    # ---- the central core hook: every user-visible error goes out as an event
    def observe_event(self, event) -> None:
        """EventBus tap: failed tool steps, failed runs and failed studies, as the app shows them."""
        try:
            t, d = event.type, event.data or {}
            if t == "tool_result" and d.get("ok") is False:
                self.record("tool", d.get("message") or d.get("summary") or "tool failed",
                            code=d.get("error"), hint=d.get("hint"), run_id=event.run_id,
                            tool=d.get("name"), build_id=d.get("build_id"))
            elif t == "run_finished" and d.get("status") == "failed":
                self.record("agent", d.get("error") or f"the run stopped ({d.get('stop_reason') or 'failed'})",
                            code=d.get("stop_reason"), run_id=event.run_id, build_id=d.get("build_id"))
            elif t in ("sim_finished", "topopt_finished", "study_progress", "assembly_study_progress") \
                    and d.get("status") in ("failed", "error"):
                self.record("study", d.get("message") or d.get("error") or d.get("summary") or f"{t}: failed",
                            code=d.get("error") if isinstance(d.get("error"), str) else d.get("kind"),
                            run_id=d.get("run_id") or event.run_id, build_id=d.get("build_id"),
                            tool=d.get("kind"))
        except Exception:  # noqa: BLE001
            pass


class ErrorLogHandler(logging.Handler):
    """ERROR-and-above records of the `mises` logger (every `log.exception` in the core's error
    paths) go to the current error log, with their stack trace."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.target: ErrorLog | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if self.target is None:
            return
        try:
            stack = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None
            self.target.record("core", record.getMessage(), code=record.name, stack=stack)
        except Exception:  # noqa: BLE001
            pass


_HANDLER = ErrorLogHandler()


def install_log_handler(log: ErrorLog) -> None:
    """Point the one `mises` logging handler at `log` (idempotent; the newest server wins)."""
    _HANDLER.target = log
    lg = logging.getLogger("mises")
    if _HANDLER not in lg.handlers:
        lg.addHandler(_HANDLER)
