# Reference

[Back to the README](../README.md). Start there for installation and everyday use.

## Installation details

From the repository root, pick one:

```sh
uv tool install .      # regular: installs a copy of the current source
uv tool install -e .   # editable: runs straight from this checkout
```

| | `uv tool install .` | `uv tool install -e .` |
|---|---|---|
| What gets installed | A built copy of the source as it is now | A link back to this checkout |
| After editing the code | Not picked up until you reinstall: `uv tool install --reinstall .` | Picked up on the next run |
| After moving or deleting this checkout | Keeps working | Breaks; reinstall from the new location |
| After changing dependencies in `pyproject.toml` | Reinstall | Reinstall too: editable only covers the code |
| Good for | Everyday use | Developing agent-switch |

Both give the tool its own environment and put `agent-switch` in uv's tool folder
(`~/.local/bin` by default).

### AppImage (Linux)

Each [release](https://github.com/scott1028/agent-switch/releases) has a self-contained AppImage for
x86_64 and aarch64. It includes Python, so you need neither uv nor Python:

```sh
release_tag=v0.1.0-a1b2c3d4  # Replace with a tag from Releases
mkdir -p ~/.local/bin
curl -fLo ~/.local/bin/agent-switch \
  "https://github.com/scott1028/agent-switch/releases/download/$release_tag/agent-switch-${release_tag#v}-x86_64.AppImage"
chmod +x ~/.local/bin/agent-switch
```

- Set `release_tag` to a tag from the releases page. A tag ends with the first eight characters of
  its commit, such as `v0.2.0-a1b2c3d4`, and so does the file name.
- For aarch64, write `aarch64` instead of `x86_64` in the URL.
- The AppImage mounts itself with FUSE. Without FUSE, run it with `APPIMAGE_EXTRACT_AND_RUN=1`.
- It runs from a temporary mount that disappears when agent-switch exits. With `--as-subagent`, the
  agent uses the Python inside that mount, so a command printed with `--no-launch --as-subagent`
  fails when you run it later. Use a uv install for that.

## Quickstart: Strata

[Strata](https://github.com/Niko1221/Strata) serves Qwen3.8-Flash-Next at `http://127.0.0.1:8080`.
agent-switch detects it as llama-server.

```sh
# 1. In the Strata checkout, start the server (--gguf-dir is optional: it reuses GGUF files you already have)
./setup.sh --setup --family qwen --model IQ3_S --gguf-dir /path/to/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF

# 2. In another terminal, start an agent against it
agent-switch claude --url http://127.0.0.1:8080
agent-switch codex --url http://127.0.0.1:8080
```

The `--url` is what points the agent at Strata. Without `--url` and `--provider`, the agent starts
natively (see [Native launch](#native-launch)).

## Tested Strata version

The [Strata quickstart](#quickstart-strata) was tested against
[Niko1221/Strata@6f32ec070f23ced9f50e704d854d775da52591ab](https://github.com/Niko1221/Strata/commit/6f32ec070f23ced9f50e704d854d775da52591ab).

## Model servers and sessions

```sh
agent-switch claude                                  # native: Claude's own model and login (see Native launch)
agent-switch codex --url http://127.0.0.1:11434      # Ollama; provider detected from the URL
agent-switch opencode --provider lmstudio -m qwen/qwen3-8b --context-length 32768
agent-switch pi --url http://127.0.0.1:8000/v1 --no-launch   # print the env and command instead
agent-switch claude --url http://127.0.0.1:8080 --as-subagent  # Claude's model with a local subagent
agent-switch dsh --url http://127.0.0.1:8080         # DeepSeek Harness, web UI
agent-switch dsh -- --profile headless "fix the failing test"   # one task, print the result, exit
agent-switch dsh-tui --url http://127.0.0.1:8080     # DeepSeek Harness TUI (alias: dst)
agent-switch opencode --url a:http://127.0.0.1:8080 --url b:http://127.0.0.1:11434   # two servers, one proxy
```

- Without `--url` or `--provider`, the agent starts natively: it keeps its own model, login and
  config, and agent-switch adds only the shared flags (see [Native launch](#native-launch)).
- `--provider` without `--url` uses that server's usual port. `--provider openai` always needs
  `--url`.

### API keys and storage

- `--api-key` (or `AGENT_SWITCH_API_KEY`) is sent as `Bearer <api-key>`.
- agent-switch remembers it per server in `~/.agent-switch/api_keys.json`, so later runs against
  that server can leave it out.
- `AGENT_SWITCH_HOME` moves `~/.agent-switch`, which also holds the `--persist` and `--no-launch`
  session folders.

### DeepSeek Harness

**dsh** starts its web profile (`dsh web`) unless your arguments name another, such as
`--profile headless`.

- Its `DSH_HOME` moves to a session folder that is deleted when dsh exits (`--persist` keeps it).
  The session cannot see the profiles, plugins or DeepSeek API key in your `~/.dsh`.
- Exception: `AGENTS.md` and `skills/` from your DSH home (`$DSH_HOME` or `~/.dsh`) are linked into
  the session and read through the link.
- dsh replaces any `User-Agent` you pass with `--header`.
- `--as-subagent` is not supported for dsh.

**dsh-tui** (alias **dst**) starts the DeepSeek Harness TUI (`@deepseek-harness-tui/dsh-tui`, tried
with 0.14.0 on dsh 0.1.5-rc.2 and 0.2.0-rc.2) on the same route. Both names share one session folder.

- The TUI keeps its state in `~/.dsh-tui`, so `HOME` and `USERPROFILE` move to the session folder
  together with `DSH_HOME`, and pnpm's store and cache stay inside it. Your own dsh and dsh-tui
  profiles, accounts, history and pnpm store are neither read nor written.
- The same `AGENTS.md` and `skills/` links apply. Because `HOME` moves, `~/.agents/skills` and
  `~/.agents/AGENTS.md` are not visible: put such skills in `~/.dsh/skills` and such instructions
  in `~/.dsh/AGENTS.md`, or either in the project.
- The session folder (`~/.agent-switch/agents/dsh-tui`) is kept by default. Settings changed with
  `/settings`, the TUI's preferences, history and the installed profile survive between runs, and
  `-- --resume <id>` or `-- -c` reopen a session.
- `--no-persist` uses a throwaway folder deleted on exit. The TUI profile is then installed with
  pnpm again on every start (about 9 s and 89 MB in testing).
- The model stays on the agent-switch route, whatever the TUI's model picker saved: each run of
  agent-switch drops the saved `/model` pick. A `--no-launch` recipe you reuse later keeps a pick
  made in between; run agent-switch again to drop it.
- It needs an interactive terminal. `--no-launch` prints the command; on Linux and macOS, its last
  line clears the TUI's inherited handoff variables with `env -u`.

Limits:

- The session is pinned to the dsh backend and the agent-switch model route. These are refused:
  `--profile`, `--from-default-profile`, a `--backend` other than `dsh`, `--compact-at` (the TUI's
  agent presets own compaction), `--as-subagent`, the launcher's own `update`, `migrate`,
  `doctor`, `safe`, `version` and `help` commands, and its CLI commands `cli`, `run`, `-p`,
  `--print`, `session`, `models`, `skills`, `mcp`, `auth`, `capabilities` and `dsh`. To send such a
  word as a prompt, put it after `-- --`. Switching the kernel inside the TUI leaves the route.
- A `--patch` you pass is applied after agent-switch's and replaces whole config blocks, so it can
  override the route. A `dsh-tui` row in it must restate `provider`, `model`, `backend` and the
  `preset`/`workspace`/`sessionId` bindings. agent-switch warns when one is passed.
- On Windows, dsh-tui runs every tool with `danger-full-access` and no approval prompts, whatever
  the permission mode says, so agent-switch requires `--yolo` there. This path is untested.
- From WSL, install dsh-tui and dsh inside WSL. A Windows dsh-tui or dsh is refused: WSL can pass a
  cleared variable to a Windows process only as an empty string, which dsh-tui reads as set.
- dsh-tui binds a socket inside the session folder, and a Unix socket path is cut at 108 bytes on
  Linux and 104 on macOS. agent-switch refuses a session folder too deep for that, so keep
  `AGENT_SWITCH_HOME` short. Editor integrations that reach a running TUI through
  `~/.dsh-tui/inject` (dsh.nvim) do not find it.
- With `HOME` moved, Git no longer finds `~/.gitconfig`, so tools the TUI runs lose your global
  identity and settings (`git commit` reports "Author identity unknown"). Point Git at a config
  yourself, for example with `GIT_CONFIG_GLOBAL` (passed through unchanged), or set the identity per
  repository with `git config user.name` / `user.email`. How SSH finds keys under the moved home is
  untested. Moving the home keeps the TUI's state apart; it does not hide every global
  configuration and is not a sandbox.
- To resume, run `agent-switch dsh-tui -- --resume <id>`. The TUI's own exit hint runs `dsh`
  directly, outside agent-switch.

### Headers

`--header [SERVER:]NAME=VALUE` (repeat it) adds an HTTP header to every request sent to the model
server, for example for a gateway that needs its own auth:

```sh
agent-switch codex --url https://gateway.example/v1 --header "Authorization=...gateway token..."
```

- An `Authorization` header replaces the built-in `Bearer <api-key>`.
- Headers go only to the server named with `--url` (or `--provider`).
- `SERVER:` limits a header to one `--url` server (its `NAME:`, or the URL's host and port). Without
  it, the header goes to every server.

### Multiple servers

Repeat `--url` to use several OpenAI-compatible servers in one session. Each `[NAME:]URL` names its
server. NAME defaults to the URL's host and port (`127-0-0-1-11434`); two servers that end up with
the same name are refused until you write `NAME:`.

With two or more `--url`:

- agent-switch starts a reverse proxy on `127.0.0.1` at a random port. Every agent sees this one
  base URL.
- Every model id becomes `NAME:ID`, so models with the same name stay apart. Each request goes to
  the server that serves its `model`, and `/v1/models` merges the servers' lists.
- The proxy answers only requests that carry its session token (`Authorization: Bearer …` or
  `x-api-key`), and it lives exactly as long as the session.

```sh
agent-switch opencode --url a:http://127.0.0.1:8080 --url b:http://127.0.0.1:11434 \
  --header 'a:Authorization=Bearer ${A_TOKEN}' --api-key b:${B_TOKEN} --model b:qwen3:8b
```

- `--model` may name `NAME:ID` directly. A bare id that two servers both serve fails and lists the
  choices; an id that no server lists goes to the first server, which may load it.
- The default model is the first server's. `--context-length` and model loading apply to the server
  that holds the model.
- Every agent lists every server's models, so you can switch to another server's model inside the
  session. `--model NAME:ID` still picks the launch model and its server.
- Claude Code takes its context-window and auto-compaction values from the launch model only, so a
  model you switch to inside the session keeps those values.
- The proxy only lives while agent-switch runs, so `--no-launch` is refused. `--provider` is refused
  too, because each server's type is detected.
- Each server takes its own `--api-key SERVER:KEY`, remembered for next time. A key without
  `SERVER:` is refused and `AGENT_SWITCH_API_KEY` is ignored, so one server's key never reaches
  another.

Known limits: sampling and reasoning flags are translated for the first server's type only; the
`model` field in a response is the upstream server's own id; and a Windows agent reaching the proxy
from WSL is untested.

### Agent arguments

Put agent arguments after `--`; they reach the agent unchanged, for example
`agent-switch claude -- -p "..."` or `agent-switch codex -- exec "..."`.

- Before `--`, agent-switch accepts only its own options and a positional model (a bare `org/name`
  hub id there is the `--model`).
- A flag agent-switch also sends to the agent (such as `--yolo`) is dropped after `--`, with a note
  on stderr.
- `--yolo`, `--dangerously-skip-permissions` and `--dangerously-bypass-approvals-and-sandbox` are the
  same switch: any spelling works for any agent and turns on that agent's own auto-approve.

### Session history

- `--persist` keeps the agent's agent-switch session folder, so the agent's own resume can reopen
  the session, for example `agent-switch codex --persist -- resume`.
- Without it, codex, pi and dsh run with their whole home moved into a session folder that is
  deleted on exit.
- claude and opencode keep sessions in your own stores (`~/.claude`, `~/.local/share/opencode`), so
  they can always resume.

### Context and compaction

- The context window comes from the server: Ollama `/api/ps`, LM Studio's loaded instance,
  llama-server `meta.n_ctx`, or vLLM `max_model_len`.
- It is capped at 250,000 tokens unless you give `--context-length`. A server that reports no window
  gets 250,000 tokens.
- `--context-length -1` uses the server's window as is, uncapped. If the server reports none,
  agent-switch sets no window and the agent uses its own default.
- agent-switch never starts a model server. By default it may load the `--model` you name into a
  server that is already running. `--no-model-load` makes it a pure client that never loads,
  reloads or unloads anything; the model must already be loaded.
- `--compact-at 0.85` starts the agent's auto-compaction when 85% of the window is used, scaled per
  agent (Claude Code, Codex, OpenCode, Pi and DeepSeek Harness). The range is 0.5–0.95; unset keeps
  each agent's own behavior.
  - Claude Code applies the fraction to its own effective window (the window minus its output
    reserve), so it can only start compaction earlier than its built-in trigger, never later.
  - Codex caps it at 0.9 of the window, so a value above 0.9 acts as 0.9.
  - DeepSeek Harness applies it only to its headless profile (`--profile headless`). Its web profile
    ignores it, and agent-switch warns before starting it.

### Reasoning and sampling

- `--reasoning on|off` overrides the model's chat template for this session.
- `--reasoning-effort <level>` picks a thinking level. The levels are the model's own, so pass one
  its chat template accepts. Unset keeps the template's choice.
- OpenCode also gets `none/low/medium/high/xhigh/max` effort variants for every model
  (`provider/model#xhigh` in its model picker). On OpenCode 2.x a pinned `--reasoning-effort` wins
  over a chosen variant; on 1.x the variant wins.
- `--temperature`, `--top-p`, `--top-k`, `--min-p`, `--repetition-penalty` and `--presence-penalty`
  are set in the agent's own requests, not on the server. Unset keeps the model's recommended
  sampling; a value the agent cannot send is ignored with a warning.
- `--max-tokens` caps one reply. The default is a quarter of the context window, up to 32,000, and
  it is capped at half the window so the conversation keeps room.

## Native launch

Without `--url` or `--provider`, the agent starts natively: it keeps its own model, login and
config, and agent-switch writes nothing into the agent's own config.

- Only the shared flags apply: the MCP flags and `--yolo`.
- Flags that only make sense with a local model server fail with "needs --url or --provider":
  `--model` and a positional model, `--context-length`, `--max-tokens`, `--reasoning`,
  `--reasoning-effort`, `--temperature`, `--top-p`, `--top-k`, `--min-p`, `--repetition-penalty`,
  `--presence-penalty`, `--compact-at`, `--api-key`, `--header`, `--no-model-load`, `--as-subagent`,
  `--persist`/`--no-persist`.
- Arguments after `--` still reach the agent unchanged.

```sh
agent-switch claude
agent-switch opencode --mcp-stdio 'blender:"uv run --directory ~/blender-mcp blender-mcp"' --mcp-env 'blender:BLENDER_MCP_PORT=9876'
```

Here MCP is **added**: the agent keeps its own MCP servers and gets the mounted ones too. Each agent
receives them its own way, and agent-switch writes only into its own session folder:

| Agent | How the mounted MCP servers arrive |
|---|---|
| Claude Code | `--mcp-config=<session>/mcp.json` (the `=` form, no `--strict-mcp-config`) |
| Codex | `-c mcp_servers.<name>=<inline table>` per server; a stdio `env` rides a private 0700 wrapper script, an http header value a uniquely named `AGENT_SWITCH_MCP_HDR_<n>` process env var, so no secret reaches argv |
| OpenCode | `OPENCODE_CONFIG=<session>/opencode.json` holding only `$schema` and `mcp` |
| Pi | `--mcp-config <session>/mcp.json`; pi itself has no such flag and rejects it, so this needs an extension that registers it, such as `pi-mcp-adapter` |
| dsh, dsh-tui, dst | `--patch <session>/agent-switch.patch.yml` with only the MCP `insert` row; without MCP flags, no patch at all |

Native launch always uses a temporary session folder. `--no-launch` uses a fixed
`~/.agent-switch/agents/<agent>-native` folder and prints the env and command instead of starting
the agent.

## Agent aliases

`agent-switch <agent>-<x>` runs your own shell function, alias or script named `<agent>-<x>` as that
agent, with the shared flags: the MCP flags, `--yolo`, and `--url`/`--provider` where they work.

```sh
agent-switch claude-personal
agent-switch codex-personal --mcp-stdio 'blender:"uv run --directory ~/blender-mcp blender-mcp"'
```

- Name lookup: the exact subcommands (`claude`, `codex`, `opencode`, `pi`, `dsh`, `dsh-tui`, `dst`)
  always win. Otherwise `dsh-tui-<x>` is dsh-tui, `dsh-<x>` is dsh, and `claude-<x>`, `codex-<x>`,
  `opencode-<x>` and `pi-<x>` are that agent. Anything else is "No such command".
- The alias replaces only the program name. agent-switch runs
  `bash -ic '<name> "$@"' agent-switch <agent args>`, so the function must pass `"$@"` on to the
  agent; the environment agent-switch builds still reaches it.
- The name must match `[A-Za-z0-9._-]+`, `bash` must be on PATH (aliases are not supported on
  Windows), and the name must exist in `bash -ic` as a function, alias or file.
- An alias whose definition mentions `agent-switch` is refused, because it would call itself.
- Native mode (no `--url`/`--provider`) works for every kind of alias.
- Local model mode works only for `claude-*` and `opencode-*`, whose session settings travel as env
  vars and flags that a forwarding function keeps. With `--url`/`--provider`, these are refused:
  `codex-*` (the function sets its own CODEX_HOME), `pi-*` (PI_CODING_AGENT_DIR) and
  `dsh-*`/`dsh-tui-*` (DSH_HOME/HOME).
- `--as-subagent` is refused for aliases.

## MCP servers

### From the registry

`--mcp NAME` (repeat it) and `--mcp-all` mount MCP servers into one agent session only. They come
from a registry agent-switch owns, `~/.agent-switch/mcp.json` (or `$AGENT_SWITCH_HOME/mcp.json`), in
the common `.mcp.json` shape:

```json
{
  "mcpServers": {
    "context7":  { "command": "npx", "args": ["-y", "@upstash/context7-mcp"] },
    "github":    { "type": "http", "url": "https://api.githubcopilot.com/mcp/",
                   "headers": { "Authorization": "Bearer ${GITHUB_TOKEN}" } },
    "atlassian": { "type": "http", "url": "https://mcp.atlassian.com/v1/mcp", "oauth": true }
  }
}
```

- A stdio server needs `command` and may have `args` and `env`. An http server uses
  `"type": "http"`, `url`, `headers` and `oauth`. Other transports (such as `sse`) are refused.
- Server names must match `[A-Za-z0-9_-]+`.
- `${VAR}` in `args`, `env` values, `url` and `headers` values is expanded from your environment at
  launch; an unset variable stops the launch and names it. Expanded values go only into the
  session's private (0600) config files, never into the printed command.

```sh
agent-switch claude --mcp context7 --mcp github   # just these two, this session only
agent-switch codex --mcp-all                      # every server in the registry
```

### Without a registry entry

- `--mcp-url [NAME:]URL` (repeat it) mounts an http server that another app already runs.
  `--mcp-oauth-url` is for one that signs in with OAuth, and `--mcp-header [NAME:]HEADER=VALUE`
  adds its headers. The name defaults to the URL's host and port (`http://127.0.0.1:8931/mcp`
  becomes `127-0-0-1-8931`).
- `--mcp-stdio [NAME:]COMMAND` starts a command-line server. It splits the command like a shell
  does (so single-quote it) but does not expand `~` (write `${HOME}`). Its name is the command's
  last plain word (`uv run --directory /opt/blender-mcp blender-mcp` becomes `blender-mcp`); give
  `NAME:` if that is wrong.
- Double-quote the whole command to write it as in a terminal:
  `--mcp-stdio 'blender:"uv run --directory ~/blender-mcp blender-mcp"'` runs it as
  `bash -lc 'exec uv run ...'`, so bash expands `~` and `${VAR}` itself. This needs `bash` on PATH
  and allows one command only: `;`, `&&`, `||`, `|` and `&` outside quotes are rejected, so put
  several steps in a script.
- Pi and the dsh-tui/dst TUI move `HOME` to the session folder, so there `~` points at that folder.
  Use an absolute path with them.
- `--mcp-env [NAME:]KEY=VALUE` sets the server's environment; for the same KEY, the later VALUE
  wins. Put secrets in `--mcp-env`, not in args: args are always visible in the server's process
  arguments.
- `NAME:` may be left out of `--mcp-header` and `--mcp-env` only when exactly one server of that
  transport is given. These shortcuts combine with `--mcp` and `--mcp-all`.
- Single-quote header and env values so that agent-switch, not your shell, expands `${VAR}`. A
  literal `Authorization` value stays in the process list for the whole session, because
  agent-switch keeps running until the agent exits, so prefer `'${VAR}'`.

```sh
agent-switch claude --mcp-url http://127.0.0.1:8931/mcp                    # name 127-0-0-1-8931
agent-switch claude --mcp-url tools:http://127.0.0.1:8931/mcp
agent-switch codex  --mcp-url api:http://127.0.0.1:9000/mcp --mcp-header 'api:Authorization=Bearer ${API_TOKEN}'
agent-switch pi     --mcp-oauth-url https://mcp.example.com/mcp
agent-switch opencode --mcp-all --mcp-url tools:http://127.0.0.1:8931/mcp
agent-switch claude --mcp-stdio 'uv run --directory /opt/blender-mcp blender-mcp'  # name blender-mcp
agent-switch claude --mcp-stdio 'blender:"uv run --directory ~/blender-mcp blender-mcp"'  # through bash
agent-switch codex  --mcp-stdio 'ctx7:npx -y @upstash/context7-mcp' --mcp-env 'ctx7:API_KEY=${CTX7_KEY}'
```

### With a local model server

- With `--url`/`--provider`, the mounted servers replace the agent's own MCP servers for the
  session. (Native launch adds them instead; see [Native launch](#native-launch).)
- Claude Code gets `--strict-mcp-config --mcp-config=<session file>`. Codex, OpenCode, Pi and dsh get
  them through their isolated session config; OpenCode also copies the servers from your global
  config that are not mounted, with `enabled: false` (OpenCode 2's `mcp.servers` with
  `disabled: true`; OpenCode 2 reads its global config from `OPENCODE_CONFIG_DIR` when set).
- MCP config inside the project you start from may still load: OpenCode's project `opencode.json`
  outranks the session overlay, for example.
- An `oauth: true` http server is mounted through the pinned `mcp-remote` (needs `npx`). It signs in
  with a browser on first use and keeps the tokens in
  `~/.agent-switch/mcp-auth/mcp-remote-v1/<url-hash>_tokens.json` (delete that file to sign in
  again). It reads its `headers` from a private `~/.agent-switch/mcp-auth/<name>.headers` file
  instead of the command line.
- No MCP flag (`--mcp`, `--mcp-all`, `--mcp-url`, `--mcp-oauth-url`, `--mcp-header`, `--mcp-stdio`,
  `--mcp-env`) can be combined with `--as-subagent`.
- Each stdio server starts once per session, when the agent starts it, and stops when the session
  ends.

## Session skills

`--skill` adds one or more skill folders to an agent session, with or without `--url` or
`--provider`.

- `PATH` is a folder containing `SKILL.md`. Relative paths use the current directory; `~` expands
  to your home directory. The skill name comes from `NAME:`, then the file's frontmatter `name`,
  then the folder name. Names must use lowercase letters, digits, and single hyphens, up to 64
  characters. Repeated names fail.
- Skills cannot be combined with `--as-subagent`. `--skill` is not supported for Codex, Pi,
  dsh-tui, or dst aliases.
- agent-switch puts injected files in its session folder. Unrenamed skills link to their source,
  so edits are visible as the agent reads them. If `NAME:` changes an existing frontmatter name,
  agent-switch copies and updates `SKILL.md`, then links its sibling files; edit the source and
  restart to refresh that copy. If there is no frontmatter name to change, the skill stays linked.
- When a Windows agent runs from WSL, injected skill files are copied because the Windows process
  cannot use the WSL links. `--persist` and `--no-launch` reuse their session folder; a later run
  removes the earlier injected files when you omit `--skill`.
- agent-switch does not install skills into your persistent agent skill list or edit the source
  folder. Codex and Pi native overlays link some of your real config files, so changes those agents
  save through those links can still reach your normal config.

```sh
agent-switch claude --skill /tmp/skills/my-test-skill
agent-switch codex --skill docs:/tmp/skills/guide --skill /tmp/skills/review
agent-switch dsh --url http://127.0.0.1:8080 --skill /tmp/skills/my-test-skill
```

| Agent | How the skill is loaded | Which same-name skill wins |
|---|---|---|
| Claude Code | `--add-dir=<session>/agent-switch`; the skill is in `.claude/skills/<name>`. | With no clash, the injected skill has the bare name. On a clash, your skill keeps that name and the injected skill is `agent-switch:<name>`. |
| Codex | Native mode sets an overlay `CODEX_HOME` linked to your Codex home. Routed mode uses the session `CODEX_HOME`; `-c skills.config` enables the injected path and disables the same-name entry. | The injected skill wins over same-name skills. |
| OpenCode | `skills.paths` in the session `OPENCODE_CONFIG`. | The injected skill wins over global skills, `~/.claude/skills`, `~/.agents/skills`, and project `.opencode/skills` on OpenCode 2.0.3. OpenCode 1.x was not verified. |
| Pi | `<agent dir>/skills`. Native mode sets an overlay `PI_CODING_AGENT_DIR` linked to your Pi home. | The injected skill wins over `~/.pi/agent/skills` and `~/.agents/skills`; project `.pi/skills` and `.agents/skills` win. If a persisted session already has its own unowned `skills` folder, injection stops with an error instead of replacing it. |
| dsh | A `--patch` row sets `skill-filesystem` `customSkillDirs`. | The injected skill wins over `$DSH_HOME/skills` and `~/.agents/skills`; project `.dsh/skills` and `.agents/skills` win. The session row replaces an explicit `skill-filesystem` config in your profile. |
| dsh-tui / dst | `preset-patch` rows add the session folder to the `standard`, `ptc`, and `liangshen` presets. The profile must export `./preset-patch`; the gate checks its runtime target, while its error asks for dsh-tui 0.17 or newer. | Same order as dsh. `cordis`, `minimal`, and other unsupported profile presets do not receive injected skills; agent-switch warns when it knows the selected preset. |

## Origin and porting from upstream

agent-switch started as a port of `unsloth start` from
[unslothai/unsloth@8e11ba15ef59b324b99ad55406e98b0391dcd7db](https://github.com/unslothai/unsloth/commit/8e11ba15ef59b324b99ad55406e98b0391dcd7db)
(AGPL-3.0-only; `NOTICE.md` maps each ported file to its source). It no longer depends on Unsloth at
runtime: it has no Unsloth Studio integration, creates no keys, and reads no `UNSLOTH_*` variables.
A Studio is not detected; named with `--url`, it is treated like any other OpenAI-compatible server.

To bring over a later upstream feature:

1. Diff these upstream files from `8e11ba15e` to the new commit: `unsloth_cli/commands/start.py`,
   `unsloth_cli/_inference.py`, `unsloth_cli/claude_subagent_mcp.py`,
   `unsloth_cli/codex_subagent_mcp.py`, `unsloth_cli/pi_subagent.ts`,
   `studio/backend/utils/coding_agents.py` and `unsloth_cli/tests/`.
2. Port agent-side changes (config writers, launch and install plumbing, WSL/Windows handling,
   subagent bridges) into the matching file listed in `NOTICE.md`. Skip Studio-only paths: `/api/...`
   routes, API-key minting, model loading and download progress, GGUF checks and the managed Node.
   Server-specific behavior belongs in `agent_switch/providers/`.
3. Rename upstream's agent-facing ids:

   | upstream | agent-switch |
   |---|---|
   | `mcp__plugin_unsloth-local-agent_unsloth__unsloth_agent` (and `..._plan_agent`) | `mcp__plugin_local-agent_local__local_agent` (and `..._plan_agent`) |
   | `unsloth_cli.claude_subagent_mcp`, `unsloth_cli.codex_subagent_mcp` | `agent_switch.claude_subagent_mcp`, `agent_switch.codex_subagent_mcp` |
   | `UNSLOTH_CLAUDE_SUBAGENT_*`, `UNSLOTH_CODEX_SUBAGENT_CONFIG`, `UNSLOTH_PI_SUBAGENT_*` | `AGENT_SWITCH_CLAUDE_SUBAGENT_*`, `AGENT_SWITCH_CODEX_SUBAGENT_CONFIG`, `AGENT_SWITCH_PI_SUBAGENT_*` |
   | `UNSLOTH_STUDIO_AUTH_TOKEN`, `UNSLOTH_API_KEY` | `AGENT_SWITCH_AUTH_TOKEN`, `AGENT_SWITCH_API_KEY` |
   | Codex profile `unsloth_api`; OpenCode, Pi and dsh provider `unsloth`; `unsloth.patch.yml` | `agent_switch`; `agent-switch`; `agent-switch.patch.yml` |
   | OpenCode `agent.unsloth`; `.unsloth-parent-overlay.json`; `.unsloth-user-resources.json` | `agent.local`; `.agent-switch-parent-overlay.json`; `.agent-switch-user-resources.json` |

4. Port the matching upstream tests and run them against the fake vLLM-shaped server (`fake_vllm` in
   `tests/conftest.py`). Then update the commit above and `NOTICE.md`.
