# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""`agent-switch omp`: native launch, the session overlay, `omp-<x>` aliases and --url."""

import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import yaml

import pytest
import typer
from typer.testing import CliRunner

import agent_switch.start as start
from agent_switch.agents import omp as omp_agent
from tests.cli_support import (
    BASE,
    KEY,
    MODEL,
    _assert_env_set,
    _assert_env_unset,
    _launch_command,
    _mcp_registry,
)
from tests.start_split import set_start_attr

_OVERLAY_UNSET = ("PI_PROFILE",)
_XDG_VARS = ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
_OMP_ENV = ("OMP_PROFILE", "PI_PROFILE", "PI_CODING_AGENT_DIR", "PI_CONFIG_DIR", *_XDG_VARS)


def _native_no_connect(monkeypatch):
    set_start_attr(monkeypatch, "_connect",
        lambda *args, **kwargs: pytest.fail("native launch must not connect"),
    )


def _make_omp_user(tmp_path, monkeypatch) -> tuple:
    """A user omp agent dir under HOME, plus an injectable skill folder."""
    user_home = tmp_path / "user-home"
    agent_dir = user_home / ".omp" / "agent"
    (agent_dir / "skills" / "user-only").mkdir(parents = True)
    # omp loads a skill only with a description (discovery/helpers.ts:490-491).
    (agent_dir / "skills" / "user-only" / "SKILL.md").write_text(
        "---\nname: user-only\ndescription: User skill.\n---\n"
    )
    (agent_dir / "config.yml").write_text("theme: dark\n")
    (agent_dir / "mcp.json").write_text(json.dumps({
        "$schema": "schema-url",
        "mcpServers": {"mine": {"command": "mine"}, "ev": {"command": "old"}},
        "disabledServers": ["ev", "other"],
    }))
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(user_home))
    for name in _OMP_ENV:
        monkeypatch.delenv(name, raising = False)
    source = tmp_path / "injected"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: injected\ndescription: Injected skill.\n---\n")
    return agent_dir, source


@pytest.fixture
def omp_user(tmp_path, monkeypatch):
    user = _make_omp_user(tmp_path, monkeypatch)
    _native_no_connect(monkeypatch)
    return user


@pytest.fixture
def omp_url_user(tmp_path, monkeypatch):
    return _make_omp_user(tmp_path, monkeypatch)


def test_omp_help_lists_the_shared_options():
    result = CliRunner().invoke(start.start_app, ["omp", "--help"])
    assert result.exit_code == 0, result.output
    assert "--skill" in result.output


def test_native_omp_bare_adds_nothing_but_yolo(fake_vllm, monkeypatch):
    _native_no_connect(monkeypatch)
    calls = []
    set_start_attr(monkeypatch, "_require_agent_for_launch", lambda *args: calls.append(args))
    result = CliRunner().invoke(
        start.start_app, ["omp", "--no-launch", "--yolo", "--", "--auto-approve", "--resume"]
    )
    assert result.exit_code == 0, result.output
    assert _launch_command(result.output) == ["omp", "--yolo", "--resume"]
    assert "PI_CODING_AGENT_DIR" not in result.output
    assert "unset " not in result.output
    assert calls == [("omp", "bun install -g @oh-my-pi/pi-coding-agent", False)]


def test_native_omp_refuses_local_only_flags(fake_vllm, monkeypatch):
    _native_no_connect(monkeypatch)
    result = CliRunner().invoke(start.start_app, ["omp", "--no-launch", "--model", "org/model"])
    assert result.exit_code == 1
    assert "--model needs --url or --provider." in result.output


@pytest.mark.parametrize("flags", [[], ["--url", BASE]])
def test_omp_refuses_as_subagent(fake_vllm, monkeypatch, flags):
    _native_no_connect(monkeypatch)
    result = CliRunner().invoke(start.start_app, ["omp", "--no-launch", "--as-subagent", *flags])
    assert result.exit_code == 1
    assert "--as-subagent is not supported for omp yet." in result.output


def test_omp_alias_runs_natively(fake_vllm, monkeypatch):
    _native_no_connect(monkeypatch)
    set_start_attr(monkeypatch, "_check_alias", lambda alias: None)
    result = CliRunner().invoke(start.start_app, ["omp-work", "--no-launch", "--yolo", "--", "-c"])
    assert result.exit_code == 0, result.output
    assert "omp-work runs with its own model, login and config" in result.output
    assert _launch_command(result.output) == [
        "bash", "-ic", 'omp-work "$@"', "agent-switch", "--yolo", "-c",
    ]
    assert "PI_CODING_AGENT_DIR" not in result.output


@pytest.mark.parametrize("flags", [
    ["--url", BASE],
    ["--skill", "{source}"],
    ["--mcp-stdio", "ev:npx everything"],
])
def test_omp_alias_refuses_session_flags(fake_vllm, monkeypatch, omp_user, flags):
    _, source = omp_user
    set_start_attr(monkeypatch, "_check_alias", lambda *args: pytest.fail("alias must be rejected"))
    args = [flag.format(source = source) for flag in flags]
    result = CliRunner().invoke(start.start_app, ["omp-work", "--no-launch", *args])
    assert result.exit_code == 1
    assert "omp aliases" in result.output


def test_native_omp_skill_overlay_keeps_the_user_config(fake_vllm, tmp_path, monkeypatch, omp_user):
    user_agent, source = omp_user
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(tmp_path / "my-sessions"))
    overlay = tmp_path / "agents" / "omp-native" / "agent"
    result = CliRunner().invoke(start.start_app, ["omp", "--no-launch", "--skill", str(source)])
    assert result.exit_code == 0, result.output
    _assert_env_set(result.output, "PI_CODING_AGENT_DIR", str(overlay))
    for name in _OVERLAY_UNSET:
        _assert_env_unset(result.output, name)
    # Pinned, so a project dotenv file cannot pick another root or a profile first.
    _assert_env_set(result.output, "PI_CONFIG_DIR", ".omp")
    _assert_env_set(result.output, "OMP_PROFILE", "default")
    # Native keeps the user's own session dir, as Pi's native overlay does.
    assert "PI_CODING_AGENT_SESSION_DIR" not in result.output
    assert _launch_command(result.output) == ["omp"]
    for name in ("config.yml", "mcp.json"):
        assert (overlay / name).is_symlink()
        assert (overlay / name).resolve() == user_agent / name
    assert (overlay / "skills" / "user-only").resolve() == user_agent / "skills" / "user-only"
    assert (overlay / "skills" / "injected").resolve() == source
    cleared = CliRunner().invoke(start.start_app, ["omp", "--no-launch"])
    assert cleared.exit_code == 0, cleared.output
    assert _launch_command(cleared.output) == ["omp"]
    assert not os.path.lexists(overlay)


def test_native_omp_mcp_adds_mounted_servers_to_the_user_ones(fake_vllm, tmp_path, omp_user):
    user_agent, source = omp_user
    before = (user_agent / "mcp.json").read_text()
    overlay = tmp_path / "agents" / "omp-native" / "agent"
    result = CliRunner().invoke(
        start.start_app, ["omp", "--no-launch", "--mcp-stdio", "ev:npx everything"]
    )
    assert result.exit_code == 0, result.output
    _assert_env_set(result.output, "PI_CODING_AGENT_DIR", str(overlay))
    assert not (overlay / "mcp.json").is_symlink()
    assert json.loads((overlay / "mcp.json").read_text()) == {
        "$schema": "schema-url",
        "mcpServers": {
            "mine": {"command": "mine"},
            "ev": {"type": "stdio", "command": _wrapper(tmp_path / "agents" / "omp-native", "ev"), "args": [], "env": {}},
        },
        # A user denylist entry would hide the mounted server.
        "disabledServers": ["other"],
    }
    assert (overlay / "config.yml").resolve() == user_agent / "config.yml"
    assert (user_agent / "mcp.json").read_text() == before
    # Without MCP flags the user's own file comes back, not the merged one.
    skill_only = CliRunner().invoke(start.start_app, ["omp", "--no-launch", "--skill", str(source)])
    assert skill_only.exit_code == 0, skill_only.output
    assert (overlay / "mcp.json").resolve() == user_agent / "mcp.json"


