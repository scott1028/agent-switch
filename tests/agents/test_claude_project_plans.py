# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""`--project-plans` and `--global-project-plans` store Claude Code plans in <project>/.tmp."""

import json
from pathlib import Path

import pytest
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


def _refuse_launch(*args, **kwargs):
    raise AssertionError("the agent must not start")


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising = False)
    # These flags only edit a file; reaching a launch or the alias probe is a bug.
    set_start_attr(monkeypatch, "_run_native", _refuse_launch)
    set_start_attr(monkeypatch, "_check_alias", _refuse_launch)
    return home


@pytest.mark.parametrize(
    ("name", "config_env", "config_dir"),
    [
        ("claude", None, ".claude"),
        ("claude", "custom-claude", "custom-claude"),
        # The alias sets its own CLAUDE_CONFIG_DIR, so the caller's one does not apply.
        ("claude-personal", "custom-claude", ".claude-personal"),
    ],
)
def test_global_project_plans_round_trip(home, monkeypatch, name, config_env, config_dir):
    if config_env:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / config_env))
    settings = home / config_dir / "settings.json"
    settings.parent.mkdir()
    # claude's own layout: two-space indent, non-ASCII kept as is.
    user_settings = {"model": "opus", "hooks": {"Stop": "完成通知"}}
    original = json.dumps(user_settings, indent = 2, ensure_ascii = False) + "\n"
    settings.write_text(original, encoding = "utf-8")

    patched = CliRunner().invoke(start.start_app, [name, "--global-project-plans"])
    assert patched.exit_code == 0, patched.output
    text = settings.read_text(encoding = "utf-8")
    assert json.loads(text) == {**user_settings, "plansDirectory": ".tmp"}
    assert "完成通知" in text
    assert f"Undo: agent-switch {name} --no-global-project-plans" in patched.output
    assert [path.name for path in home.iterdir()] == [config_dir]

    restored = CliRunner().invoke(start.start_app, [name, "--no-global-project-plans"])
    assert restored.exit_code == 0, restored.output
    assert settings.read_text(encoding = "utf-8") == original


def test_global_project_plans_never_creates_or_clobbers(home):
    missing = CliRunner().invoke(start.start_app, ["claude-work", "--global-project-plans"])
    assert missing.exit_code == 1, missing.output
    assert f"{home / '.claude-work'} does not exist" in missing.output
    assert not (home / ".claude-work").exists()

    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"model": "opus",', encoding = "utf-8")
    broken = CliRunner().invoke(start.start_app, ["claude", "--global-project-plans"])
    assert broken.exit_code == 1, broken.output
    assert f"{settings} is not a JSON object" in broken.output
    assert settings.read_text(encoding = "utf-8") == '{"model": "opus",'
