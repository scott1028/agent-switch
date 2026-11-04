# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""--compact-keep keeps the recent part of the window through OpenCode's and Pi's compaction."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import agent_switch.start as start
from agent_switch.agents import opencode as opencode_agent, pi as pi_agent
from tests.cli_support import BASE, MODEL


def _invoke(*args: str):
    return CliRunner().invoke(start.start_app, list(args))


@pytest.mark.parametrize("value", ["0.41", "-0.1"])
def test_pi_compact_keep_out_of_range_rejected(fake_vllm, value):
    result = _invoke("pi", "--url", BASE, "--no-launch", "--compact-keep", value)
    assert result.exit_code != 0
    assert "Invalid value for '--compact-keep'" in result.output


def test_pi_compact_keep_accepts_its_maximum(fake_vllm):
    result = _invoke("pi", "--url", BASE, "--no-launch", "--compact-keep", "0.4")
    assert result.exit_code == 0, result.output


def test_pi_compact_keep_needs_a_server(fake_vllm):
    result = _invoke("pi", "--no-launch", "--compact-keep", "0.3")
    assert result.exit_code != 0
    assert "--compact-keep needs --url or --provider." in result.output


def test_pi_subagent_compact_keep_warns(fake_vllm):
    result = _invoke("pi", "--url", BASE, "--no-launch", "--as-subagent", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert "--compact-keep does not apply with --as-subagent for Pi" in result.stderr


def test_pi_subagent_without_compact_keep_does_not_warn(fake_vllm):
    result = _invoke("pi", "--url", BASE, "--no-launch", "--as-subagent")
    assert result.exit_code == 0, result.output
    assert "--compact-keep" not in result.stderr


_PI_MODEL_KEY = f"agent-switch/{MODEL['id']}"


def _pi_settings(tmp_path) -> dict:
    return json.loads((tmp_path / "agents" / "pi" / ".pi" / "agent" / "settings.json").read_text())


def test_pi_compact_keep_sets_the_ratio(fake_vllm, tmp_path):
    result = _invoke("pi", "--url", BASE, "--no-launch", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert _pi_settings(tmp_path)["compaction"] == {
        "enabled": True,
        "modelOverrides": {_PI_MODEL_KEY: {"keepRecentTokens": 39321}},
    }


def test_pi_compact_keep_with_compact_at_writes_both(fake_vllm, tmp_path):
    result = _invoke("pi", "--url", BASE, "--no-launch", "--compact-at", "0.85", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert _pi_settings(tmp_path)["compaction"] == {
        "enabled": True,
        "reserveTokens": 19660,  # 131072 * 0.15
        "modelOverrides": {_PI_MODEL_KEY: {"keepRecentTokens": 39321}},  # 131072 * 0.3
    }


def test_pi_compact_keep_zero_with_compact_at_writes_only_the_reserve(fake_vllm, tmp_path):
    result = _invoke("pi", "--url", BASE, "--no-launch", "--compact-at", "0.85", "--compact-keep", "0")
    assert result.exit_code == 0, result.output
    assert _pi_settings(tmp_path)["compaction"] == {"enabled": True, "reserveTokens": 19660}


def _write_pi_compaction(tmp_path, *args, **kwargs) -> dict:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(exist_ok = True)
    pi_agent.write_pi_compaction(agent_dir, *args, **kwargs)
    path = agent_dir / "settings.json"
    return json.loads(path.read_text()) if path.exists() else {}


_PI_LAUNCH_MODEL = {"id": "launch", "context_length": 250000}


def test_pi_keep_zero_writes_no_override(tmp_path):
    served_models = [{"id": "small", "context_length": 32768}]
    settings = _write_pi_compaction(tmp_path, _PI_LAUNCH_MODEL, None, served_models, 0)
    assert "compaction" not in settings


def test_pi_compact_keep_zero_clears_an_earlier_keep_on_rerun(fake_vllm, tmp_path):
    assert _invoke("pi", "--url", BASE, "--no-launch").exit_code == 0
    assert _pi_settings(tmp_path)["compaction"]["modelOverrides"][_PI_MODEL_KEY] == {"keepRecentTokens": 26214}
    result = _invoke("pi", "--url", BASE, "--no-launch", "--compact-keep", "0")
    assert result.exit_code == 0, result.output
    assert "compaction" not in _pi_settings(tmp_path)


def test_pi_undo_clears_a_block_with_keep_and_overrides(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "settings.json").write_text(json.dumps({
        "compaction": {
            "enabled": True,
            "reserveTokens": 37500,
            "keepRecentTokens": 75000,
            "modelOverrides": {"agent-switch/small": {"reserveTokens": 4915, "keepRecentTokens": 9830}},
        },
        "theme": "dark",
    }))
    settings = _write_pi_compaction(tmp_path, _PI_LAUNCH_MODEL, None, None, 0)
    assert settings == {"theme": "dark"}


@pytest.mark.parametrize(
    "compaction",
    [
        {"enabled": False, "keepRecentTokens": 26214, "extra": 1},
        {"enabled": True, "keepRecentTokens": 26214, "extra": 1},
        {"enabled": True, "keepRecentTokens": "26214"},
        {"enabled": True, "modelOverrides": {"other/model": {"keepRecentTokens": 1, "extra": 1}}},
    ],
)
def test_pi_undo_leaves_a_block_agent_switch_did_not_write(tmp_path, compaction):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "settings.json").write_text(json.dumps({"compaction": compaction}))
    assert _write_pi_compaction(tmp_path, _PI_LAUNCH_MODEL, None, None, 0) == {"compaction": compaction}


def _seed_pi_compaction(tmp_path, compaction: dict) -> None:
    path = tmp_path / "agents" / "pi" / ".pi" / "agent" / "settings.json"
    path.parent.mkdir(parents = True)
    path.write_text(json.dumps({"compaction": compaction}))


@pytest.mark.parametrize("seed", [{"enabled": False}, {"enabled": False, "reserveTokens": 1000}])
def test_pi_default_path_keeps_a_disabled_auto_compact(fake_vllm, tmp_path, seed):
    _seed_pi_compaction(tmp_path, seed)
    result = _invoke("pi", "--url", BASE, "--no-launch")
    assert result.exit_code == 0, result.output
    assert _pi_settings(tmp_path)["compaction"] == {
        "enabled": False,
        "modelOverrides": {_PI_MODEL_KEY: {"keepRecentTokens": 26214}},
    }


def test_pi_compact_at_keeps_a_disabled_auto_compact(fake_vllm, tmp_path):
    _seed_pi_compaction(tmp_path, {"enabled": False})
    result = _invoke("pi", "--url", BASE, "--no-launch", "--compact-at", "0.85", "--compact-keep", "0")
    assert result.exit_code == 0, result.output
    assert _pi_settings(tmp_path)["compaction"] == {"enabled": False, "reserveTokens": 19660}


@pytest.mark.parametrize("seed", [{"keepRecentTokens": 1}, {"enabled": "no", "keepRecentTokens": 1}])
def test_pi_write_defaults_enabled_when_it_is_not_a_boolean(fake_vllm, tmp_path, seed):
    _seed_pi_compaction(tmp_path, seed)
    assert _invoke("pi", "--url", BASE, "--no-launch").exit_code == 0
    assert _pi_settings(tmp_path)["compaction"]["enabled"] is True


@pytest.mark.parametrize(
    "seed, expected",
    [
        ({"enabled": False}, {"enabled": False}),
        ({"enabled": False, "keepRecentTokens": 26214}, {"enabled": False}),
        (
            {
                "enabled": False,
                "reserveTokens": 19660,
                "modelOverrides": {_PI_MODEL_KEY: {"keepRecentTokens": 26214}},
            },
            {"enabled": False},
        ),
        ({"enabled": True, "keepRecentTokens": 26214}, None),
        ({"keepRecentTokens": 26214}, None),
    ],
)
def test_pi_nothing_to_write_keeps_only_a_disabled_auto_compact(fake_vllm, tmp_path, seed, expected):
    _seed_pi_compaction(tmp_path, seed)
    result = _invoke("pi", "--url", BASE, "--no-launch", "--compact-keep", "0")
    assert result.exit_code == 0, result.output
    assert _pi_settings(tmp_path).get("compaction") == expected


@pytest.mark.parametrize("value", ["0.41", "-0.1"])
def test_opencode_compact_keep_out_of_range_rejected(fake_vllm, value):
    result = _invoke("opencode", "--url", BASE, "--no-launch", "--compact-keep", value)
    assert result.exit_code != 0
    assert "Invalid value for '--compact-keep'" in result.output


def test_opencode_compact_keep_needs_a_server(fake_vllm):
    result = _invoke("opencode", "--no-launch", "--compact-keep", "0.3")
    assert result.exit_code != 0
    assert "--compact-keep needs --url or --provider." in result.output


def test_opencode_subagent_compact_keep_warns(fake_vllm):
    result = _invoke("opencode", "--url", BASE, "--no-launch", "--as-subagent", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert "--compact-keep does not apply with --as-subagent for OpenCode" in result.stderr


def test_opencode_subagent_without_compact_keep_does_not_warn(fake_vllm):
    result = _invoke("opencode", "--url", BASE, "--no-launch", "--as-subagent")
    assert result.exit_code == 0, result.output
    assert "--compact-keep" not in result.stderr


def _opencode_compaction(tmp_path) -> dict:
    return json.loads((tmp_path / "agents" / "opencode" / "opencode.json").read_text())["compaction"]


def test_opencode_compact_keep_sets_the_ratio(fake_vllm, tmp_path):
    result = _invoke("opencode", "--url", BASE, "--no-launch", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert _opencode_compaction(tmp_path)["preserve_recent_tokens"] == 39321


def test_opencode_compact_keep_zero_writes_no_key(fake_vllm, tmp_path):
    result = _invoke("opencode", "--url", BASE, "--no-launch", "--compact-keep", "0")
    assert result.exit_code == 0, result.output
    assert _opencode_compaction(tmp_path) == {"auto": True, "reserved": 13107}


def test_opencode_compact_keep_zero_clears_an_earlier_keep_on_rerun(fake_vllm, tmp_path):
    assert _invoke("opencode", "--url", BASE, "--no-launch").exit_code == 0
    assert _opencode_compaction(tmp_path)["preserve_recent_tokens"] == 26214
    result = _invoke("opencode", "--url", BASE, "--no-launch", "--compact-keep", "0")
    assert result.exit_code == 0, result.output
    assert _opencode_compaction(tmp_path) == {"auto": True, "reserved": 13107}


def test_opencode_compact_keep_with_compact_at_writes_both(fake_vllm, tmp_path):
    result = _invoke("opencode", "--url", BASE, "--no-launch", "--compact-at", "0.85", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert _opencode_compaction(tmp_path) == {
        "auto": True,
        "reserved": 19660,  # 131072 * 0.15
        "preserve_recent_tokens": 39321,  # 131072 * 0.3
    }


def test_opencode_v2_writes_the_same_key(tmp_path):
    path = tmp_path / "opencode.json"
    opencode_agent.write_opencode_config(BASE, "sk-test", MODEL, path, v2 = True)
    assert json.loads(path.read_text())["compaction"]["preserve_recent_tokens"] == 26214


@pytest.mark.parametrize(
    "compaction",
    [
        {"auto": False, "reserved": 13107, "preserve_recent_tokens": 26214},
        {"auto": True, "reserved": 1, "preserve_recent_tokens": 26214},
        {"auto": True, "reserved": 13107, "preserve_recent_tokens": 26214, "tail_turns": 3},
    ],
)
def test_opencode_subagent_leaves_a_block_agent_switch_did_not_write(tmp_path, compaction):
    path = tmp_path / "opencode.json"
    path.write_text(json.dumps({"compaction": compaction}))
    opencode_agent.write_opencode_config(BASE, "sk-test", MODEL, path, as_subagent = True)
    assert json.loads(path.read_text())["compaction"] == compaction
