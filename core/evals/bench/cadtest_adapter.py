"""CADTestBench adapter: run a sample's cadtests against an exported STEP, in CadQuery.

    uv run --no-project --with cadquery python -I cadtest_adapter.py RESULT.step TESTS.json OUT.json

Both systems write build123d, and the cadtests query a CadQuery object, so the build123d
result goes through STEP: `final_result = cq.importers.importStep(path)` (a cq.Workplane on the
imported shape, the same type the repo's baselines hand the tests). Each cadtest is executed
exactly as the official runner does (metrics/cadtest.py: the `check()` preamble, exec of the
snippet, any exception = fail). Scores follow the official definitions:
- pass_rate: passed / total cadtests of the sample;
- rs: requirement satisfaction, the share of requirement groups whose cadtests all pass;
- pr (per sample): every cadtest passed.
A missing or unreadable STEP fails every cadtest (an invalid sample).

Runs in its own process with its own CadQuery install, never inside AgentCAD's environment.
"""
import json
import signal
import sys

TEST_TIMEOUT_S = 20


def _alarm(*_):
    raise TimeoutError(f"cadtest exceeded {TEST_TIMEOUT_S} s")

PREAMBLE = """import math
_check_pass_msg = None
def check(condition, pass_msg, fail_msg):
    global _check_pass_msg
    if not condition:
        raise AssertionError(fail_msg)
    _check_pass_msg = pass_msg
"""


def main(step, tests_path, out_path):
    tests = json.load(open(tests_path))
    entries, err = [], None
    env = None
    try:
        import cadquery as cq
        final_result = cq.importers.importStep(step)
        env = {"cq": cq, "cadquery": cq, "final_result": final_result}
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    for t in tests:
        if env is None:
            entries.append({"id": t["cadtest_id"], "type": t["cadtest_type"], "pass": False,
                            "msg": err, "req": t.get("requirement_id")})
            continue
        e = dict(env)
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(TEST_TIMEOUT_S)
        try:
            exec(PREAMBLE + (t["cadtest_code"] or ""), e)
            signal.alarm(0)
            entries.append({"id": t["cadtest_id"], "type": t["cadtest_type"], "pass": True,
                            "req": t.get("requirement_id")})
        except BaseException as ex:  # noqa: BLE001
            signal.alarm(0)
            entries.append({"id": t["cadtest_id"], "type": t["cadtest_type"], "pass": False,
                            "msg": f"{type(ex).__name__}: {str(ex)[:200]}",
                            "req": t.get("requirement_id")})
    groups = {}
    for x in entries:
        if x["req"] is not None:
            groups.setdefault(x["req"], []).append(x["pass"])
    n = len(entries)
    out = {"total": n, "passed": sum(x["pass"] for x in entries),
           "pass_rate": (sum(x["pass"] for x in entries) / n) if n else 0.0,
           "rs": (sum(all(v) for v in groups.values()) / len(groups)) if groups else 0.0,
           "groups": len(groups), "pr": bool(n) and all(x["pass"] for x in entries),
           "load_error": err, "by_type": {}, "tests": entries}
    for x in entries:
        b = out["by_type"].setdefault(x["type"], [0, 0])
        b[0] += x["pass"]
        b[1] += 1
    json.dump(out, open(out_path, "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:4])