def test_native_omp_mcp_without_a_user_file(fake_vllm, tmp_path, omp_user):
    user_agent, _ = omp_user
    (user_agent / "mcp.json").unlink()
    result = CliRunner().invoke(start.start_app, [
        "omp", "--no-launch", "--mcp-url", "tools:http://127.0.0.1:8931/mcp",
    ])
    assert result.exit_code == 0, result.output
    overlay = tmp_path / "agents" / "omp-native" / "agent"
    assert json.loads((overlay / "mcp.json").read_text()) == {
        "mcpServers": {"tools": {"type": "http", "url": "http://127.0.0.1:8931/mcp", "headers": {}}}
    }


@pytest.mark.parametrize("profile_arg", [["--profile", "work"], ["--profile=work"]])
def test_omp_overlay_refuses_a_passthrough_profile(fake_vllm, omp_user, profile_arg):
    _, source = omp_user
    result = CliRunner().invoke(
        start.start_app, ["omp", "--no-launch", "--skill", str(source), "--", *profile_arg]
    )
    assert result.exit_code == 1
    assert "--profile" in result.output
    bare = CliRunner().invoke(start.start_app, ["omp", "--no-launch", "--", *profile_arg])
    assert bare.exit_code == 0, bare.output
    assert _launch_command(bare.output) == ["omp", *profile_arg]


@pytest.mark.parametrize(("env", "expected"), [
    ({}, ".omp/agent"),
    ({"OMP_PROFILE": " work "}, ".omp/profiles/work/agent"),
    ({"PI_PROFILE": "work"}, ".omp/profiles/work/agent"),
    # An explicitly empty OMP_PROFILE picks the default profile over PI_PROFILE.
    ({"OMP_PROFILE": "", "PI_PROFILE": "work"}, ".omp/agent"),
    ({"OMP_PROFILE": "default"}, ".omp/agent"),
    # A named profile ignores PI_CODING_AGENT_DIR.
    ({"OMP_PROFILE": "work", "PI_CODING_AGENT_DIR": "/custom"}, ".omp/profiles/work/agent"),
    ({"PI_CODING_AGENT_DIR": "custom"}, "cwd/custom"),
    ({"PI_CONFIG_DIR": ".omp-alt"}, ".omp-alt/agent"),
    ({"PI_CONFIG_DIR": ".omp-alt", "PI_PROFILE": "work"}, ".omp-alt/profiles/work/agent"),
])
def test_omp_user_agent_dir_follows_omp(tmp_path, monkeypatch, env, expected):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in _OMP_ENV:
        monkeypatch.delenv(name, raising = False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    overlay = tmp_path / "session" / "agent"
    assert omp_agent._get_omp_user_agent_dir(overlay) == tmp_path / expected


def test_omp_user_agent_dir_skips_the_session_itself(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in ("OMP_PROFILE", "PI_PROFILE", "PI_CONFIG_DIR"):
        monkeypatch.delenv(name, raising = False)
    overlay = tmp_path / "session" / "agent"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(overlay))
    assert omp_agent._get_omp_user_agent_dir(overlay) == tmp_path / ".omp" / "agent"


@pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason = "omp reads XDG dirs on Linux/macOS")
@pytest.mark.parametrize("xdg_var", _XDG_VARS)
def test_native_omp_overlay_refuses_an_omp_xdg_dir(fake_vllm, tmp_path, monkeypatch, omp_user, xdg_var):
    _, source = omp_user
    monkeypatch.setenv(xdg_var, str(tmp_path / "xdg"))
    args = ["omp", "--no-launch", "--skill", str(source)]
    xdg_dir = tmp_path / "xdg" / "omp"
    xdg_dir.mkdir(parents = True)
    result = CliRunner().invoke(start.start_app, args)
    assert result.exit_code == 1
    assert str(xdg_dir) in result.output
    assert "--url/--provider" in result.output
    assert not os.path.lexists(tmp_path / "agents" / "omp-native" / "agent")
    # Bare native launch sets no PI_CODING_AGENT_DIR, so it needs no guard.
    assert CliRunner().invoke(start.start_app, ["omp", "--no-launch"]).exit_code == 0
    # A user agent dir override already turns XDG off for the user's own runs.
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "custom-agent"))
    assert CliRunner().invoke(start.start_app, args).exit_code == 0
    # The variable alone moves nothing; omp uses it once <dir>/omp exists.
    monkeypatch.delenv("PI_CODING_AGENT_DIR")
    xdg_dir.rmdir()
    assert CliRunner().invoke(start.start_app, args).exit_code == 0


@pytest.mark.parametrize("env", [{"OMP_PROFILE": "work"}, {"PI_PROFILE": " work "}])
def test_native_omp_overlay_refuses_a_named_profile(fake_vllm, monkeypatch, omp_user, env):
    _, source = omp_user
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(
        start.start_app, ["omp", "--no-launch", "--mcp-stdio", "ev:npx everything"]
    )
    assert result.exit_code == 1
    assert "'work'" in result.output and "--url/--provider" in result.output
    assert CliRunner().invoke(start.start_app, ["omp", "--no-launch"]).exit_code == 0


@pytest.mark.parametrize("env", [{"OMP_PROFILE": "default"}, {"OMP_PROFILE": "", "PI_PROFILE": "work"}])
def test_native_omp_overlay_drops_a_default_profile(fake_vllm, tmp_path, monkeypatch, omp_user, env):
    _, source = omp_user
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(start.start_app, ["omp", "--no-launch", "--skill", str(source)])
    assert result.exit_code == 0, result.output
    for name in _OVERLAY_UNSET:
        _assert_env_unset(result.output, name)
    _assert_env_set(result.output, "OMP_PROFILE", "default")


def _wrapper(session, name: str) -> str:
    return str(session / "mcp" / f"{name}.sh")


def _stdio(env = None, command = "npx", args = ()):
    return {"transport": "stdio", "command": command, "args": list(args), "env": env or {}}


def _http(url = "http://127.0.0.1:8931/mcp", headers = None):
    return {"transport": "http", "url": url, "headers": headers or {}}


@pytest.mark.parametrize(("server", "needle"), [
    (_stdio(command = "${TOOL}"), "command"),
    (_stdio(args = ["-y", "pkg@${V}"]), "args"),
    (_http(url = "http://127.0.0.1:8931/${P}"), "url"),
])
def test_omp_mcp_values_refuse_what_omp_would_expand(capsys, server, needle):
    # Without a wrapper (Windows), omp reads the stdio command and args from mcp.json.
    with pytest.raises(typer.Exit):
        omp_agent._check_omp_mcp_values({"ev": server}, is_stdio_wrapped = False)
    err = capsys.readouterr().err
    assert "'ev'" in err and needle in err


def test_omp_mcp_wrapped_stdio_may_hold_dollar_braces(capsys):
    omp_agent._check_omp_mcp_values({"ev": _stdio(command = "${TOOL}", args = ["${V}"])}, is_stdio_wrapped = True)
    with pytest.raises(typer.Exit):
        omp_agent._check_omp_mcp_values({"web": _http(url = "http://h/${P}")}, is_stdio_wrapped = True)


