# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""Tests for `agent-switch <agent>` — config merging and launch env, no network."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest
from typer.testing import CliRunner

import agent_switch.start as start
from agent_switch.agents import codex as codex_agent, dsh as dsh_agent, pi as pi_agent
from tests.start_split import set_start_attr
from tests.cli_support import (
    BASE,
    MODEL,
    _RESUME_ENV_VAR,
    _capture_launch,
    _dsh_entries,
    _launch_command,
    _opencode_inline_config,
    _parse_toml,
)


def _assert_env_cwd(output: str, name: str) -> None:
    needle = f"$env:{name} = (Get-Location).Path" if os.name == "nt" else f'export {name}="$PWD"'
    assert needle in output, f"{needle!r} not found in:\n{output}"


def test_project_declares_direct_cli_dependencies():
    project = _parse_toml((_REPO_ROOT / "pyproject.toml").read_text(encoding = "utf-8"))
    assert "click>=8.0" in project["project"]["dependencies"]


@pytest.mark.parametrize("window, expected", [(32_768, 8_192), (143_616, 32_000)])
def test_pi_and_dsh_output_limit_follows_the_context(tmp_path, window, expected):
    model = {**MODEL, "context_length": window}
    pi_agent.write_pi_config(BASE, "sk-test-abc", model, tmp_path / "models.json")
    pi_agent.write_pi_subagent_config(BASE, "sk-test-abc", model, tmp_path / "subagent.json")
    dsh_agent.write_dsh_patch(BASE, model, tmp_path / "agent-switch.patch.yml")
    pi = json.loads((tmp_path / "models.json").read_text())["providers"]["agent-switch"]["models"][0]
    subagent = json.loads((tmp_path / "subagent.json").read_text())
    patch = _dsh_entries(tmp_path / "agent-switch.patch.yml")
    dsh = patch["llm-pi-ai"]["config"]["providers"]["agent-switch"]["models"][0]
    assert pi["maxTokens"] == subagent["maxTokens"] == dsh["maxTokens"] == expected


# ── --yolo: one switch routed to each agent's own auto-approve form ──


# The native "run tools without prompting" CLI flag each agent should receive.
_NATIVE_YOLO = {
    "claude": "--dangerously-skip-permissions",
    "codex": "--dangerously-bypass-approvals-and-sandbox",
    "pi": "--approve",
}


@pytest.mark.parametrize("agent, native", sorted(_NATIVE_YOLO.items()))
def test_yolo_routes_to_native_flag(fake_vllm, agent, native):
    result = CliRunner().invoke(start.start_app, [agent, "--url", BASE, "--yolo", "--no-launch"])
    assert result.exit_code == 0, result.output
    assert native in result.output


@pytest.mark.parametrize("agent, native", sorted(_NATIVE_YOLO.items()))
def test_no_yolo_omits_native_flag(fake_vllm, agent, native):
    result = CliRunner().invoke(start.start_app, [agent, "--url", BASE, "--no-launch"])
    assert result.exit_code == 0, result.output
    # pi's --approve is a real flag only added under --yolo; assert it's absent here.
    command = _launch_command(result.output)
    assert command and command[0] == agent, result.output
    assert native not in command


@pytest.mark.parametrize(
    "alias",
    ["--yolo", "--dangerously-skip-permissions", "--dangerously-bypass-approvals-and-sandbox"],
)
def test_yolo_aliases_are_interchangeable(fake_vllm, alias):
    # Any spelling on any agent routes to that agent's own flag, even the "wrong" one.
    claude = CliRunner().invoke(start.start_app, ["claude", "--url", BASE, alias, "--no-launch"])
    assert claude.exit_code == 0, claude.output
    assert "--dangerously-skip-permissions" in claude.output
    # The codex spelling must not leak through to Claude's command line.
    assert "--dangerously-bypass-approvals-and-sandbox" not in claude.output

    codex = CliRunner().invoke(start.start_app, ["codex", "--url", BASE, alias, "--no-launch"])
    assert codex.exit_code == 0, codex.output
    assert "--dangerously-bypass-approvals-and-sandbox" in codex.output
    assert "--dangerously-skip-permissions" not in codex.output

    opencode = CliRunner().invoke(
        start.start_app,
        ["opencode", "--url", BASE, alias, "--no-launch", "--", "run", "hello"],
    )
    assert opencode.exit_code == 0, opencode.output
    assert _launch_command(opencode.output) == ["opencode", "run", "hello", "--auto"]
    assert "permission" not in _opencode_inline_config(opencode.output)


