"""A fake Onshape REST server for tests, mounted via httpx.MockTransport.

It checks what the real server checks for API-key auth (per
https://onshape-public.github.io/docs/auth/apikeys/): a `Date` within 5 minutes, a unique
alphanumeric `On-Nonce` of at least 16 chars, and `Authorization: On <ak>:HmacSHA256:<sig>`,
where sig = base64(HMAC-SHA256(secret, lower(method\\nnonce\\ndate\\ncontent-type\\npath\\nquery\\n))).
Bad signatures get 401. Paths follow the v17 OpenAPI spec (https://cad.onshape.com/api/openapi).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import itertools
import json
import re
import time
from email.utils import parsedate_to_datetime

import httpx

API = "/api/v17"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
_ids = itertools.count(1)


def new_id() -> str:
    return f"{next(_ids):024x}"


class FakeOnshape:
    def __init__(self, access_key: str = "AK", secret_key: str = "SK",
                 host: str = "cad.onshape.com"):
        self.ak, self.sk, self.host = access_key, secret_key, host
        self.did, self.main, self.eid = new_id(), new_id(), new_id()
        self.workspaces: dict[str, dict] = {}
        self.snapshots: dict[str, dict] = {}   # microversion -> {"features", "variables"}
        self._add_ws(self.main, "Main", features=[
            {"btType": "BTMFeature-134", "featureId": "FaaA", "featureType": "extrude",
             "name": "Base plate", "parameters": [
                 {"btType": "BTMParameterQuantity-147", "parameterId": "depth",
                  "expression": "10 mm"}]},
        ], variables=[{"name": "thickness", "type": "LENGTH", "expression": "3 mm",
                       "value": "3 mm", "description": ""}])
        self.nonces: set[str] = set()
        self.requests: list[tuple[str, str]] = []    # (method, path) of authenticated requests
        self.bodies: list[dict | list | None] = []
        self.fail: dict[str, list[int]] = {}         # path -> statuses to return first
        self.redirects: dict[str, str] = {}          # path -> second path (302)
        self.merges: list[dict] = []
        self.docs: list[dict] = []                   # GET /documents, newest first
        self.thumb = PNG                             # bytes every thumbnail returns
        self.bbox = {"lowX": 0, "lowY": 0, "lowZ": 0, "highX": 0.1, "highY": 0.05, "highZ": 0.01}
        self.transport = httpx.MockTransport(self.handler)

    # ---- state helpers
    def _add_ws(self, wid: str, name: str, *, features: list, variables: list) -> dict:
        ws = {"id": wid, "name": name, "features": features, "variables": variables,
              "microversion": ""}
        self.workspaces[wid] = ws
        self.bump(wid)
        return ws

    def bump(self, wid: str | None = None) -> str:
        ws = self.workspaces[wid or self.main]
        ws["microversion"] = new_id()
        self.snapshots[ws["microversion"]] = {"features": copy.deepcopy(ws["features"]),
                                              "variables": copy.deepcopy(ws["variables"])}
        return ws["microversion"]

    def mv(self, wid: str | None = None) -> str:
        return self.workspaces[wid or self.main]["microversion"]

    def url(self, wid: str | None = None) -> str:
        return f"https://{self.host}/documents/{self.did}/w/{wid or self.main}/e/{self.eid}"

    def count(self, pattern: str) -> int:
        return sum(1 for _, p in self.requests if re.search(pattern, p))

    # ---- auth
    def _check_auth(self, req: httpx.Request) -> str | None:
        date, nonce = req.headers.get("Date"), req.headers.get("On-Nonce")
        auth, ctype = req.headers.get("Authorization", ""), req.headers.get("Content-Type", "")
        if not date or not nonce:
            return "missing Date or On-Nonce"
        if abs(parsedate_to_datetime(date).timestamp() - time.time()) > 300:
            return "Date not within 5 minutes"
        if len(nonce) < 16 or not nonce.isalnum() or nonce in self.nonces:
            return "bad or reused nonce"
        m = re.fullmatch(r"On ([^:]+):HmacSHA256:(.+)", auth)
        if not m or m.group(1) != self.ak:
            return "bad Authorization header"
        raw = req.url.raw_path.decode()
        path, _, query = raw.partition("?")
        msg = f"{req.method}\n{nonce}\n{date}\n{ctype}\n{path}\n{query}\n".lower()
        want = base64.b64encode(hmac.new(self.sk.encode(), msg.encode(),
                                         hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(want, m.group(2)):
            return "signature mismatch"
        self.nonces.add(nonce)
        return None

    # ---- dispatch
    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.host != self.host:
            return httpx.Response(404)
        why = self._check_auth(req)
        if why:
            return httpx.Response(401, json={"message": why})
        path = req.url.path
        self.requests.append((req.method, path))
        self.last_query = req.url.query.decode()
        body = json.loads(req.content) if req.content else None
        self.bodies.append(body)
        if self.fail.get(path):
            return httpx.Response(self.fail[path].pop(0), json={"message": "injected"})
        if path in self.redirects:
            to = self.redirects[path]
            loc = to if to.startswith("http") else f"https://{self.host}{to}?via=redirect"
            return httpx.Response(302, headers={"Location": loc})
        for pat, fn in self._routes():
            # thumbnail hrefs Onshape hands out are versionless (/api/thumbnails/...)
            m = re.fullmatch(API + pat, path) or (
                re.fullmatch("/api" + pat, path) if pat.startswith("/thumbnails") else None)
            if m and fn.__name__.startswith(req.method.lower() + "_"):
                return fn(req, body, *m.groups())
        return httpx.Response(404, json={"message": f"no route {req.method} {path}"})

    def _routes(self):
        return [
            (r"/documents/d/(\w+)/w/(\w+)/currentmicroversion", self.get_mv),
            (r"/partstudios/d/(\w+)/([wvm])/(\w+)/e/(\w+)/features", self.get_features),
            (r"/partstudios/d/(\w+)/w/(\w+)/e/(\w+)/features/updates", self.post_updates),
            (r"/variables/d/(\w+)/w/(\w+)/e/(\w+)/variables", self.get_variables),
            (r"/variables/d/(\w+)/w/(\w+)/e/(\w+)/variables", self.post_variables),
            (r"/documents/d/(\w+)/workspaces", self.get_workspaces),
            (r"/documents/d/(\w+)/workspaces", self.post_workspaces),
            (r"/partstudios/d/(\w+)/w/(\w+)/e/(\w+)/massproperties", self.get_mass),
            (r"/partstudios/d/(\w+)/w/(\w+)/e/(\w+)/boundingboxes", self.get_bbox),
            (r"/documents/(\w+)/w/(\w+)/mergePreview", self.get_merge_preview),
            (r"/documents/(\w+)/workspaces/(\w+)/merge", self.post_merge),
            (r"/redirected/(\w+)", self.get_redirected),
            (r"/users/sessioninfo", self.get_sessioninfo),
            (r"/documents", self.get_documents),
            (r"/documents/d/(\w+)/w/(\w+)/elements", self.get_elements),
            (r"/thumbnails/d/(\w+)/w/(\w+)/s/(\w+)", self.get_thumb),
            (r"/assemblies/d/(\w+)/w/(\w+)/e/(\w+)/boundingboxes", self.get_bbox),
        ]

    # ---- documents browser
    def add_docs(self, n: int, name: str = "Bracket") -> list[dict]:
        for i in range(n):
            did, wid = new_id(), new_id()
            self.docs.append({
                "jsonType": "document-summary", "id": did, "name": f"{name} {i}",
                "modifiedAt": f"2026-10-{1 + i % 28:02d}T10:00:00Z",
                "owner": {"id": "u1", "name": "Ada Lovelace", "type": 0},
                "defaultWorkspace": {"id": wid, "name": "Main"},
                "thumbnail": {"sizes": [
                    {"size": "70x40", "href": f"https://{self.host}/api/thumbnails/d/{did}/w/{wid}/s/70x40"},
                    {"size": "300x170", "href": f"https://{self.host}/api/thumbnails/d/{did}/w/{wid}/s/300x170"}]}})
        return self.docs

    def _ws(self, wid: str) -> dict | None:
        return self.workspaces.get(wid)

    # ---- handlers
    def get_mv(self, req, body, did, wid):
        ws = self._ws(wid)
        return httpx.Response(200, json={"microversion": ws["microversion"]}) if ws else httpx.Response(404)

    def get_features(self, req, body, did, wvm, wvmid, eid):
        if wvm == "m":
            snap = self.snapshots.get(wvmid)
            feats = snap["features"] if snap else None
            mv = wvmid
        else:
            ws = self._ws(wvmid)
            feats, mv = (ws["features"], ws["microversion"]) if ws else (None, None)
        if feats is None:
            return httpx.Response(404)
        return httpx.Response(200, json={"btType": "BTFeatureListResponse-2457",
                                         "features": feats, "sourceMicroversion": mv,
                                         "microversionSkew": False, "rollbackIndex": -1})

    def post_updates(self, req, body, did, wid, eid):
        ws = self._ws(wid)
        if ws is None:
            return httpx.Response(404)
        if body.get("btType") != "BTUpdateFeaturesCall-1748":
            return httpx.Response(400, json={"message": "bad btType"})
        if body.get("rejectMicroversionSkew") and body.get("sourceMicroversion") != ws["microversion"]:
            return httpx.Response(409, json={"message": "Microversion skew: document has changed"})
        by_id = {f["featureId"]: f for f in ws["features"]}
        for upd in body["features"]:
            f = by_id.get(upd.get("featureId"))
            if f is None:
                return httpx.Response(400, json={"message": "unknown featureId"})
            if "name" in upd:
                f["name"] = upd["name"]
            params = {p["parameterId"]: p for p in f["parameters"]}
            for p in upd.get("parameters", []):
                params.setdefault(p["parameterId"], {}).update(p)
            f["parameters"] = list(params.values())
        mv = self.bump(wid)
        return httpx.Response(200, json={"btType": "BTUpdateFeaturesResponse-1333",
                                         "features": ws["features"], "sourceMicroversion": mv,
                                         "microversionSkew": False})

    def get_variables(self, req, body, did, wid, eid):
        ws = self._ws(wid)
        if ws is None:
            return httpx.Response(404)
        return httpx.Response(200, json=[{"variables": ws["variables"]}])

    def post_variables(self, req, body, did, wid, eid):
        ws = self._ws(wid)
        for v in body:
            if v["type"] != v["type"].upper() or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", v["name"]):
                return httpx.Response(500, json={"message": "bad variable"})
        ws["variables"] = [{**v, "value": v["expression"]} for v in body]
        self.bump(wid)
        return httpx.Response(200, json={})

    def get_workspaces(self, req, body, did):
        return httpx.Response(200, json=[{"id": w["id"], "name": w["name"],
                                          "microversion": w["microversion"]}
                                         for w in self.workspaces.values()])

    def post_workspaces(self, req, body, did):
        if not body or not body.get("name"):
            return httpx.Response(400, json={"message": "name required"})
        base = self.snapshots.get(body.get("microversionId") or "")
        if base is None:
            return httpx.Response(400, json={"message": "unknown base"})
        ws = self._add_ws(new_id(), body["name"], features=copy.deepcopy(base["features"]),
                          variables=copy.deepcopy(base["variables"]))
        return httpx.Response(200, json={"id": ws["id"], "name": ws["name"],
                                         "microversion": ws["microversion"]})

    def get_mass(self, req, body, did, wid, eid):
        ws = self._ws(wid)
        depth = len(json.dumps(ws["features"]))
        return httpx.Response(200, json={"microversionId": ws["microversion"], "bodies": {
            "-all-": {"mass": [0.1 * depth, 0, 0], "volume": [1e-6 * depth, 0, 0]}}})

    def get_bbox(self, req, body, did, wid, eid):
        return httpx.Response(200, json=self.bbox)

    def get_merge_preview(self, req, body, did, wid):
        src = self._ws(req.url.params.get("sourceId", ""))
        if req.url.params.get("sourceType") != "w" or src is None:
            return httpx.Response(400)
        return httpx.Response(200, json={
            "sourceMicroversionId": src["microversion"],
            "targetMicroversionId": self.mv(wid),
            "changes": [{"elementId": self.eid, "sourceElementName": "Part Studio 1",
                         "mergeable": True}]})

    def post_merge(self, req, body, did, wid):
        src = self._ws(body.get("id", ""))
        if src is None or body.get("type") != "workspace":
            return httpx.Response(400)
        self.merges.append({"target": wid, **body})
        tgt = self.workspaces[wid]
        tgt["features"] = copy.deepcopy(src["features"])
        tgt["variables"] = copy.deepcopy(src["variables"])
        mv = self.bump(wid)
        return httpx.Response(200, json={"resultDocumentMicroversionId": mv,
                                         "sourceDocumentMicroversionId": src["microversion"]})

    def get_documents(self, req, body):
        p = req.url.params
        offset, limit = int(p.get("offset", 0)), int(p.get("limit", 20))
        if limit > 20:
            return httpx.Response(400, json={"message": "limit too large"})
        q = p.get("q", "").lower()
        docs = [d for d in self.docs if q in d["name"].lower()]
        page = docs[offset:offset + limit]
        nxt = (f"https://{self.host}{API}/documents?offset={offset + limit}&limit={limit}"
               if offset + limit < len(docs) else None)
        return httpx.Response(200, json={"items": page, "next": nxt, "previous": None,
                                         "href": str(req.url)})

    def get_elements(self, req, body, did, wid):
        return httpx.Response(200, json=[
            {"id": self.eid, "name": "Part Studio 1", "elementType": "PARTSTUDIO",
             "microversionId": self.mv(), "type": "Part Studio"},
            {"id": "a" * 24, "name": "Assembly 1", "elementType": "ASSEMBLY",
             "microversionId": self.mv(), "type": "Assembly"},
            {"id": "b" * 24, "name": "Drawing 1", "elementType": "DRAWING", "type": "Drawing"},
            {"id": "c" * 24, "name": "notes.pdf", "elementType": "BLOB", "type": "Blob"}])

    def get_thumb(self, req, body, did, wid, sz):
        return httpx.Response(200, content=self.thumb, headers={"Content-Type": "image/png"})

    def get_sessioninfo(self, req, body):
        return httpx.Response(200, json={"name": "Ada Lovelace", "id": "u1"})

    def get_redirected(self, req, body, x):
        return httpx.Response(200, json={"redirected": x, "via": req.url.params.get("via")})
