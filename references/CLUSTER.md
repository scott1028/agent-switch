# References cluster

`make references-cluster` starts four agents in this directory, all in one Herdr session. The
first one receives a one-line prompt and is the planner; the prompt names the command each role
runs.

## 1. Name the agents (planner)

1. Run `herdr agent list`. Your own pane is `$HERDR_PANE_ID`.
2. Rename with `herdr agent rename <pane-id> <name>`:
   - yourself: `planner`
   - the other two agents started with the planner's command: `executor` and `reviewer1`
   - the agent started with reviewer2's command: `reviewer2`
3. `herdr agent list` shows only the agent kind (`claude`, `codex`, `opencode`, ...). If both
   commands share a kind and you cannot tell the agents apart, ask the user which pane is
   `reviewer2`.

## 2. Goal

Bring `agent_switch/` in line with the upstream sources pinned in `references/`
(`git submodule status references`): config keys, environment variables, CLI flags, defaults,
version gates, and comments that cite upstream files. Change only what an upstream source shows
is wrong, missing, or outdated, and cite the upstream file and line for every item.

| agent | upstream source | agent-switch code |
|---|---|---|
| claude | `references/claude-code` (CHANGELOG and plugins, no source) | `agent_switch/agents/claude.py`, `agent_switch/claude_subagent_mcp.py` |
| codex | `references/codex` | `agent_switch/agents/codex.py`, `agent_switch/codex_subagent_mcp.py` |
| opencode | `references/opencode` | `agent_switch/agents/opencode.py` |
| pi | `references/pi` | `agent_switch/agents/pi.py`, `agent_switch/agents/pi_subagent.ts` |
| dsh | `references/deepseek-harness` | `agent_switch/agents/dsh.py` |
| dsh-tui | `references/dsh-tui` | `agent_switch/agents/dsh.py` |

Shared code lives in `agent_switch/core/`; user docs are `README.md` and `docs/reference.md`.

## 3. Workflow

Working files go in `.tmp/references-cluster/` (git-ignored).

1. planner: compare each pair above and write findings and a numbered plan to `plan.md`, each
   item with its upstream evidence.
2. planner → executor, one item at a time: `herdr agent prompt executor "<item>" --wait`. The
   executor writes a failing test first, makes it pass, then runs `uv run pytest` and
   `uv run ruff check .`.
3. planner → reviewer1 and reviewer2 together: review `git diff` against `plan.md` and
   `references/`, and write findings to `review-reviewer1.md` and `review-reviewer2.md`.
4. planner: merge the findings and send the fixes to the executor. Repeat steps 2–4 until neither
   reviewer has a blocking finding.
5. planner: write `summary.md` and report to the user.

## Rules

- Only the executor edits files; reviewers only read.
- Do not commit or push.
- Leave approval prompts to the user.
- Check exact command syntax with `herdr --help` or the herdr skill.
