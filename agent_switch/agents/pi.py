# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""`agent-switch pi`: Pi config, compaction, user resources and subagent config."""

import json
import os
from pathlib import Path
from typing import Literal, Optional

import typer

from agent_switch.core.install import (
    _agent_version_at_least,
    _npm_install_hint,
    _require_agent_for_launch,
)
from agent_switch.core.launch import (
    _ALIAS_META,
    _agent_command,
    _check_alias,
    _connect,
    _get_served_models,
    _resolve_target,
    _run,
    _run_native,
)
from agent_switch.core.mcp import load_mcp_servers
from agent_switch.core.overlay import write_native_overlay
from agent_switch.core.options import (
    LoadOptions,
    ProviderName,
    ServerOptions,
    _ALL_REQUEST_FIELDS,
    _AS_SUBAGENT_OPTION,
    _COMPACT_AT_OPTION,
    _CONTEXT_OPTION,
    _HEADER_OPTION,
    _KEY_OPTION,
    _LAUNCH_OPTION,
    _MAX_TOKENS_OPTION,
    _MCP_ALL_OPTION,
    _MCP_ENV_OPTION,
    _MCP_HEADER_OPTION,
    _MCP_OAUTH_URL_OPTION,
    _MCP_OPTION,
    _MCP_STDIO_OPTION,
    _MCP_URL_OPTION,
    _MIN_P_OPTION,
    _MODEL_LOAD_OPTION,
    _MODEL_OPTION,
    _PERSIST_OPTION,
    _PRESENCE_PENALTY_OPTION,
    _PROVIDER_OPTION,
    _REASONING_EFFORT_OPTION,
    _REASONING_OPTION,
    _REPETITION_PENALTY_OPTION,
    _SKILL_OPTION,
    _SKILL_SET_OPTION,
    _TEMPERATURE_OPTION,
    _TOP_K_OPTION,
    _TOP_P_OPTION,
    _URL_OPTION,
    _YOLO_OPTION,
    _agent_output_limit,
    _drop_overridden_args,
    _fail,
    _get_compaction_reserve,
    _refuse_local_only,
    _yolo_command_flags,
    opencode_output_limit,
)
from agent_switch.core.platform import (
    _create_directory_junction,
    _is_junction,
    _remove_overlay_entry,
    _wsl_windows_executable,
)
from agent_switch.core.session import _agent_config_path, _session_config
from agent_switch.core.skills import load_skills, write_skills_overlay
from agent_switch.core.storage import _read_json_object, _subdict, _write_private_json


_PI_PROVIDER = "agent-switch"


_PI_SUBAGENT_EXTENSION = Path(__file__).parent / "pi_subagent.ts"


_PI_USER_RESOURCE_DIRS = ("extensions", "skills", "prompts", "themes", "npm", "git")


_PI_USER_RESOURCE_SETTINGS = ("packages", "extensions", "skills", "prompts", "themes")


_PI_USER_VERBATIM_SETTINGS = ("npmCommand",)


_PI_USER_RESOURCES_MANIFEST = ".agent-switch-user-resources.json"


_PI_SAMPLING_PARAMS_MIN_VERSION = (0, 84, 0)


def _pi_config_value(value: str) -> str:
    """Pi resolves $NAME and !command in apiKey and header values; doubling $ keeps a literal value intact,
    and a leading ! is prefixed with $ so pi's $! escape makes it literal instead of a shell command."""
    value = value.replace("$", "$$")
    return f"${value}" if value.startswith("!") else value


def _pi_header_values(headers: dict) -> dict:
    return {name: _pi_config_value(value) for name, value in headers.items()}


