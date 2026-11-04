# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""--compact-keep through the real CLI: a window the server did not report gets no amount."""

import json
import shutil

import pytest
from typer.testing import CliRunner

import agent_switch.start as start
from agent_switch.core import launch as core_launch
from agent_switch.providers.types import Target
from tests.start_split import set_start_attr


def _get_ignored_warning(models: str) -> str:
    return (
        f"Warning: the server did not report a context length for {models}, "
        "so --compact-keep is ignored for them."
    )


@pytest.fixture
def cli(tmp_path, monkeypatch):
    set_start_attr(monkeypatch, "_agents_config_root", lambda: tmp_path / "agents")
    set_start_attr(monkeypatch, "_require_agent_for_launch", lambda *args: None)
    set_start_attr(monkeypatch, "_opencode_command", lambda *_: ("opencode", False))
    # No agent binaries: version probes assume a current build, as for a recipe run elsewhere.
    monkeypatch.setattr(shutil, "which", lambda name, path = None: None)
    monkeypatch.delenv("AGENT_SWITCH_API_KEY", raising = False)

    def invoke(*argv):
        return CliRunner().invoke(start.start_app, list(argv))

    return invoke


def _serve(server, models: list) -> None:
    server.route("GET", "/v1/models", body = {"object": "list", "data": models})
    server.route("POST", "/v1/messages", status = 400, body = {"error": "model is required"})
    server.route("POST", "/v1/responses", status = 400, body = {"error": "model is required"})


def _pi_compaction(tmp_path):
    path = tmp_path / "agents" / "pi" / ".pi" / "agent" / "settings.json"
    return json.loads(path.read_text()).get("compaction") if path.exists() else None


def _opencode_compaction(tmp_path) -> dict:
    return json.loads((tmp_path / "agents" / "opencode" / "opencode.json").read_text())["compaction"]


def _get_written_keep(tmp_path, agent: str):
    if agent == "pi":
        return _pi_compaction(tmp_path)
    return _opencode_compaction(tmp_path).get("preserve_recent_tokens")


def test_opencode_unreported_window_writes_no_keep(cli, fake_server, tmp_path):
    _serve(fake_server, [{"id": "m"}])
    result = cli("opencode", "--url", fake_server.base, "--no-launch")
    assert result.exit_code == 0, result.output
    assert _opencode_compaction(tmp_path) == {"auto": True, "reserved": 25000}


def test_pi_unreported_window_writes_no_keep(cli, fake_server, tmp_path):
    _serve(fake_server, [{"id": "m"}])
    result = cli("pi", "--url", fake_server.base, "--no-launch")
    assert result.exit_code == 0, result.output
    assert _pi_compaction(tmp_path) is None


def test_opencode_unreported_window_clears_an_earlier_keep(cli, fake_server, tmp_path):
    _serve(fake_server, [{"id": "m", "context_length": 131072}])
    assert cli("opencode", "--url", fake_server.base, "--no-launch").exit_code == 0
    assert _opencode_compaction(tmp_path)["preserve_recent_tokens"] == 26214
    _serve(fake_server, [{"id": "m"}])
    result = cli("opencode", "--url", fake_server.base, "--no-launch")
    assert result.exit_code == 0, result.output
    assert "preserve_recent_tokens" not in _opencode_compaction(tmp_path)


def test_pi_unreported_window_clears_an_earlier_keep(cli, fake_server, tmp_path):
    _serve(fake_server, [{"id": "m", "context_length": 131072}])
    assert cli("pi", "--url", fake_server.base, "--no-launch").exit_code == 0
    assert _pi_compaction(tmp_path)["modelOverrides"] == {"agent-switch/m": {"keepRecentTokens": 26214}}
    _serve(fake_server, [{"id": "m"}])
    result = cli("pi", "--url", fake_server.base, "--no-launch")
    assert result.exit_code == 0, result.output
    assert _pi_compaction(tmp_path) is None


