"""The text of a PDF a web page served (a datasheet, a paper), read in the sandboxed parse
worker (mises.imports.parse_worker `pdftext`: no network, writes only under its job dir, CPU
and time limits), never in the core process. The text is untrusted page data like any other."""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from ..types import MisesError

log = logging.getLogger("mises.browser")

PDF_TEXT_TIMEOUT_S = 45.0


async def extract(body: bytes) -> str | None:
    """The PDF's text layer, or None when it has none (a scan) or could not be read."""
    from ..imports.drawing_import import run_parse_worker

    job = Path(tempfile.mkdtemp(prefix="mises-pdftext-"))
    try:
        (job / "input.pdf").write_bytes(body)
        res = await run_parse_worker(job, "pdftext", timeout_s=PDF_TEXT_TIMEOUT_S)
    except (MisesError, OSError) as e:
        log.info("pdf text not read: %s", getattr(e, "message", e))
        return None
    finally:
        shutil.rmtree(job, ignore_errors=True)
    text = res.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return text
