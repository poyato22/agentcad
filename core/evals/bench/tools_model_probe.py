"""Which model aliases does the signed-in Claude Code offer? One tiny prompt each; prints the
model reported at init and the result, which vendor-key names are set (names only, never values),
and the weekly usage via the read-only helper."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

MSG = json.dumps({"type": "user", "message": {"role": "user", "content": [
    {"type": "text", "text": "reply with the word ok"}]}}) + "\n"

for alias in ("sonnet", "opus", "fable"):
    p = subprocess.run(["claude", "-p", "--input-format", "stream-json", "--output-format",
                        "stream-json", "--verbose", "--tools", "", "--setting-sources=",
                        "--no-session-persistence", "--model", alias],
                       input=MSG, capture_output=True, text=True, timeout=120,
                       cwd=Path.home() / "Documents/Claude/Projects/Mises-demo/bench")
    init, res = None, None
    for line in p.stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            init = e.get("model")
        if e.get("type") == "result":
            res = (e.get("subtype"), e.get("is_error"), e.get("total_cost_usd"),
                   str(e.get("result"))[:120])
    print(alias, "->", init, res, (p.stderr or "")[:200])

print("vendor key env vars set:", sorted(k for k in os.environ if k.endswith("_API_KEY")))

spec = importlib.util.spec_from_file_location(
    "cl", Path.home() / ".claude/cmux-limit-watcher/bin/cmuxlimit.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print("usage", (m.fetch_usage(force=True) or {}).get("seven_day"))
