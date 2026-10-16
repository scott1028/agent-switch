# Blender MCP playbook

Copy-paste steps to add the Blender MCP server to any agent with agent-switch's `--mcp-stdio`.

Tested on 2026-10-10:

- Claude Code end to end, with a real tool call (`get_objects_summary` returned the live scene).
  That run also used `--no-model-load` (optional: add it when the model is already loaded and
  agent-switch must not load or unload anything) and the read-only flag from section 4.
- The exact commands below for all seven agents, with `--no-launch`.
- Earlier end-to-end runs for every agent used the unquoted form, shown as the alternative in
  section 2.

HTTP mode is not covered (see the end).

## How it fits together

- Blender runs the **MCP add-on** (a Blender Lab extension, version 1.0.3). By default it listens
  on `localhost:9876` and starts automatically.
- Port 9876 is **not** an MCP endpoint. It is the add-on's private TCP protocol: JSON messages that
  end with `\0`.
- The **MCP server** is a separate program, `blender-mcp`. It reaches the add-on through
  `BLENDER_MCP_HOST` / `BLENDER_MCP_PORT` (defaults `localhost` / `9876`) and connects only during
  tool calls.
- So the agent talks MCP to `blender-mcp`, and `blender-mcp` talks to Blender on port 9876. In
  agent-switch terms, `--url` is the **model** server and `--mcp-stdio` is the **MCP** server.

```text
+---------------------+  MCP (stdio)  +------------------------+   private TCP    +---------------------------+
| agent               | ────────────► | blender-mcp (FastMCP)  | ───────────────► | Blender add-on "MCP"      |
| claude/codex/pi/... |  --mcp-stdio  | uv run ... blender-mcp |  localhost:9876  | mcp_to_blender_server.py  |
|                     |               |                        |  JSON + "\0"     | (inside the Blender proc) |
+---------------------+               +------------------------+                  +---------------------------+
        ^
        └── --url points here: the model server (e.g. http://127.0.0.1:19090)
```

## Prerequisites

- Blender running with the MCP add-on installed and enabled: add the Blender Lab extensions
  repository `https://lab.blender.org/`, then find, install and enable the MCP add-on (see the
  bundle README).
- `uv` on PATH.

## 1. Install blender-mcp

Download the bundle `blender-<version>.mcpb`. It is a zip with `pyproject.toml`, `blmcp/` and
`manifest.json`. Unpack and sync it (replace `/path/to/` with where you saved it):

```sh
mkdir -p ~/blender-mcp
unzip -q /path/to/blender-1.0.3.mcpb -d ~/blender-mcp
uv sync --directory ~/blender-mcp
```

Check that `~/blender-mcp/.venv/bin/blender-mcp` exists.

- You can also install from git (see the bundle README):
  `pip install git+https://projects.blender.org/lab/blender_mcp.git#subdirectory=mcp`.
- Never copy a `.venv` to another folder: it holds absolute paths. Run
  `uv sync --directory <new-place>` again instead.

## 2. Mount it in an agent

The same form works for all seven agents. Double-quote the command so it reads like a terminal
command:

```sh
agent-switch <agent> --url http://127.0.0.1:19090 \
  --mcp-stdio 'blender="uv run --directory ~/blender-mcp blender-mcp"'
```

- The double-quoted command runs through `bash -lc 'exec ...'`, so `~` and `${VAR}` expand as in
  your terminal. It needs `bash` on PATH and allows one command only: `;`, `&&`, `||`, `|` and `&`
  outside the quotes are rejected, so put several steps in a script.
- Alternative without the inner quotes:
  `--mcp-stdio 'blender=uv run --directory /home/<you>/blender-mcp blender-mcp'` starts the server
  directly, without a shell. `~` is **not** expanded there: write an absolute path, or
  `${HOME}/blender-mcp`, which agent-switch expands itself.
- pi, dsh-tui and dst move `HOME` to the session folder. There bash reads that folder's
  `~/.profile`, and `~` points at it (inherited environment variables still apply), so the
  commands below use an absolute path for these three.
- Codex passes only a few environment variables to MCP servers (PATH among them). `bash -lc` reads
  `~/.profile`, not `~/.bashrc`, and a usual `~/.bashrc` stops early in a non-interactive shell.
  So export such variables in `~/.profile`, or pass them with `--mcp-env`.
- Your `~/.profile` must not print anything: stdout is the MCP connection.
- `blender=` names the server; without it the name would be `blender-mcp`.
- Only if you changed the add-on's port: add `--mcp-env 'blender:BLENDER_MCP_PORT=<port>'`.
- dsh-tui and dst keep their session folder by default (settings, history and the installed
  profile survive). `--no-persist` is optional: it uses a throwaway folder and reinstalls the TUI
  profile with pnpm on every start. The test used it only to leave the saved session alone.

The tested commands:

