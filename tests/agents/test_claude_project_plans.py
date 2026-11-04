# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""`--project-plans` stores Claude Code plans in <project>/.tmp."""

import json
from pathlib import Path

from typer.testing import CliRunner

import agent_switch.start as start
from tests.cli_support import BASE, _launch_command
from tests.start_split import set_start_attr


def _get_local_settings(args: list) -> dict:
    result = CliRunner().invoke(start.start_app, ["claude", "--url", BASE, "--no-launch", *args])
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    return json.loads(Path(command[command.index("--settings") + 1]).read_text())


def test_local_claude_overlay_saves_plans_in_project(fake_vllm):
    assert _get_local_settings([]).get("plansDirectory") == ".tmp"


def test_native_claude_saves_plans_in_project(fake_vllm, tmp_path, monkeypatch):
    set_start_attr(monkeypatch, "_connect", lambda *args, **kwargs: None)
    result = CliRunner().invoke(
        start.start_app, ["claude", "--no-launch", "--", "--settings", "mine.json"]
    )
    assert result.exit_code == 0, result.output
    settings = tmp_path / "agents" / "claude-native" / "plans-settings.json"
    # Ours rides first, so a forwarded --settings still wins (claude keeps the last one).
    assert _launch_command(result.output) == [
        "claude", f"--settings={settings}", "--settings", "mine.json"
    ]
    assert json.loads(settings.read_text()) == {"plansDirectory": ".tmp"}


def test_subagent_parent_saves_plans_in_project(fake_vllm, tmp_path):
    result = CliRunner().invoke(
        start.start_app, ["claude", "--url", BASE, "--as-subagent", "--no-launch", "--", "hi"]
    )
    assert result.exit_code == 0, result.output
    command = _launch_command(result.output)
    settings = tmp_path / "agents" / "claude-subagent" / "plans-settings.json"
    assert command[-2:] == [f"--settings={settings}", "hi"]
    assert json.loads(settings.read_text()) == {"plansDirectory": ".tmp"}


def test_no_project_plans_keeps_claude_default(fake_vllm):
    assert "plansDirectory" not in _get_local_settings(["--no-project-plans"])
    for args in (["--no-project-plans"], ["--url", BASE, "--as-subagent", "--no-project-plans"]):
        result = CliRunner().invoke(start.start_app, ["claude", "--no-launch", *args])
        assert result.exit_code == 0, result.output
        assert not any(arg.startswith("--settings") for arg in _launch_command(result.output))
