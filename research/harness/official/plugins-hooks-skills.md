# Claude Code plugin conventions (from claude-plugins-official)

Root for all paths: `~/.claude/plugins/marketplaces/claude-plugins-official/` (abbreviated `M/`). Internal plugins live in `M/plugins/`, partner ones in `M/external_plugins/`. Agents (`*/agents/*.md`) are out of scope here.

## 1. Plugin manifest, marketplace, layout

**plugin.json** (`.claude-plugin/plugin.json`, this exact location is required: `M/plugins/plugin-dev/skills/plugin-structure/references/manifest-reference.md`)
- Only `name` is required (kebab-case, regex `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`). The minimal manifest is `{"name": "hello-world"}` (same file).
- Documented metadata: `version` (semver), `description`, `author` {name, email, url} or a string, `homepage`, `repository` (string or {type,url,directory}), `license` (SPDX), `keywords`.
- Component path overrides: `commands`, `agents` (string or array; they add to the defaults and do not replace them), plus `hooks` and `mcpServers` (a path or an inline object). Paths must be relative, start with `./`, use no `../`, and use forward slashes. Defaults are `./commands/`, `./agents/`, `./skills/`, `./hooks/hooks.json` and `./.mcp.json`.
- What real manifests contain: most Anthropic plugins have only name, description and author (e.g. `M/plugins/hookify/.claude-plugin/plugin.json`). Some add `version`, `homepage`, `license` or `keywords` (`M/plugins/claude-security/.claude-plugin/plugin.json`, `M/plugins/cwc-makers/.claude-plugin/plugin.json`). None of the plugins sets component paths explicitly. They all rely on auto-discovery.
- `userConfig` (not in the plugin-dev reference): `M/plugins/code-modernization/.claude-plugin/plugin.json` declares typed options (`type` string/boolean, `title`, `description`, `default`, `options` enum). Scripts read them as `CLAUDE_PLUGIN_OPTION_<KEY>` env vars (`M/plugins/code-modernization/scripts/telemetry.sh:32`, `telemetry.py:176`).

**marketplace.json** (`M/.claude-plugin/marketplace.json`)
- Top-level keys: `$schema` (`https://anthropic.com/claude-code/marketplace.schema.json`), `name`, `description`, `owner` {name,email}, `renames` (old slug → new slug, for auto-migration), and `plugins[]` (315 entries).
- Per-entry keys and how many entries use each: name/description/source (315), category (301), homepage (299), author (233), displayName/strict/version (14), lspServers (12), skills (3), tags (3, e.g. `"community-managed"`), keywords (1).
- `source` forms: a relative string `"./plugins/x"` (53), `{source:"url", url, sha}` (164), and `{source:"git-subdir", url, path, ref, sha}` (98).
- `strict: false` plus an explicit `skills: ["./a","./b"]` array lets a repo that has no plugin.json still ship skills. Paths are relative to `source.path`, and each skill is registered as `<plugin>:<skill>` (`M/README.md`, "Skill-bundle plugins"). LSP plugins also use `strict: false` (see §5).
- `name` is an immutable slug. Use `displayName` to relabel a plugin and `renames` for an unavoidable rename (`M/README.md`).

**Layout** (`M/plugins/example-plugin/README.md`, `M/plugins/plugin-dev/skills/plugin-structure/SKILL.md`)
```
plugin/
├── .claude-plugin/plugin.json   # required
├── .mcp.json                    # optional MCP servers
├── skills/<name>/SKILL.md       # preferred for BOTH model-invoked skills and slash commands
├── commands/*.md                # legacy slash-command layout (loaded identically to skills)
├── agents/*.md
├── hooks/hooks.json (+ scripts) # some use hooks-handlers/ for the scripts
├── scripts/                     # helper scripts referenced via ${CLAUDE_PLUGIN_ROOT}
└── README.md, LICENSE
```
- `commands/*.md` is legacy. It loads identically to `skills/<name>/SKILL.md`, and new plugins should use `skills/` (`M/plugins/example-plugin/README.md`, `M/plugins/example-plugin/commands/example-command.md`).
- All names are kebab-case. Every `.md` in commands/ and agents/ and every `SKILL.md` in skills/ subdirectories is auto-discovered (`plugin-structure/SKILL.md` lines 44, 114, 140, 168).
- No plugin has a `.lsp.json` file. LSP configs are declared inline in marketplace.json (§5).
- Per-project plugin state/config lives in `.claude/<plugin>.local.md` (YAML frontmatter + markdown body, gitignored) (`M/plugins/plugin-dev/skills/plugin-settings/SKILL.md`). Real uses: `.claude/ralph-loop.local.md` (`M/plugins/ralph-loop/hooks/stop-hook.sh`) and `.claude/hookify.*.local.md` (`M/plugins/hookify/core/config_loader.py:210`).

