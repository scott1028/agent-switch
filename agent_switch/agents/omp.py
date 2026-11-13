# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""`agent-switch omp`: OMP (oh-my-pi) native launch, its session overlay and aliases."""

import contextlib
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Literal, Optional

import typer
import yaml

from agent_switch import providers
from agent_switch.core.install import _require_agent_for_launch
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
from agent_switch.core.options import (
    LoadOptions,
    ProviderName,
    ServerOptions,
    _ALL_REQUEST_FIELDS,
    _AS_SUBAGENT_OPTION,
    _COMPACT_AT_OPTION,
    _COMPACT_KEEP_OPTION,
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
    _REASONING_EFFORTS,
    _REASONING_OPTION,
    _REPETITION_PENALTY_OPTION,
    _SAMPLING_FIELDS,
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
    _get_compaction_keep,
    _refuse_local_only,
    _warn_compact_keep_ignored,
    _yolo_command_flags,
    opencode_output_limit,
)
from agent_switch.core.overlay import link_user_store, remove_native_overlay, write_native_overlay
from agent_switch.core.platform import (
    _create_directory_junction,
    _is_junction,
    _remove_overlay_entry,
)
from agent_switch.core.session import _session_config
from agent_switch.core.skills import load_skills, write_skills_overlay
from agent_switch.core.storage import (
    _read_json_object,
    _subdict,
    _write_private_json,
    _write_private_text,
)


# README.md:56-60 "Bun (recommended)"; the binary is bin.omp in packages/coding-agent/package.json.
_OMP_INSTALL_HINT = "bun install -g @oh-my-pi/pi-coding-agent"


# A named profile ignores PI_CODING_AGENT_DIR (packages/utils/src/dirs.ts:366).
_OMP_PROFILE_ENV = ("OMP_PROFILE", "PI_PROFILE")


_OMP_XDG_ENV = ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")


_OMP_PROVIDER = "agent-switch"


# What omp reads from its agent dir besides config, auth and state (src/discovery/builtin.ts:
# 294-295,326,375,407,423,468,516,675,720,772,946; src/system-prompt.ts:124), the task agents
# in <agentDir>/agents (src/task/discovery.ts:93-111, src/config.ts:17-18,138-147), the
# secrets to redact (docs/secrets.md:35-42) and the SSH hosts (utils/src/dirs.ts:1203-1206).
_OMP_USER_RESOURCES = (
    "skills", "commands", "rules", "RULES.md", "prompts", "extensions", "instructions", "hooks",
    "tools", "agents", "SYSTEM.md", "SYSTEM_TEMPLATE.md", "AGENTS.md", "PERSONALITY.md",
    "secrets.yml", "ssh.json",
)


# The user's own session history, which every session links instead of keeping its own: the
# sessions, their blobs and archive go under omp's data dir, the terminal breadcrumbs and custom
# session file records under its state dir (packages/utils/src/dirs.ts:1003-1056; the archive
# sits beside sessions, packages/coding-agent/src/cli/gc-cli.ts:381-383). history.db, the prompt
# history and a session index omp backfills from the session files, stays the session's own: it is
# SQLite in WAL mode (src/session/history-storage.ts:134, src/session/session-index.ts:1-12,101).
_OMP_USER_DATA = ("sessions", "blobs", "archive")
_OMP_USER_STATE = ("terminal-sessions", "custom-session-files")


# The user's own safety settings a --url session keeps: without them omp approves every tool
# call (src/tools/settings.ts:314-318), loads project MCP config (docs/mcp-config.md), drops
# its bash rules and git guard (src/exec/settings.ts:144-161) and stops redacting secrets
# (src/secrets/settings.ts:14-17).
_OMP_USER_SAFETY_SETTINGS = (
    "tools.approvalMode", "tools.approval", "tools.restrictedFolders", "mcp.enableProjectConfig",
    "disabledProviders", "bash.patterns", "bash.gitGuard", "secrets.enabled",
)


# A setting that is not there; None is a value a settings file can hold.
_UNSET = object()


# One entry of a .env file as Bun's node:util parseEnv reads it, which omp's own loader uses
# (packages/utils/src/env.ts:4,267-276): an optional `export`; a value in ", ' or ` quotes, which
# may span lines and ends at the first closing quote followed by nothing but a # comment; else
# the rest of the line up to a #. No $ expansion. (Checked against Bun 1.3.14; only some
# malformed quoting, such as a backslash or a doubled quote before the closing one, differs.)
_DOTENV_ENTRY = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Za-z0-9_.-]+)[ \t]*=[ \t]*"
    r"(?:([\"'`])([\s\S]*?)\2[ \t]*(?:#.*)?$|([^#\n]*))",
    re.M,
)


# A $VAR or ${VAR} reference, as omp reads Bun's expansion of the project's .env files
# (packages/utils/src/env.ts:138-152); a backslash before the $ keeps it literal.
_DOTENV_VARIABLE = re.compile(r"(\\)?\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


# The full effort ladder, as Pi's thinkingLevelMap offers xhigh and max: omp's own default for a
# custom model stops at high (catalog/src/compat/resolve.ts:1033-1034,1085-1097). requiresEffort
# stays off, or qwenTemplateReasoningEffort would make an effort mandatory and turn
# --thinking off into the lowest effort (resolve.ts:1291-1294, ai/src/stream.ts:1751-1775).
_OMP_THINKING = {
    "mode": "effort",
    "efforts": ["minimal", "low", "medium", "high", "xhigh", "max"],
    "requiresEffort": False,
}


# What agent-switch last wrote to the session config.yml, so a later run undoes only that.
_OMP_CONFIG_MANIFEST = ".agent-switch-config.json"


# The user files agent-switch copied into the session because it could not link them.
_OMP_COPIES_MANIFEST = ".agent-switch-copies.json"


class _OmpYamlDumper(yaml.SafeDumper):
    # Model rows share the thinking and body dicts; write each in full instead of as an alias.
    def ignore_aliases(self, data) -> bool:
        return True


# Quote every string, so omp's YAML 1.2 reader never takes one such as 1e10 for a number
# (src/config/config-file.ts:245).
_OmpYamlDumper.add_representer(
    str, lambda dumper, value: dumper.represent_scalar("tag:yaml.org,2002:str", value, style = '"'),
)


class _OmpYamlLoader(yaml.SafeLoader):
    """Bun.YAML.parse's scalars where YAML 1.1 differs (src/config/settings.ts:2330-2348): only
    true/false are booleans, a date stays a string, and numbers follow YAML 1.2 core, so forms
    such as 1_000, 1:30 and 0b11 stay strings."""


_OmpYamlLoader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp) for tag, regexp in resolvers
        if tag not in (
            "tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp",
            "tag:yaml.org,2002:int", "tag:yaml.org,2002:float",
        )
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_OmpYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"),
)
# The int pattern goes first, so a plain number is an int, not a float.
_OmpYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"^[-+]?(?:[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"),
    list("-+0123456789"),
)
_OmpYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
        r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
    ),
    list("-+.0123456789"),
)


