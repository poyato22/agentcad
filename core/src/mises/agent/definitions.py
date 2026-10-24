"""Agent definitions: markdown files with a YAML frontmatter block."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError


class AgentDefinition(BaseModel):
    name: str
    description: str
    model: str = "inherit"
    tools: list[str] | None = None  # None = every registered tool
    max_turns: int = 40
    body: str
    source: Path | None = None


class DefinitionSet(dict[str, AgentDefinition]):
    """dict of definitions plus the warnings for files that were skipped."""

    def __init__(self) -> None:
        super().__init__()
        self.warnings: list[str] = []


def parse_definition(text: str, source: Path | None = None) -> AgentDefinition:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening '---' frontmatter line")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("missing closing '---' frontmatter line") from None
    try:
        meta = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML: {e}") from e
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a mapping")
    try:
        return AgentDefinition(**meta, body="\n".join(lines[end + 1:]).strip() + "\n", source=source)
    except (ValidationError, TypeError) as e:
        raise ValueError(f"invalid frontmatter: {e}") from e


def _load_dir(files: list[tuple[str, str, Path | None]], out: DefinitionSet) -> None:
    for label, text, source in files:
        try:
            d = parse_definition(text, source)
        except ValueError as e:
            out.warnings.append(f"{label}: {e}")
            continue
        out[d.name] = d


def load_definitions(dirs: list[Path] | None = None) -> DefinitionSet:
    """Built-ins first, then each dir in order; later definitions override by name."""
    out = DefinitionSet()
    builtin = resources.files("mises.agent") / "builtin"
    items = sorted((p for p in builtin.iterdir() if p.name.endswith(".md")), key=lambda p: p.name)
    _load_dir([(f"builtin/{p.name}", p.read_text(encoding="utf-8"), None) for p in items], out)
    for d in dirs or []:
        d = Path(d)
        if not d.is_dir():
            continue
        files = []
        for p in sorted(d.glob("*.md")):
            try:
                files.append((str(p), p.read_text(encoding="utf-8"), p))
            except (OSError, UnicodeDecodeError) as e:
                out.warnings.append(f"{p}: unreadable: {e}")
        _load_dir(files, out)
    return out
