"""Agent-built simulator plugins (plan 2026-11-06 S4 S5 S7 S8).

A plugin lives in `<MISES_HOME>/simulators/<name>/`: `solver.py` defining `solve(inputs: dict)
-> dict` (outputs by name, in the request's units), optional helper modules, and tests. The
manifest (inputs, outputs, units, fidelity, method, licence, validation cases) is written by
the core from the accepted request, never by the agent. The plugin's code is untrusted:

- S8 review: before its first run, an independent static review (`review`) refuses network,
  process, file-system, introspection and dynamic-code use; the review is defence in depth.
- S4 sandbox: the code runs only in the sandboxed worker (`run_sandboxed` + sandbox-exec): no
  network, no process fork, writes only in its job dir, no reads of the user's home folder
  except the Python install and its own folder, under CPU-time, memory and wall limits. It
  never runs in the core process.
- S5 validation gate: the plugin stays disabled until every validation case (known analytic or
  published answers) passes within its tolerance; its results carry `fidelity: agent_built`
  and the evidence ("agent-built simulator, validated against N cases").
- S7 registry: enabled plugins are listed by sim_types_list (`agent_built`), with version,
  source, licence and validation status; the user can disable or delete them.

Plugin state (review, validation, enabled) is kept in mises.db, which the plugin cannot reach.
"""
from __future__ import annotations

import ast
import json
import secrets
import shutil
import sys
import time
from pathlib import Path

from ..store import Store
from ..types import ErrorCode, MisesError
from .requests import SANDBOX_LIMITS, SimRequests

ALLOWED_IMPORTS = frozenset({"math", "cmath", "statistics", "fractions", "decimal", "json",
                             "dataclasses", "typing", "functools", "itertools", "collections",
                             "numpy", "scipy", "__future__", "enum"})
BANNED_NAMES = frozenset({"open", "exec", "eval", "compile", "__import__", "globals", "locals",
                          "vars", "getattr", "setattr", "delattr", "input", "breakpoint",
                          "memoryview", "help"})
MAX_FILES, MAX_BYTES = 20, 400_000

_RUNNER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import resource
resource.setrlimit(resource.RLIMIT_CPU, (int(sys.argv[4]), int(sys.argv[4])))
from solver import solve
cases = json.load(open(sys.argv[2]))
out = []
for c in cases:
    try:
        r = solve(dict(c["inputs"]))
        out.append({"name": c["name"], "outputs": {k: float(v) for k, v in dict(r).items()}})
    except Exception as e:
        out.append({"name": c["name"], "error": type(e).__name__ + ": " + str(e)[:300]})
json.dump(out, open(sys.argv[3], "w"))
"""


def simulators_root(home: Path) -> Path:
    return Path(home) / "simulators"


def plugin_dir(home: Path, name: str) -> Path:
    from .requests import NAME_RE
    if not NAME_RE.match(name):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad simulator name {name!r}")
    return simulators_root(home) / name


# ---- S4: the builder's only way to write

def write_file(home: Path, name: str, rel: str, content: str) -> Path:
    """Write one plugin file (the builder's only write path): a .py or .md file directly in
    the plugin folder or its tests/ folder, size-capped. The manifest is the core's."""
    parts = Path(rel).parts
    ok_dir = len(parts) == 1 or (len(parts) == 2 and parts[0] == "tests")
    if (not ok_dir or any(p in ("..", "") or p.startswith(".") for p in parts)
            or Path(rel).suffix not in (".py", ".md") or Path(rel).is_absolute()):
        raise MisesError(ErrorCode.OUT_OF_SCOPE, f"{rel!r}: write <file>.py or tests/<file>.py "
                         "inside the simulator's folder")
    d = plugin_dir(home, name)
    files = [p for p in d.rglob("*") if p.is_file()] if d.exists() else []
    if len(content.encode()) > MAX_BYTES or len(files) >= MAX_FILES:
        raise MisesError(ErrorCode.INVALID_INPUT, "plugin too large",
                         hint=f"at most {MAX_FILES} files of {MAX_BYTES // 1000} kB")
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_symlink():
        raise MisesError(ErrorCode.OUT_OF_SCOPE, "refusing to write through a symlink")
    p.write_text(content, encoding="utf-8")
    return p


# ---- S8: the independent static review

def review(d: Path) -> dict:
    """Static review of every .py file in the plugin (tests excluded: they never run in the
    worker). Returns {passed, findings[]}. Defence in depth: the sandbox is the boundary."""
    findings: list[str] = []
    srcs = sorted(p for p in d.glob("*.py"))
    if not (d / "solver.py").is_file():
        findings.append("solver.py is missing")
    local = {p.stem for p in srcs}
    for p in srcs:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=p.name)
        except SyntaxError as e:
            findings.append(f"{p.name}: syntax error line {e.lineno}")
            continue
        for node in ast.walk(tree):
            where = f"{p.name}:{getattr(node, 'lineno', '?')}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                        else [node.module or ""])
                if isinstance(node, ast.ImportFrom) and node.level:
                    continue
                for m in mods:
                    top = m.split(".")[0]
                    if top not in ALLOWED_IMPORTS and top not in local:
                        findings.append(f"{where}: import {m} is not allowed")
            elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
                findings.append(f"{where}: {node.id} is not allowed")
            elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                findings.append(f"{where}: dunder attribute {node.attr} is not allowed")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                    node.value.startswith("__") and node.value.endswith("__"):
                findings.append(f"{where}: dunder string {node.value!r} is not allowed")
    return {"passed": not findings, "findings": findings[:40], "reviewer": "static_ast",
            "reviewed_at": time.time()}


