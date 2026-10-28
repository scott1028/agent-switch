# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""Shared command options, panels, request fields and cross-agent helpers."""

import os
import re
from pathlib import Path
from typing import Literal, NamedTuple, NoReturn, Optional

import click
import typer
from typer.core import TyperCommand

from agent_switch import providers


_SUBAGENT_DESCRIPTION = (
    "Local coding subagent running on a local model for debugging, implementation, and "
    "codebase research. Use when the user asks to spawn a local agent."
)


_SUBAGENT_INSTRUCTIONS = (
    "You are a local coding subagent running on a local model. Complete the assigned task directly, "
    "use the available tools when useful, verify your work, and return a concise result to the "
    "parent agent."
)


# OpenCode 1 sends min(limit.output, this) as max_tokens unless the env var below raises it.
_OPENCODE_OUTPUT_TOKEN_MAX = 32_000


_PASSTHROUGH = {"allow_extra_args": True, "ignore_unknown_options": True}


class _PassthroughCommand(TyperCommand):
    """Split at the first `--`: before it only agent-switch's own arguments, after it the agent's."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        raw_args = list(args)
        separator = raw_args.index("--") if "--" in raw_args else len(raw_args)
        super().parse_args(ctx, raw_args[:separator])
        # A leading `org/name` before `--` is agent-switch's positional model.
        model, extras = _consume_positional_model(ctx.params.get("model"), ctx.args)
        if extras:
            _fail(
                f"`{extras[0]}` is not an agent-switch argument; put agent arguments after `--`, "
                f"e.g. `agent-switch {ctx.info_name} -- {extras[0]}`."
            )
        ctx.params["model"] = model
        # The first `--` is agent-switch's own; a second one belongs to the agent and stays.
        ctx.args = raw_args[separator + 1:]
        return ctx.args

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Typer's help, then each epilog section as an option-style panel titled by its first
        line; body lines starting with `|` form a table above the rest, the first one its header."""
        # Imported here, as Typer does, to keep rich off the CLI's startup path.
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from typer import rich_utils

        epilog, self.epilog = self.epilog, None
        try:
            super().format_help(ctx, formatter)
        finally:
            self.epilog = epilog
        # Typer's own console, so the panels match the option panels' width and colors.
        console = rich_utils._get_rich_console()
        for section in epilog.split("\n\n"):
            title, _, body = section.partition("\n")
            lines = body.split("\n")
            rows = [
                [rich_utils.highlighter(Text(cell.strip())) for cell in line.strip("|").split("|")]
                for line in lines
                if line.startswith("|")
            ]
            text = "\n".join(line for line in lines if not line.startswith("|"))
            renderables = []
            if rows:
                # Rich draws the borders, so they keep the panel's style and fall back to ASCII off UTF-8.
                table = Table(
                    box = box.ROUNDED,
                    border_style = rich_utils.STYLE_OPTIONS_PANEL_BORDER,
                    show_lines = True,
                )
                for header in rows[0]:
                    table.add_column(header)
                for row in rows[1:]:
                    table.add_row(*row)
                renderables.append(table)
            if text:
                renderables.append(rich_utils.highlighter(Text.from_markup(text)))
            console.print(
                Panel(
                    Group(*renderables),
                    border_style = rich_utils.STYLE_OPTIONS_PANEL_BORDER,
                    title = title,
                    title_align = rich_utils.ALIGN_OPTIONS_PANEL,
                )
            )


# Shared by every agent command; only the config/env/command differ. Help is grouped into rich panels so `--help` reads as Model / Server / Session instead of one long unaligned list. Each option keeps one plain sentence; the details live in the README.
_PANEL_MODEL = "Model"


_PANEL_SERVER = "Server"


_PANEL_SAMPLING = "Sampling"


_PANEL_SESSION = "Agent session"


_MODEL_OPTION = typer.Option(
    None,
    "--model",
    "-m",
    rich_help_panel = _PANEL_MODEL,
    help = "Pick the model; NAME:ID picks it from that --url server.",
)


