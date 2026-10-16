# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""Target resolution, env printing and the launch of the agent child process."""

import contextlib
import os
import re
import shlex
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Optional

import click
import typer

from agent_switch import providers
from agent_switch.core import proxy
from agent_switch.core.install import (
    _augment_path_with_install_dirs,
    _prefer_windows_cmd_sibling,
    _resolve_or_install_agent,
)
from agent_switch.core.mcp import _auto_mcp_name, _parse_name_value
from agent_switch.core.options import (
    LoadOptions,
    ServerOptions,
    _SAMPLING_FIELDS,
    _fail,
    parse_headers,
    parse_server_headers,
    parse_server_keys,
)
from agent_switch.core.platform import (
    _NPM_NATIVE_CMD_SHIM,
    _NPM_NODE_CMD_SHIMS,
    _apply_windows_environment,
    _merge_wslenv,
    _npm_node_shim_metadata,
    _powershell_quote,
    _wsl_bridge_names,
    _wsl_windows_executable,
)
from agent_switch.core.storage import _cached_keys, _provider_key_cache_path, _remember_key
from agent_switch.providers.types import ProviderError, Target


def _print_env(
    env: dict,
    command: list,
    unset_env: tuple = (),
    wsl_env_bridge: tuple = (),
) -> None:
    if os.name == "nt":
        for name in unset_env:
            typer.echo(f"Remove-Item Env:{name} -ErrorAction SilentlyContinue")
        for name, value in env.items():
            # PowerShell: ` is the escape char, and $ triggers expansion inside "".
            escaped = value.replace("`", "``").replace('"', '`"').replace("$", "`$")
            typer.echo(f'$env:{name} = "{escaped}"')
        typer.echo(" ".join(_powershell_quote(arg) for arg in command))
        return
    for name in unset_env:
        typer.echo(f"export {name}=" if wsl_env_bridge else f"unset {name}")
    for name, value in env.items():
        typer.echo(f"export {name}={shlex.quote(value)}")
    if wsl_env_bridge:
        typer.echo(
            f"export WSLENV={shlex.quote(_merge_wslenv(os.environ.get('WSLENV', ''), wsl_env_bridge))}"
        )
    # The final line is a SELF-CONTAINED one-liner (inline env, VAR=... cmd) rather than a bare command. People copy just the last line, and a bare `codex`/`claude` would then run against their real ~/.codex or Anthropic credentials with zero isolation, for example inheriting a pre-existing damaged ~/.codex state DB and blaming the recipe. Inline assignments scope every var, and empty-string the conflicting ones, to this single invocation, so a partial copy behaves the same as pasting the whole block.
    inline = [f"{name}=" for name in unset_env]
    inline += [f"{name}={shlex.quote(value)}" for name, value in env.items()]
    if wsl_env_bridge:
        inline.append(
            f"WSLENV={shlex.quote(_merge_wslenv(os.environ.get('WSLENV', ''), wsl_env_bridge))}"
        )
    typer.echo(" ".join((*inline, shlex.join(command))))


def _wsl_shim_env(command: list, env: dict, unset_env: tuple) -> tuple[dict, tuple]:
    if not _wsl_windows_executable(command):
        return env, ()
    wsl_env_bridge = _wsl_bridge_names(env, unset_env)
    if not wsl_env_bridge:
        return env, ()
    # Bridge PWD via WSLENV (PWD/p) so the Windows shim finds its project root from the live cwd, not a stale inherited Linux PWD. Do not freeze env["PWD"]: a --no-launch recipe must translate the live PWD when run, not when generated; _launch overrides it.
    return env, tuple(dict.fromkeys((*wsl_env_bridge, "PWD/p")))


def _resolved_launch_command(
    executable: str,
    arguments: list,
    environment: Optional[dict] = None,
) -> list:
    """Return an argv that preserves arguments through standard Windows npm shims."""
    # _launch resolves with raw shutil.which, so rescue here too; the sibling then enters the parser below.
    executable = _prefer_windows_cmd_sibling(executable)
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        # cmd.exe treats CR/LF inside `%*` as command separators, and Windows PowerShell's native-command bridge also rewrites embedded quotes. Match complete cmd-shim templates so custom wrappers keep their setup behavior.
        with contextlib.suppress(OSError, UnicodeError, IndexError):
            shim = Path(executable)
            contents = shim.read_text(encoding = "utf-8").replace("\r\n", "\n").strip()
            for pattern in _NPM_NODE_CMD_SHIMS:
                match = pattern.fullmatch(contents)
                if match is None:
                    continue
                relative = Path(*re.split(r"[\\/]+", match.group("target")))
                target = (shim.parent / relative).resolve()
                if not target.is_file() or not any(
                    part.casefold() == "node_modules" for part in target.parts
                ):
                    continue
                metadata = _npm_node_shim_metadata(target, match, environment or os.environ)
                if metadata is None:
                    continue
                node_args, environment_updates = metadata
                bundled_node = shim.parent / "node.exe"
                node = str(bundled_node) if bundled_node.is_file() else shutil.which("node.exe")
                if node:
                    if environment is not None:
                        _apply_windows_environment(environment, environment_updates)
                    return [node, *node_args, str(target), *arguments]

            match = _NPM_NATIVE_CMD_SHIM.fullmatch(contents)
            if match is not None:
                relative = Path(*re.split(r"[\\/]+", match.group("target")))
                target = (shim.parent / relative).resolve()
                if (
                    target.is_file()
                    and any(part.casefold() == "node_modules" for part in target.parts)
                    and target.suffix.lower() in {".exe", ".com"}
                ):
                    return [str(target), *arguments]
    return [executable, *arguments]


