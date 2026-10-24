# 03 - Claude Agent SDK: programmatic agent configuration

Sources: official public repos, shallow-cloned (read only, nothing executed):
- `SDK_PY` = `/private/tmp/claude-501/-Users-hartigan-Documents-Claude-Projects-cad-harness/8b6e121f-d432-4043-912f-dff97e8094a0/scratchpad/sdk/claude-agent-sdk-python` (v0.2.174, bundles Claude Code CLI 2.1.310: `src/claude_agent_sdk/_version.py`, `_cli_version.py`)
- `SDK_TS` = `/private/tmp/claude-501/-Users-hartigan-Documents-Claude-Projects-cad-harness/8b6e121f-d432-4043-912f-dff97e8094a0/scratchpad/sdk/claude-agent-sdk-typescript` (v0.3.310)

**Caveat:** the TypeScript repo has **no source code**. It holds the README, a 97 KB `CHANGELOG.md`, and `examples/session-stores/`. The TS package ships compiled on npm (`@anthropic-ai/claude-agent-sdk`). So the Python repo is the primary source here. TS details below come from its CHANGELOG only.

Key files (Python):
| File | Role |
|---|---|
| `src/claude_agent_sdk/types.py` (2.9k lines) | `ClaudeAgentOptions`, `AgentDefinition`, hook types, MCP configs, message types, control-protocol TypedDicts |
| `src/claude_agent_sdk/__init__.py` | `@tool`, `create_sdk_mcp_server`, public exports |
| `src/claude_agent_sdk/query.py` / `client.py` | `query()` (one-shot) and `ClaudeSDKClient` (bidirectional, multi-turn) |
| `src/claude_agent_sdk/_internal/transport/subprocess_cli.py` | Finds the CLI, **builds the argv**, spawns it, does stdio JSON-lines I/O |
| `src/claude_agent_sdk/_internal/transport/__init__.py` | Abstract `Transport` (connect/write/read_messages/close/end_input/is_ready), so you can plug in a custom transport |
| `src/claude_agent_sdk/_internal/query.py` | Control protocol: `initialize`, handling of `can_use_tool` / `hook_callback` / `mcp_message`, outbound control requests |
| `src/claude_agent_sdk/_internal/sdk_mcp_bridge.py` | Bridges in-process MCP servers to the CLI's JSON-RPC |

## 1. `ClaudeAgentOptions` (types.py:2326), all fields

TS uses camelCase equivalents (`allowedTools`, `permissionMode`, `settingSources`, `mcpServers`, `canUseTool`, ...).

**Prompt / model**
- `system_prompt: str | SystemPromptPreset | SystemPromptCustom | SystemPromptFile | None`
  - `None` -> `--system-prompt ""`. **The default is an empty system prompt, not Claude Code's.**
  - `str` or `{"type":"custom","prompt":..., "snapshot"?}` -> `--system-prompt`
  - `{"type":"preset","preset":"claude_code"}` -> no flag, so the CLI uses its full Claude Code prompt. Adding `"append"` gives `--append-system-prompt`. `exclude_dynamic_sections` (strips cwd/git/memory so the prompt caches across users) and `snapshot` go through `initialize`.
  - `{"type":"file","path":...}` -> `--system-prompt-file`
- `model`, `fallback_model`, `betas: ["context-1m-2025-08-07"]`
- `thinking: {"type":"adaptive"|"enabled"(budget_tokens)|"disabled", display?}`. `max_thinking_tokens` is deprecated.
- `effort: "low"|"medium"|"high"|"xhigh"|"max"`
- `output_format: {"type":"json_schema","schema":{...}}` -> `--json-schema`. The result appears in `ResultMessage.structured_output`.
- `task_budget: {"total": int}` -> `--task-budget`. This is an API-side token budget the model is made aware of.

