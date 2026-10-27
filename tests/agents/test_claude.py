# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE
# Ported from unsloth_cli/tests/test_start.py (unsloth commit 8e11ba15e); see NOTICE.md.

"""`agent-switch claude`: flags, settings overlay, session and subagent wiring."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from typer.testing import CliRunner

import agent_switch.start as start
from agent_switch.agents import (
    claude as claude_agent,
)
from agent_switch.core import (
    platform as core_platform,
    storage as core_storage,
)
from tests.cli_support import (
    BASE,
    KEY,
    MODEL,
    _assert_env_set,
    _assert_env_unset,
    _assert_windows_agent_copies_injected_skills,
    _capture_launch,
    _launch_command,
    _mcp_registry,
    _path_aware_which,
    _simulate_windows,
)
from tests.start_split import set_start_attr


@pytest.mark.parametrize("is_routed", [False, True])
def test_windows_claude_copies_injected_skills(fake_vllm, tmp_path, monkeypatch, is_routed):
    _assert_windows_agent_copies_injected_skills(
        fake_vllm, tmp_path, monkeypatch, "claude", "claude", is_routed
    )


def _get_skill_snapshot(root):
    return {
        str(path.relative_to(root)): (path.read_bytes() if path.is_file() else None)
        for path in root.rglob("*")
    }


@pytest.fixture
def claude_skill_sources(tmp_path, monkeypatch):
    home = tmp_path / "user-home"
    user_config = home / ".claude"
    user_skill = user_config / "skills" / "shared"
    user_skill.mkdir(parents = True)
    (user_skill / "SKILL.md").write_text("---\nname: shared\ndescription: USER\n---\n")
    (user_config / "settings.json").write_text('{"model": "cloud-model"}\n')
    (user_config / "CLAUDE.md").write_text("User instructions.\n")
    source = tmp_path / "skill sources" / "shared"
    source.mkdir(parents = True)
    (source / "SKILL.md").write_text("---\nname: shared\ndescription: INJECTED\n---\nBody.\n")
    (source / "notes.txt").write_text("Keep these notes.\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_config))
    before_home = _get_skill_snapshot(home)
    before_source = _get_skill_snapshot(source)
    yield home, source
    assert _get_skill_snapshot(home) == before_home
    assert _get_skill_snapshot(source) == before_source


def _fake_claude(monkeypatch, version_output: str) -> None:
    set_start_attr(monkeypatch, "_which_with_install_dirs", lambda _: "/usr/local/bin/claude")
    set_start_attr(monkeypatch, "_probe_env", lambda **_: {})
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout = version_output),
    )


def test_claude_flags_passed_to_supported_claude(monkeypatch):
    _fake_claude(monkeypatch, "2.1.98 (Claude Code)\n")
    assert claude_agent._claude_flags(MODEL["id"]) == [
        "--exclude-dynamic-system-prompt-sections",
        "--settings",
        claude_agent._claude_settings_overlay(MODEL["id"]),
    ]


def test_claude_dynamic_sections_skipped_on_old_claude(monkeypatch):
    _fake_claude(monkeypatch, "2.0.14 (Claude Code)\n")
    assert claude_agent._claude_flags(MODEL["id"]) == [
        "--settings",
        claude_agent._claude_settings_overlay(MODEL["id"]),
    ]


def test_claude_settings_retained_on_unparseable_version(monkeypatch):
    _fake_claude(monkeypatch, "weird build string\n")
    assert claude_agent._claude_flags(MODEL["id"]) == [
        "--settings",
        claude_agent._claude_settings_overlay(MODEL["id"]),
    ]


def test_claude_flags_detected_when_version_not_first_token(monkeypatch):
    # The X.Y.Z is pulled from anywhere in the output, so a format change (version not
    # the first token) doesn't silently drop the optimization flags.
    _fake_claude(monkeypatch, "claude version 2.1.98\n")
    assert claude_agent._claude_flags(MODEL["id"]) == [
        "--exclude-dynamic-system-prompt-sections",
        "--settings",
        claude_agent._claude_settings_overlay(MODEL["id"]),
    ]


def test_claude_settings_overlay_pins_served_model():
    # The session overlay must pin availableModels to the served model: a user's allowlist
    # in ~/.claude/settings.json otherwise rejects the local --model ("restricted by your
    # organization's settings"), and no env var can bypass it. The override must be a
    # NON-EMPTY array to take effect (an empty [] is ignored and the user's list still
    # applies), so it lists exactly this model, for this session only.
    overlay = json.loads(claude_agent._claude_settings_overlay(MODEL["id"]))
    assert overlay["availableModels"] == [MODEL["id"]]


def test_claude_settings_overlay_lists_served_models():
    # Every served id joins the allowlist so `/model <id>` can switch servers inside the
    # session; the launch model leads and is never duplicated.
    overlay = json.loads(
        claude_agent._claude_settings_overlay(
            MODEL["id"], served_model_ids = ["other", MODEL["id"], "a:third"]
        )
    )
    assert overlay["availableModels"] == [MODEL["id"], "other", "a:third"]


def test_claude_settings_overlay_pins_local_routing_and_auth():
    local_env = claude_agent._claude_local_env(BASE, "sk-test", MODEL)
    overlay = json.loads(claude_agent._claude_settings_overlay(MODEL["id"], local_env))
    for name, value in local_env.items():
        assert overlay["env"][name] == value
    assert overlay["env"]["ANTHROPIC_BASE_URL"] == BASE
    assert overlay["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-test"
    for name in claude_agent._CLAUDE_ENV_UNSET:
        assert overlay["env"][name] == ""
    # The attribution-header suppression is preserved alongside it.
    assert overlay["env"]["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"
    assert overlay["env"]["CLAUDE_CODE_TOTAL_TOKENS_REMINDER"] == "off"
    # Subagents fall through to the served model instead of a user's opus/sonnet pin.
    assert overlay["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "inherit"
    # Since 2.1.251 an agent's `model:` outranks it unless forced.
    assert overlay["env"]["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"] == "1"


def test_claude_settings_files_preserve_concurrent_sessions(tmp_path):
    first_env = claude_agent._claude_local_env("http://127.0.0.1:8001", "first-key", MODEL)
    second_env = claude_agent._claude_local_env("http://127.0.0.1:8002", "second-key", MODEL)
    first = claude_agent._write_claude_settings(tmp_path, MODEL["id"], first_env)
    second = claude_agent._write_claude_settings(tmp_path, MODEL["id"], second_env)
    assert first != second
    assert json.loads(first.read_text())["env"]["ANTHROPIC_AUTH_TOKEN"] == "first-key"
    assert json.loads(second.read_text())["env"]["ANTHROPIC_AUTH_TOKEN"] == "second-key"


def test_claude_flags_probes_old_agent_only_in_install_dir(monkeypatch, tmp_path):
    # Regression: the version probe must augment PATH before resolving, so an OLD claude present
    # only in ~/.local/bin (not yet on PATH) is detected as old and the unsupported flags are
    # dropped -- the same binary _launch() will run. Before the fix the probe saw no binary,
    # assumed a current build, and emitted flags the old claude rejects.
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents = True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "no-appdata"))
    monkeypatch.setenv("PATH", str(tmp_path / "existing"))
    monkeypatch.setattr(shutil, "which", _path_aware_which({"claude": local_bin}))
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout = "2.0.14 (Claude Code)\n")
    )
    assert claude_agent._claude_flags(MODEL["id"]) == [
        "--settings",
        claude_agent._claude_settings_overlay(MODEL["id"]),
    ]


def test_claude_flags_detects_supported_agent_only_in_install_dir(monkeypatch, tmp_path):
    # The counterpart: a SUPPORTED claude present only in ~/.local/bin is now resolved and gets
    # the flags, instead of being missed and (coincidentally) also assumed current.
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents = True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "no-appdata"))
    monkeypatch.setenv("PATH", str(tmp_path / "existing"))
    monkeypatch.setattr(shutil, "which", _path_aware_which({"claude": local_bin}))
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout = "2.1.98 (Claude Code)\n")
    )
    assert claude_agent._claude_flags(MODEL["id"]) == [
        "--exclude-dynamic-system-prompt-sections",
        "--settings",
        claude_agent._claude_settings_overlay(MODEL["id"]),
    ]


def test_claude_flags_probes_npm_install_dir_on_windows(monkeypatch, tmp_path):
    # npm -g shims land in %APPDATA%\npm on Windows; an old claude there (not on PATH) must still
    # be version-checked so the unsupported flags are dropped.
    _simulate_windows(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)  # no ~/.local/bin created
    npm_dir = tmp_path / "Roaming" / "npm"
    npm_dir.mkdir(parents = True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("PATH", str(tmp_path / "existing"))
    monkeypatch.setattr(shutil, "which", _path_aware_which({"claude": npm_dir}))
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout = "2.0.14 (Claude Code)\n")
    )
    assert claude_agent._claude_flags(MODEL["id"]) == [
        "--settings",
        claude_agent._claude_settings_overlay(MODEL["id"]),
    ]


@pytest.mark.parametrize(
    "agent", ["claude", "codex", "opencode", "pi", "dsh"]
)
def test_launch_preflights_agent_before_connect(agent, monkeypatch):
    events = []
    if agent == "opencode":
        set_start_attr(monkeypatch, "_opencode_command", lambda *_: ("opencode", False))

    def require(name, hint, launch):
        assert name == agent
        assert hint
        assert launch is True
        events.append("agent")

    def connect(*args, **kwargs):
        events.append("connect")
        raise RuntimeError("stop after ordering check")

    set_start_attr(monkeypatch, "_require_agent_for_launch", require)
    set_start_attr(monkeypatch, "_connect", connect)

    result = CliRunner().invoke(start.start_app, [agent, "--provider", "vllm"])

    assert result.exit_code == 1
    assert events == ["agent", "connect"]


@pytest.mark.parametrize(
    "agent", ["claude", "codex", "opencode", "pi"]
)
def test_noninteractive_missing_agent_stops_before_connect(agent, monkeypatch):
    set_start_attr(monkeypatch, "_which_with_install_dirs", lambda _: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("non-interactive launch must not install"),
    )
    set_start_attr(monkeypatch, "_connect",
        lambda *args, **kwargs: pytest.fail("missing agent must stop before connection"),
    )

    result = CliRunner().invoke(start.start_app, [agent, "--provider", "vllm"])

    assert result.exit_code == 1
    assert f"`{agent}` not found on PATH" in result.output


@pytest.mark.parametrize("agent", ["claude", "codex", "pi", "dsh"])
def test_no_launch_skips_agent_resolution(agent, monkeypatch):
    set_start_attr(monkeypatch, "_which_with_install_dirs",
        lambda _: pytest.fail("--no-launch must not resolve an agent"),
    )
    set_start_attr(monkeypatch, "_install_agent",
        lambda *args: pytest.fail("--no-launch must not install an agent"),
    )

    def stop_at_connect(*args, **kwargs):
        raise RuntimeError

    set_start_attr(monkeypatch, "_connect", stop_at_connect)

    result = CliRunner().invoke(start.start_app, [agent, "--provider", "vllm", "--no-launch"])

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)


def test_connect_claude_no_launch(fake_vllm):
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE, "--no-launch"])
    assert result.exit_code == 0, result.output
    for name in claude_agent._CLAUDE_ENV_UNSET:
        _assert_env_unset(result.output, name)
    _assert_env_set(result.output, "ANTHROPIC_BASE_URL", BASE)
    _assert_env_set(result.output, "ANTHROPIC_AUTH_TOKEN", KEY)
    _assert_env_set(result.output, "ANTHROPIC_MODEL", MODEL["id"])
    _assert_env_set(result.output, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    _assert_env_set(result.output, "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "1")
    # Suppress the full-screen TUI redraw so a bursty local server doesn't flicker.
    _assert_env_set(result.output, "CLAUDE_CODE_NO_FLICKER", "1")
    # Attribution header is suppressed for the session via env + --settings, never
    # by writing the user's ~/.claude/settings.json.
    _assert_env_set(result.output, "CLAUDE_CODE_ATTRIBUTION_HEADER", "0")
    _assert_env_set(result.output, "CLAUDE_CODE_TOTAL_TOKENS_REMINDER", "off")
    # Claude assumes 200k for an unrecognized model id and clamps the auto-compact
    # window into [100k, that], so the real window has to be pinned as well.
    _assert_env_set(result.output, "CLAUDE_CODE_MAX_CONTEXT_TOKENS", str(MODEL["context_length"]))
    _assert_env_set(result.output, "CLAUDE_CODE_AUTO_COMPACT_WINDOW", str(MODEL["context_length"]))
    _assert_env_set(result.output, "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "90")
    assert f"claude --model {MODEL['id']} --exclude-dynamic-system-prompt-sections" in result.output
    # Overlay is session-only and lives outside the user's ~/.claude.
    command = _launch_command(result.output)
    settings_path = Path(command[command.index("--settings") + 1])
    settings = json.loads(settings_path.read_text())
    assert settings["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "inherit"
    assert settings["env"]["ANTHROPIC_BASE_URL"] == BASE
    assert settings["env"]["ANTHROPIC_AUTH_TOKEN"] == KEY
    for name in claude_agent._CLAUDE_ENV_UNSET:
        assert settings["env"][name] == ""
    if os.name != "nt":
        assert settings_path.stat().st_mode & 0o777 == 0o600
    assert "--plugin-dir" not in command
    assert ".claude/settings.json" not in result.output


def test_connect_claude_session_settings_follow_forwarded_settings(fake_vllm):
    forwarded = json.dumps({"env": {"CLAUDE_CODE_USE_FOUNDRY": "1"}})
    result = CliRunner().invoke(
        start.start_app,
        ["claude", "--url", BASE, "--no-launch", "--", "--settings", forwarded],
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    positions = [index for index, arg in enumerate(command) if arg == "--settings"]
    assert len(positions) == 2
    assert command[positions[0] + 1] == forwarded
    assert Path(command[positions[1] + 1]).name.startswith("settings-")


@pytest.mark.parametrize(
    "settings_arg",
    [
        lambda value: ["--settings", value],
        lambda value: [f"--settings={value}"],
    ],
)
def test_connect_claude_session_settings_precede_subcommand(fake_vllm, settings_arg):
    forwarded = json.dumps({"env": {"CLAUDE_CODE_USE_FOUNDRY": "1"}})
    result = CliRunner().invoke(
        start.start_app,
        ["claude", "--url", BASE, "--no-launch", "--", "mcp", "list", *settings_arg(forwarded)],
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    subcommand = command.index("mcp")
    assert command.index("--model") < subcommand
    settings_positions = [
        index
        for index, arg in enumerate(command)
        if arg == "--settings" or arg.startswith("--settings=")
    ]
    assert len(settings_positions) == 2
    assert settings_positions[0] < settings_positions[1] < subcommand


def test_connect_claude_session_settings_follow_delimited_forwarded_settings(fake_vllm):
    forwarded = json.dumps({"env": {"CLAUDE_CODE_USE_FOUNDRY": "1"}})
    result = CliRunner().invoke(
        start.start_app,
        ["claude", "--url", BASE, "--no-launch", "--", "--settings", forwarded],
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    positions = [index for index, arg in enumerate(command) if arg == "--settings"]
    assert len(positions) == 2
    assert command[positions[0] + 1] == forwarded
    assert Path(command[positions[1] + 1]).name.startswith("settings-")
    assert "--" not in command


def test_connect_claude_as_subagent_preserves_cloud_parent(fake_vllm, tmp_path):
    result = CliRunner().invoke(
        start.start_app,
        [
            "claude", "--url", BASE,
            "--as-subagent",
            "--no-launch",
            "--model",
            MODEL["id"],
            "--",
            "hello",
        ],
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    plugin = tmp_path / "agents" / "claude-subagent" / "local-agent"
    assert command == [
        "claude",
        "--plugin-dir",
        str(plugin),
        f"--allowedTools={claude_agent._CLAUDE_SUBAGENT_TOOL},{claude_agent._CLAUDE_SUBAGENT_PLAN_TOOL}",
        "hello",
    ]
    assert "--model" not in command
    parent_base = "$env:ANTHROPIC_BASE_URL" if os.name == "nt" else "export ANTHROPIC_BASE_URL="
    parent_token = (
        "$env:ANTHROPIC_AUTH_TOKEN" if os.name == "nt" else "export ANTHROPIC_AUTH_TOKEN="
    )
    assert parent_base not in result.output
    assert parent_token not in result.output
    assert "unset ANTHROPIC_API_KEY" not in result.output
    assert "AGENT_SWITCH_CLAUDE_SUBAGENT_API_KEY" not in result.output
    assert KEY not in result.output
    assert json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())["name"] == (
        "local-agent"
    )
    mcp = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]["local"]
    settings_path = next(plugin.glob("settings-*.json"))
    assert mcp["command"] == sys.executable
    assert mcp["args"] == ["-m", claude_agent._CLAUDE_SUBAGENT_MCP_MODULE]
    assert mcp["env"] == {
        "AGENT_SWITCH_CLAUDE_SUBAGENT_BASE_URL": BASE,
        "AGENT_SWITCH_CLAUDE_SUBAGENT_API_KEY": KEY,
        "AGENT_SWITCH_CLAUDE_SUBAGENT_MODEL": MODEL["id"],
        "AGENT_SWITCH_CLAUDE_SUBAGENT_BYPASS_PERMISSIONS": "0",
        "AGENT_SWITCH_CLAUDE_SUBAGENT_CONTEXT_WINDOW": str(MODEL["context_length"]),
        claude_agent._CLAUDE_SUBAGENT_SETTINGS_ENV: str(settings_path),
    }
    settings = json.loads(settings_path.read_text())
    assert settings["availableModels"] == [MODEL["id"]]
    assert settings["env"]["ANTHROPIC_BASE_URL"] == BASE
    assert settings["env"]["ANTHROPIC_AUTH_TOKEN"] == KEY
    for name in claude_agent._CLAUDE_ENV_UNSET:
        assert settings["env"][name] == ""
    skill = (plugin / "skills" / "local-agent" / "SKILL.md").read_text()
    assert "spawn a local agent" in skill
    assert "In plan mode" in skill
    assert "Ask Claude to spawn a local agent." in result.output


@pytest.mark.skipif(os.name == "nt", reason = "WSL scenario")
def test_claude_subagent_plugin_uses_wsl_for_windows_claude(monkeypatch, tmp_path):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("WSLENV", "EXISTING")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda _: "/mnt/c/Users/x/AppData/Local/Programs/claude.exe",
    )
    server_env = {
        "AGENT_SWITCH_CLAUDE_SUBAGENT_BASE_URL": BASE,
        "AGENT_SWITCH_CLAUDE_SUBAGENT_API_KEY": "secret",
        "AGENT_SWITCH_CLAUDE_SUBAGENT_MODEL": MODEL["id"],
    }
    plugin = claude_agent.write_claude_subagent_plugin(tmp_path, server_env)
    mcp = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]["local"]
    settings_path = next(plugin.glob("settings-*.json"))
    assert mcp["command"] == "wsl.exe"
    assert mcp["args"] == [
        "-d",
        "Ubuntu",
        "--",
        sys.executable,
        "-m",
        claude_agent._CLAUDE_SUBAGENT_MCP_MODULE,
    ]
    assert mcp["env"]["AGENT_SWITCH_CLAUDE_SUBAGENT_API_KEY"] == "secret"
    assert mcp["env"][claude_agent._CLAUDE_SUBAGENT_SETTINGS_ENV] == str(settings_path)
    assert mcp["env"]["WSLENV"].split(":") == [
        "EXISTING",
        "AGENT_SWITCH_CLAUDE_SUBAGENT_BASE_URL",
        "AGENT_SWITCH_CLAUDE_SUBAGENT_API_KEY",
        "AGENT_SWITCH_CLAUDE_SUBAGENT_MODEL",
        claude_agent._CLAUDE_SUBAGENT_SETTINGS_ENV,
    ]


def test_connect_claude_launch_scrubs_conflicting_auth_env(fake_vllm, monkeypatch):
    captured = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-stale")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-stale")
    monkeypatch.setenv("ANTHROPIC_UNIX_SOCKET", "/tmp/remote-claude.sock")
    monkeypatch.setenv("CLAUDE_CODE_USE_FOUNDRY", "1")
    monkeypatch.setenv(
        "ANTHROPIC_FOUNDRY_BASE_URL",
        "https://corporate-gateway.azure-api.net/anthropic-stream",
    )
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-foundry-resource")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_ANTHROPIC_AWS", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_MANTLE", "1")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/claude")
    set_start_attr(monkeypatch, "_claude_flags", lambda *a, **k: [])

    def run(command, env):
        captured["command"] = command
        captured["env"] = env
        return SimpleNamespace(returncode = 0)

    monkeypatch.setattr(subprocess, "run", run)
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE])

    assert result.exit_code == 0, result.output
    assert captured["command"] == ["/usr/local/bin/claude", "--model", MODEL["id"]]
    for name in claude_agent._CLAUDE_ENV_UNSET:
        assert name not in captured["env"]
    assert captured["env"]["ANTHROPIC_AUTH_TOKEN"] == KEY
    assert captured["env"]["ANTHROPIC_BASE_URL"] == BASE
    assert captured["env"]["ANTHROPIC_MODEL"] == MODEL["id"]
    assert captured["env"]["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"
    assert captured["env"]["CLAUDE_CODE_TOTAL_TOKENS_REMINDER"] == "off"


@pytest.mark.skipif(
    os.name == "nt",
    reason = "WSL-from-Linux scenario (calling a Windows agent .exe from inside WSL); "
    "os.name is 'posix' under WSL, so this path can't run on a native Windows runner.",
)
def test_connect_claude_windows_shim_from_wsl_bridges_env(fake_vllm, monkeypatch, tmp_path):
    captured = {}
    windows_settings = r"C:\\Users\\samle\\AppData\\Local\\agent-switch\\settings.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", "/stale/outer/repo")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-stale")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-stale")
    monkeypatch.setattr(
        shutil, "which", lambda _: "/mnt/c/Users/samle/AppData/Roaming/npm/claude"
    )
    set_start_attr(monkeypatch, "_wsl_windows_path", lambda _: windows_settings)
    set_start_attr(monkeypatch, "_claude_flags",
        lambda model_id, settings: ["--settings", settings],
    )

    def run(command, env):
        captured["command"] = command
        captured["env"] = env
        return SimpleNamespace(returncode = 0)

    monkeypatch.setattr(subprocess, "run", run)
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE])

    assert result.exit_code == 0, result.output
    assert captured["command"] == [
        "/mnt/c/Users/samle/AppData/Roaming/npm/claude",
        "--model",
        MODEL["id"],
        "--settings",
        windows_settings,
    ]
    for name in claude_agent._CLAUDE_ENV_UNSET:
        assert captured["env"][name] == ""
    assert captured["env"]["ANTHROPIC_AUTH_TOKEN"] == KEY
    assert captured["env"]["ANTHROPIC_BASE_URL"] == BASE
    assert captured["env"]["ANTHROPIC_MODEL"] == MODEL["id"]
    assert captured["env"]["PWD"] == str(tmp_path)

    assert "PWD/p" in captured["env"]["WSLENV"].split(":")
    for name in (
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        *claude_agent._CLAUDE_ENV_UNSET,
    ):
        assert name in captured["env"]["WSLENV"].split(":")


@pytest.mark.skipif(
    os.name == "nt",
    reason = "WSL-from-Linux scenario (calling a Windows agent .exe from inside WSL); "
    "os.name is 'posix' under WSL, so this path can't run on a native Windows runner.",
)
def test_connect_claude_no_launch_windows_shim_from_wsl_prints_wslenv(
    fake_vllm, monkeypatch, tmp_path
):
    windows_settings = r"C:\\Users\\samle\\AppData\\Local\\agent-switch\\settings.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", "/stale/outer/repo")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, path = None: "/mnt/c/Users/samle/AppData/Roaming/npm/claude",
    )
    set_start_attr(monkeypatch, "_wsl_windows_path", lambda _: windows_settings)

    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE, "--no-launch"])

    assert result.exit_code == 0, result.output
    for name in claude_agent._CLAUDE_ENV_UNSET:
        assert f"export {name}=" in result.output
    assert "export WSLENV=" in result.output
    # PWD must NOT be frozen into the recipe (no `export PWD=`): WSLENV PWD/p translates the
    # shell's live PWD at run time, so a recipe reused from another dir resolves the project root.
    assert "export PWD=" not in result.output
    assert "PWD/p" in result.output
    assert "ANTHROPIC_AUTH_TOKEN" in result.output
    assert "CLAUDE_CODE_OAUTH_TOKEN" in result.output
    command = _launch_command(result.output)
    assert command[command.index("--settings") + 1] == windows_settings


def test_no_launch_claude_last_line_blanks_conflicting_auth(fake_vllm):
    # The unset vars must be neutralized inline too, or a partial copy would send the
    # user's own ANTHROPIC_API_KEY to the local base.
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE, "--no-launch"])
    assert result.exit_code == 0, result.output
    last = [ln for ln in result.output.splitlines() if ln.strip()][-1]
    for name in claude_agent._CLAUDE_ENV_UNSET:
        assert f"{name}= " in last
    assert "ANTHROPIC_AUTH_TOKEN=" in last  # the real key still applied after the blanks


def test_connect_explicit_key_remembered_for_keyless_runs(fake_vllm, tmp_path):
    CliRunner().invoke(
        start.start_app,
        ["claude", "--url", BASE, "--no-launch", "--api-key", "sk-test-deadbeefdeadbeef"],
    )
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE, "--no-launch"])
    assert result.exit_code == 0, result.output
    # Reused ahead of the key the server was given before.
    _assert_env_set(result.output, "ANTHROPIC_AUTH_TOKEN", "sk-test-deadbeefdeadbeef")
    cached = json.loads(core_storage._provider_key_cache_path().read_text())
    assert cached["servers"][BASE]["saved"] == ["sk-test-deadbeefdeadbeef", KEY]


def test_launch_prints_the_ready_line_and_exits_with_the_agent(fake_vllm, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/claude")
    set_start_attr(monkeypatch, "_claude_flags", lambda *a, **k: [])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, env: SimpleNamespace(returncode = 0),
    )

    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE])

    assert result.exit_code == 0, result.output
    assert f"vLLM ready at {BASE} · model {MODEL['id']}\n" in result.output
    assert "still running" not in result.output


def test_nonzero_agent_exit_notes_code(fake_vllm, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/claude")
    set_start_attr(monkeypatch, "_claude_flags", lambda *a, **k: [])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, env: SimpleNamespace(returncode = 3),
    )

    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE])

    assert result.exit_code == 3
    assert "The agent exited with code 3." in result.output


def test_connect_explicit_api_key_wins_over_a_saved_one(fake_vllm):
    result = CliRunner().invoke(
        start.start_app,
        ["claude", "--url", BASE, "--no-launch", "--api-key", "sk-test-deadbeefdeadbeef"],
    )
    assert result.exit_code == 0, result.output
    _assert_env_set(result.output, "ANTHROPIC_AUTH_TOKEN", "sk-test-deadbeefdeadbeef")


def test_claude_subagent_allowed_tools_precede_forwarded_args(fake_vllm):
    # The tool pre-approval must be parsed as an option, so it rides before the forwarded args;
    # agent-switch's own `--` is dropped, so the agent sees no delimiter.
    result = CliRunner().invoke(
        start.start_app,
        ["claude", "--url", BASE, "--as-subagent", "--no-launch", "--", "--resume", "abc123"],
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    allowed = next(arg for arg in command if arg.startswith("--allowedTools="))
    assert command.index(allowed) < command.index("--resume")
    assert "--" not in command


def test_claude_subagent_forwards_positional_prompt(fake_vllm):
    # --allowedTools is variadic: a detached value would consume the prompt.
    result = CliRunner().invoke(
        start.start_app,
        ["claude", "--url", BASE, "--as-subagent", "--no-launch", "--", "fix the failing test"],
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert command[-1] == "fix the failing test"
    assert "--allowedTools" not in command


def test_powershell_quote_single_quotes_json():
    # Bare flags/paths pass through; JSON payloads get single-quoted so PowerShell
    # keeps the embedded double quotes literal (list2cmdline's backslashes would not).
    assert core_platform._powershell_quote("--settings") == "--settings"
    assert core_platform._powershell_quote("org/gemma-4-26B") == "org/gemma-4-26B"
    overlay = claude_agent._claude_settings_overlay("org/gemma-4-26B")
    quoted = core_platform._powershell_quote(overlay)
    assert quoted == "'" + overlay + "'"
    assert "\\" not in quoted  # no cmd.exe backslash escaping
    assert core_platform._powershell_quote("a'b") == "'a''b'"  # embedded quote doubled


def test_claude_launch_does_not_clear(fake_vllm, monkeypatch):
    # Alternate-screen agents manage the terminal themselves; leave it alone.
    calls = []
    monkeypatch.setattr(click, "clear", lambda: calls.append("clear"))
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/claude")
    set_start_attr(monkeypatch, "_claude_flags", lambda *a, **k: [])
    monkeypatch.setattr(subprocess, "run", lambda command, env: SimpleNamespace(returncode = 0))
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE])
    assert result.exit_code == 0, result.output
    assert calls == []


def test_persist_bare_claude_launch_has_no_resume_token(fake_vllm, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/claude")
    set_start_attr(monkeypatch, "_claude_flags", lambda *a, **k: [])
    captured = _capture_launch(monkeypatch, ["claude", "--url", BASE, "--persist"])
    assert "--continue" not in captured["command"]
    assert captured["command"][1:] == ["--model", MODEL["id"]]


def test_native_resume_flag_passes_through_unchanged(fake_vllm, monkeypatch):
    # The persistence flag is --persist, NOT --resume, so an agent's own
    # `--resume <id>` (e.g. `agent-switch claude --resume <guid>`) still flows
    # through to the agent verbatim and is not swallowed as an agent-switch option.
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/claude")
    set_start_attr(monkeypatch, "_claude_flags", lambda *a, **k: [])
    captured = _capture_launch(monkeypatch, ["claude", "--url", BASE, "--", "--resume", "some-session-guid"])
    resume = captured["command"].index("--resume")
    assert captured["command"][resume : resume + 2] == ["--resume", "some-session-guid"]
    assert captured["command"].index("--model") < resume
    # agent-switch never auto-appends its own resume token when the user drives resume.
    assert captured["command"].count("--resume") == 1
    assert "--continue" not in captured["command"]


# ── --mcp / --mcp-all: session-only MCP mounting ─────────────────────


def test_connect_claude_mcp_writes_private_config_and_flags(fake_vllm, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    _mcp_registry()
    result = CliRunner().invoke(
        start.start_app,
        ["claude", "--url", BASE, "--no-launch", "--mcp", "context7", "--mcp", "github", "--", "hello"],
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    mcp_arg = next(arg for arg in command if arg.startswith("--mcp-config="))
    mcp_path = Path(mcp_arg.removeprefix("--mcp-config="))
    assert mcp_path == tmp_path / "agents" / "claude" / "mcp.json"
    assert "--strict-mcp-config" in command
    # --mcp-config is variadic in claude, so both flags ride in `=` form ahead of the passthrough.
    assert command.index(mcp_arg) < command.index("hello")
    servers = json.loads(mcp_path.read_text())["mcpServers"]
    assert servers["context7"] == {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp"],
        "env": {},
    }
    assert servers["github"] == {
        "type": "http",
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": "Bearer gh-secret"},
    }
    if os.name != "nt":
        assert mcp_path.stat().st_mode & 0o777 == 0o600
    # The expanded secret rides only in the private file, never in the printed recipe.
    assert "gh-secret" not in result.output


def test_connect_claude_mcp_all_mounts_every_registry_server(fake_vllm, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    _mcp_registry()
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE, "--no-launch", "--mcp-all"])
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    mcp_arg = next(arg for arg in command if arg.startswith("--mcp-config="))
    servers = json.loads(Path(mcp_arg.removeprefix("--mcp-config=")).read_text())["mcpServers"]
    assert set(servers) == {"context7", "github"}


def test_connect_claude_mcp_url_mounts_without_a_registry(fake_vllm, tmp_path):
    result = CliRunner().invoke(
        start.start_app, ["claude", "--url", BASE, "--no-launch", "--mcp-url", "ev:http://127.0.0.1:18331/mcp"]
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    mcp_arg = next(arg for arg in command if arg.startswith("--mcp-config="))
    servers = json.loads(Path(mcp_arg.removeprefix("--mcp-config=")).read_text())["mcpServers"]
    assert servers == {"ev": {"type": "http", "url": "http://127.0.0.1:18331/mcp", "headers": {}}}


def test_connect_claude_mcp_stdio_mounts_without_a_registry(fake_vllm, tmp_path):
    result = CliRunner().invoke(
        start.start_app,
        ["claude", "--url", BASE, "--no-launch", "--mcp-stdio", "ev:npx -y @modelcontextprotocol/server-everything@2026.8.31"],
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    mcp_arg = next(arg for arg in command if arg.startswith("--mcp-config="))
    servers = json.loads(Path(mcp_arg.removeprefix("--mcp-config=")).read_text())["mcpServers"]
    assert servers == {
        "ev": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-everything@2026.8.31"],
            "env": {},
        }
    }


def test_connect_claude_mcp_stdio_shell_form(fake_vllm, tmp_path, monkeypatch):
    # fake_vllm stubs shutil.which to None; the shell form needs bash found.
    monkeypatch.setattr(shutil, "which", lambda name, path = None: "/bin/bash" if name == "bash" else None)
    result = CliRunner().invoke(
        start.start_app,
        ["claude", "--url", BASE, "--no-launch", "--mcp-stdio", 'ev="npx -y @modelcontextprotocol/server-everything@2026.8.31"'],
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    mcp_arg = next(arg for arg in command if arg.startswith("--mcp-config="))
    servers = json.loads(Path(mcp_arg.removeprefix("--mcp-config=")).read_text())["mcpServers"]
    assert servers == {
        "ev": {
            "type": "stdio",
            "command": "bash",
            "args": ["-lc", "exec npx -y @modelcontextprotocol/server-everything@2026.8.31"],
            "env": {},
        }
    }


def test_connect_claude_without_mcp_flags_writes_no_mcp_config(fake_vllm, tmp_path):
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE, "--no-launch"])
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert "--strict-mcp-config" not in command
    assert not any(arg.startswith("--mcp-config") for arg in command)
    assert not (tmp_path / "agents" / "claude" / "mcp.json").exists()


def test_claude_mcp_flags_cleared_on_rerun_without_them(fake_vllm, tmp_path, monkeypatch):
    # A --no-launch session dir is reused, so the next run must not keep the earlier mount's flags.
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    _mcp_registry()
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE, "--no-launch", "--mcp", "context7"])
    assert result.exit_code == 0, result.output
    assert "--mcp-config=" in result.output
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE, "--no-launch"])
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert "--strict-mcp-config" not in command
    assert not any(arg.startswith("--mcp-config") for arg in command)
    # The persisted session dir must not keep the earlier mount's private mcp.json either.
    assert not (tmp_path / "agents" / "claude" / "mcp.json").exists()


def test_claude_mcp_with_as_subagent_fails(fake_vllm):
    _mcp_registry()
    result = CliRunner().invoke(
        start.start_app, ["claude", "--url", BASE, "--as-subagent", "--no-launch", "--mcp", "context7"]
    )
    assert result.exit_code == 1
    assert "--mcp/--mcp-all/--mcp-url/--mcp-oauth-url/--mcp-header/--mcp-stdio/--mcp-env cannot be combined with --as-subagent" in result.output


# ── Native launch (no --url/--provider) ──────────────────────────────


def _native_no_connect(monkeypatch):
    set_start_attr(monkeypatch, "_connect",
        lambda *args, **kwargs: pytest.fail("native launch must not connect"),
    )


def test_native_claude_bare_adds_nothing(fake_vllm, monkeypatch):
    _native_no_connect(monkeypatch)
    result = CliRunner().invoke(start.start_app, ["claude", "--no-launch"])
    assert result.exit_code == 0, result.output
    assert "runs with its own model, login and config" in result.output
    assert _launch_command(result.output) == ["claude"]
    assert "ANTHROPIC" not in result.output


def test_native_claude_adds_mcp_additively(fake_vllm, tmp_path, monkeypatch):
    _native_no_connect(monkeypatch)
    result = CliRunner().invoke(
        start.start_app, ["claude", "--no-launch", "--mcp-stdio", "ev:npx everything"]
    )
    assert result.exit_code == 0, result.output
    mcp_config = tmp_path / "agents" / "claude-native" / "mcp.json"
    # The `=` form before the passthrough args: --mcp-config is variadic in claude.
    assert _launch_command(result.output) == ["claude", f"--mcp-config={mcp_config}"]
    assert json.loads(mcp_config.read_text()) == {
        "mcpServers": {
            "ev": {"type": "stdio", "command": "npx", "args": ["everything"], "env": {}}
        }
    }


def test_native_claude_yolo_and_passthrough(fake_vllm, monkeypatch):
    _native_no_connect(monkeypatch)
    result = CliRunner().invoke(
        start.start_app, ["claude", "--no-launch", "--yolo", "--", "--print", "hi"]
    )
    assert result.exit_code == 0, result.output
    assert _launch_command(result.output) == [
        "claude", "--dangerously-skip-permissions", "--print", "hi"
    ]


@pytest.mark.parametrize(
    "flag",
    [
        ["--model", "org/model"],
        ["--context-length", "8192"],
        ["--temperature", "0.5"],
        ["--api-key", "sk-x"],
        ["--header", "X-Foo=bar"],
        ["--no-model-load"],
        ["--as-subagent"],
        ["--persist"],
    ],
)
def test_native_claude_refuses_local_only_flags(fake_vllm, monkeypatch, flag):
    _native_no_connect(monkeypatch)
    result = CliRunner().invoke(start.start_app, ["claude", "--no-launch", *flag])
    assert result.exit_code == 1
    assert "needs --url or --provider" in result.output


def test_native_claude_refuses_a_positional_model(fake_vllm, monkeypatch):
    _native_no_connect(monkeypatch)
    result = CliRunner().invoke(start.start_app, ["claude", "--no-launch", "org/gemma-4-26B"])
    assert result.exit_code == 1
    assert "--model needs --url or --provider" in result.output


@pytest.mark.parametrize("is_routed", [False, True])
def test_claude_skill_mounts_session_folder_with_mcp_and_hint(fake_vllm, tmp_path, claude_skill_sources, is_routed):
    _, source = claude_skill_sources
    result = CliRunner().invoke(
        start.start_app,
        ["claude", *(["--url", BASE] if is_routed else []), "--no-launch",
         "--mcp-stdio", "ev:npx everything", "--skill", str(source),
         "--skill", f"renamed:{source}", "--", "--print", "hello"],
    )
    assert result.exit_code == 0, result.output
    session = tmp_path / "agents" / ("claude" if is_routed else "claude-native")
    added_dir = session / "agent-switch"
    command = _launch_command(result.output)
    assert command.count(f"--add-dir={added_dir}") == 1
    assert "--add-dir" not in command
    assert command[-2:] == ["--print", "hello"]
    assert command.index(f"--add-dir={added_dir}") < command.index("--print")
    assert any(arg.startswith("--mcp-config=") for arg in command)
    assert ("--strict-mcp-config" in command) is is_routed
    root = added_dir / ".claude" / "skills"
    assert (root / "shared").is_symlink()
    assert (root / "shared").resolve() == source
    assert (root / "renamed").is_dir() and not (root / "renamed").is_symlink()
    assert "name: renamed\n" in (root / "renamed" / "SKILL.md").read_text()
    assert (root / "renamed" / "notes.txt").is_symlink()
    assert [line for line in result.output.splitlines() if line.startswith("Injected skills:")] == [
        "Injected skills: shared, renamed. If one shares a name with an existing skill, "
        "Claude Code 2.1.309 or newer lists it as agent-switch:<name>; older versions hide it."
    ]
    assert "CLAUDE_CONFIG_DIR" not in result.output


@pytest.mark.parametrize(("is_routed", "should_persist"), [(False, False), (True, False), (True, True)])
def test_claude_skill_rerun_clears_entries_and_restores_command_and_env(fake_vllm, tmp_path, claude_skill_sources, is_routed, should_persist):
    _, source = claude_skill_sources
    args = ["claude", *(["--url", BASE] if is_routed else []), "--no-launch",
            *(["--persist"] if should_persist else [])]
    session = tmp_path / "agents" / ("claude" if is_routed else "claude-native")
    baseline = CliRunner().invoke(start.start_app, [*args, "--", "--print", "hello"])
    assert baseline.exit_code == 0, baseline.output
    assert not (session / "agent-switch").exists()
    if not is_routed:
        assert _launch_command(baseline.output) == ["claude", "--print", "hello"]
    first = CliRunner().invoke(start.start_app, [*args, "--skill", str(source), "--skill", f"renamed:{source}"])
    assert first.exit_code == 0, first.output
    root = session / "agent-switch" / ".claude" / "skills"
    assert (root / "shared").is_symlink() and (root / "renamed").is_dir()
    (root / "user-owned.txt").write_text("Keep me.\n")
    second = CliRunner().invoke(start.start_app, [*args, "--", "--print", "hello"])
    assert second.exit_code == 0, second.output
    assert _launch_command(second.output) == _launch_command(baseline.output)
    assert [line for line in second.output.splitlines() if line.startswith(("export ", "unset ", "$env:", "Remove-Item"))] == [
        line for line in baseline.output.splitlines() if line.startswith(("export ", "unset ", "$env:", "Remove-Item"))
    ]
    assert not any(arg.startswith("--add-dir") for arg in _launch_command(second.output))
    assert "Injected skills:" not in second.output
    assert not os.path.lexists(root / "shared") and not (root / "renamed").exists()
    assert (root / "user-owned.txt").read_text() == "Keep me.\n"
    assert json.loads((root / ".agent-switch-skills.json").read_text()) == {"entries": []}


def test_native_claude_alias_carries_skill_flag(fake_vllm, tmp_path, monkeypatch, claude_skill_sources):
    _, source = claude_skill_sources
    _native_no_connect(monkeypatch)
    set_start_attr(monkeypatch, "_check_alias", lambda alias: None)
    result = CliRunner().invoke(
        start.start_app, ["claude-personal", "--no-launch", "--skill", str(source), "--", "--print", "hello"]
    )
    assert result.exit_code == 0, result.output
    added_dir = tmp_path / "agents" / "claude-native" / "agent-switch"
    assert _launch_command(result.output) == [
        "bash", "-ic", 'claude-personal "$@"', "agent-switch",
        f"--add-dir={added_dir}", "--print", "hello",
    ]
    assert "Injected skills: shared." in result.output


@pytest.mark.parametrize("is_routed", [False, True])
def test_claude_skill_refuses_subagent_before_connect(fake_vllm, monkeypatch, claude_skill_sources, is_routed):
    _, source = claude_skill_sources
    _native_no_connect(monkeypatch)
    result = CliRunner().invoke(
        start.start_app,
        ["claude", *(["--url", BASE] if is_routed else []), "--as-subagent", "--no-launch", "--skill", str(source)],
    )
    assert result.exit_code == 1
    assert "--skill cannot be combined with --as-subagent." in result.output
    assert "Injected skills:" not in result.output
    assert fake_vllm == []


def test_claude_skill_flag_uses_translated_directory(fake_vllm, tmp_path, monkeypatch, claude_skill_sources):
    _, source = claude_skill_sources
    calls = []
    translated = r"C:\Users\test user\session\agent-switch"

    def translate(path, command):
        calls.append((path, command))
        return translated

    monkeypatch.setattr(claude_agent, "_agent_config_path", translate)
    flags = claude_agent._get_claude_skill_flags(tmp_path / "session", {"shared": source})
    assert flags == [f"--add-dir={translated}"]
    assert calls == [(tmp_path / "session" / "agent-switch", ["claude"])]


@pytest.mark.parametrize("is_routed", [False, True])
def test_claude_skill_hint_precedes_launch_and_matches_recipe(fake_vllm, tmp_path, monkeypatch, claude_skill_sources, is_routed):
    _, source = claude_skill_sources
    args = ["claude", *(["--url", BASE, "--persist"] if is_routed else []), "--skill", str(source)]
    recipe = CliRunner().invoke(start.start_app, [*args, "--no-launch", "--", "--print", "hello"])
    assert recipe.exit_code == 0, recipe.output
    recipe_session = tmp_path / "agents" / ("claude" if is_routed else "claude-native")
    captured = {}

    def launch(command, env, **kwargs):
        captured.update(command = command, env = env)
        added_dir = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--add-dir=")))
        assert (added_dir / ".claude" / "skills" / "shared" / "SKILL.md").is_file()
        captured["session"] = str(added_dir.parent)
        click.echo("LAUNCH_CAPTURED")
        return 0

    set_start_attr(monkeypatch, "_launch", launch)
    launched = CliRunner().invoke(start.start_app, [*args, "--", "--print", "hello"])
    assert launched.exit_code == 0, launched.output
    assert launched.output.index("Injected skills:") < launched.output.index("LAUNCH_CAPTURED")
    assert captured["command"] == [
        arg.replace(str(recipe_session), captured["session"]) for arg in _launch_command(recipe.output)
    ]
    assert "CLAUDE_CONFIG_DIR" not in captured["env"] and "HOME" not in captured["env"]