def _launch(
    command: list,
    env: dict,
    install_hint: str,
    unset_env: tuple = (),
) -> int:
    # Resolve well-known install dirs (~/.local/bin) first, so an already-installed agent not yet on PATH is found instead of prompting a needless reinstall.
    _augment_path_with_install_dirs()
    executable = _resolve_or_install_agent(command[0], install_hint, shutil.which)
    env, wsl_env_bridge = _wsl_shim_env(command, env, unset_env)
    child_env = dict(os.environ)
    if wsl_env_bridge:
        # Override stale inherited PWD with the real cwd so the shim resolves the project root.
        env = {**env, "PWD": os.getcwd()}
        child_env["WSLENV"] = _merge_wslenv(child_env.get("WSLENV", ""), wsl_env_bridge)
        for name in unset_env:
            child_env[name] = ""
    else:
        for name in unset_env:
            child_env.pop(name, None)
    child_env.update(env)
    if os.name != "nt" and not wsl_env_bridge:
        # Keep POSIX child processes from seeing a stale inherited PWD when subprocess cwd was changed by the caller. Some Node CLIs use PWD for project-root discovery instead of process.cwd().
        child_env["PWD"] = os.getcwd()
    # Ctrl+C cancels a turn inside the agent; do not let it kill this wrapper. A no-op handler, not SIG_IGN: exec preserves an ignored signal but resets a caught one.
    previous = signal.signal(signal.SIGINT, lambda *_: None)
    try:
        launch_command = _resolved_launch_command(executable, command[1:], child_env)
        code = subprocess.run(launch_command, env = child_env).returncode
    finally:
        signal.signal(signal.SIGINT, previous)
    # Negative returncode means killed by signal N; shells expect 128+N.
    return code if code >= 0 else 128 - code


# The server this invocation talks to, for the status lines _run prints. Set by _resolve_target and _connect.
_active_target: Optional[Target] = None


# Set by the group in start.py when the invoked name is an agent alias like `claude-personal`:
# the user's own shell name, and the agent kind it was matched to. Click shares ctx.meta with
# child contexts, so the command function reads both.
_ALIAS_META = "agent_switch.alias"


_ALIAS_KIND_META = "agent_switch.alias_kind"


# The alias name goes into a `bash -ic` string, so it must stay shell-word safe.
_ALIAS_NAME = re.compile(r"[A-Za-z0-9._-]+")


def _agent_command(alias: Optional[str], agent: str, args: list, unset_env: tuple = ()) -> list:
    """The agent's argv; an alias replaces only argv[0], and the env still reaches the bash process."""
    if alias is None:
        return [agent, *args]
    script = f'{alias} "$@"'
    if unset_env:
        # bash -ic re-reads the shell config, which could re-export what the session cleared.
        script = "unset " + " ".join(unset_env) + "; " + script
    # `agent-switch` is $0, so "$@" is exactly the agent's arguments. No exec: bash cannot exec a function.
    return ["bash", "-ic", script, "agent-switch", *args]


def _check_alias(alias: str) -> None:
    """Fail unless bash can name and start the user's own function, alias or script."""
    if os.name == "nt":
        _fail(
            f"`{alias}` is an agent alias, which agent-switch starts through bash; "
            "Windows is not supported."
        )
    if shutil.which("bash") is None:
        _fail(f"`{alias}` is an agent alias, which needs `bash` on PATH.")
    if not _ALIAS_NAME.fullmatch(alias):
        _fail(f"`{alias}` carries characters an agent alias name cannot have.")
    probed = subprocess.run(
        ["bash", "-ic", f"type -t {alias}"],
        capture_output = True,
        text = True,
    )
    kind = probed.stdout.strip()
    if kind not in ("function", "alias", "file"):
        _fail(
            f"`{alias}` is not a bash function, alias or file in `bash -ic`'s shell; define it "
            'in your shell config forwarding "$@" to the agent, or use the plain subcommand.'
        )
    if kind == "file":
        path = subprocess.run(
            ["bash", "-ic", f"command -v {alias}"],
            capture_output = True,
            text = True,
        ).stdout.strip()
        try:
            definition = Path(path).read_text(encoding = "utf-8", errors = "replace")
        except OSError:
            definition = ""
    else:
        printer = "declare -f" if kind == "function" else "alias"
        definition = subprocess.run(
            ["bash", "-ic", f"{printer} {alias}"],
            capture_output = True,
            text = True,
        ).stdout
    if "agent-switch" in definition:
        _fail(
            f"`{alias}` runs agent-switch itself, so `agent-switch {alias}` would recurse; "
            "run what its definition runs instead."
        )