def _construct_yaml_1_2_int(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> int:
    # YAML 1.1's constructor reads 017 as octal; YAML 1.2 core reads it as decimal. Bun also
    # takes a sign before 0x and 0o.
    value = loader.construct_scalar(node)
    sign = -1 if value.startswith("-") else 1
    digits = value.lstrip("+-")
    if digits.startswith("0o"):
        return sign * int(digits[2:], 8)
    if digits.startswith("0x"):
        return sign * int(digits[2:], 16)
    return sign * int(digits, 10)


_OmpYamlLoader.add_constructor("tag:yaml.org,2002:int", _construct_yaml_1_2_int)


def _read_omp_yaml(path: Path) -> Optional[dict]:
    """A YAML file omp reads, with its scalars: {} when missing, None when not a mapping."""
    if not path.exists():
        return {}
    try:
        data = yaml.load(path.read_text(encoding = "utf-8"), Loader = _OmpYamlLoader)
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return None
    if data is None:
        return {}
    return data if isinstance(data, dict) else None


def _get_omp_config_dir_name() -> str:
    # PI_CONFIG_DIR || ".omp" (packages/utils/src/dirs.ts:333).
    return os.environ.get("PI_CONFIG_DIR") or ".omp"


def _get_omp_config_root() -> Path:
    # path.join(os.homedir(), ...) keeps a leading separator inside home (dirs.ts:117).
    return Path(os.path.normpath(f"{Path.home()}{os.sep}{_get_omp_config_dir_name()}"))


def _get_omp_profile() -> str:
    """The named omp profile the env selects; "" for the default profile."""
    # OMP_PROFILE wins even when empty; PI_PROFILE only when it is unset (dirs.ts:64-66,91-92).
    profile = os.environ.get("OMP_PROFILE")
    if profile is None:
        profile = os.environ.get("PI_PROFILE")
    profile = (profile or "").strip()
    return "" if profile == "default" else profile


def _get_omp_user_agent_dir(agent_dir: Path) -> Path:
    """The user's own omp agent dir, resolved as omp does (packages/utils/src/dirs.ts)."""
    root = _get_omp_config_root()
    profile = _get_omp_profile()
    if profile:
        # A named profile derives its own agent dir and ignores the override (dirs.ts:366).
        return root / "profiles" / profile / "agent"
    # path.resolve: relative to the launch dir, no trim and no ~ expansion (dirs.ts:367).
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    source = Path(os.path.abspath(configured)) if configured else root / "agent"
    if source.resolve(strict = False) == agent_dir.resolve(strict = False):
        # Do not treat this session as its own resource source.
        source = root / "agent"
    return source


def _get_omp_user_config_root() -> Path:
    """The user's own omp config root: ~/.omp, or a named profile's (dirs.ts:116-123)."""
    profile = _get_omp_profile()
    root = _get_omp_config_root()
    return root / "profiles" / profile if profile else root


def _get_omp_xdg_dir(source: Path, xdg_home: Optional[str]) -> Optional[Path]:
    """The dir under xdg_home (an $XDG_*_HOME value) that omp on agent dir source uses instead."""
    # Only the default agent dir moves there, on Linux and macOS, and only once that dir exists;
    # a named profile needs its own dir there (packages/utils/src/dirs.ts:368,385-401,427-431).
    if sys.platform not in ("linux", "darwin") or not xdg_home:
        return None
    if source != _get_omp_user_config_root() / "agent":
        return None
    xdg_dir = Path(os.path.abspath(os.path.join(xdg_home, "omp")))
    profile = _get_omp_profile()
    if profile:
        xdg_dir = xdg_dir / "profiles" / profile
    return xdg_dir if xdg_dir.exists() else None


def _get_omp_user_store(source: Path, category: Literal["data", "state"]) -> Path:
    """Where the user's own omp keeps its data or state files for the agent dir source."""
    return _get_omp_xdg_dir(source, os.environ.get(f"XDG_{category.upper()}_HOME")) or source


def write_omp_user_sessions(agent_dir: Path, source: Path) -> None:
    """Link the user's own omp session history into the session agent dir, so the session and
    native omp resume each other's sessions, and the user's secret placeholder key, so they
    show the same placeholders."""
    for category, names in (("data", _OMP_USER_DATA), ("state", _OMP_USER_STATE)):
        store = _get_omp_user_store(source, category)
        for name in names:
            if not link_user_store(store / name, agent_dir / name):
                _fail(
                    f"Couldn't link your omp {store / name} into the session ({agent_dir / name}), "
                    "and agent-switch never copies session history; fix that and re-run."
                )
    # omp keeps the key in its state dir, or still in the agent dir until it moves it there
    # (packages/utils/src/dirs.ts:1075-1115), and reads it only with secrets on, which a project
    # can turn on too (src/config/settings.ts:1404-1405). Only a key the user has is linked: an
    # empty or dangling one stops omp from making its own (src/secrets/index.ts:23-52).
    key = agent_dir / "secret-placeholder.key"
    user_keys = [
        path for path in (_get_omp_user_store(source, "state") / key.name, source / key.name)
        if path.is_file()
    ]
    if user_keys:
        _write_omp_secret_key(user_keys[0], key)
    elif key.is_symlink() and not key.exists():
        key.unlink()


def _write_omp_secret_key(user_key: Path, target: Path) -> None:
    """Link the user's placeholder key at target, else copy it in, as it never changes; without
    either, warn and go on: the session then hides secrets with a key of its own."""
    with contextlib.suppress(OSError):
        if target.read_bytes() == user_key.read_bytes():
            # A link, or the copy an earlier run made.
            return
    if link_user_store(user_key, target, is_file = True):
        return
    # Copy beside the target, then rename it in, so a copy that fails part way leaves nothing.
    temp = target.with_name(f".{target.name}.agent-switch-copy")
    try:
        _write_private_text(temp, user_key.read_text(encoding = "utf-8"))
        os.replace(temp, target)
    except (OSError, UnicodeDecodeError):
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok = True)
        typer.echo(
            f"Warning: couldn't link or copy your {user_key} into the omp session ({target}); "
            "with secrets on, the session hides them with a key of its own, which your own omp "
            "can't read back.",
            err = True,
        )


