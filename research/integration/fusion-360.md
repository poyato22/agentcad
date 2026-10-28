# Fusion 360 as a Mises CAD backend (research, 2026-10-28)

Confidence note: Autodesk's help.autodesk.com and autodesk.com pages returned 403 or "not found" to my fetcher, so several points rest on search-result snippets and third-party write-ups. Items marked (verify) need a manual check against Autodesk's pages before Mises commits.

## Verdict
Yes for the local path, with conditions. Fusion's desktop API has no documented call quota or rate limit. It runs in-process, so "calls" are function calls, not metered HTTP requests. Nothing I found limits it to a number per year. Mises gets effectively unlimited calls if (a) Fusion Desktop is installed, signed in and running on the user's Mac, (b) the user's license permits the use, and (c) Mises stays on the local API and not the cloud APIs. It is not unlimited for a hosted/headless or multi-user deployment.

## 1. Local API
- Python and C++ scripts/add-ins run inside Fusion on macOS and Windows. Add-ins can run on startup (a `context` argument says whether it loaded at startup or mid-session). Startup timing is fragile: touch the document only after `workspaceActivated`. API calls must happen on Fusion's main thread, so external requests go through a custom event queue.
- I found no documented quota or rate limit for the local API. Throughput is bounded by Fusion's single UI thread and by recompute cost of the timeline.
- Capability (from my knowledge of the API; confirm in the reference): sketches, extrude/revolve/fillet etc. features, user and model parameters, the timeline (roll, edit, delete), bodies, components and occurrences, joints, mass properties (physical properties), and export to STEP, STL, 3MF, F3D and others via ExportManager. GLB export is not something I could confirm (verify).
- Official bridge: Autodesk and Anthropic shipped an Autodesk Fusion MCP Server (April 2026 per a third-party article). It is enabled in Fusion under Preferences > General > API ("Fusion MCP Server" checkbox, with a port shown). It acts on the open session, so Fusion must be running with a document active. It is distributed as a Claude Desktop connector. A separate cloud "Fusion Data MCP Server" covers data/hub access. The official local MCP is built for Claude Desktop; whether another client such as Mises can call its endpoint directly is unconfirmed (verify). Community alternatives exist (AuraFriday/Fusion-360-MCP-Server, faust-machines/fusion360-mcp-server on localhost:9876, others), all unsupported by Autodesk.
- Branching/versions: the API exposes the timeline and document versions (DataFile versions), but no Onshape-style branch/merge. Fusion offers "Manage" versions, milestones and design "branches" in the UI with limited API coverage (verify).

## 2. Cloud (APS)
- Design Automation: 100 calls/minute per app (client ID), HTTP 429 plus Retry-After beyond that; work items have a one-minute-without-heartbeat limit; engine-specific quotas exist (page content not retrievable, verify).
- Fusion Automation API is a premium API billed with Flex tokens (per a third-party pricing aggregator; verify). Data Management API is for hub/file access. I could not confirm that a free personal user can use the paid APIs; treat them as requiring a paid APS/Flex relationship. This path is metered, so it is not "unlimited". It is also unnecessary for Mises.

## 3. Licensing
- Personal use is free for non-commercial use by individuals earning under $1,000 USD/year from it, not for primary employment or company environments. It is a three-year, renewable subscription.
- Documented restrictions: a cap of 10 editable active documents, limited import/export formats (2020 announcement named F3Z, DWG, DXF, IGES, SAT, STEP among restricted exports; the current list is unconfirmed), no advanced CAM, simulation or generative design.
- I found no statement forbidding the API or third-party add-ins on the personal tier, and no statement allowing it either (verify). The format restrictions matter for Mises: if STEP export is blocked, the export step fails.
- Anyone using it for income or clients needs a commercial Fusion subscription, which includes full export.
- Shipping an add-in: Autodesk App Store accepts add-ins (free or paid) under its publisher agreement and review. Mises could also install a small add-in itself into the user's AddIns folder; check Autodesk's developer terms on redistribution (verify).

## 4. Practical
- macOS: supported, and the API works there. Fusion must be open with a signed-in user; there is no headless mode locally. Documents save to the Autodesk cloud, so version history lives there.
- The user's Mises machine becomes the Fusion host, so there is one Fusion session per Mises instance and no server-side farming.

## Trade-offs vs Onshape
Fusion removes the metered call budget and runs at local speed, with strong C++/Python API access to a real feature timeline. It costs you a heavy desktop dependency (open GUI, single main thread, startup fragility), consumer-license risk for commercial users, tight format and document caps on the free tier, no true branch/merge model or REST-style versioning, and weaker server-side/hosted options (paid, metered Design Automation). Onshape is cloud-native with branches, merge and per-document history, but its 2,500-calls/year free cap makes it a poor fit for agents unless users pay.

## 5. Mises "Fusion adapter" architecture
- A small Python add-in (Run on Startup) opens a loopback-only HTTP/WebSocket server on 127.0.0.1 with a random token stored in Keychain, or reuses the official Fusion MCP endpoint if it can be reached.
- Requests are queued and executed on the main thread via a custom event; each handler returns a JSON result (feature IDs, parameter values, error text).
- A Swift `FusionAdapter` implements the same CAD-backend protocol Mises uses for Onshape: list parts, read parameters, propose edit, apply, export, mass properties.
- All writes go through the existing Gate and approvals: agent plans an edit, Mises shows the diff (parameter or timeline change), the user approves, then the adapter applies it. Before each applied edit, save a version (or a timeline marker) so rollback is possible. Read-only calls (metadata, mass properties) skip approval.
- Mises detects whether Fusion is running, launches it if not, and falls back to Onshape or surfaces a clear status.

## Sources
- Autodesk MCP help: https://help.autodesk.com/view/ADSKMCP/ENU/?guid=ADSKMCP_FusionDesktopMcp_connecting_to_the_fusion_mcp_server_html (not fetchable; seen via search)
- Autodesk blog, Claude connector: https://www.autodesk.com/products/fusion-360/blog/how-to-improve-your-fusion-workflow-with-the-claude-desktop-connector/
- Claude connector listing: https://claude.com/marketplace/connectors/autodesk-fusion
- Third-party explainer: https://adam.new/fusion-mcp
- Fusion API writing/debugging: https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/WritingDebugging_UM.htm
- Run-on-startup threads: https://forums.autodesk.com/t5/fusion-api-and-scripts-forum/run-on-startup-addin-problem/m-p/11850484/highlight/true
- Community MCP add-ins: https://github.com/AuraFriday/Fusion-360-MCP-Server , https://github.com/faust-machines/fusion360-mcp-server
- Design Automation rate limits: https://aps.autodesk.com/en/docs/design-automation/v3/developers_guide/rate-limits/da-rate-limits
- Pricing aggregator (unofficial): https://apis.io/plans/autodesk-fusion/autodesk-fusion-plans-pricing/
- Personal use page: https://autodesk.com/products/fusion-360/personal
- Subscription types: https://www.autodesk.com/products/fusion-360/blog/subscription-types/
- 2020 personal-use limits (dated): https://hackaday.com/blog/page/2964/?oldid=1726
