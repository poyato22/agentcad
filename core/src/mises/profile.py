"""The user's profile (no secrets) and the first-run setup state."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import copy

import httpx
from pydantic import (BaseModel, ConfigDict, Field, StrictInt, ValidationError,
                      field_validator, model_validator)

from .browser.sites import DEFAULT_SITES, on_domain

log = logging.getLogger(__name__)

DEFAULT_BROWSER_FETCHES = 60

PROFILE_FILE = "profile.json"
# 2: max_parallel_agents gained "auto" (the new default). Files without schema_version predate
#    it; their max_parallel_agents of 2 was the old default, not a choice, and becomes "auto".
# 3: the default agent became "engineer" (was "designer"). save_profile writes the whole model,
#    so every pre-v3 profile has "designer" on disk because that was the old default, not a
#    choice; it becomes "engineer". A user who picks the designer now (saved at v3+) keeps it.
PROFILE_SCHEMA_VERSION = 3
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_DOMAIN_RE = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}")

KeyStatusFn = Callable[[], dict[str, str]]          # vendor -> "env" | "keychain" | "none" | ...
ClaudeStatusFn = Callable[[], Awaitable[bool]]      # signed in to the user's Claude Code?
LocalProbeFn = Callable[[list[str]], Awaitable[bool]]

# default local endpoints probed even when the profile lists none
DEFAULT_LOCAL_ENDPOINTS = ["http://localhost:11434/v1", "http://localhost:1234/v1"]


PRINTER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:\d{1,5})?$")


class PrinterEntry(BaseModel):
    """A printer on the network. Its API key lives in the Keychain (service `mises`, account
    `printer:<id>`), never in the profile."""
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["prusalink"] = "prusalink"
    host: str
    name: str = Field(min_length=1, max_length=60)
    model: Literal["MK4", "MINI"] = "MK4"

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not PRINTER_ID_RE.match(v):
            raise ValueError(f"printer id must match {PRINTER_ID_RE.pattern}")
        return v

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        if not _HOST_RE.match(v) or (":" in v and int(v.rsplit(":", 1)[1]) > 65535):
            raise ValueError("printer host must be a hostname or IP address, optionally :port "
                             "(no scheme or path)")
        return v


class SuggestedSite(BaseModel):
    """A site agents read without an approval card (see mises.browser.sites). `search_url` is
    an https template on the site's own domain with one `{q}` for the query."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    domain: str = Field(max_length=253)
    search_url: str | None = Field(None, max_length=500)
    categories: list[Annotated[str, Field(max_length=30)]] = Field(default_factory=list,
                                                                   max_length=12)
    suggested: bool = False      # one of Mises's defaults (the UI marks it "suggested")

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        d = v.strip().lower().rstrip(".")
        if not _DOMAIN_RE.fullmatch(d):
            raise ValueError(f"{v!r} is not a domain name")
        return d

    @field_validator("search_url")
    @classmethod
    def _search(cls, v: str | None, info) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip()
        u = urlsplit(v.replace("{q}", "q"))
        if u.scheme != "https" or v.count("{q}") != 1 or u.port not in (None, 443) \
                or u.username or u.password:
            raise ValueError("search_url must be an https URL with exactly one {q}")
        dom = info.data.get("domain")
        if dom and not on_domain(u.hostname or "", dom):
            raise ValueError(f"search_url must be on {dom}")
        return v


def default_sites() -> list[SuggestedSite]:
    return [SuggestedSite.model_validate(copy.deepcopy(s)) for s in DEFAULT_SITES]
_BG_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")