def _pi_model_entry(
    model: dict,
    max_tokens: Optional[int],
    request_body: Optional[dict],
    *,
    should_warn: bool = True,
) -> dict:
    entry = {"id": model["id"]}
    window = model.get("context_length") or model.get("max_context_length")
    if window:
        window = int(window)
        # An unspecified model defaults to contextWindow 128000 / maxTokens 16384, far larger than a small local context, so Pi compacts too late and overflows the server. Pin the real window and a sane output cap, mirroring OpenCode. Only the session model's clamp is worth a warning; repeating it for every served model is noise.
        entry["contextWindow"] = window
        entry["maxTokens"] = (
            _agent_output_limit(window, max_tokens)
            if should_warn
            else opencode_output_limit(window, max_tokens)
        )
    elif max_tokens:
        entry["maxTokens"] = max_tokens
    if request_body:
        entry["samplingParams"] = request_body
    return entry


def write_pi_config(
    base: str,
    key: str,
    model: dict,
    path: Path,
    *,
    max_tokens: Optional[int] = None,
    request_body: Optional[dict] = None,
    headers: Optional[dict] = None,
    served_models: Optional[list] = None,
) -> None:
    config = _read_json_object(path)
    if config is None:
        typer.echo(
            f"Warning: couldn't parse {path} — add an '{_PI_PROVIDER}' provider there "
            "yourself, or move the file aside and re-run.",
            err = True,
        )
        return
    before = json.dumps(config, sort_keys = True)
    # Pi reads custom providers from ~/.pi/agent/models.json (HOME-relocated for the session). The server is a generic OpenAI-compatible /v1 endpoint, and the key lives in the config rather than the env, matching opencode.
    # List every model the server serves so /model can switch between them, as OpenCode does;
    # the connected entry overrides its own row so the loaded or --context-length window wins.
    catalog = {served["id"]: served for served in served_models or []}
    catalog[model["id"]] = model
    provider_entry = {
        "api": "openai-completions",
        "baseUrl": f"{base}/v1",
        # pi refuses the prompt without an apiKey ("No API key found for ..."); its OpenAI SDK merges
        # the custom Authorization over the Bearer later (case-insensitively), so one header wins.
        "apiKey": _pi_config_value(key),
        **({"headers": _pi_header_values(headers)} if headers else {}),
        "models": [
            _pi_model_entry(served, max_tokens, request_body, should_warn = served_id == model["id"])
            for served_id, served in catalog.items()
        ],
    }
    _subdict(config, "providers")[_PI_PROVIDER] = provider_entry
    if json.dumps(config, sort_keys = True) != before:
        _write_private_json(path, config)
        typer.echo(f"Updated {path}")


_PI_COMPACTION_KEYS = ({"enabled", "reserveTokens"}, {"enabled", "reserveTokens", "modelOverrides"})


def write_pi_compaction(
    agent_dir: Path,
    model: dict,
    compact_at: Optional[float],
    served_models: Optional[list] = None,
) -> None:
    """Scale Pi's auto-compaction trigger to --compact-at in the session settings."""
    path = agent_dir / "settings.json"
    settings = _read_json_object(path)
    if settings is None:
        return  # write_pi_user_resources already warned
    window = model.get("context_length") or model.get("max_context_length")
    before = json.dumps(settings, sort_keys = True)
    if compact_at is not None and window:
        # Pi compacts once the context exceeds contextWindow - reserveTokens. The ordinary
        # reserveTokens only fits the launch model, so every other served model gets a
        # per-model override keyed "<provider>/<id>" (settings-manager.js
        # getCompactionTokenSetting); a smaller-window model would otherwise go negative
        # and compact from the very first token.
        compaction = {
            "enabled": True,
            "reserveTokens": _get_compaction_reserve(int(window), compact_at),
        }
        catalog = {served["id"]: served for served in served_models or []}
        catalog[model["id"]] = model
        overrides = {}
        for served_id, served in catalog.items():
            if served_id == model["id"]:
                continue  # the ordinary reserveTokens already fits the launch model
            served_window = served.get("context_length") or served.get("max_context_length")
            if served_window:
                overrides[f"{_PI_PROVIDER}/{served_id}"] = {
                    "reserveTokens": _get_compaction_reserve(int(served_window), compact_at),
                }
        if overrides:
            compaction["modelOverrides"] = overrides
        settings["compaction"] = compaction
    else:
        # Undo a --compact-at block an earlier run left in a persisted session; compaction
        # is not a key this session inherits from the user, so these exact shapes are ours.
        compaction = settings.get("compaction")
        if (
            isinstance(compaction, dict)
            and compaction.keys() in _PI_COMPACTION_KEYS
            and compaction["enabled"] is True
            and isinstance(compaction["reserveTokens"], int)
            and all(
                isinstance(entry, dict)
                and entry.keys() == {"reserveTokens"}
                and isinstance(entry["reserveTokens"], int)
                for entry in compaction.get("modelOverrides", {}).values()
            )
        ):
            settings.pop("compaction", None)
    if json.dumps(settings, sort_keys = True) != before:
        _write_private_json(path, settings)


