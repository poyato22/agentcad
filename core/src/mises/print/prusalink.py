"""PrusaLink v1 client (the HTTP API on Prusa MK4 / MINI and other PrusaLink printers).

Used endpoints (Prusa-Link-Web spec/openapi.yaml):
  GET /api/v1/status                  -> {"printer": {"state", "temp_nozzle", "temp_bed", ...}, "job": {...}}
  PUT /api/v1/files/{storage}/{path}  -> 201; body application/octet-stream; headers
                                         `Print-After-Upload: ?1` and `Overwrite: ?1`
Auth is the `X-Api-Key` header (PrusaLink's API key). The key is never put in an error message.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from ..types import ErrorCode, MisesError

# A job may only be sent when the printer reports one of these; everything else (ERROR,
# ATTENTION, BUSY, PRINTING, PAUSED, an unknown state) is refused.
SENDABLE_STATES = frozenset({"IDLE", "READY", "FINISHED", "STOPPED"})
STORAGE = "usb"
UPLOAD_READ_TIMEOUT_S = 120.0


@dataclass
class PrinterStatus:
    state: str
    temp_nozzle: float | None = None
    temp_bed: float | None = None
    job: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.state in SENDABLE_STATES

    def to_dict(self) -> dict:
        return {"state": self.state, "sendable": self.sendable, "temp_nozzle": self.temp_nozzle,
                "temp_bed": self.temp_bed, "job": self.job}


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class PrusaLinkClient:
    def __init__(self, host: str, api_key: str, *, timeout: float = 10.0):
        self._base = f"http://{host}"
        self._key = api_key
        self._timeout = timeout

    async def _request(self, method: str, path: str, *, timeout: httpx.Timeout | float | None = None,
                       mutating: bool = False, **kw) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=timeout if timeout is not None else self._timeout,
                                         trust_env=False, follow_redirects=False) as c:
                r = await c.request(method, self._base + path,
                                    headers={"X-Api-Key": self._key, **kw.pop("headers", {})}, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise MisesError(ErrorCode.TRANSIENT, f"printer unreachable ({type(e).__name__})",
                             hint="check that the printer is on and on the same network") from None
        except httpx.HTTPError as e:
            if mutating:
                # the request may have been (partly) delivered: the job could be starting
                raise MisesError(ErrorCode.TOOL_FAILED,
                                 f"unknown outcome: the printer did not answer the upload "
                                 f"({type(e).__name__}); the job may or may not have started",
                                 hint="check the printer's screen before sending again") from None
            raise MisesError(ErrorCode.TRANSIENT, f"printer unreachable ({type(e).__name__})",
                             hint="check that the printer is on and on the same network") from None
        if r.status_code in (401, 403):
            raise MisesError(ErrorCode.DENIED, "the printer rejected the API key",
                             hint="update the printer's API key (PrusaLink settings)")
        return r

    async def status(self) -> PrinterStatus:
        r = await self._request("GET", "/api/v1/status")
        if r.status_code != 200:
            raise MisesError(ErrorCode.TOOL_FAILED, f"printer status failed (HTTP {r.status_code})")
        try:
            body = r.json()
            printer = body["printer"]
            state = str(printer["state"]).upper()
        except (ValueError, KeyError, TypeError):
            raise MisesError(ErrorCode.TOOL_FAILED, "unexpected status reply from the printer") from None
        job = body.get("job") if isinstance(body.get("job"), dict) else None
        if job is not None:
            job = {k: job[k] for k in ("id", "progress", "time_remaining", "time_printing")
                   if isinstance(job.get(k), (int, float))}
        return PrinterStatus(state=state, temp_nozzle=_num(printer.get("temp_nozzle")),
                             temp_bed=_num(printer.get("temp_bed")), job=job)

    async def upload(self, path: Path, name: str, *, print_after: bool) -> None:
        await self.upload_bytes(Path(path).read_bytes(), name, print_after=print_after)

    async def upload_bytes(self, data: bytes, name: str, *, print_after: bool) -> None:
        r = await self._request(
            "PUT", f"/api/v1/files/{STORAGE}/{quote(name, safe='')}", content=data,
            timeout=httpx.Timeout(self._timeout, read=UPLOAD_READ_TIMEOUT_S, write=UPLOAD_READ_TIMEOUT_S),
            mutating=True,
            headers={"Content-Type": "application/octet-stream",
                     "Print-After-Upload": "?1" if print_after else "?0", "Overwrite": "?1"})
        if r.status_code == 409:
            raise MisesError(ErrorCode.TOOL_FAILED,
                             "printer refused the upload (409 conflict: busy or file in use)",
                             hint="wait for the printer to be idle and retry")
        if r.status_code not in (200, 201):
            raise MisesError(ErrorCode.TOOL_FAILED, f"upload failed (HTTP {r.status_code})")