_CONTEXT_OPTION = typer.Option(
    0,
    "--max-seq-length",
    "--context-length",
    min = -1,
    rich_help_panel = _PANEL_MODEL,
    show_default = False,
    help = "Set the context window in tokens; see Context window.",
)


_URL_OPTION = typer.Option(
    None,
    "--url",
    metavar = "[NAME:]URL",
    rich_help_panel = _PANEL_SERVER,
    help = "Model server URL; repeat --url to run on several servers.",
)


_PROVIDER_OPTION = typer.Option(
    None,
    "--provider",
    rich_help_panel = _PANEL_SERVER,
    help = "Skip server-type detection; use that server's usual local port.",
)


# --provider picks the model-server adapter in providers/, not the coding agent, so these server
# names are intentional.
ProviderName = Literal["ollama", "lmstudio", "llamacpp", "vllm", "openai"]


_MODEL_LOAD_OPTION = typer.Option(
    True,
    "--model-load/--no-model-load",
    rich_help_panel = _PANEL_SERVER,
    help = "--no-model-load: use only a model already loaded on the server.",
)


_REASONING_OPTION = typer.Option(
    None,
    "--reasoning",
    rich_help_panel = _PANEL_SERVER,
    help = "Force the model's thinking on or off for this session.",
)


_REASONING_EFFORT_OPTION = typer.Option(
    None,
    "--reasoning-effort",
    rich_help_panel = _PANEL_SERVER,
    help = "Set the model's thinking level, e.g. medium.",
)


# Sampling overrides ride in the agent's own config, so only this session uses them; one the agent cannot send is ignored. Default unset means the model's recommended sampling is used.
_TEMPERATURE_OPTION = typer.Option(
    None,
    "--temperature",
    min = 0.0,
    max = 2.0,
    rich_help_panel = _PANEL_SAMPLING,
    help = "Pin the sampling temperature.",
)


_TOP_P_OPTION = typer.Option(
    None,
    "--top-p",
    min = 0.0,
    max = 1.0,
    rich_help_panel = _PANEL_SAMPLING,
    help = "Pin top-p (nucleus) sampling.",
)


_TOP_K_OPTION = typer.Option(
    None,
    "--top-k",
    min = -1,
    max = 100,
    rich_help_panel = _PANEL_SAMPLING,
    help = "Pin top-k sampling.",
)


_MIN_P_OPTION = typer.Option(
    None,
    "--min-p",
    min = 0.0,
    max = 1.0,
    rich_help_panel = _PANEL_SAMPLING,
    help = "Pin the min-p sampling threshold.",
)


_REPETITION_PENALTY_OPTION = typer.Option(
    None,
    "--repetition-penalty",
    min = 1.0,
    max = 2.0,
    rich_help_panel = _PANEL_SAMPLING,
    help = "Pin the repetition penalty.",
)


_PRESENCE_PENALTY_OPTION = typer.Option(
    None,
    "--presence-penalty",
    min = 0.0,
    max = 2.0,
    rich_help_panel = _PANEL_SAMPLING,
    help = "Pin the presence penalty.",
)


_MAX_TOKENS_OPTION = typer.Option(
    None,
    "--max-tokens",
    min = 1,
    rich_help_panel = _PANEL_SAMPLING,
    help = "Cap one reply at N tokens; see Reply length.",
)


# Agent-session knobs.
_KEY_OPTION = typer.Option(
    None,
    "--api-key",
    metavar = "[SERVER:]KEY",
    envvar = "AGENT_SWITCH_API_KEY",
    rich_help_panel = _PANEL_SESSION,
    help = "API key for the model server; SERVER:KEY picks the server.",
)


_HEADER_OPTION = typer.Option(
    None,
    "--header",
    metavar = "[SERVER:]NAME=VALUE",
    rich_help_panel = _PANEL_SESSION,
    help = "Add an HTTP header to the server requests; SERVER: scopes it.",
)


_LAUNCH_OPTION = typer.Option(
    True,
    "--launch/--no-launch",
    rich_help_panel = _PANEL_SESSION,
    help = "--no-launch prints the env and command instead of starting.",
)


