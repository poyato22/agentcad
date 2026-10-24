# CAD Harness: Integration Research (Onshape + AI agents)

Researched 2026-10-24. Sources are official docs unless marked "community". The Onshape endpoint facts come from the live OpenAPI spec (`https://cad.onshape.com/api/openapi`, build `1.222.91980`, server base `https://cad.onshape.com/api/v17`), which is more authoritative and current than the guide pages.

---

## TL;DR

- **The constraint that matters most is Onshape's API allocation.** Free, Standard and EDU Student plans get **2,500 calls per user per year**. Professional gets 5,000 per user per year. An agent loop can burn that in an afternoon. Design for this from day one: meter every call, cache aggressively, use free webhooks rather than polling, and batch work.
- **Auth for a one-person desktop app:** use an OAuth2 app you register yourself, with an `http://localhost:<port>` redirect (supported). API keys with HMAC signing are fine to start with. Both count against the allocation. No App Store listing is needed for REST-only use.
- **Everything the harness needs is available over REST.** That includes reading and editing the feature tree, setting variables, branching and merging, glTF/STL/tessellation/PNG geometry, and webhooks. Merging takes a strategy of `MERGE`, `KEEP` or `REPLACE`.
- **Agents:** host any agent over **ACP**. Claude, Codex, Gemini, Cursor, Copilot, OpenCode, Goose and about 40 others are in the ACP registry. Expose the app's capabilities as **one MCP server** and hand it to every agent in `session/new`. Use the **Claude Agent SDK** directly only where you need hooks, budgets or in-process tools.

---

# PART 1: Onshape

## 1.1 Authentication

