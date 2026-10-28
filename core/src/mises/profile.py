"""The user's profile (no secrets) and the first-run setup state."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import (BaseModel, ConfigDict, Field, StrictInt, ValidationError,
                      field_validator)

log = logging.getLogger(__name__)

PROFILE_FILE = "profile.json"
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}

KeyStatusFn = Callable[[], dict[str, str]]          # vendor -> "env" | "keychain" | "none" | ...
ClaudeStatusFn = Callable[[], Awaitable[bool]]      # signed in to the user's Claude Code?
LocalProbeFn = Callable[[list[str]], Awaitable[bool]]

# default local endpoints probed even when the profile lists none
DEFAULT_LOCAL_ENDPOINTS = ["http://localhost:11434/v1", "http://localhost:1234/v1"]


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = ""
    units: Literal["mm"] = "mm"
    default_material: str = "PLA"
    default_agent: str = "designer"
    default_model: str | None = None
    onshape_calls_year_cap: int = Field(2500, ge=0)
    onshape_calls_run_cap: int = Field(200, ge=0)
    # "auto": from this Mac's specs and the model (hardware.auto_parallel); an int is a manual limit
    max_parallel_agents: Literal["auto"] | Annotated[StrictInt, Field(ge=1, le=16)] = "auto"
    local_endpoints: dict[str, str] = {}
    setup_completed_at: float | None = None

    @field_validator("local_endpoints")
    @classmethod
    def _loopback_only(cls, v: dict[str, str]) -> dict[str, str]:
        for name, url in v.items():
            u = urlsplit(url)
            if u.scheme not in ("http", "https") or (u.hostname or "") not in _LOOPBACK:
                raise ValueError(f"local endpoint {name!r} must be an http(s) URL on this Mac "
                                 "(localhost or 127.0.0.1)")
        return v


def load_profile(home: Path) -> Profile:
    try:
        return Profile.model_validate_json((Path(home) / PROFILE_FILE).read_text())
    except FileNotFoundError:
        return Profile()
    except (OSError, ValueError, ValidationError):
        log.warning("profile.json unreadable or invalid; using defaults")
        return Profile()


def save_profile(home: Path, p: Profile) -> None:
    """Atomic write: 0600 temp file in the same directory, then os.replace."""
    path = Path(home) / PROFILE_FILE
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".profile.json.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(p.model_dump(mode="json"), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SetupState(BaseModel):
    complete: bool
    has_model: bool
    has_onshape: bool
    missing: list[Literal["model", "onshape", "profile"]]


async def probe_local(urls: list[str]) -> bool:
    """True if any OpenAI-compatible local endpoint answers GET <url>/models."""
    async def one(c: httpx.AsyncClient, url: str) -> bool:
        try:
            return (await c.get(url.rstrip("/") + "/models")).status_code < 500
        except (httpx.HTTPError, ValueError):
            return False
    async with httpx.AsyncClient(timeout=0.7, follow_redirects=False) as c:
        return any(await asyncio.gather(*(one(c, u) for u in urls)))


async def setup_state(home: Path, keys: KeyStatusFn, claude: ClaudeStatusFn | None = None,
                      local_probe: LocalProbeFn = probe_local) -> SetupState:
    """has_model: any API key, a signed-in Claude Code, or a local endpoint that answers.
    complete: the user finished (or skipped) the setup wizard; a missing model is reported in
    has_model/missing (the app shows a "connect a model" notice), so local-only users are not
    sent through the wizard on every launch. Onshape is optional."""
    profile = load_profile(home)
    status = keys()
    key_model = any(src != "none" for v, src in status.items()
                    if v not in ("onshape", "ollama", "lmstudio"))
    has_onshape = status.get("onshape", "none") != "none"
    has_model = key_model
    if not has_model and claude is not None:
        try:
            has_model = bool(await claude())
        except Exception:  # noqa: BLE001 - an unknown Claude Code state is "not signed in"
            log.warning("claude status check failed")
    if not has_model:
        urls = list(dict.fromkeys([*profile.local_endpoints.values(), *DEFAULT_LOCAL_ENDPOINTS]))
        try:
            has_model = bool(await local_probe(urls))
        except Exception:  # noqa: BLE001
            log.warning("local endpoint probe failed")
    done = profile.setup_completed_at is not None
    missing: list[Literal["model", "onshape", "profile"]] = []
    if not has_model:
        missing.append("model")
    if not has_onshape:
        missing.append("onshape")
    if not done:
        missing.append("profile")
    return SetupState(complete=done, has_model=has_model, has_onshape=has_onshape,
                      missing=missing)