# One normalized "run tools without prompting" switch. Each agent spells this differently and it is easy to forget which is which, so accept every spelling and route to the agent's own mechanism in _yolo_command_flags / the config writers.
_YOLO_OPTION = typer.Option(
    False,
    "--yolo",
    "--dangerously-skip-permissions",
    "--dangerously-bypass-approvals-and-sandbox",
    rich_help_panel = _PANEL_SESSION,
    help = "Let the agent run its tools without asking.",
)


_PERSIST_OPTION = typer.Option(
    False,
    "--persist/--no-persist",
    rich_help_panel = _PANEL_SESSION,
    help = "Keep the session dir so the agent can resume this session.",
)


_AS_SUBAGENT_OPTION = typer.Option(
    False,
    "--as-subagent",
    rich_help_panel = _PANEL_SESSION,
    help = "Keep the agent's own model; add the local model as a subagent.",
)


_MCP_OPTION = typer.Option(
    None,
    "--mcp",
    metavar = "NAME",
    rich_help_panel = _PANEL_SESSION,
    help = "Mount this MCP server from the agent-switch registry.",
)


_MCP_ALL_OPTION = typer.Option(
    False,
    "--mcp-all",
    rich_help_panel = _PANEL_SESSION,
    help = "Mount every MCP server in the agent-switch registry.",
)


_MCP_URL_OPTION = typer.Option(
    None,
    "--mcp-url",
    metavar = "[NAME:]URL",
    rich_help_panel = _PANEL_SESSION,
    help = "Mount the http MCP server at this URL.",
)


_MCP_OAUTH_URL_OPTION = typer.Option(
    None,
    "--mcp-oauth-url",
    metavar = "[NAME:]URL",
    rich_help_panel = _PANEL_SESSION,
    help = "Mount the http MCP server at this URL, signing in with oauth.",
)


_MCP_HEADER_OPTION = typer.Option(
    None,
    "--mcp-header",
    metavar = "[NAME:]HEADER=VALUE",
    rich_help_panel = _PANEL_SESSION,
    help = "Add an HTTP header to one --mcp-url server.",
)


_MCP_STDIO_OPTION = typer.Option(
    None,
    "--mcp-stdio",
    metavar = "[NAME:]COMMAND",
    rich_help_panel = _PANEL_SESSION,
    help = "Mount the stdio MCP server this command starts.",
)


_SKILL_OPTION = typer.Option(
    None,
    "--skill",
    metavar = "[NAME:]PATH|URL",
    rich_help_panel = _PANEL_SESSION,
    help = "Add a skill folder or degit URL; NAME: renames it.",
)


_SKILL_SET_OPTION = typer.Option(
    None,
    "--skill-set",
    metavar = "[NS:]PATH|URL",
    rich_help_panel = _PANEL_SESSION,
    help = "Add every skill nested in a folder or degit URL; NS: prefixes their names.",
)


_MCP_ENV_OPTION = typer.Option(
    None,
    "--mcp-env",
    metavar = "[NAME:]KEY=VALUE",
    rich_help_panel = _PANEL_SESSION,
    help = "Set an env var on one --mcp-stdio server.",
)


_COMPACT_AT_OPTION = typer.Option(
    None,
    "--compact-at",
    min = 0.5,
    max = 0.95,
    rich_help_panel = _PANEL_SESSION,
    help = "Compact at this fraction of the window; see Auto-compaction.",
)


_HEADER_NAME_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")  # RFC 7230 tchar


_HEADER_VALUE_TEXT = re.compile(r"[\t\x20-\x7e]*")  # RFC 7230 field VCHAR + SP + HTAB


# One NAME for every [NAME:] prefix (--url, --api-key, --mcp-*) and the MCP registry.
_SERVER_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")


def parse_headers(values: Optional[list]) -> dict:
    """Validate and merge repeated --header NAME=VALUE pairs; a later name wins, case-insensitively."""
    headers = {}
    for value in values or []:
        name, separator, header_value = value.partition("=")
        if not separator:
            _fail(f"--header needs NAME=VALUE, got {value!r}.")
        if not _HEADER_NAME_TOKEN.fullmatch(name):
            _fail(f"--header name {name!r} has characters an HTTP header name cannot carry.")
        if "\r" in header_value or "\n" in header_value:
            _fail(f"--header value for {name!r} cannot carry a line break.")
        if not _HEADER_VALUE_TEXT.fullmatch(header_value):
            _fail(f"--header value for {name!r} has characters an HTTP header value cannot carry.")
        headers = {k: v for k, v in headers.items() if k.lower() != name.lower()}
        headers[name] = header_value
    return headers


