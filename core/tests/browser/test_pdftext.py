"""A PDF's text layer, read in the sandboxed parse worker."""
import sys

import pytest

from mises.browser import pdftext
from mises.imports.parse_worker import _pdftext

# a one-page PDF with a text line (no xref table: pdfium rebuilds it, as for many real files)
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R"
       b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
       b"4 0 obj<</Length 44>>stream\nBT /F1 18 Tf 20 40 Td (Weight: 312 g) Tj ET\nendstream\n"
       b"endobj\n5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
       b"trailer<</Root 1 0 R>>\n%%EOF\n")


def test_the_worker_reads_the_text_layer(tmp_path):
    (tmp_path / "input.pdf").write_bytes(PDF)
    res = _pdftext(tmp_path)
    assert res["pages_total"] == 1 and "Weight: 312 g" in res["text"]


def test_a_broken_pdf_is_refused(tmp_path):
    (tmp_path / "input.pdf").write_bytes(b"not a pdf")
    with pytest.raises(SystemExit):
        _pdftext(tmp_path)


@pytest.mark.skipif(sys.platform != "darwin", reason="the sandbox is macOS sandbox-exec")
async def test_extract_runs_the_sandboxed_worker():
    assert "Weight: 312 g" in (await pdftext.extract(PDF) or "")
    assert await pdftext.extract(b"not a pdf") is None