def _write_pi_mcp_config(path: Path, servers: dict, *, should_escape: bool = False) -> None:
    """The mounted MCP servers in pi's mcp.json shape; should_escape for pi's built-in MCP, which resolves env and header values."""
    escape = _pi_header_values if should_escape else dict
    _write_private_json(
        path,
        {
            "mcpServers": {
                name: (
                    {
                        "command": server["command"],
                        "args": server["args"],
                        "env": escape(server["env"]),
                    }
                    if server["transport"] == "stdio"
                    else {"url": server["url"], "headers": escape(server["headers"])}
                )
                for name, server in servers.items()
            }
        },
    )


def _link_user_dir(source: Path, target: Path) -> bool:
    """Expose source at target. True once target resolves to source."""
    # Refresh links, but preserve real session directories.
    if target.is_symlink() or _is_junction(target):
        _remove_overlay_entry(target)
    if target.exists() or not source.is_dir():
        return False
    target.parent.mkdir(parents = True, exist_ok = True, mode = 0o700)
    try:
        target.symlink_to(source, target_is_directory = True)
    except OSError:
        if not _create_directory_junction(source, target):
            typer.echo(f"Warning: couldn't link {source} into the Pi session.", err = True)
            return False
    return True


def _pi_local_entry(
    entry: str,
    source: Path,
    home: Path,
    linked: frozenset,
    agents_skills = None,
) -> str:
    """Re-anchor a user path from the original Pi agent directory."""
    value = entry.strip()
    if not value or value == "." or value.startswith("file:"):
        # Nothing to anchor: "" and "." would name the whole agent directory.
        return entry
    if value == "~" or value.startswith(("~/", "~" + os.sep)):
        target = os.path.join(home, value[2:])
    else:
        # Pi stores local packages relative to its agent directory.
        target = os.path.join(source, value)
    target = os.path.normpath(target)
    if agents_skills is not None:
        # Pi reads ~/.agents/skills through HOME, which moved, so a rule naming the
        # user's copy must follow it or it stops matching.
        user_root, session_root = agents_skills
        try:
            inside = os.path.relpath(target, user_root)
        except ValueError:  # on another Windows drive
            inside = os.pardir
        if inside == os.curdir:
            return session_root
        if inside != os.pardir and not inside.startswith(os.pardir + os.sep):
            return os.path.join(session_root, inside)
    try:
        relative = os.path.relpath(target, source)
    except ValueError:  # on another Windows drive
        return target
    # Session-relative only where the link landed: a real session directory blocks
    # the link, and the entry would then point into it instead of at the user's.
    if relative.split(os.sep)[0] in linked:
        return relative
    return target