```sh
# Claude Code, one-shot
agent-switch claude   --url http://127.0.0.1:19090 \
  --mcp-stdio 'blender="uv run --directory ~/blender-mcp blender-mcp"' \
  -p "<prompt>"

# Codex, one-shot (exec is Codex's own subcommand)
agent-switch codex    --url http://127.0.0.1:19090 \
  --mcp-stdio 'blender="uv run --directory ~/blender-mcp blender-mcp"' \
  exec "<prompt>"

# Pi, one-shot (HOME is the session folder there: absolute path)
agent-switch pi       --url http://127.0.0.1:19090 \
  --mcp-stdio 'blender="uv run --directory /home/<you>/blender-mcp blender-mcp"' \
  -p "<prompt>"

# OpenCode, TUI
agent-switch opencode --url http://127.0.0.1:19090 \
  --mcp-stdio 'blender="uv run --directory ~/blender-mcp blender-mcp"'

# dsh, headless (--profile and the prompt go to dsh)
agent-switch dsh      --url http://127.0.0.1:19090 \
  --mcp-stdio 'blender="uv run --directory ~/blender-mcp blender-mcp"' \
  --profile headless "<prompt>"

# dsh-tui / dst, TUI (HOME is the session folder there: absolute path)
agent-switch dsh-tui  --url http://127.0.0.1:19090 \
  --mcp-stdio 'blender="uv run --directory /home/<you>/blender-mcp blender-mcp"'
agent-switch dst      --url http://127.0.0.1:19090 \
  --mcp-stdio 'blender="uv run --directory /home/<you>/blender-mcp blender-mcp"'
```

## 3. Check it works

- In a TUI: OpenCode shows `⊙ 1 MCP /mcps` in the status bar; in Claude Code and Pi, run `/mcp`.
- Read-only test prompt:

  > Call the get_objects_summary tool of the blender MCP server exactly once, then list the scene
  > name and every object name it returned.

  The default scene answers `Scene` with `Camera`, `Cube` and `Light`.
- Watch the socket with `ss -tanp | grep 9876`. Blender's `LISTEN` socket is always there. A tool
  call adds a short `ESTAB` connection owned by `blender-mcp`, which then turns into `TIME-WAIT`.
  No tool call, no connection.

## 4. Safety

- The server offers `execute_blender_code`, which runs any Python inside Blender. **Save your work
  first.**
- Limit the agent to read-only tools where it supports that (both tested):
  - Claude Code: `--allowedTools=mcp__blender__get_objects_summary`
  - Codex: `-c 'mcp_servers.blender.enabled_tools=["get_objects_summary"]'`, placed after `exec`.
  - OpenCode, dsh, dsh-tui and dst had no equivalent in the test.

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The OpenCode log `~/.local/share/opencode/log/opencode.log` shows `mcp connect failed ... MCP server process exited with code 2: error: No such file or directory (os error 2)` | The `--directory` path does not exist, or it starts with an unexpanded `~` (the unquoted form, or the moved HOME of pi, dsh-tui and dst) | Use an absolute path; in the unquoted form `${HOME}/...` also works |
| `Failed to spawn: blender-mcp` | The folder is not the unpacked, `uv sync`ed bundle | Run step 1 again in that folder |
| A tool call fails with `Cannot connect to Blender at localhost:<port>` | Blender is not running, the add-on is not started, or `BLENDER_MCP_PORT` differs from the add-on's port | Start Blender and the add-on; match the port (`--mcp-env 'blender:BLENDER_MCP_PORT=<port>'`) |
| `--mcp-url http://localhost:9876` does not work | 9876 is the add-on's private socket, not MCP | Use `--mcp-stdio`, as in this playbook |
| A scripted `codex exec` hangs with `Reading additional input from stdin...` | Codex waits for stdin | Add `< /dev/null` |
| OpenCode v2.0.26 one-shot `run` says no blender tool exists, although the log says `mcp connected server=blender tools=26` | A quirk of one-shot `run` | Use the OpenCode TUI |
| Pi with `--tools '...,mcp__blender__...'` finds no MCP tools | pi-mcp-adapter is installed: it replaces Pi's built-in MCP (it adds `"-builtin:mcp"` to Pi's `extensions` setting), and the allowlist hides the adapter's tools | Do not combine `--tools` with pi-mcp-adapter |
| dsh-tui / dst: blender-mcp's `INFO Processing request ...` lines draw over the TUI | Cosmetic | Ignore |

## HTTP mode

`blender-mcp --transport http --port <p>` serves MCP at `/` (not `/mcp`), which `--mcp-url` can
reach. It is not covered here: it was not tested with Blender, and it allows CORS `*` with
DNS-rebinding protection off while offering `execute_blender_code`.

## Tested versions (2026-10-10)

Blender 5.2.2 LTS, add-on / bundle 1.0.3, agent-switch `cc91887`, Claude Code 2.1.296,
codex-cli 0.161.0, OpenCode v2.0.26, Pi 1.1.0, dsh 0.1.5-rc.2, dsh-tui 0.14.0.
