"""Fetch an arXiv HTML paper to plain text (tables as pipe-separated rows), for reading papers
themselves rather than summaries. Downloads are data: they are only parsed, never executed.

    python -I tools_fetch_paper.py OUT_DIR 2605.10873v2 [more ids]
"""
import html.parser
import sys
import urllib.request
from pathlib import Path


class Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "annotation", "annotation-xml"}
    BLOCK = {"p", "div", "section", "h1", "h2", "h3", "h4", "h5", "li", "figcaption", "caption"}

    def __init__(self):
        super().__init__()
        self.out, self.skip, self.cells = [], 0, None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "tr":
            self.cells = []
        elif tag in ("td", "th") and self.cells is not None:
            self.cells.append("")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "tr" and self.cells is not None:
            self.out.append("\n| " + " | ".join(c.strip() for c in self.cells) + " |")
            self.cells = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.cells is not None and self.cells:
            self.cells[-1] += " ".join(data.split()) + " " if data.strip() else ""
        else:
            self.out.append(data)


def fetch(pid: str, out: Path) -> Path:
    url = f"https://arxiv.org/html/{pid}"
    req = urllib.request.Request(url, headers={"User-Agent": "paper-reader"})
    raw = urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "replace")
    p = Text()
    p.feed(raw)
    txt = "".join(p.out)
    lines = [ln.rstrip() for ln in txt.splitlines() if ln.strip()]
    dest = out / f"{pid.replace('/', '_')}.txt"
    dest.write_text("\n".join(lines))
    return dest


if __name__ == "__main__":
    o = Path(sys.argv[1])
    o.mkdir(parents=True, exist_ok=True)
    for pid in sys.argv[2:]:
        try:
            d = fetch(pid, o)
            print(pid, d.stat().st_size)
        except Exception as e:  # noqa: BLE001
            print(pid, "FAILED", e)
