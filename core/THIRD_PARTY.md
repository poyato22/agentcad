# Third-party software

Runtime dependencies (see `pyproject.toml`): pydantic, anthropic, openai, mcp,
agent-client-protocol, build123d, httpx, fastapi, uvicorn, rich, prompt_toolkit, pyyaml.
Each is used under its own license (MIT / Apache-2.0 / BSD).

Drawing import: ezdxf (MIT) reads DXF files and pypdfium2 (Apache-2.0 or BSD-3-Clause,
bundling PDFium under BSD-3-Clause / Apache-2.0) rasterizes PDF pages. Both only ever parse
user files inside a sandboxed child process (see `mises.imports.parse_worker`).

Rocket motor thrust curves (`mises/rocketry/motors/*.eng`): RASP simfiles from ThrustCurve.org
(https://www.thrustcurve.org) whose license class there is Public Domain, unmodified; each
file's simfile id and URL are in `motors/sources.json`. Curves without a PD/free license are
not shipped: `rocket_motor` fetches them into the user's own home at runtime.