| | API keys | OAuth2 app |
|---|---|---|
| Identity | Authenticates the *app*, acting as the user who made the key | Authenticates app and user, with per-user grants |
| Creation | My Account > Developer, or dev-portal.onshape.com. **Personal accounts are capped at 2 active keys** (company accounts have no cap). A key works only on the stack where it was created. | Developer settings (dev portal): register the app, receive a client ID and secret, set the redirect URL |
| Wire format | `Authorization: Basic base64(access:secret)` (docs say "local testing only"), or **HMAC-SHA256**: headers `Date` (must be within 5 minutes), `On-Nonce` (at least 16 alphanumeric chars, unique), `Authorization: On <AccessKey>:HmacSHA256:<sig>`. The signature input is method, nonce, date, content-type, path and query joined by newlines, **lowercased**, HMAC'd with the secret, then base64-encoded. **Re-sign on 307 redirects.** | `Authorization: Bearer <token>`. Authorize at `https://oauth.onshape.com/oauth/authorize`, token at `https://oauth.onshape.com/oauth/token`. Access token lasts **60 minutes**. The refresh token lasts as long as the grant, and each refresh returns a new refresh token. |
| Redirect | n/a | **`http://localhost:<port>` is explicitly allowed for installed desktop apps.** `urn:ietf:wg:oauth:2.0:oob` (copy-paste) is also supported. If you send `redirect_uri` to authorize, send the same value to token. PKCE is not documented. |
| Scopes | A key can be restricted to permission scopes | `OAuth2Read`, `OAuth2Write`, `OAuth2Delete`, `OAuth2ReadPII`, `OAuth2Share`, `OAuth2Purchase` (from the spec's `securitySchemes`). The harness needs Read + Write. Add Delete only to delete scratch branch workspaces. |
| App Store | Not allowed | Required **only** for public distribution, or for Onshape-embedded extensions (a private store entry plus a subscription works for those) |

**Recommendation:** start with API keys plus HMAC (fewest moving parts), and keep them in the Keychain. Move to a user-owned OAuth app with a loopback redirect when you want revocable, scoped credentials. Each spec operation lists its required scope, for example `updatePartStudioFeature` needs `OAuth2Write`.

Sources: https://onshape-public.github.io/docs/auth/ , https://onshape-public.github.io/docs/auth/apikeys/ , https://onshape-public.github.io/docs/auth/oauth/

## 1.2 API limits by plan

Annual call allocation (https://onshape-public.github.io/docs/auth/limits/):

| Plan | Calls per year |
|---|---|
| Free, Standard, EDU Student | **2,500 per user** |
| EDU Educator, Pro, Discovery | 2,500 per company |
| Professional | 5,000 per user in the company |
| Enterprise / Enterprise GOV | 10,000 per full user |
| EDU Enterprise | 10,000 per enterprise |

- **Counted:** API-key calls, private OAuth app calls (charged to the app owner), and Explorer calls made with key or OAuth auth. Only **2xx and 3xx responses** count.
- **Not counted:** public App Store OAuth apps, the browser/mobile clients, **webhook deliveries**, and any 4xx or 5xx response.
- **When exhausted:** requests return **HTTP 402**. Short-term rate limits return **HTTP 429**; limits vary per endpoint and are not published. Extra calls can be bought from sales.
- **Caveat on the official Labs MCP:** its server instructions say every tool call consumes "the same allocation consumed by API keys and other private OAuth apps", even though it is an App Store subscription. So do not assume App Store apps are free in practice. Its `get_api_usage` tool reports used/limit/cycle, which is useful for checking real numbers.
- **What 2,500/year means:** about 7 calls a day. One agent iteration (update feature, re-read features, fetch glTF, fetch PNG) costs 4 or more calls. The harness must treat Onshape as a **sync target, not a scratchpad.**

Budget tactics:
1. Run a local call ledger with hard per-agent caps.
2. Cache everything by `microversionId`: geometry, features and renders for a given microversion are immutable.
3. Use `updateFeatures` (batch) instead of N single updates.
4. Use one `evalFeatureScript` call to pull many measurements at once.
5. Use one sync glTF export instead of separate faces and edges calls.
6. Use webhooks (free) instead of polling.
7. Consider letting agents iterate in a **local** kernel (OCCT via build123d or CadQuery) and pushing only accepted designs to Onshape. Budget for a paid plan if Onshape stays the live working copy.

## 1.3 Feature tree, variables, editing

All paths are relative to `/api/v17`. `{wvm}` is `w`, `v` or `m` (workspace, version or microversion). Only workspaces are writable.

- **Read the tree:** `GET /partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/features`
  - Query params: `rollbackBarIndex=-1`, `includeGeometryIds`, `noSketchGeometry`, `featureId` (repeatable), `configuration`, `elementMicroversionId`.
  - Returns feature JSON plus `rollbackIndex`, `serializationVersion` and `sourceMicroversion`.
- **Feature specs:** `GET .../featurespecs` returns parameter schemas for every feature type, including custom ones. Feed these to agents.
- **Whole Part Studio as FeatureScript:** `GET .../featurescriptrepresentation`. This is compact, LLM-friendly context.
- **Add a feature:** `POST /partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/features`, body `BTFeatureDefinitionCall-1406 {feature, sourceMicroversion, rejectMicroversionSkew, ...}`.
- **Replace a feature:** `POST /partstudios/d/{did}/w/{wid}/e/{eid}/features/featureid/{fid}`. This is a **full redefine**. Always resend `featureType` and `name`; omitting them blanks them and causes errors.
- **Patch parameters on several features:** `POST .../w/{wid}/e/{eid}/features/updates`, body `BTUpdateFeaturesCall-1748 {features[], updateSuppressionAttributes}`. This updates *only the supplied parameters* and can toggle suppression. **It is the best way to change extrude depth.**
- **Delete a feature:** `DELETE .../features/featureid/{fid}`.
- **Move the rollback bar:** `POST .../features/rollback`.
- **Feature JSON shape:** `BTMFeature-134 {featureType, name, parameters[], suppressed}`. Sketches use `BTMSketch-151`.
  - Parameters: `BTMParameterQuantity-147 {parameterId, expression:"2 in"}`, `BTMParameterQueryList-148` (queries via `BTMIndividualQuery-138 {deterministicIds|queryString}` or `BTMIndividualSketchRegionQuery-140 {featureId}`), `BTMParameterBoolean-144`, `BTMParameterEnum-145 {enumName, value}`.
  - Extrude parameters: `bodyType`, `operationType`, `entities`, `endBound`, `depth`.
  - **Extrude depth over REST: yes.** Set the `depth` parameter's `expression`. The expression can reference a variable (`#wall`).
- **Concurrency control:** send `sourceMicroversion` with `rejectMicroversionSkew=true`. A write against a stale microversion is then rejected. Use this whenever more than one agent or human edits.
- **Evaluate FeatureScript:** `POST .../featurescript`. Lambda only, read-only (`OAuth2Read`). Use it for measurements, tight bounding boxes, and plane or face IDs.
- **Variable Studio:**
  - Create: `POST /variables/d/{did}/w/{wid}/variablestudio`.
  - Set: `POST /variables/d/{did}/w/{wid}/e/{eid}/variables` (`setVariables`). Body items are `{name, expression, type, description}`. `type` is UPPERCASE (`"LENGTH"`); mixed case gave a 500 per the Onshape forum.
  - Read: `GET /variables/d/{did}/{wv}/{wvid}/e/{eid}/variables` (`includeValuesAndReferencedVariables`, `configuration`).
  - Scope and references: `variablestudioscope` and `variablestudioreferences`.
  - **Part Studio `#var` features are changed through the features endpoints, not `/variables`** (Onshape staff on the forum).
- **Validate before you build:** create the feature once in the UI, read it back with `getPartStudioFeatures`, and template from that JSON. The docs warn that parameters change between API versions. Since V14, an unrecognized API version falls back to the newest one.

Sources: https://onshape-public.github.io/docs/api-adv/featureaccess/ , https://onshape-public.github.io/docs/api-adv/fs/ , https://cad.onshape.com/glassworks/explorer/ , forum https://forum.onshape.com/discussion/21103 , changelog https://onshape-public.github.io/docs/changelog/

## 1.4 Geometry for the live viewer

| Need | Endpoint | Notes |
|---|---|---|
| Fast mesh for the viewer | `GET /partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/gltf` (also `/parts/.../partid/{pid}/gltf`) | **Synchronous.** Returns a 307 to the file; follow it and re-attach or re-sign auth. Params: `angleTolerance`, `chordTolerance`, `maxFacetWidth`, `outputSeparateFaceNodes`, `outputFaceAppearances`, `precomputedLevelOfDetail`, `partId`. Best value per call. |
| Raw tessellation JSON with face IDs | `GET .../tessellatedfaces`, `.../tessellatededges`, `.../sketches/{sid}/tessellatedentities` | Coordinates in metres. Options: `outputVertexNormals`, `outputFacetNormals`, `outputIndexTable`, `outputTextureCoordinates`, `outputFaceAppearances`. Use when you need face-ID picking for "agent, fillet *this* edge". |
| STL for slicing | `GET .../stl` (sync, 307) | `mode` text or binary, `units`, `grouping` (zip), tolerances |
| Parasolid | `GET .../parasolid` (sync) | OCCT cannot read Parasolid. Use STEP for the local kernel and FEA meshing (gmsh). |
| STEP / OBJ / higher-quality glTF | `POST /partstudios/d/{did}/{wv}/{wvid}/e/{eid}/export/step` (also `/export/gltf` and `/export/obj`; assemblies likewise) | **Asynchronous.** Poll `GET /translations/{tid}` until `requestState` is `DONE` or `FAILED`, or subscribe to the `onshape.model.translation.complete` webhook. Download via `Documents/downloadExternalData` using `resultExternalDataIds`. `storeInDocument=true` saves the result as a blob tab instead. Generic `POST .../translations` takes `formatName` (list formats with `GET /translations/translationformats`). |
| Rendered images (agent vision, thumbnails) | `GET .../shadedviews` (Part Studio, part, assembly) | `viewMatrix` = 12 numbers or `top`/`front`/`iso`-style named views, plus `outputWidth`, `outputHeight`, `pixelSize`, `edges`, `useAntiAliasing`. Returns base64 PNG. The jarvis MCP uses this so Claude can see the part. |
| Mass properties | `GET .../massproperties`, `.../bodydetails`, `.../boundingboxes` | Mass properties return `microversionId`, which makes a good cache key |

**Viewer strategy:**
- Key the cache on `microversionId`.
- Use sync glTF for display and `tessellatedfaces` only for picking.
- Render locally (three.js, Metal or SceneKit) rather than calling `shadedviews`, except when an agent needs Onshape's own render.

Source: https://onshape-public.github.io/docs/api-adv/translation/ , spec ops `exportPartStudioGltf`, `getPartStudioFaces`, `getPartStudioShadedViews`, `createPartStudioExportStep`.

## 1.5 Change notifications and live sync

- **Register:** `POST /webhooks {url, events[], documentId|companyId, options:{collapseEvents}, isTransient, filter, name}`.
  - Onshape immediately POSTs `webhook.register`. **Your endpoint must return 200 or registration is cancelled.** Have the listener running before you register.
  - **The URL must be reachable from the public internet.** HTTPS needs a CA-signed certificate; self-signed fails.
  - Each register call creates a new ID (no de-duplication). Unregister with `DELETE /webhooks/{id}`. Webhooks are garbage-collected after inactivity unless `isTransient=false`.
  - Test with `POST /webhooks/{id}/ping`.
- **Relevant events** (full list from the spec's `createWebhook` callbacks):
  - `onshape.model.lifecycle.changed` (any model edit)
  - `.changed.externalreferences`, `.changed.namedviews`
  - `.createversion`, `.createworkspace`, `.deleteworkspace`, **`.mergeworkspace`**, `.restoreworkspace`, `.createelement`, `.deleteelement`, `.metadata`, `.updateworkspaceunits`
  - `onshape.model.translation.complete`
  - `onshape.document.lifecycle.*`, `onshape.comment.*`, `onshape.revision.created`, `onshape.task.*`, `onshape.team.*`, `webhook.ping|register|unregister`
  - rel-1.221 (2026-09-18) added 19 events.
- **Payload:** `{timestamp, event, documentId, workspaceId, elementId, versionId, webhookId, messageId, data, ...}`. Treat it as a "something changed" signal, then fetch the new state.
- **Webhooks are free** (they don't count against the allocation). A desktop app has no public URL, so tunnel one in: Cloudflare Tunnel or Tailscale Funnel to a localhost listener, or relay through the Pi (already reachable with DuckDNS). Verify `webhookId`, since there is no documented signature header.
- **Polling fallback:** `GET /documents/d/{did}/{wv}/{wvid}/currentmicroversion` (one call). If it differs from the cached value, re-fetch features and glTF. **Every poll costs budget**, so poll only while the window is focused, with backoff (30 seconds or more). Never poll per frame.
- **Live sync loop:**
  1. Event or poll reports a new microversion.
  2. `getPartStudioFeatures` (one call) updates the tree.
  3. Sync glTF (one call) updates the viewer.
  4. Diff against the agent's last-known `sourceMicroversion`, so agent writes are rejected if a human edited in between.

Source: https://onshape-public.github.io/docs/app-dev/webhook/

## 1.6 Workspaces, versions, branches, merging

Onshape's model:
- A **document** has workspaces (mutable branches) and versions (immutable snapshots).
- Every edit creates a **microversion** in the workspace history.

| Op | Endpoint |
|---|---|
| Create a version (snapshot to branch from) | `POST /documents/d/{did}/versions {documentId, workspaceId, name}` |
| **Branch** (new workspace from a version) | `POST /documents/d/{did}/workspaces` body `BTVersionOrWorkspaceParams {name, versionId, description, ...}` (`OAuth2Write`) |
| List workspaces | `GET /documents/d/{did}/workspaces` |
| Copy a workspace into a new document | `POST /documents/{did}/workspaces/{wid}/copy` |
| **Preview a merge** | `GET /documents/{did}/w/{wid}/mergePreview?sourceType=workspace|version&sourceId=...` |
| **Merge** | `POST /documents/{did}/workspaces/{wid}/merge` body `BTVersionOrWorkspaceMergeInfo {type, id, defaultMergeStrategy, mergeStrategyElementOverrides:{eid:strategy}}`. `BTMergeStrategy` is one of `MERGE`, `KEEP`, `REPLACE`. |
| Delete a scratch branch | `DELETE /documents/d/{did}/workspaces/{wid}` (`OAuth2Delete`; V17 returns 404 if the workspace is missing) |

Merge semantics (UI help):
- `MERGE` is available only for Part Studios and assemblies, and only when both sides changed that tab.
- `REPLACE` takes the source; `KEEP` keeps the target.
- There is no cherry-picking of individual features.

**Agent pattern:**
1. Snapshot a version.
2. Each agent gets its own branch workspace.
3. The agent edits there; the viewer can show any branch.
4. Show the human `mergePreview` plus a diff of the feature lists.
5. On approval, merge with `MERGE`.
6. Delete the branch.

The official Labs MCP's `create_geometry` does exactly this: it branches a workspace from the configured version, adds the feature, and deletes the branch by default.

Sources: https://cad.onshape.com/help/Content/Document/merging.htm , spec ops `createWorkspace`, `mergePreview`, `mergeIntoWorkspace`.

## 1.7 Existing MCP servers

| Server | What it is | Tools / approach | Auth | License / status | Reuse? |
|---|---|---|---|---|---|
| **Onshape Labs FeatureScript MCP** (official, PTC) | Remote HTTP MCP at `https://fs-mcp.labs.onshape.app/mcp`. Announced 2026-08-11 as Onshape Labs early access. Requires subscribing to "Onshape Labs FeatureScript MCP" in the App Store. | Tools (from the connected server in this environment): `test_featurescript` (lambda eval, which also discovers `libraryVersion`), `test_feature` (compiles a full feature in a real Feature Studio using a "trojan" no-op feature), `create_geometry` (branch workspace, write Feature Studio, add feature; `clean` deletes the branch by default), `create_feature_studio`, `list_feature_studios`, `get_featurescript`, `put_featurescript` (overwrites the whole studio), `search_featurescript_documentation`, `read_featurescript_notes` / `edit_featurescript_notes` (persistent lessons learned), `get_api_usage`, `set_api_allocation`, `whoami`, `logout` | OAuth (Onshape sign-in) | Proprietary hosted service. Consumes your API allocation. | **Yes, for FeatureScript authoring.** Plug it into agents as-is. It does *not* do generic feature-tree edits, variables or export. |
| **hedless/onshape-mcp** (community) | Python stdio server, about 156 stars, pushed 2026-09 | **45 tools:** documents and search, sketches (rect/circle/line/arc), extrude/revolve/thicken/fillet/chamfer/patterns/boolean, assemblies plus mates, interference checks, `get_variables`/`set_variable`, `get_features`, FS eval and bounding box, export STL/STEP/Parasolid/glTF/OBJ | API keys (env) | **No LICENSE file**, so all rights reserved by default. You can run it but cannot copy its code. | Use it as a reference for endpoint coverage only. |
| **ReshefElisha/jarvis-onshape-mcp** (community) | Claude Code plugin (Python via `uv`), about 175 stars, pushed 2026-04 | Structured "truth" result on every mutation (`{ok,status,feature_id,error_message,changes:{bbox,mass,part count deltas},hints}`), `render_part_studio_views` (shaded PNGs as image content), `compare_to_reference`, drawing OCR, `list_entities` with normals, Variable Studio upsert, `write_featurescript_feature`, vision-decompose skill | API keys (Keychain) | MIT, but its NOTICE says it contains portions derived from hedless, which has no license. Treat the code as unclear provenance. | **Copy the ideas, not the code:** mutation results with geometric deltas, render-after-write, hint rotation |
| **altendky/onshape-mcp** (community) | Rust binary or `npx onshape-mcp`, Apache-2.0, early development, active (pushed 2026-10-24) | Sketch, extrude, export STL, screenshots, FeatureScript; writes files locally | **User-owned OAuth app with `http://localhost:18338/callback`**, or API keys | Apache-2.0 | A good reference for the loopback-OAuth desktop flow. Code is reusable with attribution. |

**Recommendation:** build your own Onshape adapter inside the app. It gives you budget metering, caching, branch-per-agent and microversion concurrency control, none of which the community servers have. Expose it through your app's MCP server (Part 2). Optionally attach the official Labs MCP for FeatureScript work.

Sources: https://www.onshape.com/en/blog/featurescript-mcp-server-enables-text-code-cad , https://github.com/hedless/onshape-mcp , https://github.com/ReshefElisha/jarvis-onshape-mcp , https://github.com/altendky/onshape-mcp

## 1.8 Embedded Onshape extension vs standalone app

- **Extension model:** an HTTPS page in an iframe inside Onshape.
  - Locations: element tab, element right panel, context menus, document-list panel. **Only one element tab per app.**
  - Onshape passes `documentId`, `workspaceId`, `elementId`, `server`, `companyId`, `userId` and `clientId` as query params.
  - It talks to Onshape over postMessage. You must first send `keepAlive` and validate the origin against `server`.
  - Action extensions time out after 180 seconds.
  - **It must be an OAuth app** with a (private) store entry and a subscription. Company admins can instead assign it to users in Developer Settings.
- **Fit:** an iframe cannot reach the local GPU sim, the USB or LAN printer, or local agent processes without a cloud backend plus a public HTTPS host. Its calls still go through your OAuth app, so they still count unless the app is public.
- **Recommendation:** **standalone desktop app** as the primary product. Later, optionally add a thin right-panel extension that deep-links "open this element in the harness" using the IDs Onshape passes.

Source: https://onshape-public.github.io/docs/app-dev/extensions/

## 1.9 Client libraries

| Library | Status |
|---|---|
| `onshape-public/onshape-clients` (official, multi-language) | **Archived** (last push 2023-03). Do not use. |
| `onpy` (kyle-tennison, high-level modeling in Python, built with LLM use in mind) | **Archived** 2026-01. Fine to read, not to depend on. |
| `onshape-rest-api-client` (PyPI, generated) | Tracks Onshape releases (1.216.x seen) |
| `onshape-sdk-zachsharma`, `onshapy` | Small community SDKs |
| **Best option: generate your own client** from `https://cad.onshape.com/api/openapi` | openapi-typescript or openapi-generator for TS, Swift or Python. Wrap it with your own HMAC/OAuth signer, 307 handling, budget ledger and cache. |

Sources: https://github.com/onshape-public/onshape-clients , https://github.com/kyle-tennison/onpy , https://pypi.org/project/onshape-rest-api-client/

---

# PART 2: Making the app work with Claude and any agent

## 2.1 Claude Agent SDK

- **Packages:** TypeScript `@anthropic-ai/claude-agent-sdk` (0.3.310 on npm today) and Python `claude-agent-sdk` (0.2.174).
- **How it runs:** it **runs the Claude Code binary as a subprocess** and gives you Claude Code's agent loop, tools and context management as a library. For other languages, run `claude -p --output-format json` (or stream-json) as a subprocess.
- **Capabilities:**
  - **Agent loop and built-in tools** (Read/Edit/Write/Bash/WebSearch...). `tools: []` removes all built-ins, so the agent sees only your MCP tools. That is good for a CAD-only agent.
  - **Subagents** via the `agents` option (`AgentDefinition`):
    - Fields: `description`, `prompt`, `tools`, `disallowedTools`, `model`, `mcpServers`, `maxTurns`, `background`, `effort`, `permissionMode`, `skills`.
    - They run in the background by default (`status:"async_launched"`) and can nest: depth defaults to 3 (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`), concurrency defaults to 20 (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`).
    - `maxBudgetUsd` caps total spend, including subagents.
    - A `Workflow` tool (TS) handles dozens to hundreds of agents.
  - **MCP:**
    - Server types are stdio, `http` (streamable), `sse`, and **in-process SDK servers**: `createSdkMcpServer({name, version, tools:[tool(name, desc, zodSchema, handler, {annotations})]})` in TS, `@tool` plus `create_sdk_mcp_server` in Python.
    - Tool names are `mcp__<server>__<tool>`.
    - Tool search is on by default and defers schemas, so large tool sets are fine.
    - Handlers return `content` (text, image, audio, resource, resource_link), `structuredContent` (TS only for in-process tools) and `isError`.
    - `readOnlyHint: true` allows parallel calls.
    - Tool output over 25k tokens is spilled to a file (`MAX_MCP_OUTPUT_TOKENS`).
  - **Permissions:**
    - Evaluation order: hooks, deny rules, ask rules, mode, allow rules, `canUseTool` callback.
    - Modes: `default`, `dontAsk`, `acceptEdits`, `bypassPermissions`, `plan`, `auto`.
    - **`acceptEdits` does not auto-approve MCP tools.** Use `allowedTools: ["mcp__cad__*"]`.
    - MCP tools marked `_meta["anthropic/requiresUserInteraction"]` **always** reach the approval callback, even with allow rules or bypass mode. Put that on `start_print`, `merge_branch` and `delete_*`.
    - `setPermissionMode()` changes the mode mid-session.
  - **Hooks:** PreToolUse, PostToolUse and others. These are good for the budget ledger (deny Onshape-mutating calls when the cap is hit) and for audit logs.
  - **Sessions:** `resume` and fork. Subagent transcripts persist and can be resumed by `agentId`.
  - **Streaming for a multi-agent console:**
    - `includePartialMessages: true` yields raw API `stream_event`s (text and `input_json_delta`) **for the main agent only**.
    - Subagent messages arrive as complete messages tagged with `parent_tool_use_id` and (TS) `agent_id`, which matches the `task_started`/task events.
    - The `system:init` message lists tools and MCP server status (`connected|pending|failed|needs-auth`).
    - Build console lanes keyed by `agent_id`.
- **Auth and policy:**
  - Use an API key, or Bedrock, Vertex or other cloud providers.
  - Quote from the docs: **"Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK."**
  - Branding: "Claude Agent" or "X Powered by Claude" are fine. Never "Claude Code" or Claude Code-styled visuals.
  - Governed by the Anthropic Commercial Terms.
- **Hosting:** run a Node or Python sidecar process next to the app (the app may be Swift). The app talks to the sidecar over a local socket or stdio JSON. The sidecar owns `query()` sessions, translates SDK messages into the app's console events, and routes `canUseTool` to native approval dialogs.

Sources: https://code.claude.com/docs/en/agent-sdk/overview , https://code.claude.com/docs/en/agent-sdk/mcp , https://code.claude.com/docs/en/agent-sdk/custom-tools , https://code.claude.com/docs/en/agent-sdk/permissions , https://code.claude.com/docs/en/agent-sdk/streaming-output , https://code.claude.com/docs/en/agent-sdk/subagents

## 2.2 Agent Client Protocol (ACP): the "any agent" path

- **What it is:** a protocol that is to agents what LSP is to languages. It is JSON-RPC 2.0, and **local agents run as subprocesses of the client over stdio**. Remote transport (HTTP/WebSocket) is still in progress. ACP reuses MCP's JSON types and uses Markdown for user-readable text.
- **Methods (v1):**
  - Agent side: `initialize`, `authenticate`, `session/new`, `session/prompt`, `session/cancel` (notification). Optional: `session/load` (replays history), `session/resume`, `session/set_mode`, `logout`, plus session list, delete and close (stabilized).
  - **Client side (your app implements these):**
    - `session/request_permission`: options carry `kind` of `allow_once`, `allow_always`, `reject_once` or `reject_always`; the outcome is `selected{optionId}` or `cancelled`.
    - `fs/read_text_file` and `fs/write_text_file`: the app mediates file I/O, so you can sandbox.
    - `terminal/create|output|wait_for_exit|kill|release`
    - `elicitation/create`
  - **`session/update` notifications** stream agent, user and thought chunks, **tool calls** and updates, **plans**, available commands, mode changes, notices and compactions.
- **Tool calls:**
  - Fields: `toolCallId`, `title`, `kind` (`read|edit|delete|move|search|execute|think|fetch|switch_mode|other`), `status` (`pending|in_progress|completed|failed`), `content[]`, `locations[]`, `rawInput`, `rawOutput`.
  - Content can be a content block, a **`diff {path, oldText, newText}`**, or a live `terminal {terminalId}`.
- **MCP hand-off:** **`session/new {cwd (absolute), mcpServers:[...]}`**. Every agent must support stdio MCP (`{name, command, args, env}`). `http` and `sse` are used only if the agent advertises `mcpCapabilities.http` or `.sse`. **This is how your CAD tools reach any agent.**
- **Drafts to watch (do not ship by default):**
  - **ACP v2 draft** (2026-07-20): patchable updates by ID, structured file-change diffs replacing oldText/newText, permission requests with `title` and `subject`, updates outside turns. v1 stays supported. Implement v1 and gate v2 behind negotiation.
  - **MCP-over-ACP RFD:** the client declares an MCP server `{"type":"acp", serverId}` and the agent sends `mcp/message` over the same ACP pipe, so no separate MCP process or port is needed. It targets MCP 2026-07-28.
  - **Subagents RFD:** `claude-agent-acp` already emits child sessions and `subagent_update` when the client declares `clientCapabilities.subagents`, which is ideal for a multi-agent console.
- **Registry:** `https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json` lists 43 agents today, each with an `npx`, `binary` or `uvx` distribution, so you can build an "Add agent" picker from it. Examples:
  - `claude-acp` uses `@agentclientprotocol/claude-agent-acp@0.95.0`. The adapter is Apache-2.0 and built on the Claude Agent SDK, so Anthropic's SDK terms still apply.
  - `codex-acp` uses `@agentclientprotocol/codex-acp`.
  - `gemini` uses `@google/gemini-cli --acp`.
  - Others include `cursor`, `github-copilot-cli`, `opencode`, `goose`, `cline`, `junie`, `kimi`, `qwen-code`, `mistral-vibe`, `factory-droid`, `amp-acp` and `devin`.
- **Clients that already embed ACP** (reference implementations to study): Zed, JetBrains, Neovim (CodeCompanion, avante), Emacs agent-shell, VS Code extensions, Obsidian, Qt Creator, Unity, Sublime.
- **SDKs:** TypeScript `@agentclientprotocol/sdk` (2.0.2), plus Python, Rust, Kotlin and Java, all Apache-2.0.

Sources: https://agentclientprotocol.com/get-started/introduction , https://agentclientprotocol.com/protocol/v1/overview , https://agentclientprotocol.com/protocol/v1/session-setup , https://agentclientprotocol.com/protocol/v1/tool-calls , https://agentclientprotocol.com/get-started/agents , https://agentclientprotocol.com/get-started/clients , https://agentclientprotocol.com/get-started/registry , https://agentclientprotocol.com/announcements/acp-v2-draft , https://agentclientprotocol.com/rfds/mcp-over-acp , https://github.com/agentclientprotocol/claude-agent-acp

## 2.3 MCP: exposing the app's own capabilities

- **Spec:** the current version is **2026-07-28**. It is stateless JSON-RPC with per-request capability negotiation.
  - Server features: tools, resources, prompts. Client feature: elicitation.
  - **Extensions:**
    - **Tasks:** asynchronous long-running operations with polling, mid-flight input and durable handles. A perfect fit for FEA solves, async STEP exports and print jobs.
    - **MCP Apps:** inline interactive UI, such as a sim result chart or a filament picker.
    - **Skills over MCP.**
- **Suggested tool surface** (one `mises` server):
  - `onshape.*`:
    - Read: `list_features`, `get_feature_specs`, `get_featurescript_repr`, `get_variables`.
    - Edit: `update_feature_params` (batch), `set_variables`, `add_feature`.
    - Branches: `create_branch`, `merge_preview`, `merge_branch` (requires user interaction).
    - Inspection: `render_view` (returns an image), `measure` (FeatureScript eval).
    - Every mutation returns `{ok, microversion, changes:{bbox, mass, volume}, warnings}`.
  - `geometry.*`: local mesh and STEP ops, section views, interference checks.
  - `sim.*`: `mesh`, `solve` (a Task), `get_results` (structuredContent plus image).
  - `printer.*`: `status`, `slice`, `upload`, `start_print` (requires user interaction), `cancel`.
  - `materials.*`: filament and material lookup.
  - `budget.*`: Onshape calls remaining. Agents should see their own budget.
  - **Resources:** `cad://doc/{did}/w/{wid}/features`, `.../mesh.glb`, `sim://run/{id}/report`. Pass large blobs as `resource_link` or file paths, never inline, because of the 25k-token cap.
- **Transport:** streamable HTTP on `127.0.0.1` with a per-launch bearer token (for ACP agents that support `http`, and for external clients), plus a **stdio shim** (`mises-mcp` binary that proxies to the running app), because stdio is the only MCP transport every ACP agent must support. External agents use the same shim: `claude mcp add mises -- mises-mcp`, Codex `config.toml`, or Cursor `mcp.json`.
- **Safety:**
  - Annotate read-only tools with `readOnlyHint`.
  - Put `_meta["anthropic/requiresUserInteraction"]` on irreversible ones; Claude clients honour it.
  - Use MCP **elicitation** for confirmations, which works with any client.
  - Treat document names, comments and feature names as untrusted input (prompt-injection surface).

Sources: https://modelcontextprotocol.io/specification/latest , https://modelcontextprotocol.io/extensions/overview , https://code.claude.com/docs/en/agent-sdk/custom-tools

## 2.4 Recommended architecture

```
+------------------------------ Desktop app (Swift/Metal or Electron) ------------------------------+
|  Viewer (glTF cache by microversion) | Feature tree | Multi-agent console | Approvals | Sim | Printer |
+-------------------------------------------------^---------------------------------------------------+
                                                  | local IPC (events, approvals)
+-------------------------------- Core services (one process or sidecars) ---------------------------+
| Onshape adapter: signer(HMAC/OAuth) + 307 re-sign + call ledger/caps + microversion cache          |
|                  + branch-per-agent + rejectMicroversionSkew + webhook listener (via tunnel)        |
| Local kernel (OCCT/build123d) | FEA runner (gmsh + solver) | Printer driver | Filament DB           |
+---------------------------^-----------------------------------------------^------------------------+
                            | (single source of truth for tools)            |
              MCP server "mises" (HTTP 127.0.0.1 + stdio shim)         |
                 ^                    ^                                      |
   ACP client (agent host)        External agents                     Agent SDK sidecar (optional)
   spawns any registry agent      (Claude Code, Codex, Cursor)        "Orchestrator" with hooks,
   session/new{mcpServers:[cad]}  connect via stdio shim/HTTP          budgets, in-process tools
   renders session/update,                                             (also exposable via ACP
   handles request_permission,                                          using claude-agent-acp)
   fs/* + terminal/* sandboxed to project dir
```

1. **One domain core, one MCP server.** Internal agents, ACP agents and external agents all use the same tools, and budget, concurrency and safety are enforced in one place.
2. **The ACP client is the default agent host.** The user picks an agent from the registry. The app spawns it with `cwd` set to a per-agent sandbox directory and `mcpServers:[mises]`. Map `tool_call` and `diff` updates to console lanes, `plan` to a checklist, and `request_permission` to native dialogs.
3. **Use the Agent SDK directly only where ACP is too thin:** an orchestrator that needs PreToolUse budget hooks, `maxBudgetUsd`, programmatic subagents (designer, FEA checker, print planner) and in-process tools.
4. **Each agent gets its own Onshape branch.** Merge only after `mergePreview`, a feature diff and human approval. Every write carries `sourceMicroversion` and `rejectMicroversionSkew`.
5. **Sync:** webhooks through a tunnel first (free), slow focused-window polling of `currentmicroversion` as a fallback, and cache keyed on microversion.

## 2.5 Pitfalls

- **API allocation will be exhausted.** 402s end the session. Show a ledger, add per-agent caps, prefer local iteration, and consider a Professional plan. Polling and re-fetching are the silent killers. Only 2xx and 3xx responses count, so failed calls are free, but don't rely on that.
- **The webhook needs a public URL with a CA certificate,** has to answer 200 on register, and must be unregistered (no de-duplication). Use `isTransient=false` only deliberately.
- **307 redirects** on sync exports and some other endpoints: re-sign HMAC for the new URL. HMAC also requires the lowercased signing string and a Date within 5 minutes.
- **Updating a feature with the full-replace endpoint** blanks `featureType`/`name` if you omit them. Prefer `features/updates` for parameter changes.
- **Deterministic face and edge IDs** change after upstream edits. Re-query before using a geometry reference, and use `queryString` or FeatureScript queries where you can.
- **Variable `type` must be uppercase** (`LENGTH`). Part Studio `#var` changes go through features, not `/variables`.
- **FeatureScript `libraryVersion`:** new code must use the current version. Discover it with an eval, as the Labs MCP does, or you get "no features found".
- **ACP is moving fast:** v1 is stable, and the v2 draft and MCP-over-ACP are not. Pin adapter versions from the registry. Stdio MCP is the only guaranteed transport.
- **Permissions:** `acceptEdits` does not cover MCP tools. `bypassPermissions` approves everything, including Bash, and subagents inherit it. Use `allowedTools: ["mcp__mises__*"]` plus a hard gate on irreversible tools.
- **Policy:** do not ship claude.ai subscription login inside the app without Anthropic's approval; use API keys. Do not brand anything "Claude Code". The Agent SDK is under the Commercial Terms.
- **Community MCP licensing:** hedless has no license, and jarvis contains code derived from it. Do not vendor either one. altendky (Apache-2.0) is safe with attribution.
- **Output size:** keep meshes and sim fields out of tool results; return links. In the Python SDK, `structuredContent` and binary resources are dropped for in-process tools; TypeScript supports them.
- **Concurrency:** two agents in one workspace corrupt each other's assumptions. Use branches plus microversion skew rejection.
- **Untrusted content:** Onshape document, comment and feature text and subagent output can carry prompt injection. Never let it auto-trigger printer or merge actions.