def parse_server_headers(values: Optional[list], servers: list) -> dict:
    """Validate repeated --header [SERVER:]NAME=VALUE pairs into {server: headers}.

    A pair without a SERVER: prefix goes to every server. A header name cannot carry ':', so
    splitting the name segment on its first ':' is never ambiguous and no valid --header input
    changes meaning.
    """
    scoped: dict = {server: {} for server in servers}
    for value in values or []:
        pair, separator, header_value = value.partition("=")
        if not separator:
            _fail(f"--header needs NAME=VALUE, got {value!r}.")
        server, colon, header_name = pair.partition(":")
        if not colon:
            server, header_name = None, pair
        elif server not in scoped:
            _fail(f"--header names unknown server {server!r}. Servers: {', '.join(servers)}")
        if not _HEADER_NAME_TOKEN.fullmatch(header_name):
            _fail(f"--header name {header_name!r} has characters an HTTP header name cannot carry.")
        if "\r" in header_value or "\n" in header_value:
            _fail(f"--header value for {header_name!r} cannot carry a line break.")
        if not _HEADER_VALUE_TEXT.fullmatch(header_value):
            _fail(f"--header value for {header_name!r} has characters an HTTP header value cannot carry.")
        for name in servers if server is None else [server]:
            kept = {k: v for k, v in scoped[name].items() if k.lower() != header_name.lower()}
            kept[header_name] = header_value
            scoped[name] = kept
    return scoped


def parse_server_keys(values: Optional[list], servers: list) -> dict:
    """Validate repeated --api-key [SERVER:]KEY values into {server: key}; a later one wins.

    SERVER: only names a server when it is one of `servers`, so a key carrying : keeps its whole
    value. A key without a server in front belongs to the single server; with several it would go
    to the wrong one, so it fails.
    """
    keys: dict = {}
    for value in values or []:
        server, separator, key = value.partition(":")
        if not separator or server not in servers:
            server, key = None, value
        if server is None:
            if len(servers) > 1:
                _fail(
                    "--api-key needs SERVER:KEY with more than one --url, so a key never reaches "
                    f"the wrong server. Servers: {', '.join(servers)}"
                )
            server = servers[0]
        keys[server] = key
    return keys