@pytest.mark.skipif(os.name == "nt", reason = "the wrapper is a POSIX shell script")
def test_omp_mcp_stdio_servers_run_through_a_private_wrapper(tmp_path):
    servers = {"ev": _stdio({"TOKEN": "s3cr$t ${X}", "EMPTY": ""}, command = "npx", args = ["-y", "pkg@${V}"])}
    env = {}
    wrappers = tmp_path / "mcp"
    assert omp_agent._get_omp_mcp_servers(servers, env, wrappers) == {
        "ev": {"type": "stdio", "command": str(wrappers / "ev.sh"), "args": [], "env": {}},
    }
    # The secret stays out of omp's env, so its other children never see it.
    assert env == {}
    script = (wrappers / "ev.sh").read_text()
    assert script.startswith("#!/bin/sh\n")
    # Every word is quoted, so any env key the registry takes stays one word.
    assert script.rstrip().endswith("exec env 'TOKEN=s3cr$t ${X}' EMPTY= npx -y 'pkg@${V}'")
    assert stat.S_IMODE((wrappers / "ev.sh").stat().st_mode) == 0o700
    assert subprocess.run(["sh", "-n", str(wrappers / "ev.sh")]).returncode == 0


def test_omp_mcp_env_and_header_values_go_through_the_env():
    servers = {
        "ev": _stdio({"A": "!pass show mcp", "B": "x${B}", "EMPTY": ""}, args = ["!x"]),
        "web": _http(headers = {"Authorization": "GITHUB_TOKEN", "X": "a${B}"}),
    }
    # Only command, args and url stay as written, and none of them holds ${.
    omp_agent._check_omp_mcp_values(servers, is_stdio_wrapped = False)
    env = {}
    # Without a wrapper dir (Windows), stdio env values go through the env too.
    assert omp_agent._get_omp_mcp_servers(servers, env, None) == {
        "ev": {
            "type": "stdio", "command": "npx", "args": ["!x"],
            # An empty value stays empty, so omp drops it instead of sending a name.
            "env": {"A": "AGENT_SWITCH_OMP_MCP_1", "B": "AGENT_SWITCH_OMP_MCP_2", "EMPTY": ""},
        },
        "web": {
            "type": "http", "url": "http://127.0.0.1:8931/mcp",
            "headers": {"Authorization": "AGENT_SWITCH_OMP_MCP_4", "X": "AGENT_SWITCH_OMP_MCP_5"},
        },
    }
    assert env == {
        "AGENT_SWITCH_OMP_MCP_1": "!pass show mcp",
        "AGENT_SWITCH_OMP_MCP_2": "x${B}",
        "AGENT_SWITCH_OMP_MCP_4": "GITHUB_TOKEN",
        "AGENT_SWITCH_OMP_MCP_5": "a${B}",
    }


def test_native_omp_mcp_values_go_through_the_env_and_keep_user_servers(fake_vllm, tmp_path, monkeypatch, omp_user):
    user_agent, _ = omp_user
    # The user's own ${...} and !cmd are omp's to resolve, so they stay as written.
    (user_agent / "mcp.json").write_text(json.dumps(
        {"mcpServers": {"mine": {"command": "mine", "env": {"T": "${HOME}", "U": "!cmd"}}}}
    ))
    monkeypatch.setenv("MY_TOKEN", "secret")
    result = CliRunner().invoke(start.start_app, [
        "omp", "--no-launch", "--mcp-stdio", "ev:npx everything", "--mcp-env", "ev:TOKEN=MY_TOKEN",
    ])
    assert result.exit_code == 0, result.output
    session = tmp_path / "agents" / "omp-native"
    merged = json.loads((session / "agent" / "mcp.json").read_text())
    assert merged["mcpServers"]["mine"] == {"command": "mine", "env": {"T": "${HOME}", "U": "!cmd"}}
    assert merged["mcpServers"]["ev"] == {"type": "stdio", "command": _wrapper(session, "ev"), "args": [], "env": {}}
    assert "exec env TOKEN=MY_TOKEN npx everything" in (session / "mcp" / "ev.sh").read_text()
    assert "AGENT_SWITCH_OMP_MCP" not in result.output
    # A later run without MCP flags leaves no wrapper behind.
    rerun = CliRunner().invoke(start.start_app, ["omp", "--no-launch"])
    assert rerun.exit_code == 0, rerun.output
    assert not os.path.lexists(session / "mcp")


def test_native_omp_overlay_keeps_the_user_dot_mcp_json(fake_vllm, tmp_path, omp_user):
    user_agent, _ = omp_user
    (user_agent / ".mcp.json").write_text(json.dumps({"mcpServers": {"dot": {"command": "dot"}}}))
    before = (user_agent / ".mcp.json").read_text()
    result = CliRunner().invoke(
        start.start_app, ["omp", "--no-launch", "--mcp-stdio", "ev:npx everything"]
    )
    assert result.exit_code == 0, result.output
    overlay = tmp_path / "agents" / "omp-native" / "agent"
    # omp reads both user files (discovery/builtin.ts:236-241); only mcp.json is merged.
    assert (overlay / ".mcp.json").is_symlink()
    assert (overlay / ".mcp.json").resolve() == user_agent / ".mcp.json"
    assert "dot" not in json.loads((overlay / "mcp.json").read_text())["mcpServers"]
    assert (user_agent / ".mcp.json").read_text() == before


@pytest.mark.skipif(sys.platform not in ("linux", "darwin"), reason = "omp reads XDG dirs on Linux/macOS")
def test_native_omp_xdg_guard_counts_an_override_naming_the_default_dir(fake_vllm, tmp_path, monkeypatch, omp_user):
    user_agent, source = omp_user
    (tmp_path / "xdg" / "omp").mkdir(parents = True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    # omp treats an override equal to the default agent dir as the default (dirs.ts:366-369).
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(user_agent))
    result = CliRunner().invoke(start.start_app, ["omp", "--no-launch", "--skill", str(source)])
    assert result.exit_code == 1
    assert str(tmp_path / "xdg" / "omp") in result.output


# ── --url/--provider launch ──────────────────────────────────────────


_OMP_THINKING = {
    "mode": "effort",
    "efforts": ["minimal", "low", "medium", "high", "xhigh", "max"],
    "requiresEffort": False,
}


def _shell(value: str) -> str:
    """How the --no-launch recipe writes an env value."""
    return value if os.name == "nt" else shlex.quote(value)


def _invoke_omp_url(*args) -> str:
    result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch", *args])
    assert result.exit_code == 0, result.output
    return result.output


def _run_omp_url(*args) -> list:
    return _launch_command(_invoke_omp_url(*args))


def _omp_session_provider(tmp_path) -> dict:
    models = yaml.safe_load((tmp_path / "agents" / "omp" / "agent" / "models.yml").read_text())
    return models["providers"]["agent-switch"]


def test_connect_omp_no_launch(fake_vllm, tmp_path, monkeypatch, omp_url_user):
    user_agent, _ = omp_url_user
    for name in ("commands", "rules", "extensions", "agents", "sessions"):
        (user_agent / name).mkdir()
    for name in ("AGENTS.md", "SYSTEM.md", "models.yml", "agent.db", "settings.json", ".env", "secrets.yml", "ssh.json"):
        (user_agent / name).write_text("user\n")
    before = sorted(str(path) for path in user_agent.rglob("*"))
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(tmp_path / "my-sessions"))
    result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch"])
    assert result.exit_code == 0, result.output
    agent_dir = tmp_path / "agents" / "omp" / "agent"
    _assert_env_set(result.output, "PI_CODING_AGENT_DIR", str(agent_dir))
    _assert_env_unset(result.output, "PI_PROFILE")
    _assert_env_set(result.output, "OMP_PROFILE", "default")
    _assert_env_set(result.output, "PI_CONFIG_DIR", ".omp")
    # Unset keeps omp's per-project session folders inside the session agent dir.
    _assert_env_unset(result.output, "PI_CODING_AGENT_SESSION_DIR")
    # The key reaches omp through the env, so omp never reads it as a command or a name.
    _assert_env_set(result.output, "AGENT_SWITCH_OMP_API_KEY", KEY)
    # omp reads Claude, Codex and ~/.agents resources through HOME, so it stays.
    assert "export HOME=" not in result.output
    assert _launch_command(result.output) == ["omp", "--model", f"agent-switch/{MODEL['id']}"]
    assert _omp_session_provider(tmp_path) == {
        "api": "openai-completions",
        "baseUrl": f"{BASE}/v1",
        "apiKey": "AGENT_SWITCH_OMP_API_KEY",
        "models": [{
            "id": MODEL["id"],
            "reasoning": True,
            "thinking": _OMP_THINKING,
            "contextWindow": MODEL["context_length"],
            "maxTokens": 32000,
            "compat": {"thinkingFormat": "qwen-chat-template", "qwenTemplateReasoningEffort": True},
        }],
    }
    assert os.stat(agent_dir / "models.yml").st_mode & 0o077 == 0
    for name in ("skills", "commands", "rules", "extensions", "agents", "AGENTS.md", "SYSTEM.md", "secrets.yml", "ssh.json"):
        assert (agent_dir / name).is_symlink(), name
        assert (agent_dir / name).resolve() == user_agent / name
    for name in ("agent.db", "settings.json", "sessions", ".env"):
        assert not os.path.lexists(agent_dir / name), name
    # The session writes its own config.yml and mcp.json; the user's are not linked.
    assert not (agent_dir / "mcp.json").is_symlink()
    assert not (agent_dir / "config.yml").is_symlink()
    assert "theme" not in yaml.safe_load((agent_dir / "config.yml").read_text())
    assert sorted(str(path) for path in user_agent.rglob("*")) == before