_REQUEST_FLAGS = {
    **{name: "--" + name.replace("_", "-") for name in _SAMPLING_FIELDS},
    "enable_thinking": "--reasoning",
    "reasoning": "--reasoning",
    "reasoning_effort": "--reasoning-effort",
}


def _resolve_target(
    urls: Optional[list],
    provider: Optional[str],
    api_keys: Optional[list] = None,
    header: Optional[list] = None,
    *,
    ctx: typer.Context,
    launch: bool = True,
) -> tuple:
    """(Target, agent headers) for --url/--provider; (None, {}) means native launch.

    One --url keeps the direct route and returns that server's headers for the agent to send.
    Two or more return the first server with every server in `upstreams` and no agent headers:
    _connect starts the proxy, which adds each server's key and headers itself. Every server's
    key for the whole session is settled here and rides on its Target, so nothing reads the
    key cache again later: two --url on one base keep their own keys.
    """
    global _active_target
    cache = _provider_key_cache_path()
    # Where --api-key got its values decides how they are read: click splits a repeated option's
    # env var on whitespace, so an env value stays one whole key with no SERVER: in front. typer
    # vendors its own click, so this enum is not click's; compare the name.
    source = getattr(ctx.get_parameter_source("api_key"), "name", "")
    env_key = os.environ.get("AGENT_SWITCH_API_KEY") if source == "ENVIRONMENT" else None
    if not urls:
        # Native launch, or --provider's usual port; a --header there is refused as local-only.
        headers = parse_headers(header)
        api_key = env_key or (api_keys[-1] if api_keys else None)
        try:
            target = providers.resolve_target(None, provider, api_key, headers)
        except ProviderError as exc:
            _fail(str(exc))
        if target is not None:
            key = api_key or next(iter(_cached_keys(cache, target.base)), None)
            target = target._replace(api_key = key, is_given_key = bool(api_key))
        _active_target = target
        return (None, {}) if target is None else (target, headers)
    if len(urls) == 1:
        # NAME: only names this server for --header and --api-key; without it both apply to it.
        name, url = _parse_name_value(urls[0], wants_url = True)
        headers = parse_server_headers(header, [name])[name] if name else parse_headers(header)
        given = env_key or parse_server_keys(api_keys, [name]).get(name)
        key = given or next(iter(_cached_keys(cache, providers.root_url(url))), None)
        try:
            target = providers.resolve_target(url, provider, key, headers)
        except ProviderError as exc:
            _fail(str(exc))
        _active_target = target._replace(api_key = key, is_given_key = bool(given))
        return _active_target, headers
    if not launch:
        _fail(
            "--no-launch cannot be combined with more than one --url: the proxy that serves them "
            "only lives while agent-switch runs."
        )
    if provider:
        _fail(
            "--provider cannot be combined with more than one --url: each server's type is detected."
        )
    servers = []
    for value in urls:
        name, url = _parse_name_value(value, wants_url = True)
        servers.append((name or _auto_mcp_name(url, "--url"), url))
    names = [name for name, _url in servers]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        _fail(
            f"Two --url servers are both named {', '.join(repeated)}; "
            "give each a distinct NAME:URL."
        )
    # Only a typed --api-key names a server here: AGENT_SWITCH_API_KEY is a standing setting that
    # names none of them, so it is ignored.
    given = parse_server_keys(api_keys, names) if source == "COMMANDLINE" else {}
    server_headers = parse_server_headers(header, names)
    targets = []
    for name, url in servers:
        given_key = given.get(name)
        key = given_key or next(iter(_cached_keys(cache, providers.root_url(url))), None)
        try:
            target = providers.resolve_target(url, None, key, server_headers[name])
        except ProviderError as exc:
            _fail(str(exc))
        targets.append(
            target._replace(server = name, api_key = key, is_given_key = bool(given_key))
        )
    _active_target = targets[0]._replace(upstreams = tuple(targets))
    return _active_target, {}