**Tools and permissions**
- `tools: list[str] | {"type":"preset","preset":"claude_code"} | None`: the *base set* of built-in tools. `[]` disables all of them.
- `allowed_tools: list[str]`: auto-approve rules (e.g. `"Read"`, `"Bash(git:*)"`, `"mcp__calc__add"`).
- `disallowed_tools: list[str]`: removes tools from the model's context.
- `permission_mode: "default"|"acceptEdits"|"plan"|"bypassPermissions"|"dontAsk"|"auto"` (`auto` means a classifier decides each call).
- `can_use_tool: async (tool_name, input, ToolPermissionContext) -> PermissionResultAllow(updated_input?, updated_permissions?) | PermissionResultDeny(message, interrupt)`. It only fires when the rules evaluate to "ask". The SDK sets `permission_prompt_tool_name="stdio"` behind the scenes (`_configure_can_use_tool`, types.py:2281). It cannot be combined with `permission_prompt_tool_name`.
- `permission_prompt_tool_name`: routes permission prompts to an MCP tool.
- `sandbox: SandboxSettings` (enabled, network, ...): merged into the `--settings` JSON.

**Sessions**
- `continue_conversation`, `resume`, `session_id` (UUID), `fork_session`, `resume_session_at`, `resume_drops_turn`
- `session_store`, `session_store_flush`: external transcript mirroring (`--session-mirror`). There are Redis, S3 and Postgres examples.
- `enable_file_checkpointing`: sets an env var and enables `rewind_files()`.

**Limits:** `max_turns`, `max_budget_usd`, `load_timeout_ms` (60s for initialize)

**Environment and config loading**
- `cwd`, `add_dirs` (-> `--add-dir`), `env` (merged over `os.environ`, with `CLAUDECODE` stripped and `CLAUDE_CODE_ENTRYPOINT=sdk-py` set), `cli_path` (by default it uses the bundled binary at `src/claude_agent_sdk/_bundled/claude`, then `which claude`, then known install paths)
- `settings: str` (path or inline JSON) -> `--settings`, the highest-priority "flag settings" layer
- `setting_sources: list["user"|"project"|"local"] | None`. `None` loads all sources, which is the CLI default. `[]` gives isolation mode. You **must include `"project"`** to load CLAUDE.md and `.claude/agents|skills|commands`.
- `skills: list[str] | "all" | None`. The SDK turns this into `Skill(name)` allow rules and defaults `setting_sources` to `["user","project"]`. It is a context filter, not a sandbox.
- `plugins: [{"type":"local","path":...}]` -> `--plugin-dir`
- `mcp_servers: dict[name, Stdio|SSE|Http|Sdk config] | str | Path`, plus `strict_mcp_config`
- `agents: dict[str, AgentDefinition]` (section 2), `hooks: dict[HookEvent, list[HookMatcher]]` (section 3)

**Output stream / debugging**
- `include_partial_messages` (`StreamEvent`s), `include_hook_events`, `forward_subagent_text`, `verbatim_prompts` (no `@path` expansion or slash-command dispatch: injection hardening)
- `stderr: Callable[[str],None]`, `max_buffer_size`, `user`, `extra_args: dict[str, str|None]`. `extra_args` passes any CLI flag through as `--flag value`, which is the escape hatch for new flags.

**TS-only options seen in the CHANGELOG:** `pathToClaudeCodeExecutable`, `allowDangerouslySkipPermissions` (required before switching to `bypassPermissions`), `managedSettings` (host-supplied policy), `agentProgressSummaries`, `promptSuggestions`, `includeSystemMessages`, `debugFile`, plus `startup()`/`prewarm()`. The `unstable_v2_*` session API was removed in favor of `query()` with an `AsyncIterable<SDKUserMessage>`.

## 2. Programmatic subagents: `AgentDefinition` (types.py:108)