# Per-agent CLI flag for "run tools without prompting". OpenCode (native --auto is command-scoped, handled below) is absent from this prefix map.
_YOLO_COMMAND_FLAGS = {
    "claude": ["--dangerously-skip-permissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    # Pi never prompts per tool call; its only approval gate is project trust, so -a (trust project resources) is the closest "do not ask me" equivalent.
    "pi": ["--approve"],
}


def _yolo_command_flags(agent: str, yolo: bool) -> list:
    # .get so a config-based agent (or a typo) yields no flag instead of a KeyError.
    return _YOLO_COMMAND_FLAGS.get(agent, []) if yolo else []


# Other spellings an agent CLI accepts for a flag agent-switch sends, checked against `--help`.
_AGENT_FLAG_ALIASES = {
    "codex": {"--yolo": "--dangerously-bypass-approvals-and-sandbox", "-p": "--profile"},
    "pi": {"-a": "--approve"},
}


def _drop_overridden_args(agent: str, agent_args: list, sent: list) -> list:
    """Drop agent args that repeat a flag agent-switch already sends; agent-switch's own wins."""
    names = {flag for flag in sent if flag.startswith("-")}
    # A sent flag followed by a non-flag token takes a value, so a repeat's detached value goes too.
    takes_value = {
        flag
        for flag, following in zip(sent, sent[1:])
        if flag in names and not following.startswith("-")
    }
    aliases = _AGENT_FLAG_ALIASES.get(agent, {})
    kept = []
    index = 0
    while index < len(agent_args):
        arg = agent_args[index]
        if arg == "--":
            kept.extend(agent_args[index:])
            break
        name, equals, _ = arg.partition("=")
        name = aliases.get(name, name)
        if name not in names:
            kept.append(arg)
            index += 1
            continue
        dropped = agent_args[index : index + (2 if not equals and name in takes_value else 1)]
        typer.echo(
            f"agent-switch: dropped {' '.join(dropped)}; agent-switch's {name} applies.",
            err = True,
        )
        index += len(dropped)
    return kept


class LoadOptions(NamedTuple):
    """Model-load knobs used when --model triggers a load on the server."""

    max_seq_length: int = 0
    # --no-model-load: never load, reload or unload a model on the server.
    allow_load: bool = True


class ServerOptions(NamedTuple):
    """Start flags: carried fields ride in the agent's requests."""

    reasoning: Optional[Literal["on", "off", "auto"]] = None
    reasoning_effort: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    min_p: Optional[float] = None
    repetition_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    # Fields the agent's own config sends with each request, kept off the server.
    carried: frozenset = frozenset()
    # The server the body goes to; others name some fields differently or drop them. None keeps
    # the body untranslated.
    provider: Optional[str] = None

    def sent_by_agent(self) -> frozenset:
        # A request cannot ask for the template default back, so auto stays a server setting.
        unsent = {"reasoning"} if self.reasoning == "auto" else set()
        if self.reasoning_effort not in _REASONING_EFFORTS:
            unsent.add("reasoning_effort")
        return self.carried - unsent

    def request_body(self) -> dict:
        sent = self.sent_by_agent()
        body = {
            name: getattr(self, name)
            for name in _SAMPLING_FIELDS
            if name in sent and getattr(self, name) is not None
        }
        if "reasoning" in sent and self.reasoning in ("on", "off"):
            body["enable_thinking"] = self.reasoning == "on"
        if "reasoning_effort" in sent:
            body["reasoning_effort"] = self.reasoning_effort
        return providers.request_body(self.provider, body)[0] if self.provider else body


_SAMPLING_FIELDS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repetition_penalty",
    "presence_penalty",
)


_REASONING_FIELDS = frozenset({"reasoning", "reasoning_effort"})


_ALL_REQUEST_FIELDS = frozenset(_SAMPLING_FIELDS) | _REASONING_FIELDS


_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "max", "xhigh")


def _split_repo_variant(model: str) -> tuple:
    """Split ``org/name:QUANT`` into ``("org/name", "QUANT")``, the ``:variant`` shorthand some local servers accept. Local paths, Windows drive letters and ids without a ``:`` pass through unchanged."""
    s = (model or "").strip()
    if not s or s.startswith(("/", "./", "../", "~")) or s == ".":
        return s, None
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():  # Windows drive, e.g. C:\models\x
        return s, None
    if ":" not in s:
        return s, None
    repo, _, variant = s.rpartition(":")
    if not repo or not variant or "/" in variant:
        return s, None
    return repo, variant


def _looks_like_model(token: str) -> bool:
    """True for a bare `org/name(:variant)` hub id or a `NAME:ID` server-prefixed id that is not a flag or a local path. Reuses `_is_hub_model_id`, so a relative dir like `owner/repo` that actually exists is left for the agent instead of being taken as a model; a non-existent `org/name` is treated as a hub id. Whether a NAME prefix names a server is decided later against the parsed --url list, so the whole token stays the id when it does not."""
    if not token or token.startswith("-") or " " in token:
        return False
    repo, _ = _split_repo_variant(token)
    if _is_hub_model_id(repo):
        return True
    name, colon, rest = token.partition(":")
    if not colon or not _SERVER_NAME_RE.fullmatch(name):
        return False
    # A NAME: prefix, but not a URL (http://x) or a drive path (C:\tools\x).
    return bool(rest) and not rest.startswith(("/", "~")) and "\\" not in rest and "://" not in rest