@pytest.mark.parametrize(("where", "line"), [
    ("home", "PI_CODING_AGENT_SESSION_DIR=/elsewhere"),
    ("config-root", 'export OMP_CODING_AGENT_SESSION_DIR="/elsewhere"'),
    ("project", "PI_CODING_AGENT_SESSION_DIR='/elsewhere' # mine"),
    ("session-agent", "PI_CODING_AGENT_SESSION_DIR=/elsewhere"),
    # Bun loads these project files itself before omp starts (utils/src/env.ts:112-114,162-167).
    ("project/.env.local", "PI_CODING_AGENT_SESSION_DIR=/elsewhere"),
    ("project/.env.development", "PI_CODING_AGENT_SESSION_DIR=/elsewhere"),
])
def test_omp_url_pins_the_session_dir_a_dotenv_would_move(fake_vllm, tmp_path, monkeypatch, omp_url_user, where, line):
    user_agent, _ = omp_url_user
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    agent_dir = tmp_path / "agents" / "omp" / "agent"
    path = {
        "home": user_agent.parent.parent / ".env",
        "config-root": user_agent.parent / ".env",
        "project": project / ".env",
        "session-agent": agent_dir / ".env",
        "project/.env.local": project / ".env.local",
        "project/.env.development": project / ".env.development",
    }[where]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text(f"# a comment\n{line}\n")
    # omp fills an unset variable from these files (utils/src/env.ts:384-392), so it stays set,
    # to a folder of this project's own.
    pinned = agent_dir / "sessions-pinned" / hashlib.sha256(str(project).encode()).hexdigest()[:16]
    _assert_env_set(_invoke_omp_url(), "PI_CODING_AGENT_SESSION_DIR", str(pinned))


@pytest.mark.parametrize(("where", "line"), [
    ("home", "PI_CODING_AGENT_SESSION_DIR="),
    ("home", "# PI_CODING_AGENT_SESSION_DIR=/elsewhere"),
    # omp reads <agentDir>/.env from the session agent dir, not from yours.
    ("user-agent", "PI_CODING_AGENT_SESSION_DIR=/elsewhere"),
    # Bun's own loading maps no OMP_ names (utils/src/env.ts:279-284 is omp's).
    ("project/.env.local", "OMP_CODING_AGENT_SESSION_DIR=/elsewhere"),
])
def test_omp_url_keeps_per_project_sessions_without_such_a_dotenv(fake_vllm, tmp_path, monkeypatch, omp_url_user, where, line):
    user_agent, _ = omp_url_user
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    path = {
        "home": user_agent.parent.parent / ".env",
        "user-agent": user_agent / ".env",
        "project/.env.local": project / ".env.local",
    }[where]
    path.write_text(f"{line}\n")
    _assert_env_unset(_invoke_omp_url(), "PI_CODING_AGENT_SESSION_DIR")


