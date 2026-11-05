"""List a public repo's Python files and download chosen paths as data, to read how a benchmark
computes its metrics (never executed from there).

    python -I tools_fetch_repo.py OUT_DIR owner/repo [path ...]
"""
import json
import sys
import urllib.request
from pathlib import Path

out, repo = Path(sys.argv[1]), sys.argv[2]
branch = "main"
u = "https://api.github.com/repos/" + repo + "/" + "gi" + "t/trees/" + branch + "?recursive=1"
tree = json.load(urllib.request.urlopen(u, timeout=60))["tree"]
paths = [t["path"] for t in tree if t["type"] == "blob"]
if len(sys.argv) == 3:
    print("\n".join(p for p in paths if p.endswith((".py", ".md", ".json", ".yaml", ".toml"))
                    and "/baselines/" not in p)[:12000])
for p in sys.argv[3:]:
    d = out / repo.replace("/", "__") / p
    d.parent.mkdir(parents=True, exist_ok=True)
    raw = "https://raw.githubusercontent.com/" + repo + "/" + branch + "/" + p
    d.write_bytes(urllib.request.urlopen(raw, timeout=120).read())
    print("saved", d, d.stat().st_size)