```python
@dataclass
class AgentDefinition:
    description: str          # when the main agent should delegate (what the Agent tool shows)
    prompt: str               # the subagent's system prompt
    tools: list[str] | None = None            # allowlist; None means inherit all
    disallowedTools: list[str] | None = None
    model: str | None = None  # "sonnet"|"opus"|"haiku"|"inherit" or a full model id
    skills: list[str] | None = None
    memory: "user"|"project"|"local" | None = None
    mcpServers: list[str | dict] | None = None   # names of servers, or inline {name: config}
    initialPrompt: str | None = None
    maxTurns: int | None = None
    background: bool | None = None
    effort: EffortLevel | int | None = None
    permissionMode: PermissionMode | None = None
```
- **Transport:** agents are *not* passed as a CLI flag. The source says "Agents are always sent via initialize request (matching TypeScript SDK) / No --agents CLI flag needed" (subprocess_cli.py:~726). `_internal/client.py:123-149` serializes each definition with `asdict()`, drops `None` fields, and puts them in `initialize.agents`. This is also why the SDK always uses `--input-format stream-json`: it lets large configs go over stdin.
- **Invocation:** the main model calls the built-in **Agent** tool (formerly "Task") with the subagent's name. Subagent messages come back with `parent_tool_use_id` set to the spawning tool_use id. `TaskStarted/TaskProgress/TaskNotification/TaskUpdated` system messages track lifecycle, and `stop_task(task_id)` cancels.
- **Mapping to `.claude/agents/*.md`:** the frontmatter keys correspond to the same fields: `name` (= the dict key), `description`, `tools`, `model`, and the markdown body is `prompt`. Example in the repo, `SDK_PY/.claude/agents/test-agent.md`:
  ```
  ---
  name: test-agent
  description: A simple test agent for SDK testing
  tools: Read
  ---
  You are a simple test agent...
  ```
  Filesystem agents load only when `setting_sources` includes `"project"` (`examples/filesystem_agents.py`, which checks `SystemMessage(subtype="init").data["agents"]`). Programmatic and filesystem agents coexist. `e2e-tests/test_agents_and_settings.py` covers the precedence and loading behavior.

## 3. Hooks and in-process tools

**HookMatcher** (types.py:840): `matcher: str | None` (a tool-name regex such as `"Write|Edit"`; `None` matches everything), `hooks: list[HookCallback]`, `timeout: float | None` (default 60s).

**Callback signature** (types.py:828):
```python
async def cb(input: HookInput, tool_use_id: str | None, context: HookContext) -> HookJSONOutput
```
- `HookEvent` (Python Literal): `PreToolUse, PostToolUse, PostToolUseFailure, UserPromptSubmit, Stop, SubagentStop, PreCompact, Notification, SubagentStart, PermissionRequest`. The output types also cover `SessionStart, UserPromptExpansion, PostToolBatch, PermissionDenied, Pre/PostModelSwitch, Elicitation(Result), CwdChanged, FileChanged, WorktreeCreate, MessageDisplay`, so the CLI's hook surface is wider than the typed event list.
- Every input includes `session_id, transcript_path, cwd, permission_mode`. Tool events add `tool_name, tool_input, tool_use_id`, plus `agent_id/agent_type` when the hook fires inside a subagent.
- Output (`SyncHookJSONOutput`): `continue_`, `stopReason`, `suppressOutput`, `decision: "block"`, `reason`, `systemMessage`, `hookSpecificOutput`. The SDK renames `continue_`/`async_` to `continue`/`async` (`_convert_hook_output_for_cli`, _internal/query.py:113). There is also `AsyncHookJSONOutput` (`async_: True`) for deferring work.
  - PreToolUse: `permissionDecision: allow|deny|ask|defer`, `permissionDecisionReason`, `updatedInput`, `additionalContext`
  - PostToolUse: `additionalContext`, `updatedToolOutput`
  - UserPromptSubmit: `additionalContext`, `sessionTitle`
- **Wire mechanics** (_internal/query.py:309-364): each callback gets an id `hook_N`. `initialize.hooks = {Event: [{matcher, hookCallbackIds, timeout}]}`. When a hook fires, the CLI sends a `hook_callback` control request with that id, and the SDK runs the Python function and replies. Matchers for the same event are dispatched **concurrently**.