def _refuse_omp_profile(args: list) -> None:
    """Fail on a passthrough --profile, which would skip the session's PI_CODING_AGENT_DIR."""
    for arg in args:
        if arg == "--":
            # omp forwards everything after `--` verbatim (src/cli/profile-bootstrap.ts:103).
            return
        if arg == "--profile" or arg.startswith("--profile="):
            _fail(
                "omp's --profile can't be used here: agent-switch points PI_CODING_AGENT_DIR at "
                "its session agent dir, and an omp profile ignores that variable; drop --profile."
            )


def _refuse_omp_native_overlay() -> None:
    """Fail where a session PI_CODING_AGENT_DIR would move the user's own omp state."""
    hint = "use --url/--provider, or add the skill or MCP server to your own omp config"
    profile = _get_omp_profile()
    if profile:
        # Dropping the profile also moves its config root (plugins, logs) (dirs.ts:115-127).
        _fail(
            f"--skill, --skill-set and the MCP flags need a session omp agent dir, which can't "
            f"keep the active omp profile {profile!r} (OMP_PROFILE/PI_PROFILE); {hint}."
        )
    # omp keeps login, sessions and history under $XDG_*_HOME/omp on Linux and macOS, but only
    # for the default agent dir (dirs.ts:367-401,960-1065).
    if sys.platform not in ("linux", "darwin"):
        return
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    if configured and Path(os.path.abspath(configured)) != _get_omp_config_root() / "agent":
        return
    for name in _OMP_XDG_ENV:
        value = os.environ.get(name)
        xdg_dir = os.path.join(value, "omp") if value else None
        if xdg_dir and os.path.exists(xdg_dir):
            _fail(
                f"omp keeps your login and sessions in {xdg_dir} ({name}), and --skill, "
                f"--skill-set and the MCP flags need a session omp agent dir, which would hide "
                f"them; {hint}."
            )


def _put_omp_env_value(env: dict, name: str, value: str) -> str:
    """The name omp should resolve instead of value, with value put in env."""
    # omp reads a config value that names a set env var as that var, and fills unset vars from
    # its .env files only (config/resolve-config-value.ts:104-108, utils/src/env.ts:384-392), so
    # the value reaches it as is. An empty value would make omp send the name itself; "" makes
    # it drop the entry instead (resolve-config-value.ts:149-163, mcp/manager.ts:2365,2376).
    if not value:
        return ""
    env[name] = value
    return name


def _check_omp_mcp_values(servers: dict, *, is_stdio_wrapped: bool) -> None:
    """Fail on a mounted command, arg or url with ${, which omp expands; it has no escape."""
    for name, server in servers.items():
        if server["transport"] == "stdio":
            if is_stdio_wrapped:
                # mcp.json then names only the wrapper script.
                continue
            strings = [("command", server["command"])]
            strings += [(f"args[{index}]", arg) for index, arg in enumerate(server["args"])]
        else:
            strings = [("url", server["url"])]
        for field, value in strings:
            # omp expands ${VAR} in every string of a server entry (discovery/helpers.ts:558-588,
            # discovery/builtin.ts:134); env and header values reach it as env var names.
            if "${" in value:
                _fail(
                    f"MCP server {name!r}: {field} contains '${{', which omp would expand as an "
                    "environment variable."
                )


def _write_omp_mcp_wrapper(path: Path, server: dict) -> None:
    """A private sh script that sets the server's env, then execs it."""
    # As for Codex native (agent_switch/agents/codex.py:209-217): omp spawns every stdio server
    # and bash command with its whole env (mcp/transports/stdio.ts:592-596,
    # utils/src/procmgr.ts:43-47), so a value in it would reach them all.
    # env takes any key the registry takes, where a shell export takes only identifiers; every
    # word is quoted, so nothing in it reaches the shell as code.
    words = [f"{key}={value}" for key, value in server["env"].items()]
    argv = ("env", *words, server["command"], *server["args"])
    _write_private_text(
        path, "#!/bin/sh\nexec " + " ".join(shlex.quote(word) for word in argv) + "\n",
    )
    path.chmod(0o700)


def _get_omp_mcp_servers(servers: dict, env: dict, wrapper_dir: Optional[Path]) -> dict:
    """The mounted servers in omp's mcp.json shape (docs/mcp-config.md, server fields). A stdio
    server runs through a wrapper in wrapper_dir; without one (Windows), its env values go into
    env, as every http header value does."""
    result = {}
    count = 0
    for name, server in servers.items():
        if server["transport"] == "stdio" and wrapper_dir is not None:
            script = wrapper_dir / f"{name}.sh"
            _write_omp_mcp_wrapper(script, server)
            result[name] = {"type": "stdio", "command": str(script), "args": [], "env": {}}
            continue
        values = server["env"] if server["transport"] == "stdio" else server["headers"]
        hidden = {}
        for key, value in values.items():
            count += 1
            hidden[key] = _put_omp_env_value(env, f"AGENT_SWITCH_OMP_MCP_{count}", value)
        if server["transport"] == "stdio":
            result[name] = {
                "type": "stdio", "command": server["command"], "args": server["args"],
                "env": hidden,
            }
        else:
            result[name] = {"type": "http", "url": server["url"], "headers": hidden}
    return result