# ---- S4 + S5: run in the sandbox, and the validation gate

def _profile_rules(plugin: Path, job: Path) -> str:
    """No reads of the user's home folder except Python, the plugin and its job dir."""
    user = Path.home().resolve()
    keep = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
            Path(sys.executable).resolve().parent.parent, plugin.resolve(), job.resolve()}
    rules = f'(deny file-read* (subpath "{user}"))\n'
    for k in keep:
        if k.is_relative_to(user):
            rules += f'(allow file-read* (subpath "{k}"))\n'
    lookups = {p for k in keep for p in k.parents if p.is_relative_to(user)}
    if lookups:
        rules += "(allow file-read-metadata " + " ".join(
            f'(literal "{p}")' for p in sorted(lookups)) + ")\n"
    return rules


async def run_cases(home: Path, name: str, cases: list[dict], *,
                    timeout_s: float | None = None) -> list[dict]:
    """Run `solve` on each case in the sandboxed worker. Never in this process."""
    from .proc import run_sandboxed
    d = plugin_dir(home, name).resolve()
    job = simulators_root(home) / "_jobs" / f"{name}_{secrets.token_hex(4)}"
    job.mkdir(parents=True)
    try:
        (job / "cases.json").write_text(json.dumps(cases))
        # -I: no PYTHONPATH, user site or cwd on the path (the venv's packages stay)
        argv = [sys.executable, "-I", "-c", _RUNNER, str(d), str(job / "cases.json"),
                str(job / "out.json"), str(SANDBOX_LIMITS["cpu_s"])]
        rc, out, err = await run_sandboxed(
            argv, job, timeout_s=timeout_s or SANDBOX_LIMITS["wall_s"], what=f"simulator {name}",
            memory_limit_mb=SANDBOX_LIMITS["memory_mb"], read_dirs=(d,),
            extra_rules=_profile_rules(d, job))
        try:
            return json.loads((job / "out.json").read_text())
        except (OSError, ValueError):
            tail = (err or out or "").strip().splitlines()[-3:]
            return [{"name": c["name"], "error": f"worker exited {rc}: " + " | ".join(tail)}
                    for c in cases]
    finally:
        shutil.rmtree(job, ignore_errors=True)


def judge(cases: list[dict], results: list[dict]) -> list[dict]:
    """Each case vs its known answer: relative error within tolerance_pct (absolute
    tolerance_pct/100 when the answer is 0)."""
    by = {r["name"]: r for r in results}
    out = []
    for c in cases:
        r = by.get(c["name"], {"error": "no result"})
        rows, ok = [], "error" not in r
        for k, want in c["expected"].items():
            got = (r.get("outputs") or {}).get(k)
            if got is None:
                rows.append({"output": k, "expected": want, "got": None, "pass": False})
                ok = False
                continue
            err = abs(got - want) / abs(want) * 100 if want else abs(got) * 100
            good = err <= c["tolerance_pct"]
            ok = ok and good
            rows.append({"output": k, "expected": want, "got": got, "error_pct": round(err, 3),
                         "pass": good})
        out.append({"case": c["name"], "source": c["source"], "tolerance_pct":
                    c["tolerance_pct"], "pass": ok, "rows": rows, "error": r.get("error")})
    return out


_DDL = """CREATE TABLE IF NOT EXISTS sim_plugins (
  name TEXT PRIMARY KEY, request_id TEXT, enabled INTEGER, data TEXT, updated_at REAL);"""


