"""HMAC-signed Onshape REST client. Every HTTP request is reserved in the call ledger first.

Signing follows https://onshape-public.github.io/docs/auth/apikeys/ :
  Date (RFC 1123, within 5 minutes), On-Nonce (>=16 alphanumeric chars, unique),
  Authorization: On <AccessKey>:HmacSHA256:<base64(HMAC-SHA256(secret, S))>
  S = lower(method \\n nonce \\n date \\n content-type \\n path \\n query \\n)
where path and query are taken from the URL exactly as sent (query may be empty).
Redirects are followed by hand (up to 3) and re-signed for the new URL, as the docs require for
307. Each hop is its own HTTP request and so its own ledger entry: Onshape counts 2xx and 3xx
responses (https://onshape-public.github.io/docs/auth/limits/).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json as jsonlib
import secrets
import string
import uuid
from datetime import datetime
from email.utils import formatdate
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..store import Store
from ..types import Caps, ErrorCode, MisesError, StepKind, sha256_hex

CONTENT_TYPE = "application/json"
ACCEPT = "application/json;charset=UTF-8; qs=0.09"
MAX_REDIRECTS = 3
_ALNUM = string.ascii_letters + string.digits
_REDIRECTS = {301, 302, 303, 307, 308}


class OnshapeHTTPError(MisesError):
    """A typed error that also carries the HTTP status Onshape returned."""

    def __init__(self, code: ErrorCode, message: str, *, status: int, hint: str | None = None):
        super().__init__(code, message, hint=hint)
        self.status = status


def _year_start() -> float:
    return datetime(datetime.now().year, 1, 1).timestamp()


def _nonce() -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(25))


def sign(method: str, url: httpx.URL, *, access_key: str, secret_key: str, date: str,
         nonce: str, content_type: str = CONTENT_TYPE) -> str:
    path, _, query = url.raw_path.decode("ascii").partition("?")
    msg = f"{method}\n{nonce}\n{date}\n{content_type}\n{path}\n{query}\n".lower()
    digest = hmac.new(secret_key.encode(), msg.encode(), hashlib.sha256).digest()
    return f"On {access_key}:HmacSHA256:{base64.b64encode(digest).decode()}"


def _onshape_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        msg = body.get("message") if isinstance(body, dict) else None
    except ValueError:
        msg = None
    return str(msg if msg else resp.text)[:200]


def _is_skew(text: str) -> bool:
    t = text.lower()
    return "skew" in t or "microversion" in t


def error_for(status: int, onshape_msg: str) -> OnshapeHTTPError:
    said = f" (Onshape said, untrusted: {onshape_msg!r})" if onshape_msg else ""

    def e(code: ErrorCode, what: str, hint: str) -> OnshapeHTTPError:
        return OnshapeHTTPError(code, f"Onshape HTTP {status}: {what}{said}", status=status,
                                hint=hint)

    if status in (409, 412) and _is_skew(onshape_msg):
        return e(ErrorCode.STALE_VERSION, "the document changed since it was read",
                 "re-read features at the current microversion and re-plan")
    if status == 429:
        return e(ErrorCode.TRANSIENT, "rate limited", "wait and retry (see Retry-After)")
    if status == 402:
        return e(ErrorCode.BUDGET_EXHAUSTED, "Onshape API allocation used up",
                 "the annual Onshape API allowance is exhausted; stop calling Onshape")
    if status in (401, 403):
        return e(ErrorCode.DENIED, "not authorized",
                 "check the Onshape API keys and their scopes: `mises keys set onshape`")
    if status == 499 or status >= 500:
        return e(ErrorCode.TRANSIENT, "Onshape server error or timeout", "retry later")
    return e(ErrorCode.INVALID_INPUT, "request rejected",
             "check document/element ids and the request body")


class OnshapeClient:
    def __init__(self, access_key: str, secret_key: str, *, store: Store, user: str,
                 base_url: str = "https://cad.onshape.com",
                 transport: httpx.AsyncBaseTransport | None = None):
        self._ak, self._sk = access_key, secret_key
        self.store, self.user = store, user
        self.base_url = base_url.rstrip("/")
        self._host = urlsplit(self.base_url).hostname or ""
        self._http = httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=60)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- budget backstop (the gate checks tool estimates; this checks every real request)
    def _caps(self, run_id: str | None) -> Caps:
        if run_id is None:
            return Caps()
        try:
            return self.store.current_policy(run_id).caps
        except KeyError:
            return Caps()

    def _check_budget(self, run_id: str | None, estimate: int) -> None:
        caps = self._caps(run_id)
        used_year = self.store.ledger_used(self.user, since=_year_start())
        if used_year + estimate > caps.onshape_calls_year:
            raise MisesError(ErrorCode.BUDGET_EXHAUSTED,
                             f"Onshape allowance reached: {used_year}/{caps.onshape_calls_year}"
                             " calls this year", hint="ask the user to raise the cap")
        if run_id is not None:
            used_run = self.store.ledger_used(self.user, since=0, run_id=run_id)
            if used_run + estimate > caps.onshape_calls_run:
                raise MisesError(ErrorCode.BUDGET_EXHAUSTED,
                                 f"run Onshape cap reached: {used_run}/{caps.onshape_calls_run}",
                                 hint="ask the user to raise the run cap")

    def _host_allowed(self, url: httpx.URL) -> bool:
        host = url.host
        return url.scheme == "https" and (host == self._host or host == "onshape.com"
                                          or host.endswith(".onshape.com"))

    # ---- one ledgered HTTP request
    async def _send(self, method: str, url: httpx.URL, body: bytes | None, *,
                    run_id: str | None, estimate: int) -> httpx.Response:
        self._check_budget(run_id, estimate)
        key = sha256_hex("\n".join([method, url.path, url.query.decode("ascii"),
                                    (body or b"").decode("utf-8"), run_id or "",
                                    uuid.uuid4().hex]))
        entry = self.store.ledger_reserve(self.user, run_id, f"{method} {url.path}", estimate, key)
        try:
            date, nonce = formatdate(usegmt=True), _nonce()
            headers = {"Date": date, "On-Nonce": nonce, "Content-Type": CONTENT_TYPE,
                       "Accept": ACCEPT,
                       "Authorization": sign(method, url, access_key=self._ak,
                                             secret_key=self._sk, date=date, nonce=nonce)}
            req = self._http.build_request(method, url, content=body, headers=headers)
            resp = await self._http.send(req)
            await resp.aread()
        except BaseException as e:
            self.store.ledger_release(entry.id)
            if isinstance(e, httpx.HTTPError):
                raise MisesError(ErrorCode.TRANSIENT, f"Onshape unreachable: {type(e).__name__}",
                                 hint="check the network and retry") from None
            raise
        settled = self.store.ledger_settle(entry.id, resp.status_code)
        if run_id is not None:
            if settled.counted:
                self.store.add_usage(run_id, onshape_calls=1)
            self.store.append_step(run_id, StepKind.ONSHAPE_CALL,
                                   {"method": method, "path": url.path,
                                    "status": resp.status_code, "counted": settled.counted,
                                    "ledger_id": entry.id})
        return resp

    async def request(self, method: str, path: str, *, run_id: str | None,
                      query: dict | None = None, json: dict | list | None = None,
                      estimate: int = 1) -> dict:
        """Send a signed request; returns the JSON body (a JSON array comes back as {"items": [...]}).

        Raises MisesError with typed codes; see `error_for`.
        """
        method = method.upper()
        url = httpx.URL(self.base_url + path, params=query or None)
        body = jsonlib.dumps(json, separators=(",", ":")).encode() if json is not None else None
        hops = 0
        while True:
            resp = await self._send(method, url, body, run_id=run_id, estimate=estimate)
            if resp.status_code not in _REDIRECTS:
                break
            loc = resp.headers.get("Location")
            if not loc:
                raise MisesError(ErrorCode.TOOL_FAILED, "Onshape redirect without Location")
            if hops >= MAX_REDIRECTS:
                raise MisesError(ErrorCode.TOOL_FAILED,
                                 f"too many Onshape redirects (> {MAX_REDIRECTS})")
            nxt = resp.url.join(loc)
            if not self._host_allowed(nxt):
                raise MisesError(ErrorCode.DENIED,
                                 f"refusing to send credentials to redirect host {nxt.host!r}",
                                 hint="only https *.onshape.com redirects are followed")
            if resp.status_code == 303 and method not in ("GET", "HEAD"):
                method, body = "GET", None
            url, hops, estimate = nxt, hops + 1, 1
        if not resp.is_success:
            raise error_for(resp.status_code, _onshape_message(resp))
        if not resp.content:
            return {}
        try:
            data: Any = resp.json()
        except ValueError:
            raise MisesError(ErrorCode.TOOL_FAILED, "Onshape returned non-JSON") from None
        return data if isinstance(data, dict) else {"items": data}