def _write_omp_native_mcp_config(
    path: Path, user_path: Path, servers: dict, env: dict, wrapper_dir: Optional[Path],
) -> None:
    """The user's own mcp.json with the mounted servers added; a mounted server wins a name."""
    config = _read_json_object(user_path)
    if config is None:
        _fail(
            f"Couldn't parse {user_path}, so the mounted MCP servers can't be added to yours; "
            "fix that file or drop the MCP flags."
        )
    own = config.get("mcpServers")
    config["mcpServers"] = {
        **(own if isinstance(own, dict) else {}),
        **_get_omp_mcp_servers(servers, env, wrapper_dir),
    }
    disabled = config.get("disabledServers")
    if isinstance(disabled, list):
        # disabledServers hides a server from every source (docs/mcp-config.md "Top-level keys").
        config["disabledServers"] = [name for name in disabled if name not in servers]
    _write_private_json(path, config)


def _get_omp_model_entry(
    model: dict,
    max_tokens: Optional[int],
    request_body: Optional[dict],
    *,
    thinking_format: Optional[str],
    should_warn: bool,
) -> dict:
    entry = {"id": model["id"]}
    compat = {}
    if thinking_format:
        # omp offers thinking levels only for a reasoning model.
        entry["reasoning"] = True
        entry["thinking"] = _OMP_THINKING
        compat["thinkingFormat"] = thinking_format
        if thinking_format == "qwen-chat-template":
            # chat_template_kwargs.enable_thinking, plus reasoning_effort when on
            # (catalog/src/compat/resolve.ts:312-313, ai/src/providers/openai-shared.ts:
            # 1142-1143,1245-1255), as Pi's chat-template compat sends.
            compat["qwenTemplateReasoningEffort"] = True
    window = model.get("context_length") or model.get("max_context_length")
    if window:
        window = int(window)
        # Pin the real window and a sane output cap, as for Pi; only the launch model warns.
        entry["contextWindow"] = window
        entry["maxTokens"] = (
            _agent_output_limit(window, max_tokens)
            if should_warn
            else opencode_output_limit(window, max_tokens)
        )
    elif max_tokens:
        entry["maxTokens"] = max_tokens
    if request_body:
        # Merged into every request body (docs/models.md "extraBody").
        compat["extraBody"] = request_body
    if compat:
        entry["compat"] = compat
    return entry


def write_omp_models(
    path: Path,
    base: str,
    key: str,
    model: dict,
    *,
    env: dict,
    max_tokens: Optional[int] = None,
    request_body: Optional[dict] = None,
    headers: Optional[dict] = None,
    served_models: Optional[list] = None,
    thinking_format: Optional[str] = None,
) -> None:
    """The agent-switch provider in the session models.yml (docs/models.md, models.yml shape);
    the key and header values go into env (config/model-registry.ts:645, resolve-config-value.ts:
    149-163)."""
    config = _read_omp_yaml(path)
    if config is None:
        typer.echo(
            f"Warning: couldn't parse {path} — add an '{_OMP_PROVIDER}' provider there "
            "yourself, or move the file aside and re-run.",
            err = True,
        )
        return
    # Every served model, so /model can switch; the connected entry keeps its own window.
    catalog = {served["id"]: served for served in served_models or []}
    catalog[model["id"]] = model
    # models.yml is read before models.yaml, and models.json only migrates without it
    # (config/config-file.ts:34-37,190-196), so this file always wins.
    _subdict(config, "providers")[_OMP_PROVIDER] = {
        "api": "openai-completions",
        "baseUrl": f"{base}/v1",
        # Never empty: _connect hands over a placeholder when there is no key.
        "apiKey": _put_omp_env_value(env, "AGENT_SWITCH_OMP_API_KEY", key),
        **({"headers": {
            name: _put_omp_env_value(env, f"AGENT_SWITCH_OMP_HEADER_{index}", value)
            for index, (name, value) in enumerate(headers.items(), start = 1)
        }} if headers else {}),
        "models": [
            _get_omp_model_entry(
                served,
                max_tokens,
                request_body,
                thinking_format = thinking_format,
                should_warn = served_id == model["id"],
            )
            for served_id, served in catalog.items()
        ],
    }
    text = yaml.dump(config, Dumper = _OmpYamlDumper, sort_keys = False)
    if not path.exists() or path.read_text(encoding = "utf-8") != text:
        # Private: it holds the key.
        _write_private_text(path, text)
        typer.echo(f"Updated {path}")


def _get_omp_setting(config: dict, key: str):
    """The value at a dotted settings key, or _UNSET."""
    value = config
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return _UNSET
        value = value[part]
    return value


def _set_omp_setting(config: dict, key: str, value) -> None:
    *parents, leaf = key.split(".")
    for part in parents:
        config = _subdict(config, part)
    config[leaf] = value


def _delete_omp_setting(config: dict, key: str) -> None:
    """Drop a dotted key that exists, then any group it leaves empty."""
    *parents, leaf = key.split(".")
    chain = [config]
    for part in parents:
        chain.append(chain[-1][part])
    del chain[-1][leaf]
    for index in range(len(parents) - 1, -1, -1):
        if chain[index][parents[index]]:
            break
        del chain[index][parents[index]]


