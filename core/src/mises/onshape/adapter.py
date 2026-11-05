"""Onshape documents, features, variables, per-agent branches and merging.

Endpoints are the v17 REST API (live OpenAPI spec at https://cad.onshape.com/api/openapi):
  GET  /documents/d/{did}/{wv}/{wvid}/currentmicroversion           getCurrentMicroversion
  GET  /partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/features          getPartStudioFeatures
  POST /partstudios/d/{did}/w/{wid}/e/{eid}/features/updates        updateFeatures
  GET  /variables/d/{did}/{wv}/{wvid}/e/{eid}/variables             getVariables (w|v only)
  POST /variables/d/{did}/w/{wid}/e/{eid}/variables                 setVariables
  GET  /documents/d/{did}/workspaces                                getDocumentWorkspaces
  POST /documents/d/{did}/workspaces                                createWorkspace
  GET  /partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/massproperties    getPartStudioMassProperties
  GET  /partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/boundingboxes     getPartStudioBoundingBoxes
  GET  /documents/{did}/w/{wid}/mergePreview?sourceType=w&sourceId= mergePreview (wid = target)
  POST /documents/{did}/workspaces/{wid}/merge                      mergeIntoWorkspace (wid = target)

Features are read at the document microversion (`/m/{mv}/`), so the cached copy keyed by that
microversion is exactly what Onshape would return. Variables can only be read from a workspace or
version, so they are cached under the microversion read just before them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from ..naming import check_name
from ..store import Store
from ..types import ErrorCode, MisesError
from .client import OnshapeClient, OnshapeHTTPError

API = "/api/v17"
_ID = r"[0-9a-fA-F]{24}"
_URL_RE = re.compile(
    rf"^https://(?:[a-z0-9-]+\.)?onshape\.com/documents/({_ID})/([wvm])/({_ID})"
    rf"(?:/e/({_ID}))?/?(?:[?#].*)?$")
_VAR_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_LENGTH_RE = re.compile(r"(?<![a-z])(mm|cm|m|in|inch|ft|yd|um|µm|meter|millimeter)\b", re.I)
_ANGLE_RE = re.compile(r"(?<![a-z])(deg|degree|rad|radian)\b", re.I)
_NUMBER_RE = re.compile(r"^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$")


class DocRef(BaseModel, frozen=True):
    did: str
    wvm: Literal["w", "v", "m"]
    wvmid: str
    eid: str | None = None

    def url(self, base: str = "https://cad.onshape.com") -> str:
        u = f"{base}/documents/{self.did}/{self.wvm}/{self.wvmid}"
        return u + (f"/e/{self.eid}" if self.eid else "")


@dataclass(frozen=True)
class BranchInfo:
    ref: DocRef
    created: bool
    microversion: str | None


def parse_url(url: str) -> DocRef:
    m = _URL_RE.match(url.strip()) if isinstance(url, str) else None
    if not m:
        raise MisesError(ErrorCode.INVALID_INPUT, "not an Onshape document URL",
                         hint="expected https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>")
    did, wvm, wvmid, eid = m.groups()
    return DocRef(did=did.lower(), wvm=wvm, wvmid=wvmid.lower(), eid=eid.lower() if eid else None)


def variable_type(expression: str) -> str:
    """Onshape variable type (UPPERCASE per the spec's GBTVariableType) inferred from units."""
    if _NUMBER_RE.match(expression):
        return "NUMBER"
    if _ANGLE_RE.search(expression):
        return "ANGLE"
    if _LENGTH_RE.search(expression):
        return "LENGTH"
    return "ANY"


_VARS_HINT = ("`variables` sets a Variable Studio (pass its URL, in its own proposal); to change a"
              " Part Studio #variable, edit its Variable feature via feature_updates")


def _not_variable_studio(e: MisesError) -> MisesError:
    if e.code in (ErrorCode.INVALID_INPUT, ErrorCode.NOT_FOUND):
        return MisesError(e.code, f"cannot set variables on this element: {e.message}",
                          hint=_VARS_HINT)
    return e


def _need_eid(ref: DocRef) -> str:
    if not ref.eid:
        raise MisesError(ErrorCode.INVALID_INPUT, "URL has no element (/e/<eid>)",
                         hint="open the Part Studio tab and copy its URL")
    return ref.eid


def _need_workspace(ref: DocRef) -> None:
    if ref.wvm != "w":
        raise MisesError(ErrorCode.INVALID_INPUT, "only workspaces are writable",
                         hint="use a workspace URL (/w/<wid>/)")


def _stale(e: OnshapeHTTPError | None = None) -> MisesError:
    msg = "the branch changed since its features were read (microversion skew)"
    return MisesError(ErrorCode.STALE_VERSION, msg + (f": {e.message}" if e else ""),
                      hint="re-read features (onshape_features_list) and re-plan against the"
                           " current microversion")


class OnshapeAdapter:
    def __init__(self, client: OnshapeClient, store: Store):
        self.client = client
        self.store = store
        self._previews: dict[tuple[str, str, str], str] = {}

    async def _get(self, path: str, run_id: str | None, query: dict | None = None) -> dict:
        return await self.client.request("GET", API + path, run_id=run_id, query=query)

    async def _post(self, path: str, run_id: str | None, body, query: dict | None = None) -> dict:
        return await self.client.request("POST", API + path, run_id=run_id, json=body, query=query)

    # ---- reads
    async def microversion(self, ref: DocRef, run_id: str | None) -> str:
        if ref.wvm == "m":
            return ref.wvmid
        out = await self._get(f"/documents/d/{ref.did}/{ref.wvm}/{ref.wvmid}/currentmicroversion",
                              run_id)
        mv = out.get("microversion")
        if not isinstance(mv, str) or not mv:
            raise MisesError(ErrorCode.TOOL_FAILED, "Onshape returned no microversion")
        return mv

    async def features(self, ref: DocRef, run_id: str | None, *,
                       microversion: str | None = None) -> dict:
        """Pass `microversion` when it was just read, to save the currentmicroversion call."""
        eid = _need_eid(ref)
        mv = microversion or await self.microversion(ref, run_id)
        key = f"feat:{ref.did}:{mv}:{eid}"
        hit = self.store.cache_get(key)
        if hit is not None:
            return hit
        out = await self._get(f"/partstudios/d/{ref.did}/m/{mv}/e/{eid}/features", run_id)
        out.setdefault("sourceMicroversion", mv)
        self.store.cache_put(key, out)
        return out

    async def variables(self, ref: DocRef, run_id: str | None, *,
                        microversion: str | None = None) -> list[dict]:
        eid = _need_eid(ref)
        if ref.wvm == "m":
            raise MisesError(ErrorCode.INVALID_INPUT,
                             "variables can only be read from a workspace or version URL")
        mv = microversion or await self.microversion(ref, run_id)
        key = f"vars:{ref.did}:{mv}:{eid}"
        hit = self.store.cache_get(key)
        if hit is not None:
            return hit["items"]
        items = await self._read_variables(ref, run_id)
        self.store.cache_put(key, {"items": items})
        return items

    async def _read_variables(self, ref: DocRef, run_id: str | None) -> list[dict]:
        out = await self._get(f"/variables/d/{ref.did}/{ref.wvm}/{ref.wvmid}/e/{ref.eid}/variables",
                              run_id)
        rows: list[dict] = []
        for table in out.get("items", []):
            if isinstance(table, dict):
                rows.extend(v for v in table.get("variables", []) if isinstance(v, dict))
        return rows

    # ---- branches
    async def open_branch(self, ref: DocRef, agent_name: str, run_id: str | None, *,
                          base_microversion: str | None = None) -> BranchInfo:
        """Find the workspace `agent/<agent_name>` or branch it from `ref` at base_microversion."""
        name = f"agent/{agent_name}"
        listing = await self._get(f"/documents/d/{ref.did}/workspaces", run_id)
        for ws in listing.get("items", []):
            if isinstance(ws, dict) and ws.get("name") == name and ws.get("id"):
                return BranchInfo(DocRef(did=ref.did, wvm="w", wvmid=ws["id"], eid=ref.eid),
                                  created=False, microversion=ws.get("microversion"))
        base = base_microversion or await self.microversion(ref, run_id)
        body = {"name": name, "description": "AgentCAD agent branch", "microversionId": base,
                "fromHistory": True}
        if ref.wvm == "v":
            body["versionId"] = ref.wvmid
        elif ref.wvm == "w":
            body["workspaceId"] = ref.wvmid
        ws = await self._post(f"/documents/d/{ref.did}/workspaces", run_id, body)
        if not ws.get("id"):
            raise MisesError(ErrorCode.TOOL_FAILED, "Onshape created no workspace id")
        return BranchInfo(DocRef(did=ref.did, wvm="w", wvmid=ws["id"], eid=ref.eid),
                          created=True, microversion=ws.get("microversion"))

    async def ensure_branch(self, ref: DocRef, agent_name: str, run_id: str | None) -> DocRef:
        return (await self.open_branch(ref, agent_name, run_id)).ref

    # ---- writes
    @staticmethod
    def validate_proposal(variables: dict[str, str] | None, feature_updates: list[dict] | None) -> None:
        if not variables and not feature_updates:
            raise MisesError(ErrorCode.INVALID_INPUT, "nothing to propose",
                             hint="pass variables or feature_updates")
        if variables and feature_updates:
            # setVariables targets a Variable Studio, features/updates a Part Studio: one element
            # cannot be both, so a combined call could only half-apply.
            raise MisesError(ErrorCode.INVALID_INPUT,
                             "variables and feature_updates target different element types",
                             hint=_VARS_HINT)
        for name, expr in (variables or {}).items():
            check_name(name)
            if not _VAR_NAME_RE.match(name):
                raise MisesError(ErrorCode.INVALID_INPUT, f"variable name {name!r} is not valid",
                                 hint="letters, digits and underscores; start with a letter")
            if not isinstance(expr, str) or not expr.strip():
                raise MisesError(ErrorCode.INVALID_INPUT, f"variable {name!r} needs an expression")
        for f in feature_updates or []:
            if not isinstance(f, dict) or not isinstance(f.get("featureId"), str):
                raise MisesError(ErrorCode.INVALID_INPUT, "each feature update needs a featureId",
                                 hint="take featureId values from onshape_features_list")
            if "name" in f:
                check_name(f["name"])

    async def propose(self, branch: DocRef, run_id: str | None, *, source_microversion: str,
                      variables: dict[str, str] | None,
                      feature_updates: list[dict] | None) -> dict:
        _need_workspace(branch)
        eid = _need_eid(branch)
        self.validate_proposal(variables, feature_updates)
        base = f"/d/{branch.did}/w/{branch.wvmid}/e/{eid}"
        latest = None
        if feature_updates:
            body = {"btType": "BTUpdateFeaturesCall-1748",
                    "features": [{"btType": "BTMFeature-134", **f} for f in feature_updates],
                    "sourceMicroversion": source_microversion, "rejectMicroversionSkew": True,
                    "updateSuppressionAttributes": False}
            try:
                out = await self._post(f"/partstudios{base}/features/updates", run_id, body)
            except OnshapeHTTPError as e:
                # The request asked Onshape to reject skew, so a conflict here is a stale read.
                if e.status in (409, 412):
                    raise _stale(e) from None
                raise
            if out.get("microversionSkew"):
                raise _stale()
            latest = out.get("sourceMicroversion")
        elif await self.microversion(branch, run_id) != source_microversion:
            raise _stale()
        if variables:
            # setVariables takes the whole table, so merge into what is there.
            try:
                current = {v["name"]: v for v in await self._read_variables(branch, run_id)
                           if isinstance(v.get("name"), str)}
                for name, expr in variables.items():
                    old = current.get(name, {})
                    current[name] = {"name": name, "expression": expr,
                                     "type": old.get("type") or variable_type(expr),
                                     "description": old.get("description", "")}
                table = [{k: v.get(k) for k in ("name", "type", "expression", "description")
                          if v.get(k) is not None} for v in current.values()]
                await self._post(f"/variables{base}/variables", run_id, table)
            except MisesError as e:
                raise _not_variable_studio(e) from None
            latest = None
        changes: dict = {"mass": None, "volume": None, "bbox": None}
        warnings: list[str] = []
        try:
            mp = await self._get(f"/partstudios{base}/massproperties", run_id)
            latest = mp.get("microversionId") or latest
            bodies = [b for b in (mp.get("bodies") or {}).values() if isinstance(b, dict)]
            changes["mass"] = sum((b.get("mass") or [0])[0] for b in bodies) if bodies else None
            changes["volume"] = sum((b.get("volume") or [0])[0] for b in bodies) if bodies else None
        except MisesError as e:
            warnings.append(f"mass properties unavailable: {e.message}")
        try:
            bb = await self._get(f"/partstudios{base}/boundingboxes", run_id)
            changes["bbox"] = {k: bb.get(k) for k in
                               ("lowX", "lowY", "lowZ", "highX", "highY", "highZ")}
        except MisesError as e:
            warnings.append(f"bounding box unavailable: {e.message}")
        if latest is None:
            latest = await self.microversion(branch, run_id)
        return {"microversion": latest, "changes": changes, "warnings": warnings}

    # ---- merging
    async def merge_preview(self, branch: DocRef, target: DocRef, run_id: str | None) -> dict:
        _need_workspace(branch)
        _need_workspace(target)
        out = await self._get(f"/documents/{target.did}/w/{target.wvmid}/mergePreview", run_id,
                              query={"sourceType": "w", "sourceId": branch.wvmid})
        src = out.get("sourceMicroversionId")
        if isinstance(src, str) and src:
            self._previews[(branch.did, branch.wvmid, target.wvmid)] = src
        return out

    async def merge(self, branch: DocRef, target: DocRef, run_id: str | None) -> dict:
        """Merge branch into target. Requires a preview from this adapter, and refuses if the
        branch moved since that preview (so the human approved what is merged)."""
        _need_workspace(branch)
        _need_workspace(target)
        seen = self._previews.pop((branch.did, branch.wvmid, target.wvmid), None)
        if seen is None:
            raise MisesError(ErrorCode.NEEDS_APPROVAL, "merge without a merge preview",
                             hint="request the merge with onshape_merge_request")
        if await self.microversion(branch, run_id) != seen:
            raise MisesError(ErrorCode.STALE_VERSION, "the branch changed after the merge preview",
                             hint="request the merge again so the preview is current")
        return await self._post(f"/documents/{target.did}/workspaces/{target.wvmid}/merge", run_id,
                                {"type": "workspace", "id": branch.wvmid,
                                 "defaultMergeStrategy": "MERGE"})