class SimPlugins:
    def __init__(self, store: Store, home: Path):
        self.store, self.home = store, Path(home)
        if not getattr(store, "_sim_plugins_ready", False):
            with store._lock:
                store._db.executescript(_DDL)
            store._sim_plugins_ready = True

    def get(self, name: str) -> dict | None:
        r = self.store._one("SELECT * FROM sim_plugins WHERE name=?", (name,))
        return None if r is None else {**json.loads(r["data"]), "name": r["name"],
                                       "request_id": r["request_id"],
                                       "enabled": bool(r["enabled"])}

    def list(self) -> list[dict]:
        return [self.get(r["name"]) for r in
                self.store._all("SELECT name FROM sim_plugins ORDER BY name")]

    def _put(self, name: str, request_id: str, enabled: bool, data: dict) -> dict:
        with self.store._tx() as db:
            db.execute("INSERT OR REPLACE INTO sim_plugins VALUES (?,?,?,?,?)",
                       (name, request_id, int(enabled), json.dumps(data), time.time()))
        return self.get(name)

    def manifest(self, req_rec: dict, version: int) -> dict:
        q = req_rec["request"]
        return {"name": req_rec["name"], "version": version, "computes": q["computes"],
                "inputs": q["inputs"], "outputs": q["outputs"], "method": q["method"],
                "fidelity_class": q["fidelity_class"], "fidelity": "agent_built",
                "licence": "agent-written (user's)", "source": f"simulator_request "
                f"{req_rec['id']}", "dependencies": q["dependencies"],
                "validation_cases": q["validation_cases"]}

    async def validate(self, request_id: str) -> dict:
        """S8 review, then S5 validation in the sandbox. Enabled only if both pass."""
        reqs = SimRequests(self.store)
        rec = reqs.get(request_id)
        if rec["status"] not in ("building",):
            raise MisesError(ErrorCode.NEEDS_APPROVAL,
                             f"simulator request is {rec['status']}, not building")
        name = rec["name"]
        prev = self.get(name)
        version = (prev or {}).get("manifest", {}).get("version", 0) + 1
        man = self.manifest(rec, version)
        d = plugin_dir(self.home, name)
        rv = review(d) if d.is_dir() else {"passed": False, "findings": ["no plugin folder"],
                                           "reviewer": "static_ast"}
        evidence: list[dict] = []
        if rv["passed"]:
            cases = rec["request"]["validation_cases"]
            evidence = judge(cases, await run_cases(self.home, name, cases))
        passed = rv["passed"] and bool(evidence) and all(e["pass"] for e in evidence)
        n = sum(e["pass"] for e in evidence)
        val = {"passed": passed, "cases": len(evidence), "passed_cases": n,
               "evidence": evidence, "validated_at": time.time(),
               "label": (f"agent-built simulator, validated against {n} cases" if passed else
                         "agent-built simulator, NOT validated: disabled")}
        why = (None if passed else "review refused: " + "; ".join(rv["findings"][:5])
               if not rv["passed"] else f"validation failed: {n} of {len(evidence)} cases pass")
        out = self._put(name, request_id, passed, {"manifest": man, "review": rv,
                                                   "validation": val, "why_disabled": why})
        r = reqs.get(request_id)
        reqs._cas(request_id, "building", "building", {**reqs._data(r), "build": {
            **(r["build"] or {}), "last_validation": {"passed": passed, "why": why}}})
        if passed:
            r = reqs.get(request_id)
            reqs._cas(request_id, "building", "built",
                      {**reqs._data(r), "build": {**r["build"], "finished": True}})
        return out

    async def run(self, name: str, inputs: dict) -> dict:
        p = self.get(name)
        if p is None or not p["enabled"]:
            raise MisesError(ErrorCode.DENIED, f"simulator {name!r} is not enabled",
                             hint=(p or {}).get("why_disabled") or "it must pass validation")
        res = (await run_cases(self.home, name, [{"name": "run", "inputs": inputs}]))[0]
        if "error" in res:
            raise MisesError(ErrorCode.TOOL_FAILED, f"{name}: {res['error']}")
        v = p["validation"]
        return {"simulator": name, "version": p["manifest"]["version"], "outputs": res["outputs"],
                "units": {o["name"]: o["unit"] for o in p["manifest"]["outputs"]},
                "fidelity": "agent_built", "fidelity_class": p["manifest"]["fidelity_class"],
                "label": v["label"], "validation": {"cases": v["cases"], "evidence": v["evidence"]}}

    def set_enabled(self, name: str, enabled: bool) -> dict:
        p = self.get(name)
        if p is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no simulator {name!r}")
        if enabled and not p["validation"]["passed"]:
            raise MisesError(ErrorCode.DENIED, "a simulator that failed validation stays off")
        return self._put(name, p["request_id"], enabled,
                         {k: p[k] for k in ("manifest", "review", "validation", "why_disabled")})

    def delete(self, name: str) -> None:
        with self.store._tx() as db:
            db.execute("DELETE FROM sim_plugins WHERE name=?", (name,))
        shutil.rmtree(plugin_dir(self.home, name), ignore_errors=True)

    def registry(self) -> list[dict]:
        """S7: what sim_types_list shows."""
        return [{"id": p["name"], "name": p["name"].replace("_", " "), "solver": "agent_built",
                 "fidelity_class": p["manifest"]["fidelity_class"], "fidelity": "agent_built",
                 "doc": p["manifest"]["computes"], "version": p["manifest"]["version"],
                 "source": p["manifest"]["source"], "licence": p["manifest"]["licence"],
                 "available": p["enabled"], "validation": p["validation"]["label"],
                 "inputs": p["manifest"]["inputs"], "outputs": p["manifest"]["outputs"]}
                for p in self.list()]
