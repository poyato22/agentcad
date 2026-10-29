"""Agent definitions: markdown files with a YAML frontmatter block."""
from __future__ import annotations

import os
import re
import tempfile
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from ..types import ErrorCode, MisesError

# custom-agent names double as filenames, so they are tightly constrained: lowercase, no
# path separators, no dots — a name can never escape the agents directory.
AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")


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


# ---- custom agent CRUD (Plan 5 C1): the user's own agents live as markdown files under
# home/agents/. Built-in agents (source is None) can only be hidden, via the profile.

def agents_dir(home: Path) -> Path:
    return Path(home) / "agents"


def _check_agent_name(name: str) -> None:
    if not AGENT_NAME_RE.match(name):
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"agent name {name!r} must match {AGENT_NAME_RE.pattern}",
                         hint="lowercase letters, digits, '-' or '_'; start with a letter")


def custom_agent_path(home: Path, name: str) -> Path:
    """The <name>.md path, verified to stay inside the agents directory (no traversal)."""
    _check_agent_name(name)
    base = agents_dir(home)
    path = base / f"{name}.md"
    # a belt-and-braces check on top of the name regex: the resolved path must be a direct
    # child of the agents directory
    if path.resolve().parent != base.resolve():
        raise MisesError(ErrorCode.INVALID_INPUT, "agent name escapes the agents directory")
    return path


def render_definition(*, name: str, description: str, model: str = "inherit",
                      tools: list[str] | None = None, body: str) -> str:
    """Serialize an agent definition to the markdown-with-frontmatter form load_definitions
    reads. Validates by round-tripping through parse_definition."""
    meta: dict = {"name": name, "description": description, "model": model}
    if tools is not None:
        meta["tools"] = tools
    text = "---\n" + yaml.safe_dump(meta, sort_keys=False).strip() + "\n---\n" + body.strip() + "\n"
    parse_definition(text)  # raises ValueError on anything load_definitions would skip
    return text


def _atomic_write(path: Path, text: str) -> None:
    """A fresh temp file in the same directory (mkstemp: O_CREAT|O_EXCL, so a planted symlink
    at the temp name is never followed), then os.replace, which swaps the directory entry
    itself rather than writing through a link at the target."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_custom_agent(home: Path, *, name: str, description: str, model: str = "inherit",
                       tools: list[str] | None = None, body: str, create: bool) -> AgentDefinition:
    """Create (create=True) or update (create=False) a custom agent file. A create that would
    overwrite an existing file, or an update of a missing one, is refused."""
    path = custom_agent_path(home, name)
    if create and path.exists():
        raise MisesError(ErrorCode.INVALID_INPUT, f"agent {name!r} already exists",
                         hint="use PUT to update it")
    if not create and not path.exists():
        raise MisesError(ErrorCode.NOT_FOUND, f"no custom agent {name!r}")
    try:
        text = render_definition(name=name, description=description, model=model, tools=tools,
                                 body=body)
    except ValueError as e:
        raise MisesError(ErrorCode.INVALID_INPUT, f"invalid agent definition: {e}") from e
    agents_dir(home).mkdir(parents=True, exist_ok=True)
    _atomic_write(path, text)
    return parse_definition(text, source=path)


def delete_custom_agent(home: Path, name: str) -> None:
    path = custom_agent_path(home, name)
    if not path.exists():
        raise MisesError(ErrorCode.NOT_FOUND, f"no custom agent {name!r}")
    path.unlink()