@pytest.mark.parametrize("agent", ["pi", "opencode"])
def test_explicit_keep_on_an_unreported_window_warns(cli, fake_server, tmp_path, agent):
    _serve(fake_server, [{"id": "m"}])
    result = cli(agent, "--url", fake_server.base, "--no-launch", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert _get_ignored_warning("m") in result.stderr
    assert _get_written_keep(tmp_path, agent) is None


@pytest.mark.parametrize("agent", ["pi", "opencode"])
def test_default_keep_on_an_unreported_window_adds_no_warning(cli, fake_server, agent):
    _serve(fake_server, [{"id": "m"}])
    result = cli(agent, "--url", fake_server.base, "--no-launch")
    assert result.exit_code == 0, result.output
    assert "--compact-keep" not in result.stderr


def test_opencode_context_length_is_a_known_window(cli, fake_server, tmp_path):
    _serve(fake_server, [{"id": "m"}])
    result = cli("opencode", "--url", fake_server.base, "--no-launch", "--context-length", "32768", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert _opencode_compaction(tmp_path)["preserve_recent_tokens"] == 9830
    assert "--compact-keep" not in result.stderr


def test_pi_context_length_is_a_known_window(cli, fake_server, tmp_path):
    _serve(fake_server, [{"id": "m"}])
    result = cli("pi", "--url", fake_server.base, "--no-launch", "--context-length", "32768", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert _pi_compaction(tmp_path)["modelOverrides"] == {"agent-switch/m": {"keepRecentTokens": 9830}}
    assert "--compact-keep" not in result.stderr


@pytest.mark.parametrize("agent", ["pi", "opencode", "codex", "claude", "dsh"])
def test_the_assumed_window_mark_stays_out_of_agent_config(cli, fake_server, tmp_path, agent):
    _serve(fake_server, [{"id": "m"}, {"id": "other"}])
    result = cli(agent, "--url", fake_server.base, "--model", "m", "--no-launch")
    assert result.exit_code == 0, result.output
    assert "is_window_assumed" not in result.output
    files = [path for path in (tmp_path / "agents").rglob("*") if path.is_file()]
    assert files
    assert [path for path in files if "is_window_assumed" in path.read_text(errors = "ignore")] == []


_MIXED_MODELS = [
    {"id": "big", "context_length": 250000},
    {"id": "small", "context_length": 32768},
    {"id": "unreported"},
]


def test_pi_keep_goes_only_to_models_with_a_reported_window(cli, fake_server, tmp_path):
    _serve(fake_server, _MIXED_MODELS)
    result = cli("pi", "--url", fake_server.base, "--model", "big", "--no-launch")
    assert result.exit_code == 0, result.output
    assert _pi_compaction(tmp_path) == {
        "enabled": True,
        "modelOverrides": {
            "agent-switch/big": {"keepRecentTokens": 50000},
            "agent-switch/small": {"keepRecentTokens": 6553},
        },
    }


def test_pi_unreported_launch_window_still_sizes_the_other_models(cli, fake_server, tmp_path):
    _serve(fake_server, [{"id": "m"}, {"id": "small", "context_length": 32768}])
    result = cli("pi", "--url", fake_server.base, "--model", "m", "--no-launch")
    assert result.exit_code == 0, result.output
    assert _pi_compaction(tmp_path) == {
        "enabled": True,
        "modelOverrides": {"agent-switch/small": {"keepRecentTokens": 6553}},
    }


def test_pi_reserve_stays_in_place_beside_the_keep_overrides(cli, fake_server, tmp_path):
    _serve(fake_server, _MIXED_MODELS)
    result = cli(
        "pi", "--url", fake_server.base, "--model", "big", "--no-launch",
        "--compact-at", "0.85", "--compact-keep", "0.3",
    )
    assert result.exit_code == 0, result.output
    assert _pi_compaction(tmp_path) == {
        "enabled": True,
        "reserveTokens": 37500,
        "modelOverrides": {
            "agent-switch/big": {"keepRecentTokens": 75000},
            "agent-switch/small": {"reserveTokens": 4915, "keepRecentTokens": 9830},
            "agent-switch/unreported": {"reserveTokens": 37500},
        },
    }


def test_pi_explicit_keep_warns_about_the_unreported_served_model(cli, fake_server, tmp_path):
    _serve(fake_server, _MIXED_MODELS)
    result = cli("pi", "--url", fake_server.base, "--model", "big", "--no-launch", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert _get_ignored_warning("unreported") in result.stderr
    assert _pi_compaction(tmp_path)["modelOverrides"] == {
        "agent-switch/big": {"keepRecentTokens": 75000},
        "agent-switch/small": {"keepRecentTokens": 9830},
    }


def test_pi_explicit_keep_lists_only_an_unreported_launch_model(cli, fake_server):
    _serve(fake_server, [{"id": "m"}, {"id": "small", "context_length": 32768}])
    result = cli("pi", "--url", fake_server.base, "--model", "m", "--no-launch", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert _get_ignored_warning("m") in result.stderr


def test_pi_explicit_keep_lists_every_unreported_model_once(cli, fake_server):
    _serve(fake_server, [{"id": "m"}, {"id": "unreported"}, {"id": "small", "context_length": 32768}])
    result = cli("pi", "--url", fake_server.base, "--model", "m", "--no-launch", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert result.stderr.count("--compact-keep") == 1
    assert _get_ignored_warning("m, unreported") in result.stderr


def test_pi_context_length_takes_the_launch_model_off_the_list(cli, fake_server):
    _serve(fake_server, [{"id": "m"}, {"id": "other"}])
    result = cli(
        "pi", "--url", fake_server.base, "--model", "m", "--no-launch",
        "--context-length", "32768", "--compact-keep", "0.3",
    )
    assert result.exit_code == 0, result.output
    assert _get_ignored_warning("other") in result.stderr


def test_opencode_explicit_keep_does_not_list_served_models(cli, fake_server, tmp_path):
    _serve(fake_server, _MIXED_MODELS)
    result = cli("opencode", "--url", fake_server.base, "--model", "big", "--no-launch", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert "--compact-keep" not in result.stderr
    assert _opencode_compaction(tmp_path)["preserve_recent_tokens"] == 75000


def test_opencode_explicit_keep_lists_only_the_launch_model(cli, fake_server):
    _serve(fake_server, [{"id": "m"}, {"id": "other"}])
    result = cli("opencode", "--url", fake_server.base, "--model", "m", "--no-launch", "--compact-keep", "0.3")
    assert result.exit_code == 0, result.output
    assert _get_ignored_warning("m") in result.stderr


@pytest.mark.parametrize("agent", ["pi", "opencode"])
def test_zero_keep_on_an_unreported_window_does_not_warn(cli, fake_server, agent):
    _serve(fake_server, [{"id": "m"}])
    result = cli(agent, "--url", fake_server.base, "--no-launch", "--compact-keep", "0")
    assert result.exit_code == 0, result.output
    assert "--compact-keep" not in result.stderr


def test_pi_default_keep_with_mixed_windows_adds_no_warning(cli, fake_server):
    _serve(fake_server, _MIXED_MODELS)
    result = cli("pi", "--url", fake_server.base, "--model", "big", "--no-launch")
    assert result.exit_code == 0, result.output
    assert "--compact-keep" not in result.stderr


def test_served_models_mark_assumed_windows_across_servers(fake_server_pair):
    first, second = fake_server_pair
    _serve(first, [{"id": "known", "context_length": 8192}])
    _serve(second, [{"id": "unreported"}])
    upstreams = (
        Target("openai", first.base, server = "a"),
        Target("openai", second.base, server = "b"),
    )
    target = upstreams[0]._replace(upstreams = upstreams)
    assert core_launch._get_served_models(target, None, "a:known") == [
        {"id": "a:known", "context_length": 8192},
        {"id": "b:unreported", "context_length": 250000, "is_window_assumed": True},
    ]