**Custom in-process tools** (`__init__.py:277`, `:517`):
```python
@tool("add", "Add two numbers", {"a": float, "b": float}, annotations=ToolAnnotations(readOnlyHint=True))
async def add(args): return {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}  # "is_error": True on failure
calc = create_sdk_mcp_server(name="calc", version="1.0.0", tools=[add])
ClaudeAgentOptions(mcp_servers={"calc": calc}, allowed_tools=["mcp__calc__add"])
```
- `input_schema` can be a `{name: type}` dict, a TypedDict, or raw JSON Schema. `Annotated[type, "desc"]` adds a parameter description.
- The tool name the model sees is `mcp__<server>__<tool>`.
- Config is `{"type":"sdk","name":..., "instance": McpServer}`. `instance` is stripped before the config goes into `--mcp-config`. The CLI then sends JSON-RPC to the SDK as `mcp_message` control requests (`server_name`, `message`), and `sdk_mcp_bridge.py` runs them against the in-process server. No extra process is spawned.

## 4. How the SDK talks to the CLI

**Process:** `SubprocessCLITransport` (subprocess_cli.py) spawns `claude` with anyio. stdin and stdout are newline-delimited JSON. stderr goes to the `stderr` callback. The SDK checks the CLI version on startup unless `CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK` is set.

**argv built by `_build_command` (subprocess_cli.py:570-795).** This list is effectively the runtime's headless configuration surface:
```
claude --output-format stream-json --verbose
  --system-prompt <s> | --system-prompt-file <p> | --append-system-prompt <s>
  --tools <csv|""|default>     --allowedTools <csv>     --disallowedTools <csv>
  --max-turns N  --max-budget-usd X  --task-budget N
  --model M  --fallback-model M  --betas <csv>
  --permission-prompt-tool <name|stdio>  --permission-mode <mode>
  --continue | --resume=<id>  --session-id=<uuid>  --fork-session
  --resume-session-at=<uuid>  --resume-drops-turn=<uuid>  --session-mirror
  --settings <json|path>  --add-dir <d> (repeat)  --mcp-config '{"mcpServers":{...}}'  --strict-mcp-config
  --include-partial-messages  --include-hook-events
  --setting-sources=user,project,local   --plugin-dir <p> (repeat)
  --thinking adaptive|disabled  --max-thinking-tokens N  --thinking-display <d>  --effort <lvl>
  --json-schema '<schema>'
  --<extra_args...>
  --input-format stream-json        # always last; the prompt arrives on stdin, never in argv
```
Values that start with a dash use the `--flag=value` form, which guards against flag injection. Env vars it sets: `CLAUDE_CODE_ENTRYPOINT=sdk-py`, `CLAUDE_AGENT_SDK_VERSION`, `PWD`, `CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING`, W3C `TRACEPARENT` (OpenTelemetry), and optionally `CLAUDE_AGENT_SDK_CLIENT_APP` (User-Agent).

**stream-json protocol (stdin to the CLI):**
- User turn: `{"type":"user","message":{"role":"user","content":<str|blocks>},"parent_tool_use_id":null,"session_id":"default"}` (client.py:249). Setting `client_composed: true` (via `verbatim_prompts`) suppresses `@`-expansion.
- SDK-to-CLI control: `{"type":"control_request","request_id":"req_N_<hex>","request":{"subtype":...}}`. Subtypes: `initialize` (hooks, agents, skills, excludeDynamicSections, systemPromptSnapshot, forwardSubagentText), `interrupt`, `set_permission_mode`, `set_model`, `rewind_files`, `mcp_status`, `mcp_reconnect`, `mcp_toggle`, `stop_task`, `get_context_usage`. Each gets a reply of `{"type":"control_response","response":{"subtype":"success"|"error","request_id",...}}`.
- CLI-to-SDK control requests: `can_use_tool` (tool_name, input, permission_suggestions, blocked_path, tool_use_id, agent_id), answered with `{behavior:"allow", updatedInput, updatedPermissions}` or `{behavior:"deny", message, interrupt}`. Also `hook_callback` (callback_id, input, tool_use_id) and `mcp_message` (server_name, JSON-RPC message, answered with `{mcp_response}`).