def _read_omp_user_settings(agent_dir: Path) -> dict:
    """The user's global omp settings, from the file omp reads (docs/settings.md:15-18)."""
    for name in ("config.yml", "config.yaml"):
        path = agent_dir / name
        if path.exists():
            settings = _read_omp_yaml(path)
            break
    else:
        # Read only when neither YAML file exists (src/config/settings.ts:2885-2899).
        path = agent_dir / "settings.json"
        settings = _read_json_object(path)
    if settings is None:
        # omp itself refuses a broken settings file (docs/settings.md:38), so stop rather than
        # start a session without the user's approval and MCP guards.
        _fail(
            f"Couldn't read your omp settings file {path}, so this session can't keep its "
            "approval and MCP safety settings; fix or move that file, then re-run."
        )
    return settings


def write_omp_config(
    agent_dir: Path,
    model: dict,
    *,
    user_settings: dict,
    compact_at: Optional[float] = None,
    compact_keep: Optional[float] = None,
    thinking_format: Optional[str] = None,
) -> None:
    """agent-switch's keys in the session config.yml; settings made in the session stay."""
    path = agent_dir / "config.yml"
    # Read as omp writes it (utils/src/yaml-config.ts:5-7), so a date-like string stays one.
    config = _read_omp_yaml(path)
    if config is None:
        typer.echo(
            f"Warning: couldn't parse {path}; omp's onboarding, thinking level and compaction "
            "keep their defaults in this session.",
            err = True,
        )
        return
    # A fresh session dir has no setup version, so omp would open its onboarding on every
    # launch (src/modes/settings.ts:32,1271-1275; src/main.ts:693-712).
    _subdict(config, "startup").setdefault("setupWizard", False)
    if thinking_format:
        # Start at xhigh as Pi does; a level saved in the session stays
        # (src/session/settings.ts:238-243).
        config.setdefault("defaultThinkingLevel", "xhigh")
    _warn_compact_keep_ignored([model], compact_keep)
    keep = _get_compaction_keep(model, compact_keep)
    # (key, value or _UNSET, whether a value set inside the session wins)
    managed = [
        # A percent of each model's own window, so other served models need no entry
        # (docs/settings.md "compaction.thresholdPercent").
        (
            "compaction.thresholdPercent",
            round(compact_at * 100) if compact_at is not None else _UNSET,
            False,
        ),
        # omp has one global amount, so the other served models keep the launch model's.
        ("compaction.keepRecentTokens", keep if keep is not None else _UNSET, False),
        *((key, _get_omp_setting(user_settings, key), True) for key in _OMP_USER_SAFETY_SETTINGS),
    ]
    manifest_path = agent_dir / _OMP_CONFIG_MANIFEST
    previous = _read_json_object(manifest_path) or {}
    written = {}
    for key, value, should_keep_session_value in managed:
        current = _get_omp_setting(config, key)
        is_session_value = current is not _UNSET and current != previous.get(key, _UNSET)
        if should_keep_session_value and is_session_value:
            # Set or changed inside the session, so the session owns it now.
            continue
        if value is not _UNSET:
            _set_omp_setting(config, key, value)
            written[key] = value
        elif key in previous and current == previous[key]:
            # A session may set many keys; drop only an unchanged one an earlier run wrote.
            _delete_omp_setting(config, key)
    text = yaml.dump(config, Dumper = _OmpYamlDumper, sort_keys = False)
    if not path.exists() or path.read_text(encoding = "utf-8") != text:
        _write_private_text(path, text)
    if written != previous:
        if written:
            _write_private_json(manifest_path, written)
        else:
            manifest_path.unlink(missing_ok = True)