def test_yolo_config_fallbacks_add_no_legacy_command_flag(fake_vllm):
    # OpenCode's append-safe bare recipe uses its config fallback, so it must not leak a legacy
    # yolo/dangerous alias onto argv.
    for agent in ("opencode",):
        result = CliRunner().invoke(start.start_app, [agent, "--url", BASE, "--yolo", "--no-launch"])
        assert result.exit_code == 0, result.output
        command = _launch_command(result.output)
        assert command and command[0] == agent, result.output
        assert not any("--yolo" in arg or "--dangerous" in arg for arg in command)


# ── `--` boundary: before it only agent-switch's own arguments, after it the agent's ──


def test_agent_option_before_the_boundary_is_refused(fake_vllm):
    result = CliRunner().invoke(start.start_app, ["codex", "--search"])
    assert result.exit_code == 1
    assert "`--search` is not an agent-switch argument" in result.output
    assert "put agent arguments after `--`" in result.output


def test_agent_subcommand_before_the_boundary_is_refused(fake_vllm):
    result = CliRunner().invoke(start.start_app, ["codex", "--persist", "resume"])
    assert result.exit_code == 1
    assert "`resume` is not an agent-switch argument" in result.output


def test_model_shaped_positional_before_the_boundary_routes_to_model(fake_vllm, tmp_path):
    result = CliRunner().invoke(
        start.start_app, ["codex", "--url", BASE, "--no-launch", MODEL["id"]]
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert MODEL["id"] not in command
    profile = (tmp_path / "agents" / "codex" / f"{codex_agent._CODEX_PROFILE}.config.toml").read_text()
    assert f'model = "{MODEL["id"]}"' in profile


def test_model_shaped_argument_after_the_boundary_reaches_the_agent(fake_vllm, tmp_path):
    result = CliRunner().invoke(
        start.start_app, ["codex", "--url", BASE, "--no-launch", "--", "owner/repo"]
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert command[-1] == "owner/repo"
    assert "--" not in command
    profile = (tmp_path / "agents" / "codex" / f"{codex_agent._CODEX_PROFILE}.config.toml").read_text()
    assert f'model = "{MODEL["id"]}"' in profile
    assert "owner/repo" not in profile


def test_second_boundary_belongs_to_the_agent(fake_vllm):
    result = CliRunner().invoke(
        start.start_app, ["codex", "--no-launch", "--", "--", "--yolo"]
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert command[-2:] == ["--", "--yolo"]
    # agent-switch's own --yolo was not set, so its routed flag is absent.
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


# ── override: a repeat of a flag agent-switch sends is dropped, with one stderr line per drop ──


def test_codex_repeat_of_sent_yolo_flag_is_dropped(fake_vllm):
    result = CliRunner().invoke(
        start.start_app, ["codex", "--yolo", "--no-launch", "--", "--yolo", "--search"]
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert command.count("--dangerously-bypass-approvals-and-sandbox") == 1
    assert "--yolo" not in command
    assert command[-1] == "--search"
    assert (
        "agent-switch: dropped --yolo; agent-switch's "
        "--dangerously-bypass-approvals-and-sandbox applies." in result.output
    )


def test_agent_yolo_flag_survives_when_agent_switch_sends_none(fake_vllm):
    result = CliRunner().invoke(start.start_app, ["codex", "--no-launch", "--", "--yolo"])
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert command[-1] == "--yolo"
    assert "agent-switch: dropped" not in result.output


def test_nothing_after_the_agent_own_boundary_is_dropped(fake_vllm):
    result = CliRunner().invoke(
        start.start_app, ["codex", "--yolo", "--no-launch", "--", "--", "--yolo"]
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert command[-2:] == ["--", "--yolo"]
    assert "agent-switch: dropped" not in result.output


def test_codex_local_repeats_of_sent_flags_are_dropped(fake_vllm):
    result = CliRunner().invoke(
        start.start_app,
        ["codex", "--url", BASE, "--no-launch", "--", "-p", "x", "--oss", "--profile=y"],
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert command.count("--oss") == 1
    assert command.count("--profile") == 1
    assert command[command.index("--profile") + 1] == codex_agent._CODEX_PROFILE
    assert "x" not in command
    assert "-p" not in command
    assert "--profile=y" not in command


def test_claude_local_repeat_of_sent_model_is_dropped(fake_vllm):
    result = CliRunner().invoke(
        start.start_app, ["claude", "--url", BASE, "--no-launch", "--", "--model", "other"]
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert command.count("--model") == 1
    assert command[command.index("--model") + 1] == MODEL["id"]
    assert "other" not in command


def test_pi_local_alias_of_sent_yolo_flag_is_dropped(fake_vllm):
    result = CliRunner().invoke(
        start.start_app, ["pi", "--url", BASE, "--yolo", "--no-launch", "--", "-a"]
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    assert command.count("--approve") == 1
    assert "-a" not in command


def test_agent_help_draws_each_epilog_section_as_a_panel():
    result = CliRunner().invoke(start.start_app, ["codex", "--help"])
    assert result.exit_code == 0, result.output
    for title in (
        "Native launch",
        "Several model servers",
        "Agent arguments",
        "MCP servers",
        "Context window",
        "Auto-compaction",
        "Reply length",
        "Examples",
    ):
        assert f"╭─ {title} " in result.output


@pytest.mark.parametrize(
    "title, rows", [("Context window", 3), ("Auto-compaction", 4), ("Reply length", 2)]
)
def test_agent_help_draws_each_table_panel(monkeypatch, title, rows):
    monkeypatch.setenv("COLUMNS", "80")
    result = CliRunner().invoke(start.start_app, ["codex", "--help"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    top = next(n for n, line in enumerate(lines) if line.startswith(f"╭─ {title} "))
    bottom = next(n for n in range(top, len(lines)) if lines[n].startswith("╰"))
    panel = lines[top:bottom + 1]
    # Rich draws the nested table, so no ASCII border reaches the option highlighter.
    assert "╭───" in panel[1] and any("╰───" in line for line in panel[2:-1])
    # One rule under the header and one between each pair of rows.
    assert sum("├───" in line for line in panel) == rows
    assert "+--" not in "\n".join(panel)
    # Every row, the nested table's included, closes at the panel's right edge.
    assert {len(line) for line in panel} == {80}


# The eli5 help budget: each agent subcommand stays short at 80 columns. The details live in
# the README, so growing an option's help past this line is a deliberate change to review.
_AGENT_HELP_MAX_LINES = 184


@pytest.mark.parametrize("agent", ["claude", "codex", "opencode", "pi", "dsh"])
def test_agent_help_stays_within_its_line_budget(monkeypatch, agent):
    monkeypatch.setenv("COLUMNS", "80")
    result = CliRunner().invoke(start.start_app, [agent, "--help"])
    assert result.exit_code == 0, result.output
    assert len(result.output.splitlines()) <= _AGENT_HELP_MAX_LINES


def test_top_level_help_mentions_repeating_url():
    result = CliRunner().invoke(start.start_app, ["--help"])
    assert result.exit_code == 0, result.output
    # Rich wraps the paragraph, so compare it with its line breaks folded away.
    assert "Repeat --url to put several model servers behind one session proxy" in " ".join(
        result.output.split()
    )


@pytest.mark.parametrize("agent", sorted(_RESUME_ENV_VAR))
def test_resume_persists_agent_home_to_stable_dir(agent, fake_vllm, tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, path = None: f"/usr/local/bin/{agent}")
    if agent == "dsh":
        set_start_attr(monkeypatch, "is_deepseek_harness_executable", lambda _: True)
    captured = _capture_launch(monkeypatch, [agent, "--url", BASE, "--persist"])
    stable = tmp_path / "agents" / agent
    assert captured["env"][_RESUME_ENV_VAR[agent]] == str(stable)
    # The stable dir survives the agent exit, so the session can be resumed.
    assert stable.exists()


def test_resume_persist_only_agents_have_no_resume_token(fake_vllm, monkeypatch):
    # Persistence alone must not select a session.
    for agent in ("dsh",):
        monkeypatch.setattr(shutil, "which", lambda _, a = agent: f"/usr/local/bin/{a}")
        if agent == "dsh":
            set_start_attr(monkeypatch, "is_deepseek_harness_executable", lambda _: True)
        captured = _capture_launch(monkeypatch, [agent, "--url", BASE, "--persist"])
        assert "resume" not in captured["command"]
        assert "--continue" not in captured["command"]


# These also pin Codex's wording, now that the helpers are shared.


# Tolerance: callers tear the server down on any exception, so only "is_gguf": false rejects.


@pytest.mark.parametrize(
    ("agent", "unset"),
    [
        ("codex", ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")),
    ],
)
def test_launch_drops_provider_credentials(agent, unset, fake_vllm, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, path = None: f"/usr/local/bin/{agent}")
    for name in unset:
        monkeypatch.setenv(name, "sk-stale")
    captured = _capture_launch(monkeypatch, [agent, "--url", BASE])
    for name in unset:
        assert name not in captured["env"]
