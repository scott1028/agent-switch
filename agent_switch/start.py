# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""`agent-switch <agent>` — launch a coding agent against a running model server."""

import re
from typing import Optional

import click
import typer
from typer.core import TyperGroup

from agent_switch.agents import claude, codex, dsh, opencode, pi
from agent_switch.core.launch import _ALIAS_KIND_META, _ALIAS_META
from agent_switch.core.options import _PASSTHROUGH, _PassthroughCommand
from agent_switch.core.storage import _agent_switch_home

# `dsh-tui-<x>` must win over `dsh-<x>`, so the longer kinds come first; the suffix keeps the
# whole name inside the shell-safe characters _check_alias accepts.
_ALIAS_KIND = re.compile(r"(dsh-tui|dsh|claude|codex|opencode|pi)-[A-Za-z0-9._-]+")


class _AliasGroup(TyperGroup):
    """Resolve `<agent>-<x>` names to that agent's command, for the user's own shell alias."""

    def get_command(self, ctx: click.Context, name: str) -> Optional[click.Command]:
        command = super().get_command(ctx, name)
        if command is not None:
            return command
        match = _ALIAS_KIND.fullmatch(name)
        if match is None:
            return None
        # Click shares ctx.meta with the child context, so the command sees the alias it runs.
        ctx.meta[_ALIAS_META] = name
        ctx.meta[_ALIAS_KIND_META] = match.group(1)
        return super().get_command(ctx, match.group(1))


start_app = typer.Typer(
    help = "With --url/--provider the agent runs against that local OpenAI-compatible model "
    "server (--provider lists the supported types) with a throwaway, session-only "
    "configuration; without them it runs natively, on its own model, login and config, and only "
    "the shared flags (MCP mounting, --yolo) are added. Either way your own agent config is not "
    "modified. Repeat --url to put several model servers behind one session proxy; see "
    "<agent> --help. "
    "A name like claude-<x>, codex-<x>, opencode-<x>, pi-<x>, dsh-<x> or dsh-tui-<x> runs your "
    "own bash function, alias or script of that name as that agent, through `bash -ic`.",
    no_args_is_help = True,
    context_settings = {"help_option_names": ["-h", "--help"]},
    cls = _AliasGroup,
)

# Each \n\n-separated section is "title\nbody"; _PassthroughCommand.format_help draws it as an
# option-style panel. Body lines keep their newlines and indentation and stay within 76 columns,
# the panel's inner width at 80. Rich markup tags only start lowercase/#//@, so the [NAME:]
# metavars below are never read as markup.
_MCP_EPILOG = (
    "Native launch\n"
    "Without --url/--provider the agent runs on its own model, login and config;\n"
    "only the MCP flags and --yolo apply, and mounted servers add to its own.\n\n"
    "Several model servers\n"
    "--url NAME:URL                    one per server (NAME: host-port)\n"
    "--api-key SERVER:KEY              a key for that server, remembered\n"
    "--header [SERVER:]NAME=VALUE      a header for one server, or for all\n"
    "--model NAME:ID                   pick a model by its server prefix\n"
    "Two or more --url start a session proxy and rename every model id to\n"
    "NAME:ID; --no-launch and --provider need a single --url.\n\n"
    "Agent arguments\n"
    "Before -- only agent-switch's own options and a positional model are taken;\n"
    "after it everything goes to the agent unchanged, except a flag agent-switch\n"
    "also sends, which is dropped there (with a note on stderr).\n\n"
    "MCP servers\n"
    f"Registry: {_agent_switch_home() / 'mcp.json'}, in the .mcp.json shape.\n"
    "--mcp NAME / --mcp-all mount its entries; --mcp-url, --mcp-oauth-url\n"
    "and --mcp-stdio mount one-off servers, named by NAME: for --mcp-header\n"
    "and --mcp-env. With --url/--provider they replace the agent's own.\n\n"
    "Examples\n"
    "agent-switch claude --mcp context7\n"
    "agent-switch codex --yolo -- --search\n"
    "agent-switch claude --mcp-url tools:http://127.0.0.1:8931/mcp\n"
    "agent-switch opencode --url a:http://127.0.0.1:11434 \\\n"
    "  --url b:https://gw.example/v1 --api-key b:KEY --model b:qwen3:8b"
)

# Registration order is the --help order; the old dsh decorators applied bottom-up (dsh, dsh-tui, dst).
start_app.command(
    "claude", cls = _PassthroughCommand, context_settings = _PASSTHROUGH, epilog = _MCP_EPILOG
)(claude.claude)
start_app.command(
    "codex", cls = _PassthroughCommand, context_settings = _PASSTHROUGH, epilog = _MCP_EPILOG
)(codex.codex)
start_app.command(
    "opencode", cls = _PassthroughCommand, context_settings = _PASSTHROUGH, epilog = _MCP_EPILOG
)(opencode.opencode)
start_app.command(
    "pi", cls = _PassthroughCommand, context_settings = _PASSTHROUGH, epilog = _MCP_EPILOG
)(pi.pi)
start_app.command(
    "dsh", cls = _PassthroughCommand, context_settings = _PASSTHROUGH, epilog = _MCP_EPILOG
)(dsh.dsh)
start_app.command(
    "dsh-tui",
    cls = _PassthroughCommand,
    context_settings = _PASSTHROUGH,
    epilog = _MCP_EPILOG,
    help = "Point the DeepSeek Harness TUI (dsh-tui) at a local model server and start it.",
)(dsh.dsh)
start_app.command(
    "dst",
    cls = _PassthroughCommand,
    context_settings = _PASSTHROUGH,
    epilog = _MCP_EPILOG,
    help = "Alias of dsh-tui.",
)(dsh.dsh)