def _read_dotenv(path: Path) -> dict:
    """A .env file's KEY=VALUE pairs, as omp's own loader reads them."""
    try:
        # Read in text mode, so a CRLF line ends as LF, as parseEnv takes it.
        text = path.read_text(encoding = "utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    values = {}
    for match in _DOTENV_ENTRY.finditer(text):
        key, quote, quoted, plain = match.groups()
        if quote is None:
            values[key] = plain.strip()
        elif quote == '"':
            # Only double quotes turn \n and \r into line breaks.
            values[key] = re.sub(
                r"(?<!\\)\\([nr])", lambda escape: "\n" if escape[1] == "n" else "\r", quoted,
            )
        else:
            values[key] = quoted
    return values


def _get_omp_dotenv_files(agent_dir: Path) -> list:
    """(path, whether omp itself loads it) for each .env file whose values reach omp's env."""
    cwd = Path.cwd()
    # omp's own files, with agent_dir the one omp uses (utils/src/env.ts:289-293).
    omp_files = (
        cwd / ".env", agent_dir / ".env", _get_omp_config_root() / ".env", Path.home() / ".env",
    )
    # Bun loads the project's dotenv files itself before omp starts
    # (utils/src/env.ts:112-114,162-167).
    mode = os.environ.get("NODE_ENV") or "development"
    bun_files = (cwd / f".env.{mode}", cwd / ".env.local", cwd / f".env.{mode}.local")
    return [(path, True) for path in omp_files] + [(path, False) for path in bun_files]


def _get_omp_user_dotenv_files(source: Path) -> list:
    """(path, whether omp itself loads it, whether only the user's own omp does) for each .env
    file the user's own omp on agent dir source reads, the winning one first."""
    files = _get_omp_dotenv_files(source)
    root = _get_omp_user_config_root()
    # Bun loads its files first, a later one winning (utils/src/env.ts:162-167); omp then fills
    # an unset name from the project, agent dir, config root and home files in that order, the
    # config root a named profile's own (env.ts:288-293,384-392). The session's omp has its own
    # agent dir and the default profile, so it never reads the user's agent dir or profile file.
    return [
        *((path, False, False) for path, is_omp_file in reversed(files) if not is_omp_file),
        (Path.cwd() / ".env", True, False),
        (source / ".env", True, True),
        (root / ".env", True, root != _get_omp_config_root()),
        (Path.home() / ".env", True, False),
    ]


def _get_omp_user_dotenv_value(source: Path, name: str) -> tuple:
    """(value, path, whether only the user's own omp reads that file) for the value of name the
    user's own omp on agent dir source takes from its .env files; (None, None, False) for none."""
    for path, is_omp_file, is_user_only in _get_omp_user_dotenv_files(source):
        values = _read_dotenv(path)
        value = values.get(name)
        if is_omp_file and name.startswith("PI_"):
            # In omp's own files an OMP_ name replaces its PI_ one (utils/src/env.ts:279-284).
            value = values.get("OMP_" + name[len("PI_"):], value)
        if value and path.parent == Path.cwd():
            # Bun loads the project's files itself and expands $VAR and ${VAR} in them from the
            # env (utils/src/env.ts:112-114,138-152).
            value = _DOTENV_VARIABLE.sub(
                lambda ref: ref[0][1:] if ref[1] else os.environ.get(ref[2] or ref[3], ""), value,
            )
        if value:
            return value, path, is_user_only
    return None, None, False


def _get_omp_user_session_dir(source: Path) -> Optional[str]:
    """The session dir to export so the session's omp writes where the user's own omp on agent
    dir source does; None when the session's omp gets the same value by itself."""
    name = "PI_CODING_AGENT_SESSION_DIR"
    if os.environ.get(name):
        # A non-empty launcher value wins over every dotenv file (utils/src/env.ts:204-206,387).
        return None
    value, _, is_user_only = _get_omp_user_dotenv_value(source, name)
    profile = _get_omp_profile()
    default_dotenv = _get_omp_config_root() / ".env"
    session_values = _read_dotenv(default_dotenv) if profile else {}
    if session_values.get("OMP_CODING_AGENT_SESSION_DIR", session_values.get(name)):
        # The session's omp runs on the default profile, so it reads ~/.omp/.env, which a named
        # profile does not (utils/src/env.ts:291): only an export keeps the session off its value.
        if value:
            return value
        # Unset, omp keeps one sessions folder per project, which no value can say.
        _fail(
            f"{default_dotenv} sets a session dir that this session's omp reads, as it runs on "
            f"the default profile, but your omp profile {profile!r} does not read that file, so "
            "the session would keep its history apart from yours. Set the session dir in "
            f"{_get_omp_user_config_root() / '.env'} or your shell too, or remove that line."
        )
    return value if is_user_only else None


def _refuse_omp_dotenv_overrides(agent_dir: Path, *, is_session_agent_dir: bool) -> None:
    """Fail when a .env file omp or Bun loads selects a named omp profile, an agent dir or a
    config root: agent-switch follows those only from the shell."""
    for path, is_omp_file in _get_omp_dotenv_files(agent_dir):
        values = _read_dotenv(path)
        for name in _OMP_PROFILE_ENV:
            profile = (values.get(name) or "").strip()
            if profile and profile != "default":
                # Bun fills it before omp picks its dirs, omp's own loader before a subagent
                # does (utils/src/env.ts:112-114, coding-agent/src/cli.ts:477-490), and a
                # named profile ignores PI_CODING_AGENT_DIR (utils/src/dirs.ts:366).
                _fail(
                    f"{path} sets {name}={profile}: an omp profile ignores PI_CODING_AGENT_DIR, "
                    "so omp would skip the session agent dir agent-switch prepares. Export "
                    f"{name} in your shell instead, which agent-switch follows with "
                    "--url/--provider, or remove that line."
                )
        if is_session_agent_dir and path == agent_dir / ".env":
            # agent-switch's own session dir points at no user dir.
            continue
        for name in ("PI_CODING_AGENT_DIR", "PI_CONFIG_DIR"):
            if os.environ.get(name):
                # Bun and omp keep a non-empty launcher value over any dotenv line
                # (utils/src/env.ts:204-206,387), so both agent-switch and omp follow the shell.
                continue
            # omp follows these from its files too (utils/src/env.ts:393-399, dirs.ts:494-501),
            # with OMP_ names mapped onto PI_ ones in its own (env.ts:279-284).
            keys = (name, "OMP_" + name[len("PI_"):]) if is_omp_file else (name,)
            for key in keys:
                if values.get(key):
                    _fail(
                        f"{path} sets {key}: your own omp follows it, but agent-switch reads "
                        f"your omp settings and resources from the shell's {name}, so it would "
                        f"read the wrong ones. Export {name} in your shell instead, which "
                        "agent-switch follows, or remove that line."
                    )
    source = _get_omp_user_agent_dir(agent_dir) if is_session_agent_dir else agent_dir
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME"):
        if os.environ.get(name):
            # A non-empty launcher value wins over every dotenv file (utils/src/env.ts:204-206).
            continue
        # omp redoes its dirs with the value it ends up with (utils/src/env.ts:394-400).
        value, path, _ = _get_omp_user_dotenv_value(source, name)
        xdg_dir = _get_omp_xdg_dir(source, value)
        if xdg_dir:
            _fail(
                f"{path} sets {name}: your own omp then keeps its sessions in {xdg_dir}, but "
                f"agent-switch reads {name} only from the shell, so it would link the wrong ones. "
                f"Export {name} in your shell instead, which agent-switch follows, or remove that "
                "line."
            )


def _get_omp_session_env(agent_dir: Path) -> dict:
    """The env that points omp at agent_dir, set so no dotenv file can move it."""
    return {
        "PI_CODING_AGENT_DIR": str(agent_dir),
        # Bun and omp fill only an unset or empty variable from a dotenv file
        # (utils/src/env.ts:204-206,384-392), so pin the root agent-switch resolved
        # (utils/src/dirs.ts:116-117,331-333) and the default profile (dirs.ts:595-625).
        "PI_CONFIG_DIR": _get_omp_config_dir_name(),
        "OMP_PROFILE": "default",
    }


def _link_omp_user_entry(source: Path, target: Path) -> bool:
    """Link source at target, or copy a file that cannot be linked, as core/overlay.py
    _link_overlay_entry does; a real session entry stays the session's own. True for a copy."""
    # Drop an earlier run's link first, so a resource the user removed since goes too.
    if target.is_symlink() or _is_junction(target):
        _remove_overlay_entry(target)
    if os.path.lexists(target) or not source.exists():
        return False
    try:
        target.symlink_to(source, target_is_directory = source.is_dir())
        return False
    except OSError:
        pass
    if source.is_dir():
        if not _create_directory_junction(source, target):
            typer.echo(f"Warning: couldn't link {source} into the omp session.", err = True)
        return False
    # Copy beside the target, then rename it in, so a copy that fails part way leaves nothing.
    temp = target.with_name(f".{target.name}.agent-switch-copy")
    try:
        shutil.copy2(source, temp)
        os.replace(temp, target)
        return True
    except OSError:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok = True)
        typer.echo(f"Warning: couldn't link or copy {source} into the omp session.", err = True)
        return False


