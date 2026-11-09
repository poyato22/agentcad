"""Dataset fetchers. Everything is cached under CACHE (outside the repo); nothing is committed.

CADBench (DeCoDELab/CADBench on Hugging Face; repo MIT, each source keeps its own licence:
DeepCAD MIT, Fusion 360 Gallery non-commercial research licence, MCB MIT, so internal
evaluation only, no redistribution of derived files). Task: single-view grayscale image ->
CAD; ground truth = the dataset's STL. Subset: 20 per family from M (MCB, mechanical),
F (Fusion 360) and B (DeepCAD); B and F stratified 7 easy / 7 medium / 6 hard by reading
one parquet shard from the start, middle and end of the split (the splits are stored sorted
by label). Picks are seed-pinned (sha256 of seed + file_id).

CADTestBench (dimitrismallis/CADTestBench, MIT): 50 seed-pinned prompts of the `detailed`
partition with all their cadtests. The prompts ask for "Python code using CADQuery"; that
framing is replaced by a neutral "Create" for both systems (both write build123d; the tests
run on the exported STEP through the CadQuery adapter).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.request
from pathlib import Path

from .common import CACHE, UNITS_NOTE, Task

HF = "https://huggingface.co/datasets"
CADBENCH_SHARDS = {
    # family: (split file prefix, n shards, [(shard, label)]); the splits are stored easy, hard, medium
    "M": ("bench2", 20, [(0, "")]),
    "B": ("bench0", 18, [(0, "easy"), (16, "medium"), (8, "hard")]),
    "F": ("bench0F", 20, [(0, "easy"), (18, "medium"), (9, "hard")]),
}
CADBENCH_PROMPT = (
    "Model the object shown in the attached image as a CAD part: one watertight solid that "
    "matches its shape, features and proportions as closely as you can. Only one view is "
    "given; infer hidden sides sensibly. The absolute size is not important, the proportions "
    "are.")


def _download(url: str, dest: Path) -> Path:
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    return dest


def _rank(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def fetch_cadbench(per_family: int = 20, seed: int = 0, cache: Path = CACHE,
                   split: str = "dev") -> list[Task]:
    """split "dev": the first picks of the seed-pinned ranking (the set tuned on); "test": the
    next ones in the same ranking, disjoint by construction (held out, never tuned on);
    "test2": the slice after that, never run before the round-3 comparison."""
    import pyarrow.parquet as pq

    raw, root = cache / "cadbench_raw", cache / "cadbench"
    tasks: list[Task] = []
    for fam, (prefix, nshards, shards) in CADBENCH_SHARDS.items():
        quotas = _quotas(per_family, len(shards))
        for (shard, label), quota in zip(shards, quotas):
            name = f"{prefix}-{shard:05d}-of-{nshards:05d}.parquet"
            path = _download(f"{HF}/DeCoDELab/CADBench/resolve/main/data/{name}", raw / name)
            pf = pq.ParquetFile(path)
            ids = pf.read(columns=["file_id", "label"]).to_pylist()
            ids = [r for r in ids if (r["label"] or "") == label]
            ranked = sorted(ids, key=lambda r: _rank(seed, r["file_id"]))
            k = {"dev": 0, "test": 1, "test2": 2}[split]
            pick = ranked[k * quota:(k + 1) * quota]
            want = {r["file_id"] for r in pick}
            for batch in pf.iter_batches(columns=["file_id", "label", "stl", "step",
                                                  "singleview_image"], batch_size=16):
                for r in batch.to_pylist():
                    if r["file_id"] not in want:
                        continue
                    d = root / fam / _safe(r["file_id"])
                    d.mkdir(parents=True, exist_ok=True)
                    (d / "gt.stl").write_bytes(r["stl"])
                    if r.get("step"):
                        (d / "gt.step").write_bytes(r["step"])
                    img = r["singleview_image"]["bytes"]
                    (d / "view.png").write_bytes(_to_png(img))
                    tasks.append(Task({"dev": "cadbench", "test": "cadbench_test",
                                       "test2": "cadbench_t2"}[split],
                                      f"{fam}-{_safe(r['file_id'])}",
                                      CADBENCH_PROMPT, [str(d / "view.png")],
                                      {"family": fam, "label": label or "mcb",
                                       "file_id": r["file_id"], "gt_stl": str(d / "gt.stl")}))
    tasks.sort(key=lambda t: t.id)
    return tasks


def _quotas(n: int, k: int) -> list[int]:
    base = [n // k] * k
    for i in range(n - sum(base)):
        base[i] += 1
    return base


def _to_png(data: bytes) -> bytes:
    if data[:4] == b"\x89PNG":
        return data
    from PIL import Image
    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).save(buf, "PNG")
    return buf.getvalue()


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


_CQ_FRAMING = re.compile(
    r"^\s*(?:write|generate|create)\s+(?:a\s+)?python\s+(?:code|script)\s+(?:using|with|in)\s+"
    r"cad\s*query\s+(?:to\s+)?", re.I)


def neutral_prompt(prompt: str) -> str:
    """Drop the 'Write Python code using CADQuery to' framing; keep the geometry text."""
    p = _CQ_FRAMING.sub("", prompt.strip())
    p = re.sub(r"\bCAD\s*Query\b", "CAD", p, flags=re.I)
    return p[:1].upper() + p[1:]


def fetch_cadtestbench(n: int = 50, seed: int = 0, partition: str = "detailed",
                       cache: Path = CACHE, split: str = "dev") -> list[Task]:
    """split "dev": the first n of the seed-pinned ranking; "test": the next n (disjoint)."""
    import pyarrow.parquet as pq

    root = cache / "cadtestbench"
    base = f"{HF}/dimitrismallis/CADTestBench/resolve/main"
    sp = _download(f"{base}/samples/{partition}.parquet", root / "samples" / f"{partition}.parquet")
    tp = _download(f"{base}/cadtests/{partition}.parquet", root / "cadtests" / f"{partition}.parquet")
    samples = pq.read_table(sp).to_pylist()
    tests = pq.read_table(tp).to_pylist()
    by_id: dict[str, list] = {}
    for t in tests:
        by_id.setdefault(t["sample_id"], []).append(
            {k: t.get(k) for k in ("cadtest_id", "cadtest_code", "cadtest_type",
                                   "cadtest_description", "requirement_id")})
    ranked = sorted(samples, key=lambda s: _rank(seed, s["sample_id"]))
    pick = ranked[:n] if split == "dev" else ranked[n:2 * n]
    out = []
    for s in sorted(pick, key=lambda s: s["sample_id"]):
        d = root / "tasks" / s["sample_id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "cadtests.json").write_text(json.dumps(by_id.get(s["sample_id"], []), indent=1))
        out.append(Task("cadtestbench" if split == "dev" else "cadtestbench_test", s["sample_id"],
                        neutral_prompt(s["prompt"]) + "\n\n" + UNITS_NOTE, [],
                        {"partition": partition, "original_prompt": s["prompt"],
                         "cadtests": str(d / "cadtests.json"),
                         "n_tests": len(by_id.get(s["sample_id"], []))}))
    return out


#: CADPrompt (Alrashedy et al., ICLR 2025): github.com/Kamel773/CAD_Code_Generation, main as of
#: its last push (2025-02-28). The repo has no LICENSE file; its README asks users to cite the
#: paper and says the STLs come from DeepCAD. So: local evaluation only, the data is never
#: committed or redistributed.
CADPROMPT_TARBALL = "https://codeload.github.com/Kamel773/CAD_Code_Generation/tar.gz/refs/heads/main"
CADPROMPT_PROMPT_FILE = "Natural_Language_Descriptions_Prompt_with_specific_measurements.txt"


def fetch_cadprompt(n: int = 100, seed: int = 0, cache: Path = CACHE,
                    split: str = "bench4") -> list[Task]:
    """CADPrompt's 200 objects ranked with the same seed-pinned hash as CADTestBench, whose 200
    sample ids are these objects. Ranks 0-99 were CADTestBench DEV and TEST (rounds 1 and 2);
    split "bench4" takes ranks 100-199, which no AgentCAD build has seen. Each task gets the
    "with specific measurements" prompt, with the CadQuery framing made neutral as for
    CADTestBench, plus the units line. The order returned is the rank order, so a --limit
    prefix (the pilot) is itself a seed-pinned random sample."""
    import tarfile

    raw = cache / "cadprompt_raw"
    root = raw / "CAD_Code_Generation-main" / "CADPrompt"
    if not root.is_dir():
        tgz = _download(CADPROMPT_TARBALL, raw / "repo.tar.gz")
        with tarfile.open(tgz) as tf:
            members = [m for m in tf.getmembers() if m.isfile() and ".." not in m.name
                       and not m.name.startswith("/")]
            tf.extractall(raw, members=members)
    ids = sorted(p.name for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d+", p.name))
    ranked = sorted(ids, key=lambda i: _rank(seed, i))
    lo = {"bench4": 100, "seen": 0}[split]
    out = []
    for k, i in enumerate(ranked[lo:lo + n]):
        d = root / i
        prompt = (d / CADPROMPT_PROMPT_FILE).read_text(errors="replace").strip()
        out.append(Task("cadprompt", i, neutral_prompt(prompt) + "\n\n" + UNITS_NOTE, [],
                        {"original_prompt": prompt, "gt_stl": str(d / "Ground_Truth.stl"),
                         "rank": lo + k}))
    return out
