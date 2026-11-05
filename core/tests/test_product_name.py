"""The product is called AgentCAD everywhere a user or an agent reads its name. Internal
identifiers (bundle id, package, CLI, MCP server, data folder) deliberately keep `mises`."""
import json
import re
from pathlib import Path

from mises.agent.prompts import RULES
from mises.orchestrator.lead import LEAD_PROMPT

APP = Path(__file__).resolve().parents[2] / "app"
OLD_NAME = re.compile(r"(?<![Vv]on )\bMises\b")      # von Mises stress is an engineering term


def test_visible_title_and_lead_prompt_say_agentcad():
    conf = json.loads((APP / "src-tauri" / "tauri.conf.json").read_text())
    assert conf["productName"] == "AgentCAD"
    assert conf["app"]["windows"][0]["title"] == "AgentCAD"
    assert conf["identifier"] == "app.mises.desktop"     # keeps the user's data and credentials
    assert "<title>AgentCAD</title>" in (APP / "index.html").read_text()

    assert "You are an agent inside AgentCAD" in RULES
    assert "AgentCAD" in LEAD_PROMPT
    assert not OLD_NAME.search(RULES)
    assert not OLD_NAME.search(LEAD_PROMPT)
