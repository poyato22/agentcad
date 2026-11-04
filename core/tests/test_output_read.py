"""DOG demo: assembly_get on the 48-instance dog spilled to a file handle (> 16 KB) and the
Claude Code designers had no tool to read it ("assembly_get returned only a file handle, and I
have no tool to read it"). output_read pages through a spilled result of the run's own."""
import json
from types import SimpleNamespace

import pytest

from mises.gate import Gate
from mises.output_tools import OutputReadArgs, output_read
from mises.types import MisesError, ToolOutput


def _spilled(tmp_path):
    big = ToolOutput(summary="48 instance(s)",
                     data={"instances": [{"id": f"i{k}", "name": f"part_{k}", "pad": "x" * 400}
                                         for k in range(60)]})
    out = Gate._spill(big, tmp_path, "mcp_abc123")
    assert out.handles and out.data == {}
    return out


async def test_reads_a_spilled_result_by_handle_and_key(tmp_path):
    out = _spilled(tmp_path)
    assert "output_read" in " ".join(out.warnings)
    ctx = SimpleNamespace(workdir=tmp_path)
    r = await output_read.handler(ctx, OutputReadArgs(handle=out.handles[0],
                                                      key="data.instances.3"))
    assert json.loads(r.data["text"])["name"] == "part_3" and r.data["complete"]
    page = await output_read.handler(ctx, OutputReadArgs(handle="mcp_abc123.json", limit=2000))
    assert len(page.data["text"]) == 2000 and page.data["next_offset"] == 2000
    assert page.data["total_chars"] > 16 * 1024


async def test_only_the_runs_own_outputs(tmp_path):
    _spilled(tmp_path)
    ctx = SimpleNamespace(workdir=tmp_path)
    for bad in ("../secrets.json", "/etc/passwd", "mcp_abc123.txt"):
        with pytest.raises(MisesError):
            await output_read.handler(ctx, OutputReadArgs(handle=bad))