**stdout messages:** `system/init` (session_id, tools, mcp_servers, model, agents, skills, slash_commands, plugins, permissionMode, claude_code_version), then `assistant`, `user` (tool results), `stream_event` (partial messages), `system/task_*`, `system/session_state_changed`, and `result`. The `result` message carries subtype, num_turns, total_cost_usd, usage, model_usage, structured_output, permission_denials, and errors. These are parsed in `_internal/message_parser.py`.

**Client API:** `query(prompt, options)` is a one-shot async iterator. `ClaudeSDKClient` adds `connect/query/receive_response/interrupt/set_permission_mode/set_model/rewind_files/reconnect_mcp_server/toggle_mcp_server/stop_task/get_mcp_status/get_context_usage/get_server_info/disconnect` and works as an async context manager.

## 5. Notable examples (`SDK_PY/examples/`)
| File | Shows |
|---|---|
| `quick_start.py` | Minimal `query()`, then options (system_prompt, max_turns, allowed_tools) |
| `agents.py` | Inline `AgentDefinition`s (code-reviewer, doc-writer, analyzer and tester together) |
| `filesystem_agents.py` | Loading `.claude/agents/*.md` via `setting_sources=["project"]` and checking the init message |
| `hooks.py` | PreToolUse deny on a Bash pattern, UserPromptSubmit `additionalContext`, PostToolUse review, `permissionDecision` allow/deny, `continue_=False` to stop |
| `tool_permission_callback.py` | `can_use_tool` that allows, denies, or rewrites input (redirects writes to a safe path) |
| `mcp_calculator.py` | `@tool` with `create_sdk_mcp_server`, tools allowlisted as `mcp__calc__*` |
| `system_prompt.py` | None / string / preset / preset+append |
| `setting_sources.py` | Default vs `[]` (isolation) vs `["user","project"]` |
| `tools_option.py` | `tools=[...]`, `[]`, and the preset |
| `streaming_mode*.py` | Multi-turn `ClaudeSDKClient`, interrupts, asyncio/trio/IPython |
| `max_budget_usd.py`, `include_partial_messages.py`, `stderr_callback_example.py` | Budget cap, token streaming, stderr capture |
| `plugin_example.py` with `plugins/demo-plugin/` | Local plugin (`.claude-plugin/plugin.json`, `commands/greet.md`) |
| `session_stores/` (Python and TS) | Redis, S3 and Postgres `SessionStore` implementations with conformance tests |

`e2e-tests/` (e.g. `test_agents_and_settings.py`, `test_hooks.py`, `test_sdk_mcp_tools.py`, `test_dynamic_control.py`, `test_structured_output.py`) works as executable documentation of expected runtime behavior.

## Takeaways for a harness
1. The SDK is a thin wrapper. All agent behavior lives in the CLI, which is configured by **argv flags, the `initialize` control payload, and the filesystem layers** (`setting_sources`). Anything the CLI accepts can be reached with `extra_args`.
2. The defaults differ from interactive Claude Code: the SDK sends an **empty system prompt** unless you choose the `claude_code` preset, and you need `setting_sources` including `"project"` to pick up CLAUDE.md, agents, and skills.
3. There are three gating layers, from cheapest to most flexible: `tools`/`disallowed_tools` decide what exists, `allowed_tools`/`permission_mode` decide what is auto-approved, and `can_use_tool` and PreToolUse hooks handle dynamic decisions and input rewriting.
4. Subagents are pure data (`AgentDefinition` = .md frontmatter + body) sent in `initialize`. Their per-agent tools, model, MCP servers, maxTurns, and permissionMode make them a natural unit for specialized workers.
