# Mises

A Mac and Windows desktop app where people and AI agents design physical parts together on a live Onshape model, test them in a built-in simulator, and send them to Prusa and Bambu printers. Named after von Mises stress.

**Status (2026-10-24):** design drafted, awaiting approval. No code yet. Next step after approval: the build plan, starting with milestone M0 (app shell on both platforms).

## Where things are

| Path | What |
|---|---|
| `docs/design/2026-10-24-mises-design.md` | **The full design.** Goals, core loop, screens, architecture, components, data model, packaging, tiers, roadmap, decision log. |
| `docs/design/pending-changes.md` | Research and UI proposals waiting for approval before they go into the design. |
| `docs/overview/Mises overview.pdf` | 8-page visual summary with UI screenshots (source: `overview.html`, images in `img/`). |
| `mockups/` | Interactive HTML mockups. `terminal-core-loop.html` is the latest look. |
| `research/README.md` | Index of all research with reading order and trust levels. |
| `.superpowers/` | Mockup preview server's working files (safe to ignore). |

## Key decisions so far

- Product for other people; free tier costs almost nothing per user, paid tier unlocks cloud studies and more agents.
- v1 = the core loop: agent edits on its own Onshape branch → local test → you approve → merge → print.
- Onshape is the master copy; agents iterate on a local build123d copy to protect the 2,500-call yearly allowance.
- Any agent via ACP; one `mises` MCP server; Claude Agent SDK for the orchestrator. No leaked source.
- Simulator is a guide, not a guarantee.
- Look: engineering-drawing style, square corners, greyscale with blue highlights, Bloomberg-style density (pending).
