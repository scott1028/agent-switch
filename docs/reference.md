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
agent-switch omp --url http://127.0.0.1:8080         # OMP (oh-my-pi) on a session agent dir
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
- Your session history is shared: the session's patch points dsh's session log
  (`session-persistence-jsonl`) at `sessions` in your DSH home and its image store
  (`attachment-local`) at your DSH home, so either side resumes the other's sessions, images
  included. Nothing is created there before dsh writes its first session. A Windows dsh run from
  WSL keeps its own history instead, with a warning.
- The session does not read your own patch layers (`cordis.patch.yml` in your DSH home and in the
  profile's folder there). When one of them sets the `session-persistence-jsonl` or
  `attachment-local` row, for example another `root`, a `compression` or a `dshHome`, agent-switch
  warns: the session keeps to `sessions` in your DSH home with the default settings, so it may not
  see or open sessions your own dsh writes elsewhere. This applies to dsh-tui's `dsh-tui` profile
  folder too.
- dsh replaces any `User-Agent` you pass with `--header`.
- `--as-subagent` is not supported for dsh.

**dsh-tui** (alias **dst**) starts the DeepSeek Harness TUI (`@deepseek-harness-tui/dsh-tui`, tried
with 0.14.0 on dsh 0.1.5-rc.2 and 0.2.0-rc.2) on the same route. Both names share one session folder.

- The TUI keeps its state in `~/.dsh-tui`, so `HOME` and `USERPROFILE` move to the session folder
  together with `DSH_HOME`, and pnpm's store and cache stay inside it. Your own dsh and dsh-tui
  profiles, accounts, TUI state and pnpm store are neither read nor written; your dsh `storages/`
  and `~/.dsh-tui` stay apart too.
- Session history is shared: `DSH_TUI_SESSION_ROOT` is your shell's value when it is not blank,
  else `sessions` in your DSH home, the store native dsh-tui and dsh web share. The image store
  points at your DSH home as for dsh. After you open a session made with another provider, pick
  this route's model with `/model`.
- The same `AGENTS.md` and `skills/` links apply. Because `HOME` moves, `~/.agents/skills` and
  `~/.agents/AGENTS.md` are not visible: put such skills in `~/.dsh/skills` and such instructions
  in `~/.dsh/AGENTS.md`, or either in the project.
- The session folder (`~/.agent-switch/agents/dsh-tui`) is kept by default. Settings changed with
  `/settings`, the TUI's preferences and the installed profile survive between runs.
  `-- --resume <id>` reopens any session in the shared store; `-- -c` continues only the last
  session started from this session folder.
- `--no-persist` uses a throwaway folder deleted on exit; your sessions still land in your store.
  The TUI profile is then installed with pnpm again on every start (about 9 s and 89 MB in
  testing).
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

If dsh or dsh-tui stops at start with "No usable native binding found", see
[Troubleshooting](troubleshooting.md#dsh-and-dst-crash-with-no-usable-native-binding-found).

### OMP (oh-my-pi)

`agent-switch omp` starts OMP, the `omp` command of `@oh-my-pi/pi-coding-agent`. When `omp` is
missing, agent-switch offers to run `bun install -g @oh-my-pi/pi-coding-agent`. It also finds `omp`
in `$BUN_INSTALL/bin` (`~/.bun/bin` when `BUN_INSTALL` is unset or empty) when that folder is not on
your PATH.

With `--url` or `--provider`, omp runs on a session agent dir:

- `PI_CODING_AGENT_DIR` points at `<session>/agent`. `PI_PROFILE` is cleared. `HOME` stays yours.
- omp and Bun fill only unset or empty variables from `.env` files: `.env` in the project, the
  session agent dir, `~/.omp` and `~`, plus the project's `.env.local`, `.env.<NODE_ENV>` and
  `.env.<NODE_ENV>.local` that Bun loads before omp starts (`development` when `NODE_ENV` is
  unset). So agent-switch sets what such a line must not change:
  - `PI_CONFIG_DIR` to the root agent-switch reads (yours, else `.omp`) and `OMP_PROFILE` to
    `default`.
  - When one of them sets `OMP_PROFILE` or `PI_PROFILE` to a named profile, the launch stops
    with the file's name: a profile ignores `PI_CODING_AGENT_DIR`.
  - When one of them, other than the session agent dir's own, sets `PI_CODING_AGENT_DIR` or
    `PI_CONFIG_DIR` (or, in omp's own files, `OMP_CODING_AGENT_DIR` or `OMP_CONFIG_DIR`) and your
    shell does not, the launch stops too: your own omp follows it, but agent-switch reads your
    settings and resources only from the shell's value. A non-empty shell value wins over the
    file, for omp too, so then the line is ignored.
  - On Linux and macOS, when your shell does not set `XDG_DATA_HOME` or `XDG_STATE_HOME`, the
    value your own omp ends up with from these files (the first non-empty one in the order it
    reads them, your omp agent dir's `.env` included) names a folder whose `omp` subfolder
    exists, and you use the default agent dir, the launch stops too: your own omp then keeps its
    sessions there, but agent-switch follows those variables only from the shell.
  In all these cases, export the variable in your shell instead of the `.env` file: agent-switch
  follows a shell value (a profile with `--url`/`--provider`). These checks read the files as
  omp's own parser does: quotes, `#` comments, and no `$VAR` expansion. For the XDG and session
  dir values, `$VAR` and `${VAR}` in the project files Bun loads are filled in from your
  environment, as Bun does. All of them run before agent-switch connects to the model server.
- omp starts with `--model agent-switch/<id>`. The session `models.yml` holds the `agent-switch`
  provider with one row per served model; the session `config.yml` turns off omp's onboarding
  (`startup.setupWizard: false`) and starts at `defaultThinkingLevel: xhigh` where agent-switch
  sets the thinking format. Either key already set in a `--persist` session keeps its value.
- From your omp agent dir (`~/.omp/agent`, `~/.omp/profiles/<name>/agent` for an active profile,
  or your `PI_CODING_AGENT_DIR`) only these are linked: `skills`, `commands`, `rules`, `RULES.md`,
  `prompts`, `extensions`, `instructions`, `hooks`, `tools`, `agents`, `SYSTEM.md`,
  `SYSTEM_TEMPLATE.md`, `AGENTS.md`, `PERSONALITY.md`, `secrets.yml` and `ssh.json`. A real file
  or folder of the same name in a `--persist` session stays the session's own. A file that cannot
  be linked, for example on Windows without the right to make symlinks, is copied, and the next
  run refreshes the copy or removes it with your file. A copy goes to a temporary name first, so
  one that fails part way leaves nothing. When you have a `secrets.yml` of your own and
  `secrets.enabled` is on (in your settings or the session's), agent-switch stops before
  connecting unless the session's `secrets.yml` is a link to yours or a whole copy it made this
  run: omp reads the secret rules only from its agent dir.
- Your session history is shared, so `--resume` and `--continue` see the sessions of native omp,
  and native omp sees the ones made here:
  - `sessions`, `blobs` and `archive` in the session agent dir link to those in your omp agent
    dir, `terminal-sessions` and `custom-session-files` too. On Linux and macOS, with the default
    agent dir, the first three link to `$XDG_DATA_HOME/omp` and the other two to
    `$XDG_STATE_HOME/omp` when that folder exists, as omp does (`.../omp/profiles/<name>` for a
    profile).
  - A folder missing on your side is created there first, so a first run writes into your
    history too.
  - These are always links (a symlink, or a junction on Windows), never copies. When none can be
    made, the launch stops.
  - A real folder of the same name with content, such as the sessions of an earlier `--persist`
    session, is moved to `<name>.agent-switch-old` (`-1`, `-2` and so on when that exists), with
    a note on stderr. An empty one is removed.
  - `PI_CODING_AGENT_SESSION_DIR` follows your own omp. A shell value, and one from a `.env`
    file the session's omp reads too (the project's, `~/.omp/.env` and `~/.env`), reach it as
    they are. When your own omp would take the value from a file only it reads, your omp agent
    dir's `.env` or a named profile's `~/.omp/profiles/<name>/.env` (`OMP_CODING_AGENT_SESSION_DIR`
    counts there too), agent-switch exports that value, so the session writes where your omp
    does.
  - With a named profile, the session's omp also reads `~/.omp/.env`, which your profile does
    not. When that file sets a session dir, agent-switch exports your profile's own value, and
    stops when your profile has none: left unset, omp keeps one folder per project, which no
    value can say.
  - `history.db` (your prompt history and omp's session index) stays the session's own.
  - When you have a `secret-placeholder.key` (in `$XDG_STATE_HOME/omp` or your omp agent dir),
    the session's links to it, so hidden secrets keep the same placeholders. Without one, nothing
    is created on your side and omp makes one in the session when secrets are on. A key that
    cannot be linked, for example across disks on Windows, is copied (0600) instead, as it never
    changes; when that fails too, agent-switch warns and goes on.
  - `/resume` inside the session switches to the session's original model when that model's API
    key is at hand. Native omp opening a session made here warns and falls back to another model.
- Not inherited from your omp agent dir: `config.yml`, `models.yml`, `mcp.json`, the legacy
  `settings.json`, `.env` and your login (`agent.db`).
- Your safety settings do come along, because without them omp approves every tool call:
  `tools.approvalMode`, `tools.approval`, `tools.restrictedFolders`, `mcp.enableProjectConfig`,
  `disabledProviders`, `bash.patterns`, `bash.gitGuard` and `secrets.enabled` are copied from
  your omp settings file (`config.yml`, else `config.yaml`, else the legacy `settings.json`) into
  the session `config.yml` on every launch, and your `mcp.json` `disabledServers` list, minus the
  servers mounted now, into the session `mcp.json`. A value you change inside a `--persist`
  session stays; one you remove from your own file goes from the session too. If that settings
  file exists but cannot be read, agent-switch stops, as omp itself does. `--yolo` still switches
  omp to its yolo approval mode, though restricted folders ask even then.
- omp still reads what lives outside its agent dir: plugins in `~/.omp/plugins` (only there, not
  in an XDG or profile plugins folder), project config such as `.omp/mcp.json` or a project
  `.claude/.mcp.json` (unless your `mcp.enableProjectConfig: false` came along), and `~/.agents`
  skills through `HOME`. Claude Code and Codex user config load only when a project-level
  `enabledProviders` enables them, or for Claude Code when `CLAUDE_CONFIG_DIR` is set: your own
  `enabledProviders` is not copied.

omp reads a config value that starts with `!` as a shell command, and one that is the name of a
set environment variable as that variable's value. So the session files never hold such values:

- The API key (the session proxy's token with several `--url`), `--header` values and mounted
  MCP http header values are written as the names `AGENT_SWITCH_OMP_API_KEY`,
  `AGENT_SWITCH_OMP_HEADER_<n>` and `AGENT_SWITCH_OMP_MCP_<n>`, and the values ride in omp's
  environment, where its `.env` files cannot replace them. omp then sends each one exactly as
  given. An empty value stays empty in the file, and omp leaves that header out. `--no-launch`
  prints these values in its recipe, like any other session env.
- On Linux and macOS each mounted stdio server runs through a private (0700) `sh` wrapper in the
  session folder, which sets the server's `env` and then starts it. Those values never enter
  omp's environment, so its bash commands and other MCP servers do not see them, and `${` in the
  server's command or arguments is fine. omp sees only the wrapper, so its check for the same
  server under another name and its filter for browser MCP packages miss the real command.
- On Windows, stdio `env` values ride in omp's environment as `AGENT_SWITCH_OMP_MCP_<n>` like the
  header values, and omp would expand `${VAR}` in a stdio command or argument.
- omp expands `${VAR}` in a mounted server's `url` (and, on Windows, its stdio command and
  arguments) with no escape, so agent-switch stops when one of them contains `${`. Your own
  servers in a merged `mcp.json` are left as they are.

Limits:

- `--as-subagent` is not supported for omp.
- A `--profile` after `--` is refused whenever agent-switch sets `PI_CODING_AGENT_DIR` (`--url`
  mode, and native launch with skills or MCP flags), because an omp profile ignores that variable.
- With the `openai` thinking format, omp's `--thinking off` sends the lowest effort instead of
  turning thinking off (see [Reasoning and sampling](#reasoning-and-sampling)).
- Native launch and aliases have their own rules: see [Native launch](#native-launch) and
  [Agent aliases](#agent-aliases).

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

Every agent reads and writes your own session history in each mode: native, native with
`--skill` or MCP flags, `--as-subagent`, `--url`, and `--url --persist`. A session you start
natively can be resumed through agent-switch, and the other way round. The exceptions: a Windows
codex, pi or dsh run from WSL keeps its own history; dsh and dsh-tui don't follow a session store
your own dsh patch layers move or re-encode (see [DeepSeek Harness](#deepseek-harness)); codex
with `--no-persist` shares nothing; and codex lists sessions by model provider (see below).

| Agent | Your history | How a session reaches it |
|---|---|---|
| claude | `~/.claude/projects` | agent-switch changes only the config |
| opencode | `~/.local/share/opencode` | agent-switch changes only the config |
| omp | your omp agent dir, or `$XDG_DATA_HOME/omp` and `$XDG_STATE_HOME/omp` | links, see [OMP](#omp-oh-my-pi) |
| pi | `sessions` in your pi agent dir | a link, see below |
| dsh | `sessions` and `attachments` in your DSH home | patch rows, see [DeepSeek Harness](#deepseek-harness) |
| dsh-tui, dst | the same store, or your `DSH_TUI_SESSION_ROOT` | `DSH_TUI_SESSION_ROOT`, see [DeepSeek Harness](#deepseek-harness) |
| codex | `sessions`, `archived_sessions`, `thread-writer-locks`, `session_index.jsonl`, `history.jsonl` and the SQLite state in your codex home | links and `CODEX_SQLITE_HOME`, see below |

- `--persist` keeps the agent-switch session folder between runs: settings and other state changed
  inside the session, and dsh-tui's installed profile. It is on by default for codex and dsh-tui;
  the other agents resume without it.
- codex with `--url` or `--provider` keeps `CODEX_HOME` in a session folder that stays between
  runs, so its `agent_switch` profile, and a `/model` pick made there, never touch your
  `config.toml`. codex records each new thread's file path through `CODEX_HOME` and keeps that
  path until the thread is written again, so the folder has to stay: until a thread is resumed,
  its recorded path runs through that folder's `sessions`, and deleting the folder leaves the
  thread impossible to resume. Each codex home gets its own folder, so one never re-points
  another's links: `~/.agent-switch/agents/codex` for `~/.codex`, and
  `~/.agent-switch/agents/codex-<8 hex digits>` for any other `CODEX_HOME`, from a hash of its
  path (the `--skill` and `--as-subagent` folders below follow the same rule). Two codex sessions
  on different servers at the same time share the folder and its `config.toml`. Your history is
  shared:
  - `sessions`, `archived_sessions`, `thread-writer-locks`, `session_index.jsonl` and
    `history.jsonl` in the session link to those in your codex home (`CODEX_HOME`, else
    `~/.codex`). One missing on your side is created there first. These are always links, never
    copies (a file falls back to a hard link, a folder to a junction on Windows); when none can
    be made, the launch stops. Real ones with content from an earlier `--persist` session are
    moved to `<name>.agent-switch-old`, as for pi.
  - The SQLite state (`state_5.sqlite` and the other `*.sqlite` files) is never linked:
    agent-switch sets `CODEX_SQLITE_HOME` to your codex home unless your shell sets it. A
    `sqlite_home` in your own `config.toml` is not followed with `--url`, since the session reads
    its own `config.toml`; export `CODEX_SQLITE_HOME` with the same folder instead.
  - Native `--skill` and `--as-subagent` overlays link the same names, skip the SQLite files and
    set `CODEX_SQLITE_HOME` the same way; they link your `config.toml`, so a `sqlite_home` there
    applies. They stay between runs too: `~/.agent-switch/agents/codex-native/home` and
    `~/.agent-switch/agents/codex-subagent/parent`. A later `--no-launch` without `--skill`
    removes everything else in the overlay, a login or state codex saved there included, but
    keeps the history links and the overlay's record of them, and moves real threads found
    there aside.
  - `--no-persist` makes a throwaway session that shares nothing: a temporary `CODEX_HOME`
    deleted on exit, no links into your codex home, no `CODEX_SQLITE_HOME`, and an inherited
    `CODEX_SQLITE_HOME` is cleared. Its threads can't be resumed after it exits. With
    `--no-launch` it gets a kept folder of its own, `~/.agent-switch/agents/codex-isolated` (or
    `<alias>-isolated`, `codex-subagent-isolated`), and never touches the shared folder; links
    found in its own are removed first. `--as-subagent --no-persist` shares nothing either.
  - A `codex-<x>` alias works with `--url`: its history is `~/.<alias>` (the `CODEX_HOME` such an
    alias usually sets), linked the same way, and its folder is `~/.agent-switch/agents/<alias>`.
    When `~/.<alias>` is not a directory, the session keeps its own history, with a note.
    agent-switch starts plain `codex`, not the alias, so flags and env the alias adds do not
    apply. `--skill` and `--as-subagent` are still refused for an alias.
  - codex lists sessions, and picks `--last`, by model provider: native sessions record `openai`,
    agent-switch ones `agent_switch`. Only `codex resume <UUID>` crosses that line, for example
    `agent-switch codex --url ... -- resume <UUID>`, and resuming records the session under the
    provider that resumed it. To open an agent-switch session natively, add `-m <model>` or
    `-c model_provider=openai`.
  - Deleting a thread inside the session rewrites `session_index.jsonl` over the link; the next
    run moves that copy aside and links yours again, so the deletion does not reach your index.
  - A Windows codex run from WSL can't follow the links, so its session keeps its own history,
    with a note, and an inherited `CODEX_SQLITE_HOME` is cleared for it. It gets folders of its
    own (`codex-windows`, `codex-native-windows`, `codex-subagent-windows`, `<alias>-windows`),
    so it never meets a Linux codex's links; links found in them are removed first.
- pi with `--url` or `--provider` moves `HOME` and `PI_CODING_AGENT_DIR` into its session folder,
  but shares your history, so `--resume` and `--continue` see your native pi sessions and native
  pi sees the ones made there:
  - `<session>/.pi/agent/sessions` links to `sessions` in your pi agent dir (`~/.pi/agent`, or
    your `PI_CODING_AGENT_DIR`). A folder missing on your side is created there first. These are
    always links (a symlink, or a junction on Windows), never copies; when none can be made, the
    launch stops.
  - A real `sessions` folder with content in the session, such as an earlier `--persist`
    session's, is moved to `sessions.agent-switch-old` (`-1`, `-2` and so on when that exists),
    with a note on stderr. An empty one is removed.
  - A `PI_CODING_AGENT_SESSION_DIR` in your shell reaches pi, with a leading `~` expanded against
    your own home, since pi would expand it against the moved `HOME`. The `sessionDir` in your
    own `settings.json` is copied into the session's the same way; a value set inside the
    session wins, and the copy goes when you remove yours. A project's `.pi/settings.json`
    `sessionDir` applies as it does natively; when you don't set `PI_CODING_AGENT_SESSION_DIR`
    and it starts with `~`, agent-switch expands it against your own home and passes it as
    `PI_CODING_AGENT_SESSION_DIR`, which pi reads before its settings. A `--no-launch` recipe
    leaves that value out, since it may run in another project, and a note on stderr asks you to
    export it in your shell or make the `sessionDir` absolute. A `--session-dir` after `--` still
    wins over both.
  - With native `--skill`, the overlay's `sessions` links to yours too. A later `--no-launch`
    without `--skill` moves real sessions found in the overlay aside before removing it.
  - A Windows pi run from WSL can't follow the links, so its session keeps its own history,
    with a note, and a `PI_CODING_AGENT_SESSION_DIR` in your shell, meant for a Linux pi, is
    cleared for it. Native Windows pi keeps its history in `%USERPROFILE%\.pi`.
- agent-switch does not move older omp sessions into your store. Those of a `--persist` or
  `--no-launch` session are in `~/.agent-switch/agents/omp/agent/sessions` (moved to
  `sessions.agent-switch-old` on the next run), in
  `~/.agent-switch/agents/omp/agent/sessions-pinned/`, and for `--skill` or MCP flags with
  `--no-launch` in `~/.agent-switch/agents/omp-native/agent/sessions` (or
  `sessions.agent-switch-old`). Open one with `omp --resume <path>`. Their images and large
  outputs stay in the `blobs.agent-switch-old` folder beside them, and omp reads blobs from the
  linked `blobs`, so an old session opened later lacks them unless you move them back into your
  `blobs`. Those of a run without `--persist` were deleted on exit.
- agent-switch does not move older pi sessions into your store either. Those of a `--persist` or
  `--no-launch` session are in `~/.agent-switch/agents/pi/.pi/agent/sessions` (moved to
  `sessions.agent-switch-old` on the next run), and for `--skill` with `--no-launch` in
  `~/.agent-switch/agents/pi-native/agent/sessions` (or `sessions.agent-switch-old`). Open one
  with `pi --session <path>`. Those of a run without `--persist` were deleted on exit.
- Older dsh and dsh-tui sessions stay where they were too: `~/.agent-switch/agents/dsh/sessions`
  for dsh with `--persist` or `--no-launch`, and `~/.agent-switch/agents/dsh-tui/.dsh/sessions`
  for dsh-tui. To browse the dsh-tui ones, start it once with `DSH_TUI_SESSION_ROOT` set to that
  folder. Those of a run without `--persist` (dsh) or with `--no-persist` (dsh-tui) were deleted
  on exit.
- Older codex sessions stay where they were as well: `~/.agent-switch/agents/codex/sessions` and
  `archived_sessions` for a `--persist` or `--no-launch` session (moved to
  `<name>.agent-switch-old` on the next run), and the `sessions` of a fixed `--no-launch` overlay
  in `~/.agent-switch/agents/codex-native/home` or `codex-subagent/parent`. Those of a run without
  `--persist` were deleted on exit.

### Plan files

Plan mode in Claude Code writes the plan to a file. `agent-switch claude` keeps that file with the
project: it goes to `<project-root>/.tmp/` instead of `~/.claude/plans/`. Add `.tmp/` to the
project's `.gitignore` if git should not see the plans.

- agent-switch passes `{"plansDirectory": ".tmp"}` in a session `--settings` file, which overrides
  a `plansDirectory` in the project's `.claude/settings.json`.
- claude keeps only the last `--settings`. In native and `--as-subagent` launches, a `--settings`
  after `--` replaces this file; with a local model server, the session overlay carries it and
  stays last.
- `--no-project-plans` keeps claude's default, `~/.claude/plans/`.

To keep plans with the project when claude starts without agent-switch, write the setting into
claude's own global settings:

```sh
agent-switch claude --global-project-plans            # $CLAUDE_CONFIG_DIR, else ~/.claude
agent-switch claude-personal --global-project-plans   # ~/.claude-personal
agent-switch claude-personal --no-global-project-plans
```

- It adds `"plansDirectory": ".tmp"` to `settings.json` in that directory and exits without
  starting claude. Other keys and the file's layout stay as they were.
- A `claude-<x>` alias sets its own `CLAUDE_CONFIG_DIR`, which agent-switch cannot read, so it edits
  `~/.claude-<x>/settings.json`. The directory must already exist.
- The setting is global: every claude run with that config uses it, also runs started without
  agent-switch. While it is set, `--no-project-plans` no longer falls back to
  `~/.claude/plans/`.
- `--no-global-project-plans` removes the key again.

### Context and compaction

- The context window comes from the server: Ollama `/api/ps`, LM Studio's loaded instance,
  llama-server `meta.n_ctx`, or vLLM `max_model_len`.
- By default (`--context-length -1`) the agent gets the server's window as is, uncapped. A server
  that reports no window gets 250,000 tokens, and agent-switch warns at startup.
- `--context-length <tokens>` sets the window yourself. `0` is refused.
- agent-switch never starts a model server. By default it may load the `--model` you name into a
  server that is already running. `--no-model-load` makes it a pure client that never loads,
  reloads or unloads anything; the model must already be loaded.
- `--compact-at 0.85` starts the agent's auto-compaction when 85% of the window is used, scaled per
  agent (Claude Code, Codex, OpenCode, Pi, OMP and DeepSeek Harness). The range is 0.5–0.95; unset
  keeps each agent's own behavior.
  - Claude Code applies the fraction to its own effective window (the window minus its output
    reserve), so it can only start compaction earlier than its built-in trigger, never later.
    agent-switch warns about this whenever you pass `--compact-at` to Claude Code.
  - Codex caps it at 0.9 of the window, so a value above 0.9 acts as 0.9, and agent-switch warns.
  - DeepSeek Harness applies it only to its headless profile (`--profile headless`). Its web profile
    ignores it, and agent-switch warns before starting it.
  - OMP gets it as `compaction.thresholdPercent` (85 for 0.85), which omp applies to each model's
    own window.
- `--compact-keep 0.3` keeps the most recent 30% of the window beside the summary when OpenCode,
  Pi or OMP compacts. Unset keeps 20%, so a 250,000-token window keeps 50,000 tokens and a
  1,000,000-token window keeps 200,000. The range is 0–0.4. `0` leaves the agent's own default
  (OpenCode 2.x 15,000 tokens, Pi and OMP 20,000). With `--as-subagent` the flag is ignored with a
  warning.
  - A model whose window the server does not report gets no amount and uses the agent's own
    default. A `--compact-keep` you passed is ignored for those models only, with a warning that
    names them. OpenCode's and OMP's amount is global, so only the launch model counts.
    `--context-length` counts as a reported window.
  - Pi keeps the original messages. OpenCode turns the kept part into text: tool output is cut to
    1,250 characters, reasoning is dropped, and attachments keep only their names.
  - Pi gets a separate amount for each served model with a reported window. The other models use
    Pi's default. This needs Pi 0.86.0 or later; older versions ignore the amount.
  - A `compaction.keepRecentTokens` in a project's `.pi/settings.json` has no effect on a model
    that has an amount. Pass `--compact-keep 0` to let it apply.
  - OpenCode's amount comes from the launch model's window, so switching to a smaller-window model
    mid-session does not recalculate it. Compaction can then run often, so use a smaller
    `--compact-keep` for such models.
  - OpenCode 1.x is also limited by `tail_turns` if you set it.
  - OMP's amount (`compaction.keepRecentTokens`) comes from the launch model's window, and every
    served model shares it. On a small window omp keeps less by itself.

### Reasoning and sampling

- `--reasoning on|off` overrides the model's chat template for this session.
- `--reasoning-effort <level>` picks a thinking level. The levels are the model's own, so pass one
  its chat template accepts. Unset keeps the template's choice.
- OpenCode also gets `none/low/medium/high/xhigh/max` effort variants for every model
  (`provider/model#xhigh` in its model picker). On OpenCode 2.x a pinned `--reasoning-effort` wins
  over a chosen variant; on 1.x the variant wins.
- Pi (0.84.0 or later) on llama.cpp, vLLM or another OpenAI-compatible server picks the thinking
  level itself: `/thinking` and Shift+Tab switch it between `off` and `max`, and a session starts at
  `xhigh` (a level saved in a `--persist` session stays). llama.cpp and vLLM get it as
  `chat_template_kwargs`; other servers get `reasoning_effort`. `--reasoning off` and
  `--reasoning-effort` only set the starting level, and `--reasoning auto` leaves thinking to the
  server. LM Studio and Ollama keep the pinned behaviour above.
- OMP on llama.cpp, vLLM or another OpenAI-compatible server also picks the thinking level itself:
  every model offers `minimal` to `max`, and a session starts at `xhigh` (a level saved in a
  `--persist` session stays). llama.cpp and vLLM get it as `chat_template_kwargs`
  (`enable_thinking`, plus `reasoning_effort` when thinking is on); other servers get
  `reasoning_effort`, and there omp's `off` sends the lowest effort instead of turning thinking
  off. `--reasoning off` and `--reasoning-effort` set the starting level (omp's `--thinking`), and
  `--reasoning auto` leaves thinking to the server. LM Studio and Ollama keep the pinned behaviour
  above.
- `--temperature`, `--top-p`, `--top-k`, `--min-p`, `--repetition-penalty` and `--presence-penalty`
  are set in the agent's own requests, not on the server. Unset keeps the model's recommended
  sampling; a value the agent cannot send is ignored with a warning.
- `--max-tokens` caps one reply. The default is a quarter of the context window, up to 32,000, and
  it is capped at half the window so the conversation keeps room.

## Native launch

Without `--url` or `--provider`, the agent starts natively: it keeps its own model, login and
config, and agent-switch writes nothing into the agent's own config. The one exception is claude's
`--global-project-plans`, which edits claude's `settings.json` on request (see
[Plan files](#plan-files)).

- Only the shared flags apply: the MCP flags, `--yolo` and claude's `--project-plans`.
- Flags that only make sense with a local model server fail with "needs --url or --provider":
  `--model` and a positional model, `--context-length`, `--max-tokens`, `--reasoning`,
  `--reasoning-effort`, `--temperature`, `--top-p`, `--top-k`, `--min-p`, `--repetition-penalty`,
  `--presence-penalty`, `--compact-at`, `--compact-keep`, `--api-key`, `--header`,
  `--no-model-load`, `--as-subagent`, `--persist`/`--no-persist`.
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
| OMP | an overlay agent dir, `PI_CODING_AGENT_DIR=<session>/agent`, that links every entry of your omp agent dir, except that `mcp.json` is a merged copy: your servers plus the mounted ones, which reach omp as in `--url` mode: stdio servers through wrappers, header values in env vars (see [OMP](#omp-oh-my-pi)). A mounted server wins a shared name over your user-level servers and is taken out of your `disabledServers`; a same-name server in project `.omp/mcp.json` still wins. `/mcp add`, `remove` or `disable` inside that session changes only the session copy. |

Native launch always uses a temporary session folder. `--no-launch` uses a fixed
`~/.agent-switch/agents/<agent>-native` folder and prints the env and command instead of starting
the agent.

OMP uses its overlay only with `--skill`, `--skill-set` or MCP flags. Without them, omp starts with
your environment unchanged, plus `--yolo` if you pass it. The overlay moves omp's agent dir, so a
few setups cannot use it:

- An active omp profile (`OMP_PROFILE`, or `PI_PROFILE` when `OMP_PROFILE` is unset): the overlay
  would switch the profile off. An empty or `default` value is fine and is cleared.
- On Linux and macOS, an existing `$XDG_DATA_HOME/omp`, `$XDG_STATE_HOME/omp` or
  `$XDG_CACHE_HOME/omp` while you use the default agent dir: omp keeps your login and sessions
  there, and the overlay would hide them. An `XDG_DATA_HOME` or `XDG_STATE_HOME` set in a `.env`
  file stops the launch as in `--url` mode; an `XDG_CACHE_HOME` there is not seen by this check.
- A `--profile` after `--`.

For those, use `--url`/`--provider`, or add the skill or MCP server to your own omp config. As in
`--url` mode, the overlay sets `PI_CONFIG_DIR` and `OMP_PROFILE=default`, and stops when a `.env`
file selects a named profile, an agent dir or a config root (see [OMP](#omp-oh-my-pi)). Your
`PI_CODING_AGENT_SESSION_DIR` stays as it is. The overlay's `sessions`, `blobs`, `archive`,
`terminal-sessions` and `custom-session-files` link to those in your agent dir, as in `--url`
mode, so history omp wrote into a `--no-launch` overlay is moved aside, never removed. A later
`--no-launch` without those flags first moves real history at those names aside too, then
removes everything else in the overlay (links, a login, a merged `mcp.json`), and keeps the
folder only while history moved aside is in it.

## Agent aliases

`agent-switch <agent>-<x>` runs your own shell function, alias or script named `<agent>-<x>` as that
agent, with the shared flags: the MCP flags, `--yolo`, and `--url`/`--provider` where they work.

```sh
agent-switch claude-personal
agent-switch codex-personal --mcp-stdio 'blender:"uv run --directory ~/blender-mcp blender-mcp"'
```

- Name lookup: the exact subcommands (`claude`, `codex`, `opencode`, `pi`, `omp`, `dsh`, `dsh-tui`,
  `dst`) always win. Otherwise `dsh-tui-<x>` is dsh-tui, `dsh-<x>` is dsh, and `claude-<x>`,
  `codex-<x>`, `opencode-<x>`, `pi-<x>` and `omp-<x>` are that agent. Anything else is "No such
  command".
- The alias replaces only the program name. agent-switch runs
  `bash -ic '<name> "$@"' agent-switch <agent args>`, so the function must pass `"$@"` on to the
  agent; the environment agent-switch builds still reaches it.
- The name must match `[A-Za-z0-9._-]+`, `bash` must be on PATH (aliases are not supported on
  Windows), and the name must exist in `bash -ic` as a function, alias or file.
- An alias whose definition mentions `agent-switch` is refused, because it would call itself.
- Native mode (no `--url`/`--provider`) works for every kind of alias. `omp-*` runs natively only
  without `--skill`, `--skill-set` and MCP flags.
- Local model mode works for `claude-*` and `opencode-*`, whose session settings travel as env
  vars and flags that a forwarding function keeps, and for `codex-*`, which then starts plain
  `codex` on the alias's `~/.<alias>` history (see [Session history](#session-history)). With
  `--url`/`--provider`, these are refused: `pi-*` (PI_CODING_AGENT_DIR),
  `dsh-*`/`dsh-tui-*` (DSH_HOME/HOME) and `omp-*` (an `omp --profile <name> --alias` alias selects
  a profile, and a profile ignores PI_CODING_AGENT_DIR).
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
  several steps in a script. On Windows, OMP refuses `${` in a mounted command or argument,
  because omp would expand it first; there, write `~` in this form, or use the plain form, where
  agent-switch expands `${VAR}` itself.
- Pi and the dsh-tui/dst TUI move `HOME` to the session folder, so there `~` points at that folder.
  Use an absolute path with them. OMP keeps `HOME`.
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
- Claude Code gets `--strict-mcp-config --mcp-config=<session file>`. Codex, OpenCode, Pi, OMP and
  dsh get them through their isolated session config; OpenCode also copies the servers from your
  global config that are not mounted, with `enabled: false` (OpenCode 2's `mcp.servers` with
  `disabled: true`; OpenCode 2 reads its global config from `OPENCODE_CONFIG_DIR` when set).
- For OMP the session config is `<session>/agent/mcp.json`, which takes the place of your
  user-level omp `mcp.json` only and keeps its `disabledServers`. omp still loads servers from
  `~/.omp/plugins`, from project config such as `.omp/mcp.json` or a project `.claude/.mcp.json`
  unless your `mcp.enableProjectConfig: false` came along, and from Claude Code or Codex user
  config only as [OMP](#omp-oh-my-pi) describes.
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

- `PATH` is a folder containing `SKILL.md`. `SKILL.md` should start with YAML frontmatter that has
  a `description`; Codex, Pi, OMP, and dsh skip a skill without one. Relative paths use the current
  directory; `~` expands to your home directory. The skill name comes from `NAME:`, then the file's
  frontmatter `name`, then the folder name. Names must use lowercase letters, digits, and single
  hyphens, up to 64 characters. When injected skills share a name, the later one wins and replaces
  the earlier one whole: first the `--skill-set` values in flag order, then the `--skill` values
  in flag order, and inside one set the later path in the sorted walk. Each dropped name prints
  one line on stderr naming the winner and the dropped folders, and a folder counted twice is
  not a drop. The winner then competes with the
  agent's own skills as the table below shows.
- `PATH` can also be a URL that starts with a scheme such as `https://`, and `NAME:URL` works
  too. agent-switch downloads it with [degit](https://github.com/Rich-Harris/degit) into
  `<temp dir>/agent-switch-skills-<uid>/<hash>/<leaf>`: on Linux the temp dir is `$TMPDIR`,
  else `/tmp`; on Windows the root is `agent-switch-skills` in your per-user temp folder. That
  root must be a private folder you own, or agent-switch stops: remove it, or point `TMPDIR`
  at a private folder, because a folder another user created in a shared `/tmp` cannot be
  removed by you. `<leaf>` is the URL's last path segment with `.git` removed and other
  characters made folder-safe; it is the folder-name fallback for the skill name. A later
  run with the same URL reuses that folder and prints its path; delete the folder to
  download it again. A different `#ref` gets its own folder. degit must be installed
  (`npm install -g degit`); without it, agent-switch stops with that hint. GitHub
  `/tree/<ref>/<dir>` links need degit 3 or newer, because degit 2 reads `tree/<ref>` as a
  folder. degit also runs a `degit.json` found in the downloaded folder, and its actions can
  read your environment variables, so use only URLs you trust. Short forms such as
  `user/repo` or `github:user/repo` are read as a local path or as `NAME:`, so use the full
  URL.
- `--skill-set [NS:]PATH|URL` adds every skill nested in a folder. Each folder holding `SKILL.md`
  is one skill, at any depth, and its inside is not searched further, so a `SKILL.md` under
  a skill's `references/` stays part of that skill. A set whose root folder holds `SKILL.md`
  itself, or that holds no skill at all, fails: use `--skill` for a single skill. Local
  skills are used where they are, not copied, and a local set follows folder links wherever
  they point, so a skill folder linked into the set counts, and a link to a parent folder
  is searched too. One unreadable folder inside a set stops the whole set. A URL is
  downloaded with degit and flattened
  into `<temp dir>/agent-switch-skills-<uid>/<hash>/<leaf>`, one folder per skill, and the
  set root's other files, such as `README.md`, are dropped. That cache is reused on later
  runs like `--skill`, and the same URL keeps a separate cache for each flag. Flattening
  names each cache folder after the skill, frontmatter `name` first and folder name when
  there is none, and dedupes by that name in source-path order: the later skill of a
  shared name replaces the earlier one, and the cache holds only the winners. That drop
  prints the stderr notice described above, but only on the first download of the URL;
  a cached run knows nothing about it. Each set skill is named like `--skill`, first by
  frontmatter `name`, then by folder name; an `NS:` prefix, parsed like `NAME:`, names
  all of them `NS-<name>`, and the result must pass the name check. A one-letter NS
  before an absolute path, like `g:/x`, reads as a Windows drive, so use a longer
  namespace. Shared names follow the last-wins order above.
- Skills cannot be combined with `--as-subagent`. `--skill` and `--skill-set` are not
  supported for Codex, Pi, OMP, dsh-tui, or dst aliases.
- agent-switch puts injected files in its session folder. A skill whose frontmatter `name` already
  matches links to its source, so edits are visible as the agent reads them. If the frontmatter
  `name` differs or is missing, agent-switch copies `SKILL.md` with `name: <name>` written in, then
  links its sibling files; edit the source and restart to refresh that copy. A `SKILL.md` with no
  frontmatter stays linked.
- When a Windows agent runs from WSL, injected skill files are copied because the Windows process
  cannot use the WSL links. `--persist` and `--no-launch` reuse their session folder; a later run
  removes the earlier injected files when you omit `--skill` and `--skill-set`.
- agent-switch does not install skills into your persistent agent skill list or edit the source
  folder. Codex, Pi and OMP native overlays link some of your real config files, so changes those
  agents save through those links can still reach your normal config.

```sh
agent-switch claude --skill /tmp/skills/my-test-skill
agent-switch codex --skill docs:/tmp/skills/guide --skill /tmp/skills/review
agent-switch dsh --url http://127.0.0.1:8080 --skill /tmp/skills/my-test-skill
agent-switch claude --skill https://github.com/user/repo/tree/main/skills/my-skill
```

| Agent | How the skill is loaded | Which same-name skill wins |
|---|---|---|
| Claude Code | `--add-dir=<session>/agent-switch`; the skill is in `.claude/skills/<name>`. | With no clash, the injected skill has the bare name. On a clash, your skill keeps that name and the injected skill is `agent-switch:<name>` on Claude Code 2.1.309 or newer; older versions hide the injected skill. |
| Codex | Native mode sets an overlay `CODEX_HOME` linked to your Codex home, your thread history included, and `CODEX_SQLITE_HOME` at your Codex home for its SQLite state (see [Session history](#session-history)). Routed mode uses the session `CODEX_HOME`; `-c skills.config` enables the injected path and disables the same-name entry. | The injected skill wins over same-name skills. |
| OpenCode | `skills.paths` in the session `OPENCODE_CONFIG`. | The injected skill wins over global skills, `~/.claude/skills`, `~/.agents/skills`, and project `.opencode/skills` on OpenCode 2.0.3. OpenCode 1.x was not verified. |
| Pi | `<agent dir>/skills`. Native mode sets an overlay `PI_CODING_AGENT_DIR` linked to your Pi home. | The injected skill wins over `~/.pi/agent/skills` and `~/.agents/skills`; project `.pi/skills` and `.agents/skills` win, as do a same-name skill from a `skills` entry in user or project `settings.json` and pi's own `--skill`. A `!name` pattern in `settings.json` or `--no-skills` hides the injected skill. From `~/.pi/agent/skills`, only top-level skill folders of the same name are hidden; same-name `*.md` files at its root and nested `SKILL.md` folders still load. With native `--skill`, user `settings.json` paths that start with `../` do not resolve. If a persisted session already has its own unowned `skills` folder, injection stops with an error instead of replacing it. |
| OMP | `<agent dir>/skills`. Native mode sets an overlay `PI_CODING_AGENT_DIR` linked to your omp agent dir; `--url` mode uses the session agent dir. | The injected skill wins over the other skills in your agent dir's `skills` and over `~/.agents/skills`, Claude Code, Codex and omp plugin skills. Project `.omp/skills` and `skills.customDirectories` win. `--no-skills` hides it, and omp's `--skills` patterns filter it like any skill. |
| dsh | A `--patch` row sets `skill-filesystem` `customSkillDirs`. | In the `headless` profile, the injected skill wins over `$DSH_HOME/skills` and `~/.agents/skills`; project `.dsh/skills` and `.agents/skills` win. In the `web` profile, a same-name skill the agent preset finds (project, `$DSH_HOME/skills`, `~/.agents/skills`) wins. The session row replaces an explicit `skill-filesystem` config in your profile. |
| dsh-tui / dst | `preset-patch` rows add the session folder to the `standard`, `ptc`, and `liangshen` presets. The profile must export `./preset-patch`; the gate checks its runtime target, while its error asks for dsh-tui 0.17 or newer. The presets also need dsh 0.1.7 or newer. | Same order as dsh. `cordis`, `minimal`, and other unsupported profile presets do not receive injected skills; agent-switch warns when it knows the selected preset. |

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