def _warn_unsent_pins(server_options: ServerOptions) -> None:
    """Sampling/reasoning pins this agent cannot carry in its own requests are ignored."""
    sent = server_options.sent_by_agent()
    unsent = [
        _REQUEST_FLAGS[name]
        for name in (*_SAMPLING_FIELDS, "reasoning", "reasoning_effort")
        if getattr(server_options, name) is not None
        and name not in sent
        and not (name == "reasoning" and server_options.reasoning == "auto")
    ]
    if unsent:
        typer.echo(f"Warning: this agent can't send {', '.join(unsent)} itself, so it is ignored.", err = True)


def _connect(
    model: Optional[str],
    load: LoadOptions = LoadOptions(),
    *,
    server_options: ServerOptions = ServerOptions(),
    target: Target,
    needs: tuple = (),
) -> tuple:
    global _active_target
    _active_target = target
    label = providers.label(target.name)
    _warn_unsent_pins(server_options)
    _, dropped = providers.request_body(target.name, server_options._replace(provider = None).request_body())
    for name in dropped:
        typer.echo(f"Warning: {label} ignores {_REQUEST_FLAGS[name]}, so it is left out.", err = True)
    cache = _provider_key_cache_path()
    if target.upstreams:
        # Every server already carries the key settled for this session; the agent only ever sees
        # the proxy's token.
        keys = [upstream.api_key for upstream in target.upstreams]
        try:
            _, entry, routes = providers.connect_servers(
                target.upstreams,
                keys,
                model,
                load.max_seq_length or None,
                needs,
                allow_load = load.allow_load,
            )
            base, token = proxy.start_proxy(routes, (target.upstreams[0], keys[0]))
        except ProviderError as exc:
            _fail(str(exc))
        for upstream in target.upstreams:
            if upstream.is_given_key:
                _remember_key(cache, upstream.base, upstream.api_key)
        return base, token, entry
    key = target.api_key
    try:
        base, key, entry = providers.connect(
            target, key, model, load.max_seq_length or None, needs, allow_load = load.allow_load
        )
    except ProviderError as exc:
        _fail(str(exc))
    if target.is_given_key:
        _remember_key(cache, base, target.api_key)
    return base, key, entry


def _get_served_models(
    target: Target, key: Optional[str], context_length: Optional[int] = None
) -> list:
    try:
        if not target.upstreams:
            return providers.get_served_models(target, key, context_length)
        # Several servers: each listing uses the key settled for it at resolve time, and every id
        # gains its server prefix.
        served_models = []
        for upstream in target.upstreams:
            for served in providers.get_served_models(upstream, upstream.api_key, context_length):
                served_models.append({**served, "id": f"{upstream.server}:{served['id']}"})
        return served_models
    except ProviderError as exc:
        _fail(str(exc))


def _run(
    base: str,
    entry: dict,
    env: dict,
    command: list,
    *,
    launch: bool,
    install_hint: str,
    unset_env: tuple = (),
    clear_screen: bool = False,
) -> None:
    # Some agents (Pi) render inline from wherever the cursor sits: their first paint assumes a clean screen rather than clearing or entering the alternate screen themselves. Hand them one so the session does not start mid-scroll under our connection output. click.clear() is cross-platform and a no-op when stdout is not a terminal, so transcripts and --no-launch recipes stay intact.
    if launch and clear_screen:
        click.clear()
    if _active_target.upstreams:
        typer.echo(f"agent-switch proxy ready at {base} · model {entry['id']}")
        for upstream in _active_target.upstreams:
            typer.echo(f"{upstream.server}: {providers.label(upstream.name)} at {upstream.base}")
    else:
        typer.echo(f"{providers.label(_active_target.name)} ready at {base} · model {entry['id']}")
    if not launch:
        env, wsl_env_bridge = _wsl_shim_env(command, env, unset_env)
        _print_env(
            env,
            command,
            unset_env = unset_env,
            wsl_env_bridge = wsl_env_bridge,
        )
        return
    code = _launch(
        command,
        env,
        install_hint = install_hint,
        unset_env = unset_env,
    )
    if code:
        # A failed session must not end silently behind the agent's own output.
        typer.echo(f"The agent exited with code {code}.")
    raise typer.Exit(code = code)


def _run_native(
    agent: str,
    env: dict,
    command: list,
    *,
    launch: bool,
    install_hint: str,
    unset_env: tuple = (),
    clear_screen: bool = False,
) -> None:
    """Native launch: the agent keeps its own model, login and config; only shared flags were added."""
    if launch and clear_screen:
        click.clear()
    typer.echo(f"{agent} runs with its own model, login and config.")
    if not launch:
        env, wsl_env_bridge = _wsl_shim_env(command, env, unset_env)
        _print_env(
            env,
            command,
            unset_env = unset_env,
            wsl_env_bridge = wsl_env_bridge,
        )
        return
    code = _launch(
        command,
        env,
        install_hint = install_hint,
        unset_env = unset_env,
    )
    if code:
        typer.echo(f"The agent exited with code {code}.")
    raise typer.Exit(code = code)