def _pi_settings_entries(
    key: str,
    entries,
    source: Path,
    home: Path,
    linked: frozenset,
    agents_skills = None,
) -> list:
    if not isinstance(entries, list):
        return []
    result = []
    for entry in entries:
        if key == "packages":
            spec = entry.get("source") if isinstance(entry, dict) else entry
            # All other package sources are local paths.
            if isinstance(spec, str) and not spec.strip().startswith(
                ("npm:", "git:", "github:", "http:", "https:", "ssh:")
            ):
                spec = _pi_local_entry(spec, source, home, linked, agents_skills)
                entry = {**entry, "source": spec} if isinstance(entry, dict) else spec
        elif isinstance(entry, str):
            prefix = entry[:1] if entry.startswith(("!", "+", "-")) else ""
            pattern = entry[len(prefix) :]
            if not prefix and "*" not in entry and "?" not in entry:
                entry = _pi_local_entry(entry, source, home, linked, agents_skills)
            else:
                # Pi matches patterns against paths relative to the agent directory and expands ~
                # against HOME, both of which moved. Keep the original too: it still matches
                # basenames and linked directories.
                anchored = prefix + _pi_local_entry(
                    pattern,
                    source,
                    home,
                    linked,
                    agents_skills,
                )
                if anchored != entry:
                    result.append(entry)
                    entry = anchored
        result.append(entry)
    return result


def _clear_pi_user_resources(agent_dir: Path, home: Path) -> None:
    """Undo what an earlier launch linked and copied, leaving session state alone."""
    targets = [agent_dir / name for name in _PI_USER_RESOURCE_DIRS]
    targets.append(home / ".agents" / "skills")
    for target in targets:
        if target.is_symlink() or _is_junction(target):
            _remove_overlay_entry(target)
    manifest_path = agent_dir / _PI_USER_RESOURCES_MANIFEST
    previous = _read_json_object(manifest_path)
    if not previous:
        return
    settings_path = agent_dir / "settings.json"
    settings = _read_json_object(settings_path)
    if settings is None:
        return
    before = json.dumps(settings, sort_keys = True)
    for key, copied in previous.items():
        own = settings.get(key)
        if key in _PI_USER_VERBATIM_SETTINGS:
            # An argument vector, not entries: subtracting drops whatever the two share.
            if own == copied:
                settings.pop(key, None)
        elif isinstance(copied, list) and isinstance(own, list):
            rest = [item for item in own if item not in copied]
            if rest:
                settings[key] = rest
            else:
                settings.pop(key, None)
        elif own == copied:
            settings.pop(key, None)
    if json.dumps(settings, sort_keys = True) != before:
        _write_private_json(settings_path, settings)
    manifest_path.unlink(missing_ok = True)


def _get_pi_source_agent_dir(agent_dir: Path) -> Path:
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    configured = configured.strip() if configured else ""
    # Pi resolves a relative override from the launch directory.
    source = (
        Path(os.path.abspath(os.path.expanduser(configured)))
        if configured
        else Path.home() / ".pi" / "agent"
    )
    if source.resolve(strict = False) == agent_dir.resolve(strict = False):
        # Do not treat this session as its own resource source.
        source = Path.home() / ".pi" / "agent"
    return source