class ViewerBackground(BaseModel):
    """The viewer's background (Plan 7 U8, R21): the theme's drafting sheet, one solid color, or
    a soft two-stop gradient (top, bottom), Onshape-style."""
    model_config = ConfigDict(extra="forbid")

    mode: Literal["theme", "solid", "gradient"] = "theme"
    colors: list[str] = Field(default_factory=list, max_length=2)

    @field_validator("colors")
    @classmethod
    def _hex(cls, v: list[str]) -> list[str]:
        out = [c.strip().lower() for c in v]
        for c in out:
            if not _BG_HEX_RE.match(c):
                raise ValueError(f"{c!r} is not a #rrggbb color")
        return out

    @model_validator(mode="after")
    def _count(self) -> "ViewerBackground":
        need = {"theme": 0, "solid": 1, "gradient": 2}[self.mode]
        if self.mode != "theme" and len(self.colors) != need:
            raise ValueError(f"a {self.mode} background needs {need} color(s)")
        return self


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(PROFILE_SCHEMA_VERSION, ge=1)

    display_name: str = ""
    units: Literal["mm"] = "mm"
    default_material: str = "PLA"
    default_agent: str = "engineer"
    default_model: str | None = None
    onshape_calls_year_cap: int = Field(2500, ge=0)
    onshape_calls_run_cap: int = Field(200, ge=0)
    # "auto": from this Mac's specs and the model (hardware.auto_parallel); an int is a manual limit
    max_parallel_agents: Literal["auto"] | Annotated[StrictInt, Field(ge=1, le=16)] = "auto"
    local_endpoints: dict[str, str] = {}
    theme: Literal["system", "light", "dark"] = "system"
    viewer_background: ViewerBackground = Field(default_factory=ViewerBackground)
    setup_completed_at: float | None = None
    printers: list[PrinterEntry] = []
    # builds (Plan 5 C1): the active build and the open build tabs, plus hidden built-in agents
    active_project_id: str | None = None
    open_project_ids: list[str] = []
    hidden_agents: list[str] = []
    # parts-directory imports an agent may make without an approval card: only ever added by
    # the user ("Always allow <domain>" on the card, POST /catalog/allowed_domains)
    catalog_allowed_domains: list[str] = Field(default_factory=list, max_length=200)
    # the agent browser's suggested sites (U4c): read without a card; user-editable
    suggested_sites: list[SuggestedSite] = Field(default_factory=default_sites, max_length=100)
    # web fetches (sites searched + pages opened) one agent run may make
    browser_fetches_per_run: int = Field(DEFAULT_BROWSER_FETCHES, ge=1, le=1000)
    # Plan 8 G1: the user's ceilings for the render-critique loop. A critique_model call can
    # lower them for itself, never raise them.
    critique_max_rounds: int = Field(3, ge=1, le=6)
    critique_time_budget_s: float = Field(300.0, ge=30, le=3600)
    critique_max_image_tokens: int = Field(12_000, ge=1000, le=100_000)

    @field_validator("catalog_allowed_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for d in v:
            d = d.strip().lower().rstrip(".")
            if not _DOMAIN_RE.fullmatch(d):
                raise ValueError(f"{d!r} is not a domain name")
            if d not in out:
                out.append(d)
        return out

    @field_validator("suggested_sites")
    @classmethod
    def _unique_sites(cls, v: list[SuggestedSite]) -> list[SuggestedSite]:
        doms = [s.domain for s in v]
        if len(doms) != len(set(doms)):
            raise ValueError("suggested site domains must be unique")
        return v

    @field_validator("printers")
    @classmethod
    def _unique_printers(cls, v: list[PrinterEntry]) -> list[PrinterEntry]:
        ids = [p.id for p in v]
        if len(ids) != len(set(ids)):
            raise ValueError("printer ids must be unique")
        return v

    @field_validator("local_endpoints")
    @classmethod
    def _loopback_only(cls, v: dict[str, str]) -> dict[str, str]:
        for name, url in v.items():
            u = urlsplit(url)
            if u.scheme not in ("http", "https") or (u.hostname or "") not in _LOOPBACK:
                raise ValueError(f"local endpoint {name!r} must be an http(s) URL on this Mac "
                                 "(localhost or 127.0.0.1)")
        return v


def _migrate(data):
    if not isinstance(data, dict):
        return data
    data = dict(data)
    ver = data.get("schema_version")
    if ver is None:
        # predates v2: max_parallel_agents of 2 was the old default, not a choice
        if data.get("max_parallel_agents") == 2:
            data["max_parallel_agents"] = "auto"
        ver = 1
    if ver < 3 and data.get("default_agent") == "designer":
        # the whole model was saved, so "designer" here is the old default, not a choice
        data["default_agent"] = "engineer"
    if ver < PROFILE_SCHEMA_VERSION:
        data["schema_version"] = PROFILE_SCHEMA_VERSION
    return data


def load_profile(home: Path) -> Profile:
    """The saved profile, migrated to PROFILE_SCHEMA_VERSION (the next save writes it)."""
    try:
        raw = json.loads((Path(home) / PROFILE_FILE).read_text())
        return Profile.model_validate(_migrate(raw))
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
            json.dump({**p.model_dump(mode="json"), "schema_version": PROFILE_SCHEMA_VERSION},
                      f, indent=2)
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