def _consume_positional_model(model: Optional[str], args: list) -> tuple:
    """Route a leading `org/name` positional to --model when --model was not given. Only the FIRST token is considered so an option value like `--profile owner/repo` is never stolen, and only when --model is absent so an explicit --model always wins. Returns (model, remaining_args) with the consumed token removed from the passthrough."""
    args = list(args)
    if model or not args or not _looks_like_model(args[0]):
        return model, args
    return args[0], args[1:]


def _fail(message: str) -> NoReturn:
    typer.echo(message, err = True)
    raise typer.Exit(code = 1)


def _refuse_local_only(
    *,
    model: Optional[str] = None,
    max_seq_length: int = 0,
    max_tokens: Optional[int] = None,
    reasoning: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    top_k: Optional[int] = None,
    min_p: Optional[float] = None,
    repetition_penalty: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    compact_at: Optional[float] = None,
    api_key: Optional[list] = None,
    header: Optional[list] = None,
    model_load: bool = True,
    as_subagent: bool = False,
    persist: Optional[bool] = False,
    persist_default: Optional[bool] = False,
) -> None:
    """Fail naming the first flag that only makes sense with a local model server.

    Each value is compared against that option's real default, so only a flag the user
    actually gave is refused; dsh's --persist defaults to None, so both of its spellings
    count there. --api-key also arrives through AGENT_SWITCH_API_KEY, which is not the user
    typing the flag, so a value equal to that env var does not count.
    """
    # A repeated option reads its env var as one value per whitespace run, like the flag list.
    env_keys = os.environ.get("AGENT_SWITCH_API_KEY", "").split()
    given = (
        ("--model", model is not None),
        ("--context-length", max_seq_length != 0),
        ("--max-tokens", max_tokens is not None),
        ("--reasoning", reasoning is not None),
        ("--reasoning-effort", reasoning_effort is not None),
        ("--temperature", temperature is not None),
        ("--top-p", top_p is not None),
        ("--top-k", top_k is not None),
        ("--min-p", min_p is not None),
        ("--repetition-penalty", repetition_penalty is not None),
        ("--presence-penalty", presence_penalty is not None),
        ("--compact-at", compact_at is not None),
        ("--api-key", bool(api_key) and api_key != env_keys),
        ("--header", bool(header)),
        ("--no-model-load", not model_load),
        ("--as-subagent", as_subagent),
        ("--persist", persist is True),
        ("--no-persist", persist is False and persist_default is not False),
    )
    for flag, was_given in given:
        if was_given:
            _fail(f"{flag} needs --url or --provider.")


_HF_REPO_ID_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _is_hub_model_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if "\\" in text:
        return False
    if text.startswith(("/", "./", "../", "~")):
        return False
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        return False
    # A hub id is exactly "namespace/name" over a restricted charset. Anything with extra path segments (a relative path such as models/Llama/Foo.gguf) is not a hub id. The existence probe below leaves a local directory of that shape to the agent.
    parts = text.split("/")
    if len(parts) != 2:
        return False
    if any(part in ("", ".", "..") or not _HF_REPO_ID_SEGMENT_RE.match(part) for part in parts):
        return False
    try:
        if Path(os.path.expanduser(text)).exists():
            return False
    except OSError:
        return False
    return True


def _check_compact_at(compact_at: Optional[float], model: dict) -> None:
    """--compact-at scales off the server-reported window; without one there is nothing to scale."""
    if compact_at is not None and not (model.get("context_length") or model.get("max_context_length")):
        typer.echo(
            "Warning: the server did not report the model's context length, so --compact-at is ignored.",
            err = True,
        )


def opencode_output_limit(window: int, max_tokens: Optional[int] = None) -> int:
    if max_tokens:
        return max(1, min(int(max_tokens), window // 2))
    return max(1, min(window // 4, _OPENCODE_OUTPUT_TOKEN_MAX))


def _agent_output_limit(window: int, max_tokens: Optional[int]) -> int:
    output = opencode_output_limit(window, max_tokens)
    if max_tokens and output < max_tokens:
        typer.echo(
            f"Warning: --max-tokens {max_tokens} leaves too little of the {window:,}-token "
            f"context for the conversation; using {output:,}.",
            err = True,
        )
    return output


def _get_compaction_reserve(window: int, ratio: float) -> int:
    return max(1, int(window * (1 - ratio)))
