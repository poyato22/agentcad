"""Make a pairwise-preference sheet from two design-eval runs (D28), for the user to fill in.

    cd core && uv run python evals/design/preference_sheet.py RUN_A RUN_B --out sheet.html

RUN_A and RUN_B are run_eval.py output folders (e.g. two models, or two judge settings). The
sheet is one self-contained HTML file: per prompt, the two builds side by side (hero and
zebra), shuffled left/right per prompt so the labels do not bias the choice, with "left /
right / tie" buttons and a note. Choices stay in the browser (localStorage) and "Export"
downloads a JSON of {prompt id: "A" | "B" | "tie", note}, already un-shuffled. Score it with
`summarise(json)`: the win rate per run and per design class.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import random
import sys
from pathlib import Path


def _img(p: Path) -> str:
    if not p.is_file():
        return '<div class="none">no render</div>'
    return f'<img src="data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}">'


def build(run_a: Path, run_b: Path, seed: int = 7) -> str:
    rows_a = {r["id"]: r for r in json.loads((run_a / "results.json").read_text())}
    rows_b = {r["id"]: r for r in json.loads((run_b / "results.json").read_text())}
    rng = random.Random(seed)
    cards, key = [], {}
    for pid in [p for p in rows_a if p in rows_b]:
        flip = rng.random() < 0.5
        left, right = (run_b, run_a) if flip else (run_a, run_b)
        key[pid] = {"left": "B" if flip else "A", "right": "A" if flip else "B"}
        cls = (rows_a[pid].get("design_class") or {}).get("cls", "?")
        cards.append(f"""
<section data-id="{html.escape(pid)}" data-cls="{html.escape(cls)}">
  <h2>{html.escape(pid)} <small>{html.escape(cls)}</small></h2>
  <p class="prompt">{html.escape(rows_a[pid]['prompt'])}</p>
  <div class="pair">
    <figure>{_img(left / pid / 'hero.png')}{_img(left / pid / 'zebra.png')}
      <button data-pick="left">Left is better</button></figure>
    <figure>{_img(right / pid / 'hero.png')}{_img(right / pid / 'zebra.png')}
      <button data-pick="right">Right is better</button></figure>
  </div>
  <button data-pick="tie">Tie</button>
  <input class="note" placeholder="Why (optional)">
</section>""")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design preference</title><style>
:root {{ --bg: #f6f6f7; --ink: #1b1e23; --dim: #6b7280; --pick: #2563eb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #14161a; --ink: #e9ebee; --dim: #9aa1aa; }} }}
body {{ background: var(--bg); color: var(--ink); font: 14px system-ui, sans-serif;
       margin: 0 auto; max-width: 1200px; padding: 16px; }}
section {{ border-top: 1px solid var(--dim); padding: 12px 0; }}
h2 small {{ color: var(--dim); font-weight: 400; }} .prompt {{ color: var(--dim); }}
.pair {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
figure {{ margin: 0; }} img {{ width: 100%; height: auto; display: block; }}
button {{ margin: 6px 6px 0 0; padding: 6px 10px; border-radius: 7px; border: 1px solid var(--dim);
          background: transparent; color: var(--ink); cursor: pointer; }}
button.on {{ background: var(--pick); color: #fff; border-color: var(--pick); }}
.note {{ width: 100%; margin-top: 6px; padding: 6px; box-sizing: border-box; }}
@media (max-width: 700px) {{ .pair {{ grid-template-columns: 1fr; }} }}
</style></head><body>
<h1>Which build looks better?</h1>
<p>Judge the design: silhouette, proportion, surfaces, covered mechanism. Left and right are
shuffled per prompt. <button id="export">Export choices</button></p>
{''.join(cards)}
<script>
const KEY = {json.dumps(key)};
const store = (() => {{ try {{ return JSON.parse(localStorage.getItem("pref") || "{{}}"); }}
                       catch (e) {{ return {{}}; }} }})();
function save() {{ try {{ localStorage.setItem("pref", JSON.stringify(store)); }} catch (e) {{}} }}
for (const sec of document.querySelectorAll("section")) {{
  const id = sec.dataset.id, note = sec.querySelector(".note");
  const mark = () => sec.querySelectorAll("button").forEach(b =>
      b.classList.toggle("on", store[id] && b.dataset.pick === store[id].pick));
  sec.querySelectorAll("button").forEach(b => b.onclick = () => {{
      store[id] = {{ pick: b.dataset.pick, note: note.value }}; save(); mark(); }});
  note.oninput = () => {{ if (store[id]) {{ store[id].note = note.value; save(); }} }};
  if (store[id]) note.value = store[id].note || ""; mark();
}}
document.getElementById("export").onclick = () => {{
  const out = {{}};
  for (const [id, v] of Object.entries(store))
    out[id] = {{ choice: v.pick === "tie" ? "tie" : KEY[id][v.pick], note: v.note || "" }};
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], {{type: "application/json"}}));
  a.download = "design_preferences.json"; a.click();
}};
</script></body></html>"""


def summarise(prefs: dict, results: list[dict]) -> dict:
    """Win rates of run A and B overall and per design class, from an exported sheet."""
    cls = {r["id"]: (r.get("design_class") or {}).get("cls", "?") for r in results}
    out: dict = {}
    for pid, v in prefs.items():
        for group in ("all", cls.get(pid, "?")):
            g = out.setdefault(group, {"A": 0, "B": 0, "tie": 0})
            g[v["choice"]] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_a", type=Path)
    ap.add_argument("run_b", type=Path)
    ap.add_argument("--out", type=Path, default=Path("design_preference.html"))
    a = ap.parse_args(argv)
    a.out.write_text(build(a.run_a, a.run_b))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