## 2. Skills

**Frontmatter fields seen** across the 27 `M/plugins/*/skills/**/SKILL.md` files (plus 6 in `M/external_plugins/{telegram,imessage,discord}/skills/`, 33 in total):

| Field | Count | Example |
|---|---|---|
| `name`, `description` | 27/27 | all |
| `version` | 13 | `plugin-dev/skills/*/SKILL.md` (`0.1.0`) |
| `allowed-tools` | 4 | `claude-security/skills/claude-security/SKILL.md` (YAML list), `math-proof/skills/siege/SKILL.md` (comma string with `Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/ledger.py *)`) |
| `argument-hint` | 3 | `math-proof/skills/solo/SKILL.md`, `example-plugin/skills/example-command/SKILL.md` |
| `disable-model-invocation: true` | 2 | `math-proof/skills/{siege,solo}/SKILL.md` (user-only slash command) |
| `disallowed-tools` | 2 | same, `WebSearch, WebFetch, AskUserQuestion` |
| `user-invocable: true` | 1 (+ externals) | `project-artifact/skills/project-artifact/SKILL.md`, `external_plugins/telegram/skills/access/SKILL.md` |
| `tools` | 2 | `claude-code-setup/.../claude-automation-recommender/SKILL.md`, `claude-md-management/.../claude-md-improver/SKILL.md` |
| `license` | 1 | `frontend-design/skills/frontend-design/SKILL.md` |