def write_pi_user_resources(agent_dir: Path, home: Path, *, skills: Optional[dict] = None) -> None:
    """Expose selected user Pi resources inside an isolated session."""
    skills_root = agent_dir / "skills"
    if (
        skills is None and not skills_root.is_symlink() and not _is_junction(skills_root)
        and (skills_root / ".agent-switch-skills.json").is_file()
    ):
        _remove_overlay_entry(skills_root)
    if _wsl_windows_executable(["pi"]):
        # Windows Pi cannot reliably follow WSL links into mounted drives.
        _clear_pi_user_resources(agent_dir, home)
        if skills is not None:
            write_skills_overlay(skills_root, None, skills, should_copy = True)
        return
    user_home = Path.home()
    source = _get_pi_source_agent_dir(agent_dir)
    configured = (os.environ.get("PI_CODING_AGENT_DIR") or "").strip()
    if configured and not source.is_dir():
        # Otherwise this looks exactly like the bug this function exists to fix.
        typer.echo(
            f"Warning: PI_CODING_AGENT_DIR points at {source}, which is not a directory; "
            "no Pi extensions or packages will load in this session.",
            err = True,
        )
    if skills is not None:
        write_skills_overlay(skills_root, source / "skills", skills)
    linked = frozenset(
        name for name in _PI_USER_RESOURCE_DIRS
        if (name == "skills" and skills is not None) or _link_user_dir(source / name, agent_dir / name)
    )
    # HOME is relocated, so link Pi's other global skill directory too.
    user_skills = user_home / ".agents" / "skills"
    session_skills = home / ".agents" / "skills"
    agents_skills = (
        (str(user_skills), str(session_skills))
        if _link_user_dir(user_skills, session_skills)
        else None
    )

    user_settings_path = source / "settings.json"
    user_settings = _read_json_object(user_settings_path)
    if user_settings is None:
        typer.echo(
            f"Warning: couldn't parse {user_settings_path}; "
            "Pi packages listed there won't load in this session.",
            err = True,
        )
        user_settings = {}
    settings_path = agent_dir / "settings.json"
    settings = _read_json_object(settings_path)
    if settings is None:
        typer.echo(
            f"Warning: couldn't parse {settings_path}; your Pi packages won't load in this session.",
            err = True,
        )
        return
    manifest_path = agent_dir / _PI_USER_RESOURCES_MANIFEST
    previous = _read_json_object(manifest_path)
    if previous is None:
        # Provenance is lost, so entries the user has since removed cannot be reconciled.
        typer.echo(
            f"Warning: couldn't parse {manifest_path}; Pi resources copied by an earlier "
            "launch stay in this session even if you removed them since.",
            err = True,
        )
        previous = {}
    before = json.dumps(settings, sort_keys = True)
    copied = {}
    for key in _PI_USER_RESOURCE_SETTINGS:
        entries = _pi_settings_entries(
            key,
            user_settings.get(key),
            source,
            user_home,
            linked,
            agents_skills,
        )
        # Refresh copied entries while preserving settings added inside the session.
        stale = previous.get(key) if isinstance(previous.get(key), list) else []
        own = settings.get(key)
        if own is not None and not isinstance(own, list):
            # Pi types these as arrays; leave a shape we do not understand alone.
            typer.echo(
                f"Warning: {settings_path} has a non-list {key!r}; "
                "leaving it as is, so your Pi entries for it won't load in this session.",
                err = True,
            )
            continue
        own = [item for item in own or [] if item not in stale and item not in entries]
        if entries or own:
            # Pi de-dupes packages by identity keeping the FIRST, so session entries
            # lead. Patterns apply in order instead, so those stay user-first.
            settings[key] = own + entries if key == "packages" else entries + own
        else:
            settings.pop(key, None)
        if entries:
            copied[key] = entries
    for key in _PI_USER_VERBATIM_SETTINGS:
        # Pi runs every package lookup and install through npmCommand, so inheriting
        # the package list without it falls back to an npm that cannot find them.
        value = user_settings.get(key)
        own = settings.get(key)
        if own is not None and own != previous.get(key):
            continue  # changed inside the session, so the session owns it now
        if isinstance(value, list) and value and all(isinstance(arg, str) for arg in value):
            settings[key] = value
            copied[key] = value
        else:
            settings.pop(key, None)
    if json.dumps(settings, sort_keys = True) != before:
        _write_private_json(settings_path, settings)
    if copied != previous:
        if copied:
            _write_private_json(manifest_path, copied)
        else:
            manifest_path.unlink(missing_ok = True)