def write_omp_user_resources(
    agent_dir: Path, source: Path, *, skills: Optional[dict] = None,
) -> list:
    """Expose the user's own omp resources in the session; config, auth and other state stay
    out, and write_omp_user_sessions shares the session history. The names copied whole
    instead of linked come back."""
    agent_dir.mkdir(parents = True, exist_ok = True, mode = 0o700)
    skills_root = agent_dir / "skills"
    if skills is not None:
        write_skills_overlay(skills_root, source / "skills", skills)
    elif (
        skills_root.is_dir() and not skills_root.is_symlink() and not _is_junction(skills_root)
        and (skills_root / ".agent-switch-skills.json").is_file()
    ):
        # An earlier run's injected skills give way to the user's own link again.
        _remove_overlay_entry(skills_root)
    # A copy from an earlier run goes like a link, so it is refreshed, or gone with its source.
    copies_path = agent_dir / _OMP_COPIES_MANIFEST
    previous = _read_json_object(copies_path) or {}
    for name in previous.get("copies", []) if isinstance(previous.get("copies"), list) else []:
        if name in _OMP_USER_RESOURCES and (agent_dir / name).is_file():
            (agent_dir / name).unlink()
    copies = [
        name for name in _OMP_USER_RESOURCES
        if (name != "skills" or skills is None)
        and _link_omp_user_entry(source / name, agent_dir / name)
    ]
    if copies:
        _write_private_json(copies_path, {"copies": copies})
    else:
        copies_path.unlink(missing_ok = True)
    return copies


def omp(
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
    compact_keep: Optional[float] = _COMPACT_KEEP_OPTION,
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
    """Point OMP (oh-my-pi coding agent) at a local model server and start it."""
    if as_subagent:
        _fail("--as-subagent is not supported for omp yet.")
    # Validate the MCP selection before anything else, so a registry error fails fast.
    mcp_servers = load_mcp_servers(
        mcp,
        should_mount_all = mcp_all,
        urls = mcp_url,
        oauth_urls = mcp_oauth_url,
        headers = mcp_header,
        stdios = mcp_stdio,
        envs = mcp_env,
    )
    skills = load_skills(skill, skill_set)
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
            compact_keep = compact_keep,
            api_key = api_key,
            header = header,
            model_load = model_load,
            persist = persist,
        )
    alias = ctx.meta.get(_ALIAS_META)
    if alias and (target is not None or skills is not None or mcp_servers is not None):
        # `omp --profile <name> --alias` makes an alias that selects a profile, and a profile
        # ignores PI_CODING_AGENT_DIR (packages/utils/src/dirs.ts:366), so the session
        # config would be silently skipped.
        _fail(
            "omp aliases run natively only: an alias may select an omp profile, which ignores "
            "the session's PI_CODING_AGENT_DIR, so --url/--provider, --skill, --skill-set and "
            "the MCP flags can't reach it; drop them or use `agent-switch omp`."
        )
    is_overlaid = target is None and bool(skills or mcp_servers)
    if target is not None or is_overlaid:
        # Both set PI_CODING_AGENT_DIR, which a profile would skip.
        _refuse_omp_profile(ctx.args)
    if is_overlaid:
        _refuse_omp_native_overlay()
    if mcp_servers:
        _check_omp_mcp_values(mcp_servers, is_stdio_wrapped = os.name != "nt")
    if alias:
        _check_alias(alias)
    else:
        _require_agent_for_launch("omp", _OMP_INSTALL_HINT, launch)
    yolo_flags = _yolo_command_flags("omp", yolo)
    if target is not None:
        _run_omp_session(
            ctx,
            target,
            headers,
            model = model,
            load = LoadOptions(max_seq_length, model_load),
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
            ),
            max_tokens = max_tokens,
            compact_at = compact_at,
            compact_keep = compact_keep,
            yolo_flags = yolo_flags,
            persist = persist,
            launch = launch,
            mcp_servers = mcp_servers,
            skills = skills,
        )
        return
    command = _agent_command(
        alias, "omp", [*yolo_flags, *_drop_overridden_args("omp", ctx.args, yolo_flags)]
    )
    with _session_config("omp-native", launch) as config:
        agent_dir = config / "agent"
        env = {}
        unset_env = ()
        # Wrappers from an earlier run go; this run writes its own.
        _remove_overlay_entry(config / "mcp")
        if is_overlaid:
            # Without skills or MCP flags omp keeps its own env; with them it reads this overlay of
            # the user's agent dir, whose user-scope mcp.json is <agentDir>/mcp.json
            # (src/discovery/builtin.ts:234-241).
            source = _get_omp_user_agent_dir(agent_dir)
            # The overlay links the user's own .env, so read it there.
            _refuse_omp_dotenv_overrides(source, is_session_agent_dir = False)
            # A merged mcp.json from an earlier run is not an entry the overlay manifest owns.
            _remove_overlay_entry(agent_dir / "mcp.json")
            write_native_overlay(
                agent_dir,
                source,
                excluded = ("mcp.json",) if mcp_servers else (),
                skills = skills,
                # The XDG refusal above keeps these in the user's agent dir.
                shared = (*_OMP_USER_DATA, *_OMP_USER_STATE),
            )
            if mcp_servers:
                _write_omp_native_mcp_config(
                    agent_dir / "mcp.json", source / "mcp.json", mcp_servers, env,
                    None if os.name == "nt" else config / "mcp",
                )
            env.update(_get_omp_session_env(agent_dir))
            # PI_CODING_AGENT_SESSION_DIR stays the user's, as in Pi's native overlay.
            unset_env = ("PI_PROFILE",)
        else:
            remove_native_overlay(agent_dir, shared = (*_OMP_USER_DATA, *_OMP_USER_STATE))
        _run_native(
            alias or "omp",
            env,
            command,
            launch = launch,
            install_hint = _OMP_INSTALL_HINT,
            unset_env = unset_env,
        )