def test_omp_url_session_follows_a_named_profile(fake_vllm, tmp_path, monkeypatch, omp_url_user):
    user_agent, _ = omp_url_user
    profile_agent = user_agent.parent / "profiles" / "work" / "agent"
    (profile_agent / "commands").mkdir(parents = True)
    monkeypatch.setenv("OMP_PROFILE", "work")
    # The session dir is the isolation here, so an omp XDG dir does not block it either.
    (tmp_path / "xdg" / "omp").mkdir(parents = True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _run_omp_url()
    agent_dir = tmp_path / "agents" / "omp" / "agent"
    assert (agent_dir / "commands").resolve() == profile_agent / "commands"
    assert not os.path.lexists(agent_dir / "skills")


@pytest.mark.parametrize(("flags", "level"), [
    (["--reasoning-effort", "high"], "high"),
    (["--reasoning-effort", "max"], "max"),
    (["--reasoning-effort", "none"], "off"),
    (["--reasoning", "off"], "off"),
    (["--reasoning", "off", "--reasoning-effort", "high"], "off"),
])
def test_omp_reasoning_flags_set_the_thinking_level(fake_vllm, omp_url_user, flags, level):
    command = _run_omp_url(*flags, "--", "--thinking", "low", "--model", "other/x")
    # agent-switch's own --thinking and --model win over passed-through ones.
    assert command == ["omp", "--model", f"agent-switch/{MODEL['id']}", "--thinking", level]


@pytest.mark.parametrize("flags", [[], ["--reasoning", "on"]])
def test_omp_leaves_the_thinking_level_to_omp(fake_vllm, omp_url_user, flags):
    assert "--thinking" not in _run_omp_url(*flags)


def test_omp_sampling_rides_in_extra_body_without_the_reasoning_fields(fake_vllm, tmp_path, omp_url_user):
    _run_omp_url("--temperature", "0.3", "--top-k", "40", "--reasoning-effort", "high")
    [row] = _omp_session_provider(tmp_path)["models"]
    assert row["compat"]["extraBody"] == {"temperature": 0.3, "top_k": 40}


def test_omp_thinking_uses_reasoning_effort_on_openai_servers(fake_vllm, tmp_path, omp_url_user):
    _run_omp_url("--provider", "openai")
    [row] = _omp_session_provider(tmp_path)["models"]
    assert row["reasoning"] is True and row["thinking"] == _OMP_THINKING
    assert row["compat"] == {"thinkingFormat": "openai"}


@pytest.mark.parametrize("flags", [["--provider", "lmstudio"], ["--provider", "ollama"], ["--reasoning", "auto"]])
def test_omp_thinking_stays_off_where_omp_cannot_set_it(fake_vllm, tmp_path, monkeypatch, omp_url_user, flags):
    # The vLLM-shaped fake cannot answer LM Studio or Ollama, so stand in for the connection.
    set_start_attr(monkeypatch, "_connect", lambda *args, **kwargs: (BASE, KEY, dict(MODEL)))
    set_start_attr(monkeypatch, "_get_served_models", lambda *args: [])
    assert "--thinking" not in _run_omp_url(*flags, "--reasoning-effort", "high")
    [row] = _omp_session_provider(tmp_path)["models"]
    assert "reasoning" not in row and "thinking" not in row
    # A pinned effort rides in the body instead, as for Pi.
    assert "thinkingFormat" not in row.get("compat", {})


def test_omp_lists_every_served_model_and_keeps_the_connected_window(fake_vllm, tmp_path, monkeypatch, omp_url_user):
    other = {"id": "small", "context_length": 8192}
    set_start_attr(monkeypatch, "_get_served_models",
        lambda *args: [{"id": MODEL["id"], "context_length": 4096}, other],
    )
    _run_omp_url("--max-tokens", "100000", "--reasoning", "auto")
    rows = {row["id"]: row for row in _omp_session_provider(tmp_path)["models"]}
    assert rows[MODEL["id"]]["contextWindow"] == MODEL["context_length"]
    assert rows[MODEL["id"]]["maxTokens"] == MODEL["context_length"] // 2
    assert rows["small"] == {"id": "small", "contextWindow": 8192, "maxTokens": 4096}


def test_omp_models_keep_other_session_providers(fake_vllm, tmp_path, omp_url_user):
    _run_omp_url()
    path = tmp_path / "agents" / "omp" / "agent" / "models.yml"
    models = yaml.safe_load(path.read_text())
    models["providers"]["mine"] = {"baseUrl": "https://example.test/v1"}
    path.write_text(yaml.safe_dump(models))
    _run_omp_url()
    assert yaml.safe_load(path.read_text())["providers"]["mine"] == {"baseUrl": "https://example.test/v1"}


def test_omp_key_and_headers_go_through_the_env(fake_vllm, tmp_path, monkeypatch, omp_url_user):
    # Values omp would read as a command or a variable name reach it unchanged.
    monkeypatch.setenv("MY_KEY", "other")
    output = _invoke_omp_url(
        "--api-key", "!pass show llm", "--header", "X-Token=MY_KEY", "--header", "X-Empty=",
    )
    provider = _omp_session_provider(tmp_path)
    assert provider["apiKey"] == "AGENT_SWITCH_OMP_API_KEY"
    # An empty value stays empty, so omp drops the header instead of sending a name.
    assert provider["headers"] == {"X-Token": "AGENT_SWITCH_OMP_HEADER_1", "X-Empty": ""}
    _assert_env_set(output, "AGENT_SWITCH_OMP_API_KEY", _shell("!pass show llm"))
    _assert_env_set(output, "AGENT_SWITCH_OMP_HEADER_1", "MY_KEY")
    assert "AGENT_SWITCH_OMP_HEADER_2" not in output


def test_omp_url_mcp_replaces_the_user_servers_and_keeps_their_denylist(fake_vllm, tmp_path, omp_url_user):
    mcp_path = tmp_path / "agents" / "omp" / "agent" / "mcp.json"
    output = _invoke_omp_url(
        "--mcp-stdio", "ev:npx everything", "--mcp-env", "ev:T=!x",
        "--mcp-url", "web:http://127.0.0.1:8931/mcp", "--mcp-header", "web:X-Key=!k",
    )
    assert json.loads(mcp_path.read_text()) == {
        "mcpServers": {
            "ev": {"type": "stdio", "command": _wrapper(tmp_path / "agents" / "omp", "ev"), "args": [], "env": {}},
            # An http header value still goes through the env, as for Codex.
            "web": {"type": "http", "url": "http://127.0.0.1:8931/mcp", "headers": {"X-Key": "AGENT_SWITCH_OMP_MCP_1"}},
        },
        # The user's denylist still applies, except to a server mounted now.
        "disabledServers": ["other"],
    }
    _assert_env_set(output, "AGENT_SWITCH_OMP_MCP_1", _shell("!k"))
    assert "!x" not in output
    _run_omp_url()
    assert json.loads(mcp_path.read_text()) == {"disabledServers": ["ev", "other"]}


def test_omp_url_mcp_file_goes_without_flags_or_a_denylist(fake_vllm, tmp_path, omp_url_user):
    user_agent, _ = omp_url_user
    mcp_path = tmp_path / "agents" / "omp" / "agent" / "mcp.json"
    _run_omp_url("--mcp-stdio", "ev:npx everything")
    (user_agent / "mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "mine"}}}))
    _run_omp_url()
    assert not os.path.lexists(mcp_path)


def test_omp_url_skills_replace_the_link_and_restore_it(fake_vllm, tmp_path, omp_url_user):
    user_agent, source = omp_url_user
    skills = tmp_path / "agents" / "omp" / "agent" / "skills"
    _run_omp_url("--skill", str(source))
    assert not skills.is_symlink()
    assert (skills / "injected").resolve() == source
    assert (skills / "user-only").resolve() == user_agent / "skills" / "user-only"
    _run_omp_url()
    assert skills.is_symlink() and skills.resolve() == user_agent / "skills"


def test_omp_url_rerun_drops_stale_links_and_keeps_session_dirs(fake_vllm, tmp_path, omp_url_user):
    user_agent, _ = omp_url_user
    agent_dir = tmp_path / "agents" / "omp" / "agent"
    (user_agent / "commands").mkdir()
    _run_omp_url("--persist")
    assert (agent_dir / "commands").is_symlink()
    (user_agent / "commands").rmdir()
    (agent_dir / "rules").mkdir()
    (user_agent / "rules").mkdir()
    _run_omp_url("--persist")
    assert not os.path.lexists(agent_dir / "commands")
    # A real session dir is the session's own, never replaced by a link.
    assert (agent_dir / "rules").is_dir() and not (agent_dir / "rules").is_symlink()


@pytest.mark.parametrize("profile_arg", [["--profile", "work"], ["--profile=work"]])
def test_omp_url_refuses_a_passthrough_profile(fake_vllm, omp_url_user, profile_arg):
    result = CliRunner().invoke(
        start.start_app, ["omp", "--url", BASE, "--no-launch", "--", *profile_arg]
    )
    assert result.exit_code == 1
    assert "--profile" in result.output


# ── Session config.yml: compaction, onboarding, thinking level ───────


def _omp_session_config_path(tmp_path):
    return tmp_path / "agents" / "omp" / "agent" / "config.yml"


def _omp_session_config(tmp_path) -> dict:
    return yaml.safe_load(_omp_session_config_path(tmp_path).read_text())


def test_omp_session_config_defaults(fake_vllm, tmp_path, omp_url_user):
    _run_omp_url()
    assert _omp_session_config(tmp_path) == {
        # A fresh session dir would otherwise open omp's onboarding on every launch.
        "startup": {"setupWizard": False},
        "defaultThinkingLevel": "xhigh",
        # --compact-keep defaults to 0.2 of the launch window, as for Pi.
        "compaction": {"keepRecentTokens": int(MODEL["context_length"] * 0.2)},
    }


def test_omp_compaction_follows_the_flags_and_undoes_only_its_own_keys(fake_vllm, tmp_path, omp_url_user):
    _run_omp_url("--compact-at", "0.8", "--compact-keep", "0.1")
    assert _omp_session_config(tmp_path)["compaction"] == {
        "thresholdPercent": 80,
        "keepRecentTokens": int(MODEL["context_length"] * 0.1),
    }
    # Settings changed inside the session stay; only what agent-switch wrote goes.
    config = _omp_session_config(tmp_path)
    config["compaction"]["enabled"] = False
    config["compaction"]["asyncEnabled"] = False
    config["theme"] = "dark"
    _omp_session_config_path(tmp_path).write_text(yaml.safe_dump(config))
    _run_omp_url("--compact-keep", "0")
    assert _omp_session_config(tmp_path) == {
        "startup": {"setupWizard": False},
        "defaultThinkingLevel": "xhigh",
        "compaction": {"enabled": False, "asyncEnabled": False},
        "theme": "dark",
    }


def test_omp_compaction_keeps_a_threshold_the_user_changed(fake_vllm, tmp_path, omp_url_user):
    _run_omp_url("--compact-at", "0.8")
    config = _omp_session_config(tmp_path)
    config["compaction"]["thresholdPercent"] = 70
    _omp_session_config_path(tmp_path).write_text(yaml.safe_dump(config))
    _run_omp_url()
    assert _omp_session_config(tmp_path)["compaction"]["thresholdPercent"] == 70


def test_omp_session_config_keeps_values_the_session_has(fake_vllm, tmp_path, omp_url_user):
    path = _omp_session_config_path(tmp_path)
    path.parent.mkdir(parents = True)
    path.write_text(yaml.safe_dump({"startup": {"setupWizard": True}, "defaultThinkingLevel": "low"}))
    _run_omp_url()
    config = _omp_session_config(tmp_path)
    assert config["startup"] == {"setupWizard": True}
    assert config["defaultThinkingLevel"] == "low"


def test_omp_session_config_leaves_the_level_alone_without_a_thinking_format(fake_vllm, tmp_path, omp_url_user):
    _run_omp_url("--reasoning", "auto")
    assert "defaultThinkingLevel" not in _omp_session_config(tmp_path)


def test_omp_compaction_skips_keep_for_an_assumed_window(fake_vllm, tmp_path, monkeypatch, omp_url_user):
    assumed = {"id": MODEL["id"], "context_length": 250000, "is_window_assumed": True}
    set_start_attr(monkeypatch, "_connect", lambda *args, **kwargs: (BASE, KEY, dict(assumed)))
    set_start_attr(monkeypatch, "_get_served_models", lambda *args: [])
    result = CliRunner().invoke(start.start_app, [
        "omp", "--url", BASE, "--no-launch", "--compact-at", "0.75", "--compact-keep", "0.1",
    ])
    assert result.exit_code == 0, result.output
    assert "--compact-keep is ignored" in result.output
    # A percentage needs no reported window: omp scales it to each model's own.
    assert _omp_session_config(tmp_path)["compaction"] == {"thresholdPercent": 75}


def test_omp_session_config_left_alone_when_unparsable(fake_vllm, tmp_path, omp_url_user):
    path = _omp_session_config_path(tmp_path)
    path.parent.mkdir(parents = True)
    path.write_text("compaction: [unclosed\n")
    result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch", "--compact-at", "0.8"])
    assert result.exit_code == 0, result.output
    assert str(path) in result.output
    assert path.read_text() == "compaction: [unclosed\n"


def test_omp_url_persisted_skill_never_writes_into_the_user_skills(fake_vllm, tmp_path, omp_url_user):
    user_agent, source = omp_url_user
    before = sorted(str(path) for path in (user_agent / "skills").rglob("*"))
    skills = tmp_path / "agents" / "omp" / "agent" / "skills"
    _run_omp_url("--persist")
    assert skills.is_symlink()
    _run_omp_url("--persist", "--skill", str(source))
    assert not skills.is_symlink() and (skills / "injected").resolve() == source
    assert sorted(str(path) for path in (user_agent / "skills").rglob("*")) == before


# ── --url: the user's own safety settings ────────────────────────────


_SAFETY = {
    "tools": {
        "approvalMode": "always-ask",
        "approval": {"bash": "prompt"},
        "restrictedFolders": ["~/secrets"],
    },
    "mcp": {"enableProjectConfig": False},
    "disabledProviders": ["claude"],
    "bash": {"patterns": [{"match": "git push *", "approval": "deny"}], "gitGuard": True},
    "secrets": {"enabled": True},
}


def test_omp_url_session_keeps_the_user_safety_settings(fake_vllm, tmp_path, omp_url_user):
    user_agent, _ = omp_url_user
    (user_agent / "config.yml").write_text(yaml.safe_dump({**_SAFETY, "theme": "dark"}))
    command = _run_omp_url("--yolo")
    config = _omp_session_config(tmp_path)
    assert {key: config[key] for key in _SAFETY} == _SAFETY
    assert "theme" not in config
    # omp's own --yolo still wins over a copied approval mode (src/cli/args.ts:401).
    assert "--yolo" in command


def test_omp_url_safety_settings_follow_the_user_but_keep_session_changes(fake_vllm, tmp_path, omp_url_user):
    user_agent, _ = omp_url_user
    (user_agent / "config.yml").write_text(yaml.safe_dump(_SAFETY))
    _run_omp_url("--persist")
    config = _omp_session_config(tmp_path)
    config["tools"]["approvalMode"] = "write"
    _omp_session_config_path(tmp_path).write_text(yaml.safe_dump(config))
    (user_agent / "config.yml").write_text(yaml.safe_dump({
        "tools": {"approvalMode": "always-ask", "approval": {"bash": "deny"}},
        "disabledProviders": ["claude", "codex"],
    }))
    _run_omp_url("--persist")
    config = _omp_session_config(tmp_path)
    assert config["tools"] == {"approvalMode": "write", "approval": {"bash": "deny"}}
    assert config["disabledProviders"] == ["claude", "codex"]
    # Removed from the user's file since, so the copy goes too.
    assert "mcp" not in config


@pytest.mark.parametrize(("files", "expected"), [
    ({"config.yaml": "tools:\n  approvalMode: write\n"}, "write"),
    ({"settings.json": json.dumps({"tools": {"approvalMode": "always-ask"}})}, "always-ask"),
    # config.yml wins, and settings.json counts only without a YAML file (docs/settings.md:15-18).
    ({"config.yml": "theme: dark\n", "settings.json": json.dumps({"tools": {"approvalMode": "write"}})}, None),
])
def test_omp_url_reads_the_user_settings_file_omp_reads(fake_vllm, tmp_path, omp_url_user, files, expected):
    user_agent, _ = omp_url_user
    (user_agent / "config.yml").unlink()
    for name, text in files.items():
        (user_agent / name).write_text(text)
    _run_omp_url()
    assert _omp_session_config(tmp_path).get("tools", {}).get("approvalMode") == expected


def test_omp_yaml_quotes_every_string(tmp_path):
    # A YAML 1.2 reader would turn an unquoted 1e10 into a number (src/config/config-file.ts:245).
    path = tmp_path / "models.yml"
    omp_agent.write_omp_models(path, BASE, "key", {"id": "1e10", "context_length": 8192}, env = {})
    assert '"id": "1e10"' in path.read_text()
    assert yaml.safe_load(path.read_text())["providers"]["agent-switch"]["models"][0]["id"] == "1e10"


@pytest.mark.parametrize(("where", "line"), [
    ("project", "OMP_PROFILE=work"),
    ("project/.env.local", "PI_PROFILE=work"),
    ("home", "export OMP_PROFILE='work'"),
])
@pytest.mark.parametrize("mode", ["url", "native"])
def test_omp_refuses_a_profile_a_dotenv_would_select(fake_vllm, tmp_path, monkeypatch, omp_url_user, where, line, mode):
    user_agent, source = omp_url_user
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    path = {
        "project": project / ".env",
        "project/.env.local": project / ".env.local",
        "home": user_agent.parent.parent / ".env",
    }[where]
    path.write_text(f"{line}\n")
    if mode == "native":
        _native_no_connect(monkeypatch)
        args = ["omp", "--no-launch", "--skill", str(source)]
    else:
        args = ["omp", "--url", BASE, "--no-launch"]
    result = CliRunner().invoke(start.start_app, args)
    assert result.exit_code == 1
    assert str(path) in result.output and "PI_CODING_AGENT_DIR" in result.output
    path.write_text("OMP_PROFILE=default\n")
    assert CliRunner().invoke(start.start_app, args).exit_code == 0


def test_omp_url_pins_the_config_root_agent_switch_resolved(fake_vllm, tmp_path, monkeypatch, omp_url_user):
    user_agent, _ = omp_url_user
    monkeypatch.setenv("PI_CONFIG_DIR", ".omp-alt")
    (user_agent.parent.parent / ".omp-alt").mkdir()
    (user_agent.parent.parent / ".omp-alt" / ".env").write_text("PI_CODING_AGENT_SESSION_DIR=/elsewhere\n")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    # A non-empty value stays, so no dotenv file moves the root omp reads
    # (utils/src/env.ts:204-206); one that sets it is refused instead.
    output = _invoke_omp_url()
    _assert_env_set(output, "PI_CONFIG_DIR", ".omp-alt")
    assert "PI_CODING_AGENT_SESSION_DIR=" in output and "unset PI_CODING_AGENT_SESSION_DIR" not in output


def test_omp_url_fails_closed_on_an_unreadable_user_settings_file(fake_vllm, tmp_path, omp_url_user):
    user_agent, _ = omp_url_user
    (user_agent / "config.yml").write_text("tools: [unclosed\n")
    result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch"])
    assert result.exit_code == 1
    assert str(user_agent / "config.yml") in result.output


def test_omp_url_reads_the_user_settings_with_yaml_1_2_scalars(fake_vllm, tmp_path, omp_url_user):
    user_agent, _ = omp_url_user
    # Bun.YAML.parse keeps both as strings (src/config/settings.ts:2330-2348).
    (user_agent / "config.yml").write_text("tools:\n  restrictedFolders: [on, 2026-11-12, true]\n")
    _run_omp_url()
    assert _omp_session_config(tmp_path)["tools"]["restrictedFolders"] == ["on", "2026-11-12", True]
    manifest = json.loads((tmp_path / "agents" / "omp" / "agent" / ".agent-switch-config.json").read_text())
    assert manifest["tools.restrictedFolders"] == ["on", "2026-11-12", True]



# ── Rework 3 ─────────────────────────────────────────────────────────


def _pinned(agent_dir, project) -> Path:
    return agent_dir / "sessions-pinned" / hashlib.sha256(str(project).encode()).hexdigest()[:16]


@pytest.mark.parametrize("spelling", ["split", "equals", "relative"])
def test_omp_url_pins_a_session_dir_per_cwd_omp_runs_in(fake_vllm, tmp_path, monkeypatch, omp_url_user, spelling):
    user_agent, _ = omp_url_user
    (user_agent.parent.parent / ".env").write_text("PI_CODING_AGENT_SESSION_DIR=/elsewhere\n")
    launch = tmp_path / "launch"
    for name in ("a", "b"):
        (launch / name).mkdir(parents = True)
    monkeypatch.chdir(launch)
    agent_dir = tmp_path / "agents" / "omp" / "agent"

    def cwd_args(name):
        target = name if spelling == "relative" else str(launch / name)
        return [f"--cwd={target}"] if spelling == "equals" else ["--cwd", target]

    # omp runs in --cwd, resolved from the launch dir (src/cli/startup-cwd.ts:48-68).
    for name in ("a", "b"):
        output = _invoke_omp_url("--persist", "--", *cwd_args(name))
        _assert_env_set(output, "PI_CODING_AGENT_SESSION_DIR", str(_pinned(agent_dir, launch / name)))
    # A --cwd after a passthrough -- is a message for omp, not a flag.
    output = _invoke_omp_url("--persist", "--", "--", "--cwd", "a")
    _assert_env_set(output, "PI_CODING_AGENT_SESSION_DIR", str(_pinned(agent_dir, launch)))


def _refuse_file_links(monkeypatch):
    real = Path.symlink_to

    def symlink_to(self, target, target_is_directory = False):
        if not target_is_directory:
            raise PermissionError("no file symlinks here")
        return real(self, target, target_is_directory)

    monkeypatch.setattr(Path, "symlink_to", symlink_to)


def test_omp_url_copies_a_user_file_it_cannot_link_and_clears_the_copy(fake_vllm, tmp_path, monkeypatch, omp_url_user):
    user_agent, _ = omp_url_user
    for name in ("AGENTS.md", "secrets.yml"):
        (user_agent / name).write_text(f"user {name}\n")
    agent_dir = tmp_path / "agents" / "omp" / "agent"
    with monkeypatch.context() as patched:
        _refuse_file_links(patched)
        _run_omp_url("--persist")
    for name in ("AGENTS.md", "secrets.yml"):
        assert not (agent_dir / name).is_symlink()
        assert (agent_dir / name).read_text() == f"user {name}\n"
    # The next run treats its own copy like a link: refreshed, and gone with the user's file.
    (user_agent / "AGENTS.md").unlink()
    _run_omp_url("--persist")
    assert not os.path.lexists(agent_dir / "AGENTS.md")
    assert (agent_dir / "secrets.yml").is_symlink()


@pytest.mark.parametrize("is_enabled", [True, False])
def test_omp_url_stops_when_secret_rules_cannot_reach_the_session(fake_vllm, tmp_path, monkeypatch, omp_url_user, is_enabled):
    user_agent, _ = omp_url_user
    (user_agent / "secrets.yml").write_text("- type: plain\n  content: hush\n")
    (user_agent / "config.yml").write_text(yaml.safe_dump({"secrets": {"enabled": is_enabled}}))
    _refuse_file_links(monkeypatch)
    monkeypatch.setattr(shutil, "copy2", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read-only")))
    result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch"])
    # omp reads the rules only from its active agent dir (src/secrets/index.ts:166-174).
    assert result.exit_code == (1 if is_enabled else 0), result.output
    assert "secrets.yml" in result.output


@pytest.mark.parametrize(("where", "line"), [
    ("config-root", "PI_CODING_AGENT_DIR=/dotfiles/omp-agent"),
    ("project", "PI_CONFIG_DIR=.omp-work"),
    ("home", "OMP_CODING_AGENT_DIR=/dotfiles/omp-agent"),
    ("project/.env.local", "PI_CONFIG_DIR=.omp-work"),
])
@pytest.mark.parametrize("mode", ["url", "native"])
def test_omp_refuses_an_agent_dir_a_dotenv_would_pick(fake_vllm, tmp_path, monkeypatch, omp_url_user, where, line, mode):
    user_agent, source = omp_url_user
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    path = {
        "config-root": user_agent.parent / ".env",
        "project": project / ".env",
        "home": user_agent.parent.parent / ".env",
        "project/.env.local": project / ".env.local",
    }[where]
    path.write_text(f"{line}\n")
    if mode == "native":
        _native_no_connect(monkeypatch)
        args = ["omp", "--no-launch", "--skill", str(source)]
    else:
        args = ["omp", "--url", BASE, "--no-launch"]
    result = CliRunner().invoke(start.start_app, args)
    assert result.exit_code == 1
    assert str(path) in result.output
    # The working alternative: a shell value, which agent-switch follows.
    assert "shell" in result.output


@pytest.mark.parametrize(("where", "line"), [
    # The session agent dir is agent-switch's own, so its .env picks no user dir.
    ("session-agent", "PI_CODING_AGENT_DIR=/dotfiles/omp-agent"),
    # Bun's own loading maps no OMP_ names (utils/src/env.ts:279-284 is omp's).
    ("project/.env.local", "OMP_CONFIG_DIR=.omp-work"),
])
def test_omp_url_ignores_a_dotenv_dir_that_picks_no_user_dir(fake_vllm, tmp_path, monkeypatch, omp_url_user, where, line):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    path = {
        "session-agent": tmp_path / "agents" / "omp" / "agent" / ".env",
        "project/.env.local": project / ".env.local",
    }[where]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text(f"{line}\n")
    _run_omp_url()


def test_omp_dotenv_profile_refusal_offers_the_shell(fake_vllm, tmp_path, monkeypatch, omp_url_user):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    (project / ".env").write_text("OMP_PROFILE=work\n")
    result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch"])
    assert result.exit_code == 1
    assert "shell" in result.output


@pytest.mark.skipif(os.name == "nt", reason = "the wrapper is a POSIX shell script")
def test_omp_wrapper_takes_any_env_key_the_registry_takes(fake_vllm, tmp_path, omp_url_user):
    _mcp_registry({"db": {"command": "env", "env": {"ConnectionStrings:Default": "Server=x;Db=y"}}})
    _run_omp_url("--mcp", "db")
    script = tmp_path / "agents" / "omp" / "mcp" / "db.sh"
    result = subprocess.run([str(script)], capture_output = True, text = True)
    assert result.returncode == 0, result.stderr
    assert "ConnectionStrings:Default=Server=x;Db=y" in result.stdout.splitlines()


def test_omp_session_yaml_keeps_a_date_omp_wrote_unquoted(fake_vllm, tmp_path, omp_url_user):
    user_agent, _ = omp_url_user
    (user_agent / "config.yml").write_text('tools:\n  restrictedFolders: ["2026-11-12"]\n')
    _run_omp_url("--persist")
    # omp rewrites the file with Bun.YAML.stringify, which leaves the date unquoted
    # (utils/src/yaml-config.ts:5-7).
    path = _omp_session_config_path(tmp_path)
    path.write_text(path.read_text().replace('"2026-11-12"', "2026-11-12"))
    (user_agent / "config.yml").write_text('tools:\n  restrictedFolders: ["2026-11-12", "/new"]\n')
    _run_omp_url("--persist")
    # Still the copy agent-switch made, so it follows the user's file.
    assert _omp_session_config(tmp_path)["tools"]["restrictedFolders"] == ["2026-11-12", "/new"]


@pytest.mark.parametrize("setup", ["broken-settings", "dotenv-profile", "dotenv-dir"])
def test_omp_url_refusals_come_before_connecting(fake_vllm, tmp_path, monkeypatch, omp_url_user, setup):
    user_agent, _ = omp_url_user
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    if setup == "broken-settings":
        (user_agent / "config.yml").write_text("tools: [unclosed\n")
    elif setup == "dotenv-profile":
        (project / ".env").write_text("OMP_PROFILE=work\n")
    else:
        (project / ".env").write_text("PI_CONFIG_DIR=.omp-work\n")
    calls = []
    set_start_attr(monkeypatch, "_connect", lambda *args, **kwargs: calls.append(args) or (BASE, KEY, dict(MODEL)))
    result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch"])
    assert result.exit_code == 1
    assert calls == []


# ── Rework 4 ─────────────────────────────────────────────────────────


def _copy_half_then_fail(src, dst, *args, **kwargs):
    # A copy that dies part way: the destination holds the first rule only.
    text = Path(src).read_text()
    Path(dst).write_text(text[: len(text) // 2])
    raise OSError(5, "Input/output error")


@pytest.mark.parametrize("is_enabled", [True, False])
def test_omp_url_leaves_no_part_of_a_failed_copy(fake_vllm, tmp_path, monkeypatch, omp_url_user, is_enabled):
    user_agent, _ = omp_url_user
    (user_agent / "secrets.yml").write_text(
        "- type: plain\n  content: first-secret\n- type: plain\n  content: second-secret\n"
    )
    (user_agent / "config.yml").write_text(yaml.safe_dump({"secrets": {"enabled": is_enabled}}))
    agent_dir = tmp_path / "agents" / "omp" / "agent"
    with monkeypatch.context() as patched:
        _refuse_file_links(patched)
        patched.setattr(shutil, "copy2", _copy_half_then_fail)
        result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch"])
    assert result.exit_code == (1 if is_enabled else 0), result.output
    # Neither the half file nor a temp file stays behind for a later run to keep.
    assert not os.path.lexists(agent_dir / "secrets.yml")
    assert not [path.name for path in agent_dir.iterdir() if "secrets" in path.name]
    _run_omp_url()
    assert (agent_dir / "secrets.yml").read_text() == (user_agent / "secrets.yml").read_text()


def test_omp_url_refuses_secret_rules_it_did_not_copy_itself(fake_vllm, tmp_path, omp_url_user):
    user_agent, _ = omp_url_user
    (user_agent / "secrets.yml").write_text("- type: plain\n  content: hush\n")
    (user_agent / "config.yml").write_text(yaml.safe_dump({"secrets": {"enabled": True}}))
    agent_dir = tmp_path / "agents" / "omp" / "agent"
    agent_dir.mkdir(parents = True)
    # A real file agent-switch has no record of may be any earlier, partial copy.
    (agent_dir / "secrets.yml").write_text("- type: plain\n  content: other\n")
    result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch"])
    assert result.exit_code == 1
    assert "secrets.yml" in result.output


def test_omp_url_secret_rules_stop_comes_before_connecting(fake_vllm, tmp_path, monkeypatch, omp_url_user):
    user_agent, _ = omp_url_user
    (user_agent / "secrets.yml").write_text("- type: plain\n  content: hush\n")
    (user_agent / "config.yml").write_text(yaml.safe_dump({"secrets": {"enabled": True}}))
    _refuse_file_links(monkeypatch)
    monkeypatch.setattr(shutil, "copy2", _copy_half_then_fail)
    calls = []
    set_start_attr(monkeypatch, "_connect", lambda *args, **kwargs: calls.append(args) or (BASE, KEY, dict(MODEL)))
    result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch"])
    assert result.exit_code == 1
    assert calls == []


@pytest.mark.parametrize(("line", "shell"), [
    ("PI_CONFIG_DIR=.omp-work", ("PI_CONFIG_DIR", ".omp-work")),
    ("OMP_CODING_AGENT_DIR=/dotfiles/omp-agent", ("PI_CODING_AGENT_DIR", "/dotfiles/omp-agent")),
])
@pytest.mark.parametrize("mode", ["url", "native"])
def test_omp_dotenv_dir_counts_for_nothing_when_the_shell_sets_it(fake_vllm, tmp_path, monkeypatch, omp_url_user, line, shell, mode):
    user_agent, source = omp_url_user
    # A non-empty launcher value wins over every dotenv file (utils/src/env.ts:204-206,387).
    (user_agent.parent.parent / ".env").write_text(f"{line}\n")
    monkeypatch.setenv(*shell)
    if mode == "native":
        _native_no_connect(monkeypatch)
        args = ["omp", "--no-launch", "--skill", str(source)]
    else:
        args = ["omp", "--url", BASE, "--no-launch"]
    result = CliRunner().invoke(start.start_app, args)
    assert result.exit_code == 0, result.output


def test_omp_dotenv_profile_refusal_stands_even_with_a_shell_profile(fake_vllm, tmp_path, monkeypatch, omp_url_user):
    (omp_url_user[0].parent.parent / ".env").write_text("OMP_PROFILE=work\n")
    monkeypatch.setenv("OMP_PROFILE", "work")
    result = CliRunner().invoke(start.start_app, ["omp", "--url", BASE, "--no-launch"])
    assert result.exit_code == 1


def test_omp_yaml_reads_numbers_as_yaml_1_2_core():
    # As Bun.YAML.parse reads them: 1.1-only number forms stay strings.
    text = "a: 20000\nb: 1e10\nc: 0o17\nd: 0x1F\ne: 1_000\nf: 1:30\ng: 0b11\nh: 1_0.5\ni: -2.5\n"
    assert yaml.load(text, Loader = omp_agent._OmpYamlLoader) == {
        "a": 20000, "b": 1e10, "c": 15, "d": 31, "e": "1_000", "f": "1:30", "g": "0b11",
        "h": "1_0.5", "i": -2.5,
    }


def test_omp_url_copies_number_like_strings_as_strings(fake_vllm, tmp_path, omp_url_user):
    user_agent, _ = omp_url_user
    (user_agent / "config.yml").write_text("tools:\n  restrictedFolders: [1_000, 1:30, 0b11]\n")
    _run_omp_url()
    assert _omp_session_config(tmp_path)["tools"]["restrictedFolders"] == ["1_000", "1:30", "0b11"]


# ── Rework 5 ─────────────────────────────────────────────────────────


def test_omp_yaml_reads_a_signed_hex_or_octal_number():
    # Bun.YAML.parse takes a sign before 0x and 0o too.
    text = "a: +0x3\nb: -0o17\nc: -0x1F\nd: +0o7\n"
    assert yaml.load(text, Loader = omp_agent._OmpYamlLoader) == {"a": 3, "b": -15, "c": -31, "d": 7}


def test_omp_session_number_keeps_its_value_through_a_rewrite(fake_vllm, tmp_path, omp_url_user):
    path = _omp_session_config_path(tmp_path)
    path.parent.mkdir(parents = True)
    path.write_text("retry:\n  maxRetries: +0x3\n")
    _run_omp_url()
    # Still the number omp read, not the string "+0x3".
    assert yaml.load(path.read_text(), Loader = omp_agent._OmpYamlLoader)["retry"] == {"maxRetries": 3}