def write_pi_subagent_config(
    base: str,
    key: str,
    model: dict,
    path: Path,
    approve: bool = False,
    max_tokens: Optional[int] = None,
    request_body: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> None:
    """Write private bootstrap data for the bundled Pi extension."""
    window = model.get("context_length") or model.get("max_context_length")
    window = int(window) if window else 32768
    _write_private_json(
        path,
        {
            "baseUrl": f"{base}/v1",
            "apiKey": _pi_config_value(key),
            "model": model["id"],
            "contextWindow": window,
            "maxTokens": _agent_output_limit(window, max_tokens),
            "approve": approve,
            **({"headers": _pi_header_values(headers)} if headers else {}),
            **({"samplingParams": request_body} if request_body else {}),
        },
    )


def pi(
    ctx: typer.Context,
    model: Optional[str] = _MODEL_OPTION,
    api_key: Optional[list[str]] = _KEY_OPTION,
    header: Optional[list[str]] = _HEADER_OPTION,
    launch: bool = _LAUNCH_OPTION,
    max_seq_length: int = _CONTEXT_OPTION,
    max_tokens: Optional[int] = _MAX_TOKENS_OPTION,
    reasoning: Optional[Literal["on", "off", "auto"]] = _REASONING_OPTION,
    reasoning_effort: Optional[str] = _REASONING_EFFORT_OPTION,
    temperature: Optional[float] = _TEMPERATURE_OPTION,
    top_p: Optional[float] = _TOP_P_OPTION,
    top_k: Optional[int] = _TOP_K_OPTION,
    min_p: Optional[float] = _MIN_P_OPTION,
    repetition_penalty: Optional[float] = _REPETITION_PENALTY_OPTION,
    presence_penalty: Optional[float] = _PRESENCE_PENALTY_OPTION,
    compact_at: Optional[float] = _COMPACT_AT_OPTION,
    model_load: bool = _MODEL_LOAD_OPTION,
    url: Optional[list[str]] = _URL_OPTION,
    provider: Optional[ProviderName] = _PROVIDER_OPTION,
    yolo: bool = _YOLO_OPTION,
    persist: bool = _PERSIST_OPTION,
    as_subagent: bool = _AS_SUBAGENT_OPTION,
    mcp: Optional[list[str]] = _MCP_OPTION,
    mcp_all: bool = _MCP_ALL_OPTION,
    mcp_url: Optional[list[str]] = _MCP_URL_OPTION,
    mcp_oauth_url: Optional[list[str]] = _MCP_OAUTH_URL_OPTION,
    mcp_header: Optional[list[str]] = _MCP_HEADER_OPTION,
    mcp_stdio: Optional[list[str]] = _MCP_STDIO_OPTION,
    mcp_env: Optional[list[str]] = _MCP_ENV_OPTION,
    skill: Optional[list[str]] = _SKILL_OPTION,
    skill_set: Optional[list[str]] = _SKILL_SET_OPTION,
):
    """Point Pi (coding agent) at a local model server and start it."""
    # Validate the MCP selection before _connect, so a registry error fails fast.
    mcp_servers = load_mcp_servers(
        mcp,
        should_mount_all = mcp_all,
        urls = mcp_url,
        oauth_urls = mcp_oauth_url,
        headers = mcp_header,
        stdios = mcp_stdio,
        envs = mcp_env,
        as_subagent = as_subagent,
    )
    skills = load_skills(skill, skill_set, as_subagent = as_subagent)
    target, headers = _resolve_target(url, provider, api_key, header, ctx = ctx, launch = launch)
    if target is None:
        _refuse_local_only(
            model = model,
            max_seq_length = max_seq_length,
            max_tokens = max_tokens,
            reasoning = reasoning,
            reasoning_effort = reasoning_effort,
            temperature = temperature,
            top_p = top_p,
            top_k = top_k,
            min_p = min_p,
            repetition_penalty = repetition_penalty,
            presence_penalty = presence_penalty,
            compact_at = compact_at,
            api_key = api_key,
            header = header,
            model_load = model_load,
            as_subagent = as_subagent,
            persist = persist,
        )
    alias = ctx.meta.get(_ALIAS_META)
    if alias and as_subagent:
        _fail("--as-subagent is not supported for agent aliases.")
    if alias and skills:
        _fail(
            "--skill and --skill-set are not supported for pi aliases: the alias sets "
            "its own PI_CODING_AGENT_DIR; use `agent-switch pi`."
        )
    if alias and target is not None:
        # The function sets its own PI_CODING_AGENT_DIR, which outranks the session env, so the
        # session models.json with the local provider and key would be silently ignored.
        _fail(
            "pi aliases run natively only: the alias sets its own PI_CODING_AGENT_DIR, so --url "
            "cannot route it; drop --url/--provider or use `agent-switch pi`."
        )
    install_hint = _npm_install_hint(
        "@earendil-works/pi-coding-agent",
        ignore_scripts = True,
    )
    if as_subagent and not _PI_SUBAGENT_EXTENSION.is_file():
        _fail(f"Missing Pi subagent extension: {_PI_SUBAGENT_EXTENSION}")
    if alias:
        _check_alias(alias)
    else:
        _require_agent_for_launch("pi", install_hint, launch)
    should_copy_skills = bool(skills and _wsl_windows_executable(["pi"]))
    if should_copy_skills:
        typer.echo(
            "Note: pi runs as a Windows program, so injected skills are copied; "
            "restart to pick up edits."
        )
    if target is None:
        # Without skills, native launch leaves HOME and PI_CODING_AGENT_DIR untouched.
        # Mounted servers only ride --mcp-config. Pi itself has
        # no such flag and rejects it; it works only with an extension that registers the flag,
        # such as pi-mcp-adapter.
        with _session_config("pi-native", launch) as config:
            env = {}
            if skills:
                agent_dir = config / "agent"
                write_native_overlay(
                    agent_dir, _get_pi_source_agent_dir(agent_dir), skills = skills,
                    should_copy_skills = should_copy_skills,
                )
                env["PI_CODING_AGENT_DIR"] = str(agent_dir)
            else:
                _remove_overlay_entry(config / "agent")
            mcp_path = config / "mcp.json"
            mcp_flags = []
            if mcp_servers:
                _write_pi_mcp_config(mcp_path, mcp_servers)
                mcp_flags = ["--mcp-config", str(mcp_path)]
            else:
                mcp_path.unlink(missing_ok = True)
            command = _agent_command(
                alias,
                "pi",
                [
                    *mcp_flags,
                    *_yolo_command_flags("pi", yolo),
                    *_drop_overridden_args("pi", ctx.args, _yolo_command_flags("pi", yolo)),
                ],
            )
            _run_native(
                alias or "pi",
                env,
                command,
                launch = launch,
                install_hint = install_hint,
                clear_screen = True,
            )
        return
    server_options = ServerOptions(
        reasoning = reasoning,
        reasoning_effort = reasoning_effort,
        temperature = temperature,
        top_p = top_p,
        top_k = top_k,
        min_p = min_p,
        repetition_penalty = repetition_penalty,
        presence_penalty = presence_penalty,
        carried = _ALL_REQUEST_FIELDS,
        provider = target.name,
    )
    if server_options.request_body() and not _agent_version_at_least(
        "pi", _PI_SAMPLING_PARAMS_MIN_VERSION
    ):
        server_options = server_options._replace(carried = frozenset())
    base, key, entry = _connect(
        model,
        LoadOptions(max_seq_length, model_load),
        server_options = server_options,
        target = target,
    )
    if as_subagent:
        if compact_at is not None:
            typer.echo(
                "Warning: --compact-at does not apply with --as-subagent for Pi; ignoring it.",
                err = True,
            )
        extension = _agent_config_path(_PI_SUBAGENT_EXTENSION, ["pi"])
        with _session_config("pi-subagent", launch, persist = persist) as config:
            config_path = config / "subagent.json"
            write_pi_subagent_config(
                base,
                key,
                entry,
                config_path,
                approve = yolo,
                max_tokens = max_tokens,
                request_body = server_options.request_body(),
                headers = headers,
            )
            command = [
                "pi",
                "--extension",
                extension,
                *_yolo_command_flags("pi", yolo),
                *_drop_overridden_args("pi", ctx.args, _yolo_command_flags("pi", yolo)),
            ]
            typer.echo(
                "A local agent is available, and the model is in /model. "
                "Ask Pi to spawn a local agent."
            )
            _run(
                base,
                entry,
                {"AGENT_SWITCH_PI_SUBAGENT_CONFIG": str(config_path)},
                command,
                launch = launch,
                install_hint = install_hint,
                clear_screen = True,
            )
        return
    served_models = _get_served_models(target, key, entry["id"])
    # Pi starts on a saved default model, else a known provider's default, else the first available one, so pin our provider/model on the command line; the custom OpenAI-compatible endpoint itself is only configurable via ~/.pi/agent/models.json.
    command = [
        "pi",
        "--provider",
        _PI_PROVIDER,
        "--model",
        entry["id"],
        *_yolo_command_flags("pi", yolo),
        *_drop_overridden_args(
            "pi",
            ctx.args,
            [
                "--provider",
                _PI_PROVIDER,
                "--model",
                entry["id"],
                *_yolo_command_flags("pi", yolo),
            ],
        ),
    ]
    # --ignore-scripts matches Pi's documented install recipe (its README notes Pi needs no install scripts), so accepting the prompt skips dependency lifecycle scripts.
    with _session_config("pi", launch, persist = persist) as home:
        # Pi resolves its config dir from PI_CODING_AGENT_DIR first (getAgentDir() prefers it over $HOME/.pi/agent), so pin it at the session dir: an inherited PI_CODING_AGENT_DIR in the user's shell would otherwise send Pi to their real config and skip our provider/key. HOME is relocated too so any other ~/.pi paths stay in the session. The key rides in the config rather than the env.
        pi_agent_dir = home / ".pi" / "agent"
        write_pi_config(
            base,
            key,
            entry,
            pi_agent_dir / "models.json",
            max_tokens = max_tokens,
            request_body = server_options.request_body(),
            headers = headers,
            served_models = served_models,
        )
        write_pi_user_resources(pi_agent_dir, home, skills = skills)
        write_pi_compaction(pi_agent_dir, entry, compact_at, served_models)
        # Pi's built-in MCP (since 0.99.0) reads <agentDir>/mcp.json (as does the user's pi-mcp-adapter);
        # HOME and PI_CODING_AGENT_DIR are already the session's, so this file replaces the
        # user's own servers for the session. Without MCP flags it is removed again, so a
        # persisted session keeps no earlier mount. The $$/$! escapes in env and headers are for
        # pi's built-in MCP; an extension that replaces it, such as pi-mcp-adapter, gets them as is.
        mcp_path = pi_agent_dir / "mcp.json"
        if mcp_servers:
            _write_pi_mcp_config(mcp_path, mcp_servers, should_escape = True)
        else:
            mcp_path.unlink(missing_ok = True)
        env = {"HOME": str(home), "PI_CODING_AGENT_DIR": str(pi_agent_dir)}
        if os.name == "nt" or os.environ.get("WSL_DISTRO_NAME"):
            # Node resolves ~/.pi via USERPROFILE (then HOMEDRIVE + HOMEPATH) on Windows, not HOME. Set them whenever Pi may run as a Windows process: native Windows, or a /mnt Windows shim launched from WSL, where the WSLENV bridge then translates the path. Otherwise the Windows process falls back to the user's real %USERPROFILE%\\.pi. splitdrive yields no drive off a POSIX path, so HOMEDRIVE/HOMEPATH stay unset there.
            env["USERPROFILE"] = str(home)
            drive, tail = os.path.splitdrive(str(home))
            if drive:
                env["HOMEDRIVE"], env["HOMEPATH"] = drive, tail
        # Pi defaults to fullscreen since 1.0.0, but tuiMode "regular" paints inline from the cursor without clearing first, so give it a clean screen.
        _run(
            base,
            entry,
            env,
            command,
            launch = launch,
            install_hint = install_hint,
            clear_screen = True,
            # Pi picks this over <agentDir>/sessions, so an inherited one escapes the session home.
            unset_env = ("PI_CODING_AGENT_SESSION_DIR",),
        )