Two variables appear in skills: `${CLAUDE_SKILL_DIR}` (the skill's own directory) and `${CLAUDE_PLUGIN_ROOT}`. siege lists both forms in its `allowed-tools` (`M/plugins/math-proof/skills/siege/SKILL.md:6`).

**How descriptions are written for triggering**
- plugin-dev style: third person, with quoted trigger phrases, e.g. `This skill should be used when the user asks to "create a hook", "add a PreToolUse/PostToolUse/Stop hook", ... or mentions hook events (...)` (`M/plugins/plugin-dev/skills/hook-development/SKILL.md:3`). The rule is stated in `M/plugins/plugin-dev/skills/skill-development/SKILL.md:44,162,384`.
- skill-creator style: "what it does + Use when ...". Descriptions should be a bit "pushy" because Claude tends to undertrigger, and all "when to use" information belongs in the description, not the body (`M/plugins/skill-creator/skills/skill-creator/SKILL.md:67`).
- Newer skills write long single-paragraph descriptions that also give usage and scope limits. Examples: siege embeds `Usage: /math-proof:siege ...`, and project-artifact ends with `Not for single-PR changes or public docs` (`M/plugins/math-proof/skills/siege/SKILL.md:3`, `M/plugins/project-artifact/skills/project-artifact/SKILL.md:3`).

**Progressive disclosure** (`skill-development/SKILL.md:77-84`)
- There are three levels. (1) name+description is always in context (~100 words). (2) The SKILL.md body loads when the skill triggers (<5k words). (3) Bundled resources load on demand, without limit, because scripts can run without being read into context.
- Subdirectories: `references/` (docs loaded as needed; for files over 10k words, put grep patterns in SKILL.md), `scripts/` (deterministic code), `assets/` (output templates that are not loaded into context), and `examples/` (`skill-development/SKILL.md:50-75`). Other subdirectories seen: `templates/` (playground), `evals/` (math-olympiad), `eval-viewer/` and `agents/` (skill-creator), `specs/` and `jobs/` (claude-security).
- Body style: imperative/infinitive form, not second person (`skill-development/SKILL.md:160`).

**Size conventions**
- plugin-dev says 1,500-2,000 words for the body, ideally under 3,000 and never over 5k. Each reference file may run 2,000-5,000+ words (`skill-development/SKILL.md:190,329,340,433`).
- skill-creator says SKILL.md should stay under 500 lines, with a table of contents for any reference over 300 lines (`skill-creator/SKILL.md:90-98`).
- Actual sizes range from 39 lines/162 words (`example-plugin/skills/example-command`) to 732 lines/12,971 words (`math-proof/skills/siege`). The plugin-dev skills themselves run 401-884 lines and 1,600-3,200 words.

## 3. Slash commands

These are `commands/*.md` files (32 of them), or skills with `argument-hint`.
- **Frontmatter counts**: `description` (32), `argument-hint` (21), `allowed-tools` (14), `arguments` (13, all in code-modernization), `disable-model-invocation` (2), `hide-from-slash-command-tool: "true"` (2, ralph-loop). `model` is documented (haiku/sonnet/opus; `M/plugins/plugin-dev/skills/command-development/references/frontmatter-reference.md:141-186`, `M/plugins/example-plugin/commands/example-command.md`), but no shipped command uses it.
- **allowed-tools syntax**: a comma string or a JSON/YAML array. Bash permissions use the scoped form `Bash(git add:*)` (`M/plugins/commit-commands/commands/commit.md:2`). Plugin scripts can be pre-approved as `Bash(${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh:*)` (`M/plugins/ralph-loop/commands/ralph-loop.md:4`). Exact-file permissions look like `Read(.claude/ralph-loop.local.md)` (`M/plugins/ralph-loop/commands/cancel-ralph.md:3`). MCP tools are written `mcp__plugin_<plugin>_<server>__<tool>`, with `*` wildcards allowed but discouraged (`M/plugins/plugin-dev/skills/mcp-integration/SKILL.md:194-218,375`).
- **Arguments**: `$ARGUMENTS` is the whole argument string (`example-command.md`, `feature-dev/commands/feature-dev.md`). `$1`, `$2` are positional (`commit-commands/commands/clean_gone.md`, `command-development/SKILL.md:239`). Named arguments come from `arguments: system` in frontmatter and are then referenced as `$system` (`M/plugins/code-modernization/commands/modernize-assess.md:4-8`).
- **`!` bash interpolation**: the inline form is `` !`git status` ``, which is run and substituted before the prompt reaches Claude (`M/plugins/commit-commands/commands/commit.md:7-10`). There is also a fenced block form, `` ```! `` ... `` ``` ``, used in `M/plugins/ralph-loop/commands/ralph-loop.md:12-14` to run `"${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh" $ARGUMENTS`. The commands that rely on interpolation pre-approve the matching Bash patterns in `allowed-tools`.
- **@file references**: `@path` puts file contents inline, as in `Review @$1`, `@package.json`, or `@${CLAUDE_PLUGIN_ROOT}/templates/report.md` (`command-development/SKILL.md:286-332,595-666`). Shipped uses: `M/plugins/agent-sdk-dev/commands/new-sdk-app.md` and `M/plugins/mcp-tunnels/commands/create-docker-mcp-tunnel.md`.
- **Writing style**: a command is "instructions FOR Claude, not messages to the user" (`command-development/SKILL.md:34-60`). The `description` should stay under 60 characters (line 138). Plugin commands are namespaced as `/plugin:command`, e.g. `/code-modernization:modernize`, `/math-proof:siege`.

## 4. Hooks

**File format**: `hooks/hooks.json` = `{"description": "...", "hooks": {"<Event>": [{"matcher": "...", "hooks": [{...}]}]}}`. The `hooks` wrapper is plugin-specific; settings.json puts the events directly at top level (`M/plugins/plugin-dev/skills/hook-development/SKILL.md`, "Hook Configuration Formats").

**The 7 hooks.json files and what they use**

| Plugin | Events (matcher / `if`) | Script |
|---|---|---|
| `M/plugins/explanatory-output-style/hooks/hooks.json` | SessionStart | `bash "${CLAUDE_PLUGIN_ROOT}/hooks-handlers/session-start.sh"` |
| `M/plugins/learning-output-style/hooks/hooks.json` | SessionStart | same pattern |
| `M/plugins/ralph-loop/hooks/hooks.json` | Stop | `hooks/stop-hook.sh` |
| `M/plugins/hookify/hooks/hooks.json` | PreToolUse, PostToolUse, Stop, UserPromptSubmit (no matcher = all), `timeout: 10` | `python3 .../hooks/<event>.py` |
| `M/plugins/security-guidance/hooks/hooks.json` | SessionStart (`timeout:180`), UserPromptSubmit, PostToolUse `Edit\|Write\|MultiEdit\|NotebookEdit`, PostToolUse `Bash` + `if: "Bash(git*commit*)"` / `"Bash(git*push*)"` / `"Bash(gt *)"` with `asyncRewake`, Stop with `asyncRewake` | `bash sg-python.sh security_reminder_hook.py` |
| `M/plugins/claude-security/hooks/hooks.json` | UserPromptExpansion (matcher `^claude-security:claude-security$`), PostToolUse/PostToolUseFailure `Bash` + `if`, `async`, `asyncRewake`, PermissionRequest `AskUserQuestion` (`timeout:90`) | `sh hooks/hooks.sh <mode>` |
| `M/plugins/code-modernization/hooks/hooks.json` | UserPromptSubmit, Stop, SessionStart, PostToolUseFailure, StopFailure (all `asyncRewake`, `timeout:10`), plus top-level `"modules": ["./register.ts"]` (function hooks, gated by `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`, `M/plugins/code-modernization/hooks/README.md`) | `sh scripts/telemetry.sh <mode>` |

- **Documented event set** (`hook-development/SKILL.md`): PreToolUse, PostToolUse, Stop, SubagentStop, SessionStart, SessionEnd, UserPromptSubmit, PreCompact, Notification. Shipped plugins also use events the skill does not list: PostToolUseFailure, StopFailure, PermissionRequest and UserPromptExpansion. None of the 7 files uses SubagentStop, PreCompact or SessionEnd, though security-guidance handles a SubagentStop event if one arrives (`security_reminder_hook.py:2349`).
- **Hook entry fields seen**: `type`, `command`, `timeout` (seconds; documented defaults are 60 for command hooks and 30 for prompt hooks), `if` (permission-rule syntax that filters on tool input, e.g. `Bash(git push *)`), `async`, `asyncRewake` (runs in the background, and exit 2 wakes the model), `rewakeMessage`, `rewakeSummary` (`M/plugins/security-guidance/hooks/hooks.json`). The comment at `security_reminder_hook.py:2865-2880` explains that every matching `if` spawns its own process, so the script deduplicates by `tool_use_id`.
- **Matchers**: exact names, `A|B`, `*`, or a regex such as `mcp__.*__delete.*`. Matching is case-sensitive, and an omitted matcher means "all" (`hook-development/SKILL.md`, "Matchers"; `M/plugins/hookify/hooks/hooks.json`).
- **Command vs prompt hooks**: plugin-dev calls `type:"prompt"` the recommended option (an LLM evaluates `$TOOL_INPUT`; supported on Stop, SubagentStop, UserPromptSubmit and PreToolUse; `hook-development/SKILL.md:20-40`). All 7 shipped hooks.json files use `type:"command"` only. security-guidance does its LLM review inside the command script with the Agent SDK instead (`security_reminder_hook.py:1-30`).
- **Paths**: always quote them, as in `"${CLAUDE_PLUGIN_ROOT}/..."`. Python hooks also add `CLAUDE_PLUGIN_ROOT` to `sys.path` (`M/plugins/hookify/hooks/pretooluse.py:12-15`). Other env vars: `$CLAUDE_PROJECT_DIR`, `$CLAUDE_ENV_FILE` (SessionStart only; append `export X=Y` lines to it, as in `M/plugins/plugin-dev/skills/hook-development/examples/load-context.sh`), `$CLAUDE_CODE_REMOTE`, and `$CLAUDE_PLUGIN_DATA` (a persistent per-plugin data dir: `M/plugins/claude-security/hooks/hooks.sh`, `M/plugins/code-modernization/scripts/telemetry.sh:58`).

**Input contract** (JSON on stdin): `session_id`, `transcript_path` (JSONL), `cwd`, `permission_mode`, `hook_event_name`, plus event-specific fields: `tool_name`, `tool_input`, `tool_response`, `tool_use_id`, `stop_hook_active` (`hook-development/SKILL.md`, "Hook Input Format"; `M/plugins/security-guidance/hooks/security_reminder_hook.py:2836-2840,2346`; `M/plugins/ralph-loop/hooks/stop-hook.sh` reads `.session_id` and `.transcript_path`).

**Output contract**
- Common fields: `continue`, `suppressOutput`, `systemMessage` (shown to the user; plugin-dev describes it as a message for Claude).
- Context injection uses `{"hookSpecificOutput": {"hookEventName": "<Event>", "additionalContext": "..."}}`. It works on SessionStart (`M/plugins/explanatory-output-style/hooks-handlers/session-start.sh`, `M/plugins/learning-output-style/hooks-handlers/session-start.sh`, `M/plugins/security-guidance/hooks/ensure_agent_sdk.py:989`) and on PostToolUse (`security_reminder_hook.py:2975-2978`).
- PreToolUse decisions: `hookSpecificOutput.permissionDecision: allow|deny|ask`, plus `updatedInput` (`hook-development/SKILL.md`; `M/plugins/hookify/core/rule_engine.py:72-78`).
- Stop: `{"decision":"block","reason":"<fed back to Claude as the next prompt>","systemMessage":"..."}` (`M/plugins/ralph-loop/hooks/stop-hook.sh` end; `rule_engine.py:66-70`).
- PermissionRequest: `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"deny","message":"..."}}}` (`M/plugins/claude-security/hooks/hooks.py:303-312`).
- Telemetry: `{"metrics":{...}}` with keys matching `^[a-z][a-z0-9_]{0,39}$`, bool or number values, and a cap of 20 keys. `rewakeSummary` overrides the static one per run (`security_reminder_hook.py:246-262`).
- asyncRewake delivery: the model sees `stderr || stdout`, and only the first `{`-prefixed stdout line is parsed as JSON (`security_reminder_hook.py:264-290`).

**Exit codes**: 0 means success, and stdout JSON is parsed. 2 is a blocking error: stderr goes to Claude, and on asyncRewake it means "findings, wake the model". Any other code is a non-blocking error (`hook-development/SKILL.md`, "Exit Codes"). Conventions in shipped plugins:
- Fail open with exit 0 on internal errors. hookify always exits 0 and reports errors via `systemMessage` (`M/plugins/hookify/hooks/pretooluse.py:50-60`). ralph-loop deletes a corrupt state file and exits 0.
- Never exit 2 from a launcher failure on an asyncRewake hook, or Stop will loop (`M/plugins/security-guidance/hooks/sg-python.sh`, last block). Check `stop_hook_active` to avoid recursion (`security_reminder_hook.py:2355`).
- claude-security appends `|| true` to its commands (`M/plugins/claude-security/hooks/hooks.json`).
- Caveat: plugin-dev's `examples/validate-bash.sh:26` writes the deny JSON to stderr and exits 2. If the official docs are right that JSON is parsed only on exit 0, that JSON would be ignored. Prefer stdout JSON with exit 0, as hookify does.

**Plugins worth reading closely**
- **Output styles**: a SessionStart command `cat`s a heredoc containing the entire style prompt as `additionalContext` and exits 0. That is the whole plugin (`M/plugins/explanatory-output-style/hooks-handlers/session-start.sh`).
- **ralph-loop**: `/ralph-loop` runs setup via `` ```! `` and writes `.claude/ralph-loop.local.md` (frontmatter: iteration, max_iterations, completion_promise, session_id). The Stop hook parses the transcript JSONL for the last assistant text and looks for `<promise>TEXT</promise>`. If the loop is not done, it bumps the iteration and returns `decision:block` with `reason` set to the original prompt. It isolates sessions by comparing `session_id` (`M/plugins/ralph-loop/hooks/stop-hook.sh`, `M/plugins/ralph-loop/commands/ralph-loop.md`).
- **hookify**: one generic Python dispatcher per event loads user rules from `.claude/hookify.*.local.md`. A rule's frontmatter has `name`, `enabled`, `event` (bash/file/stop/prompt/all), `pattern` or `conditions[{field, operator, pattern}]`, and `action` (warn/block), and the markdown body is the message. Matched rules are translated into the output shape each event expects (`M/plugins/hookify/core/rule_engine.py:55-95`, `M/plugins/hookify/examples/dangerous-rm.local.md`, `require-tests-stop.local.md`).
- **security-guidance**: UserPromptSubmit captures a git baseline with `git stash create`. PostToolUse on edits runs regex patterns and returns `additionalContext`, deduplicated per file and rule. PostToolUse on Bash commits and pushes, and Stop, run an LLM diff review asynchronously and exit 2 with the findings on stderr. Kill switch: `SECURITY_GUIDANCE_DISABLE=1`. `sg-python.sh` is a portable interpreter-finding shim (`M/plugins/security-guidance/hooks/security_reminder_hook.py:1-40,2809-2990`).

## 5. MCP and LSP configs

**MCP** (`.mcp.json` at the plugin root, or `mcpServers` in plugin.json; `M/plugins/plugin-dev/skills/mcp-integration/SKILL.md`)
- Two top-level shapes ship. One is a flat `{"<server>": {...}}` (`M/plugins/example-plugin/.mcp.json`, `M/external_plugins/github/.mcp.json`, `linear`, `playwright`). The other wraps servers in `{"mcpServers": {...}}` (`M/external_plugins/context7/.mcp.json`, `discord`, `telegram`, `imessage`, `fakechat`).
- Types:
  - stdio: `command`, `args`, `env` (`M/external_plugins/playwright/.mcp.json` uses `npx @playwright/mcp@latest`; `serena` uses `uvx`; `terraform` uses `docker run -i --rm -e TFE_TOKEN=${TFE_TOKEN}`).
  - http: `type:"http"`, `url`, `headers` (`M/external_plugins/github/.mcp.json` sends `Authorization: Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}`).
  - `sse` and `ws` are documented too (`mcp-integration/examples/sse-server.json`, `SKILL.md:144-153`).
- Bundled servers run from the plugin directory, e.g. `"args": ["run","--cwd","${CLAUDE_PLUGIN_ROOT}","--shell=bun","--silent","start"]` (`M/external_plugins/discord/.mcp.json`).
- Variable expansion: `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PROJECT_DIR}`, and any user env var `${VAR}` in `command`, `args`, `env`, `url` and `headers`. Secrets come from env, never literals (`mcp-integration/examples/stdio-server.json`, `SKILL.md:167-190`). HTTP/SSE servers that have no headers rely on OAuth, which Claude Code handles (`M/external_plugins/linear/.mcp.json`, `SKILL.md:243-256`).
- Tool naming: `mcp__plugin_<plugin>_<server>__<tool>` (`SKILL.md:194-200`).
- MCPB bundles (a separate format) use `${__dirname}` and `${user_config.<key>}` (`M/plugins/mcp-server-dev/skills/build-mcpb/references/manifest-schema.md:43-63`).

**LSP**: no `.lsp.json` files exist. The 12 `*-lsp` plugin directories contain only README.md and LICENSE (e.g. `M/plugins/typescript-lsp/`). Their configuration is inline in `M/.claude-plugin/marketplace.json` as `lspServers`, with `strict: false`:
```json
"pyright": {"command": "pyright-langserver", "args": ["--stdio"],
            "extensionToLanguage": {".py": "python", ".pyi": "python"}}
```
Fields are `command`, optional `args`, `extensionToLanguage` (file extension → LSP language id), and optional `startupTimeout` in ms (120000 for jdtls and kotlin-lsp). The binary must already be on PATH, and each plugin's README gives install instructions (`M/plugins/typescript-lsp/README.md`).