def _run_omp_session(
    ctx: typer.Context,
    target,
    headers: dict,
    *,
    model: Optional[str],
    load: LoadOptions,
    server_options: ServerOptions,
    max_tokens: Optional[int],
    compact_at: Optional[float],
    compact_keep: Optional[float],
    yolo_flags: list,
    persist: bool,
    launch: bool,
    mcp_servers: Optional[dict],
    skills: Optional[dict],
) -> None:
    """--url/--provider: omp on a session agent dir with the agent-switch provider."""
    with _session_config("omp", launch, persist = persist) as session:
        # HOME stays: omp reads Claude, Codex and ~/.agents resources through it.
        agent_dir = session / "agent"
        source = _get_omp_user_agent_dir(agent_dir)
        # Refuse before connecting, so nothing loads a model for a launch that cannot run.
        _refuse_omp_dotenv_overrides(agent_dir, is_session_agent_dir = True)
        user_settings = _read_omp_user_settings(source)
        copies = write_omp_user_resources(agent_dir, source, skills = skills)
        # Either side may turn secrets on: the user's file, or the session's own setting.
        session_settings = _read_omp_yaml(agent_dir / "config.yml") or {}
        secrets_path = agent_dir / "secrets.yml"
        if (
            True in (
                _get_omp_setting(user_settings, "secrets.enabled"),
                _get_omp_setting(session_settings, "secrets.enabled"),
            )
            and (source / "secrets.yml").exists()
            and not (secrets_path.is_symlink() or "secrets.yml" in copies)
        ):
            # omp reads the rules only from its agent dir and the project
            # (src/secrets/index.ts:166-174), so a missing file, or one agent-switch has no
            # record of copying whole, could leave rules out.
            _fail(
                f"Couldn't link or copy {source / 'secrets.yml'} whole into the omp session "
                f"({secrets_path}), so secrets.enabled would run without all of your secret "
                "rules; fix that and re-run."
            )
        write_omp_user_sessions(agent_dir, source)
        session_dir = _get_omp_user_session_dir(source)
        base, key, entry = _connect(model, load, server_options = server_options, target = target)
        served_models = _get_served_models(target, key, entry["id"])
        # omp sets the thinking level itself where the server carries it, as Pi does; auto
        # leaves thinking to the server's template.
        thinking_format = None
        if server_options.reasoning != "auto":
            if providers.get_has_template_kwargs(target.name):
                thinking_format = "qwen-chat-template"
            elif target.name == "openai":
                thinking_format = "openai"
        request_body = server_options.request_body()
        thinking_flags = []
        if thinking_format:
            # A pinned effort would override the level omp picks.
            request_body = server_options._replace(
                carried = frozenset(_SAMPLING_FIELDS),
            ).request_body()
            if server_options.reasoning == "off" or server_options.reasoning_effort == "none":
                thinking_flags = ["--thinking", "off"]
            elif server_options.reasoning_effort in _REASONING_EFFORTS:
                thinking_flags = ["--thinking", server_options.reasoning_effort]
        # With a provider the id must match exactly (docs/cli-reference.md "Model selection");
        # the exact match runs before any :level suffix is read
        # (config/model-resolver.ts:2296-2308).
        sent_flags = ["--model", f"{_OMP_PROVIDER}/{entry['id']}", *thinking_flags, *yolo_flags]
        command = ["omp", *sent_flags, *_drop_overridden_args("omp", ctx.args, sent_flags)]
        env = _get_omp_session_env(agent_dir)
        # omp picks PI_CODING_AGENT_SESSION_DIR over <agentDir>/sessions (src/cli/args.ts:260),
        # which links to the user's own. The shell's value and the .env files both omps read
        # reach it as they reach the user's omp; a value only the user's omp reads goes along.
        if session_dir:
            env["PI_CODING_AGENT_SESSION_DIR"] = session_dir
        unset_env = ("PI_PROFILE",)
        # Wrappers from an earlier run go; this run writes its own.
        _remove_overlay_entry(session / "mcp")
        write_omp_models(
            agent_dir / "models.yml",
            base,
            key,
            entry,
            env = env,
            max_tokens = max_tokens,
            request_body = request_body,
            headers = headers,
            served_models = served_models,
            thinking_format = thinking_format,
        )
        write_omp_config(
            agent_dir,
            entry,
            user_settings = user_settings,
            compact_at = compact_at,
            compact_keep = compact_keep,
            thinking_format = thinking_format,
        )
        # The session's user-scope MCP file: mounted servers replace the user's own, and a
        # persisted session keeps no earlier mount. The user's denylist still applies, except
        # to a server mounted now; omp reads it from this file (src/mcp/config.ts:114-128), and
        # an unreadable user file gives it none either.
        user_mcp = _read_json_object(source / "mcp.json") or {}
        disabled = user_mcp.get("disabledServers")
        disabled = [
            name for name in disabled if name not in (mcp_servers or {})
        ] if isinstance(disabled, list) else []
        session_mcp = {}
        if mcp_servers:
            session_mcp["mcpServers"] = _get_omp_mcp_servers(
                mcp_servers, env, None if os.name == "nt" else session / "mcp",
            )
        if disabled:
            session_mcp["disabledServers"] = disabled
        mcp_path = agent_dir / "mcp.json"
        if session_mcp:
            _write_private_json(mcp_path, session_mcp)
        else:
            mcp_path.unlink(missing_ok = True)
        _run(
            base,
            entry,
            env,
            command,
            launch = launch,
            install_hint = _OMP_INSTALL_HINT,
            unset_env = unset_env,
        )
