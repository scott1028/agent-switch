# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""`agent-switch <agent> --url ...` against model servers, end to end through the CLI."""

import json
import shlex
import subprocess
import urllib.request
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import agent_switch.start as start
from agent_switch import providers
from agent_switch.core import storage as core_storage
from agent_switch.providers import ollama as ollama_provider
import shutil
from agent_switch.agents import codex as codex_agent, opencode as opencode_agent, pi as pi_agent
from tests.start_split import set_start_attr


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


def _exports(output: str) -> dict:
    env = {}
    for line in output.splitlines():
        if line.startswith("export "):
            name, _, value = line[len("export "):].partition("=")
            env[name] = shlex.split(value)[0] if value else ""
    return env


def _ollama(server, model = "smollm2:135m", ctx = 8192, messages = True, responses = True):
    server.route("GET", "/api/version", body = {"version": "0.34.3"})
    server.route("GET", "/api/ps", body = {"models": [{"name": model, "model": model, "context_length": ctx}]})
    server.route("GET", "/v1/models", body = {"object": "list", "data": [{"id": model}]})
    if messages:
        server.route("POST", "/v1/messages", status = 400, body = {"error": "model is required"})
    if responses:
        server.route("POST", "/v1/responses", status = 400, body = {"error": "model is required"})


def _vllm(server, ctx = 32768):
    server.route("GET", "/v1/models", body = {"object": "list", "data": [{"id": "Qwen/Qwen3-8B", "owned_by": "vllm", "max_model_len": ctx}]})
    server.route("POST", "/v1/messages", status = 400, body = {"error": "bad request"})
    server.route("POST", "/v1/responses", status = 400, body = {"error": "bad request"})


def test_context_length_below_server_sentinel_is_rejected(cli):
    result = cli("claude", "--context-length", "-2", "--no-launch")
    assert result.exit_code != 0
    assert "range" in result.output.lower()


def test_claude_against_ollama(cli, fake_server):
    _ollama(fake_server)
    result = cli("claude", "--url", fake_server.base, "--no-launch")
    assert result.exit_code == 0, result.output
    env = _exports(result.output)
    assert env["ANTHROPIC_BASE_URL"] == fake_server.base
    assert env["ANTHROPIC_MODEL"] == "smollm2:135m"
    assert env["ANTHROPIC_AUTH_TOKEN"] == providers.NO_KEY
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "8192"
    assert f"Ollama ready at {fake_server.base}" in result.output
    assert "Unsloth" not in result.output


def test_codex_against_vllm(cli, fake_server, tmp_path):
    _vllm(fake_server)
    result = cli("codex", "--url", fake_server.base + "/v1", "--no-launch")
    assert result.exit_code == 0, result.output
    config = (tmp_path / "agents" / "codex" / "config.toml").read_text()
    assert f'base_url = "{fake_server.base}/v1"' in config
    profile = (tmp_path / "agents" / "codex" / f"{codex_agent._CODEX_PROFILE}.config.toml").read_text()
    assert 'model = "Qwen/Qwen3-8B"' in profile
    assert "model_context_window" not in profile  # the window rides the catalog row
    catalog = json.loads((tmp_path / "agents" / "codex" / "model-catalog.json").read_text())
    assert catalog["models"][0]["context_window"] == 32768


def test_opencode_against_llamacpp(cli, fake_server, tmp_path):
    fake_server.route("GET", "/props", body = {"default_generation_settings": {"n_ctx": 4096}})
    fake_server.route("GET", "/v1/models", body = {"data": [{"id": "/m/jan.gguf", "owned_by": "llamacpp", "meta": {"n_ctx": 4096}}]})
    result = cli("opencode", "--url", fake_server.base, "--no-launch")
    assert result.exit_code == 0, result.output
    config = json.loads((tmp_path / "agents" / "opencode" / "opencode.json").read_text())
    provider = config["provider"][opencode_agent._OPENCODE_PROVIDER]
    assert provider["options"]["baseURL"] == f"{fake_server.base}/v1"
    assert provider["models"]["/m/jan.gguf"]["limit"]["context"] == 4096


def test_pi_against_lmstudio(cli, fake_server, tmp_path):
    fake_server.route(
        "GET",
        "/api/v1/models",
        body = {"models": [{"type": "llm", "key": "qwen/qwen3-8b", "loaded_instances": [{"id": "qwen/qwen3-8b", "config": {"context_length": 16384}}]}]},
    )
    fake_server.route("GET", "/v1/models", body = {"object": "list", "data": [{"id": "qwen/qwen3-8b"}]})
    result = cli("pi", "--url", fake_server.base, "--no-launch")
    assert result.exit_code == 0, result.output
    models = json.loads((tmp_path / "agents" / "pi" / ".pi" / "agent" / "models.json").read_text())
    entry = models["providers"][pi_agent._PI_PROVIDER]
    assert entry["baseUrl"] == f"{fake_server.base}/v1"
    assert entry["models"][0]["contextWindow"] == 16384


def test_colon_model_names_are_not_split_into_a_gguf_variant(cli, fake_server):
    _ollama(fake_server)
    result = cli("claude", "--url", fake_server.base, "-m", "smollm2:135m", "--no-launch")
    assert result.exit_code == 0, result.output
    assert _exports(result.output)["ANTHROPIC_MODEL"] == "smollm2:135m"


def test_unsupported_sampling_fields_warn_and_stay_out_of_the_body(cli, fake_server):
    _ollama(fake_server)
    result = cli("claude", "--url", fake_server.base, "--temperature", "0.6", "--top-k", "20", "--no-launch")
    assert result.exit_code == 0, result.output
    assert "Ollama ignores --top-k" in result.output
    assert json.loads(_exports(result.output)["CLAUDE_CODE_EXTRA_BODY"]) == {"temperature": 0.6}


def test_reasoning_off_rides_in_chat_template_kwargs_on_vllm(cli, fake_server):
    _vllm(fake_server)
    result = cli("claude", "--url", fake_server.base, "--reasoning", "off", "--no-launch")
    assert result.exit_code == 0, result.output
    body = json.loads(_exports(result.output)["CLAUDE_CODE_EXTRA_BODY"])
    assert body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_claude_subagent_uses_the_served_model_id(cli, fake_server, tmp_path):
    _ollama(fake_server)
    result = cli("claude", "--url", fake_server.base, "--as-subagent", "--no-launch")
    assert result.exit_code == 0, result.output
    plugin = tmp_path / "agents" / "claude-subagent" / "local-agent" / ".mcp.json"
    env = json.loads(plugin.read_text())["mcpServers"]["local"]["env"]
    assert env["AGENT_SWITCH_CLAUDE_SUBAGENT_MODEL"] == "smollm2:135m"
    assert env["AGENT_SWITCH_CLAUDE_SUBAGENT_BASE_URL"] == fake_server.base
    assert not any("/api/inference" in path for _, path, *_ in fake_server.requests)


def test_claude_needs_the_anthropic_endpoint(cli, fake_server):
    _ollama(fake_server, messages = False)
    result = cli("claude", "--url", fake_server.base, "--no-launch")
    assert result.exit_code == 1
    assert "/v1/messages" in result.output


def test_codex_needs_the_responses_endpoint(cli, fake_server):
    _ollama(fake_server, responses = False)
    result = cli("codex", "--url", fake_server.base, "--no-launch")
    assert result.exit_code == 1
    assert "/v1/responses" in result.output


def test_no_url_launches_the_agent_natively(cli, monkeypatch):
    # Without --url/--provider nothing is probed: the agent keeps its own model and login.
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda *a, **k: pytest.fail("no server may be probed")
    )
    result = cli("claude", "--no-launch")
    assert result.exit_code == 0, result.output
    assert "runs with its own model, login and config" in result.output
    assert "Unsloth" not in result.output


def test_api_key_is_remembered_for_that_server(cli, fake_server):
    fake_server.route("GET", "/v1/models", lambda _: (200, {"data": [{"id": "m", "context_length": 4096}]}))
    first = cli("pi", "--url", fake_server.base, "--api-key", "secret", "--no-launch")
    assert first.exit_code == 0, first.output
    fake_server.requests.clear()
    second = cli("pi", "--url", fake_server.base, "--no-launch")
    assert second.exit_code == 0, second.output
    assert all(auth == "Bearer secret" for _, path, _, auth in fake_server.requests if path == "/v1/models")


# "unsloth" names the removed Studio integration.
@pytest.mark.parametrize("name", ["nope", "unsloth"])
def test_unknown_provider_is_a_usage_error(cli, name):
    result = cli("claude", "--provider", name, "--no-launch")
    assert result.exit_code == 2


# ── more than one --url ──


def _openai_pair(a, b):
    for server in (a, b):
        server.route(
            "GET",
            "/v1/models",
            body = {"object": "list", "data": [{"id": "qwen3", "context_length": 8192}]},
        )
        server.route("POST", "/v1/chat/completions", body = {"choices": [{"message": {"content": "ok"}}]})


@pytest.mark.parametrize(
    "extra, refused",
    [
        (["--no-launch"], "--no-launch"),
        (["--api-key", "secret"], "SERVER:KEY"),
        (["--provider", "ollama"], "--provider"),
    ],
)
def test_two_urls_refuse_what_needs_a_single_server(cli, fake_server_pair, extra, refused):
    a, b = fake_server_pair
    result = cli("opencode", "--url", f"a:{a.base}", "--url", f"b:{b.base}", *extra)
    assert result.exit_code == 1
    assert refused in result.output


def test_two_urls_refuse_a_typed_api_key_that_equals_the_env_var(cli, fake_server_pair, monkeypatch):
    # The value matching AGENT_SWITCH_API_KEY is not what decides this; typing the flag is.
    a, b = fake_server_pair
    monkeypatch.setenv("AGENT_SWITCH_API_KEY", "demo-key")
    result = cli("opencode", "--url", f"a:{a.base}", "--url", f"b:{b.base}", "--api-key", "demo-key")
    assert result.exit_code == 1
    assert "SERVER:KEY" in result.output


def test_two_urls_run_on_when_only_the_env_var_gave_an_api_key(cli, fake_server_pair, monkeypatch):
    a, b = fake_server_pair
    _openai_pair(a, b)
    set_start_attr(monkeypatch, "_resolve_or_install_agent", lambda name, hint, resolver: name)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode = 0))
    monkeypatch.setenv("AGENT_SWITCH_API_KEY", "demo-key")
    result = cli("opencode", "--url", f"a:{a.base}", "--url", f"b:{b.base}")
    assert result.exit_code == 0, result.output
    assert "agent-switch proxy ready at http://127.0.0.1:" in result.output


def test_two_urls_pi_lists_every_servers_model(cli, fake_server_pair, monkeypatch, tmp_path):
    a, b = fake_server_pair
    _openai_pair(a, b)
    # Pi's session relocates HOME, so the user's real ~/.pi is not read for resources.
    monkeypatch.setenv("HOME", str(tmp_path / "pi-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "pi-home"))
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising = False)
    set_start_attr(monkeypatch, "_resolve_or_install_agent", lambda name, hint, resolver: name)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode = 0))
    monkeypatch.setenv("AGENT_SWITCH_API_KEY", "demo-key")
    result = cli("pi", "--url", f"a:{a.base}", "--url", f"b:{b.base}", "--persist")
    assert result.exit_code == 0, result.output
    config_path = tmp_path / "agents" / "pi" / ".pi" / "agent" / "models.json"
    models = json.loads(config_path.read_text())["providers"]["agent-switch"]["models"]
    # Both servers' models are listed under their NAME: prefixes, so /model can cross servers.
    assert [model["id"] for model in models] == ["a:qwen3", "b:qwen3"]


def test_two_urls_named_alike_fail(cli, fake_server_pair):
    a, b = fake_server_pair
    result = cli("opencode", "--url", f"a:{a.base}", "--url", f"a:{b.base}")
    assert result.exit_code == 1
    assert "distinct NAME:" in result.output


def test_two_urls_whose_auto_names_collide_fail(cli, fake_server):
    # Both default to the same host-port name, so the model prefix would be ambiguous.
    result = cli("opencode", "--url", fake_server.base, "--url", fake_server.base)
    assert result.exit_code == 1
    assert "distinct NAME:" in result.output


def test_one_named_url_keeps_the_plain_model_id(cli, fake_server):
    _ollama(fake_server)
    result = cli("claude", "--url", f"a:{fake_server.base}", "--no-launch")
    assert result.exit_code == 0, result.output
    assert _exports(result.output)["ANTHROPIC_MODEL"] == "smollm2:135m"
    assert f"Ollama ready at {fake_server.base}" in result.output


def test_one_named_url_takes_its_own_prefixed_api_key(cli, fake_server):
    _ollama(fake_server)
    result = cli("claude", "--url", f"a:{fake_server.base}", "--api-key", "a:KEY", "--no-launch")
    assert result.exit_code == 0, result.output
    assert _exports(result.output)["ANTHROPIC_AUTH_TOKEN"] == "KEY"


# click splits a repeated option's env var on whitespace and then reads NAME: in it, so an env
# value has to be taken as it stands: one key, spaces and any = inside it kept.
@pytest.mark.parametrize("env_key", ["part1 part2", "a:KA"])
def test_one_url_sends_an_env_var_key_whole(cli, fake_server, monkeypatch, env_key):
    _ollama(fake_server)
    monkeypatch.setenv("AGENT_SWITCH_API_KEY", env_key)
    result = cli("claude", "--url", f"a:{fake_server.base}", "--no-launch")
    assert result.exit_code == 0, result.output
    assert _exports(result.output)["ANTHROPIC_AUTH_TOKEN"] == env_key
    assert {request[3] for request in fake_server.requests} == {f"Bearer {env_key}"}


def test_provider_only_sends_an_env_var_key_whole(cli, fake_server, monkeypatch):
    # --provider has no --url to point at the fake, so its own default port is moved there.
    _ollama(fake_server)
    monkeypatch.setattr(ollama_provider, "DEFAULT_URL", fake_server.base)
    monkeypatch.setenv("AGENT_SWITCH_API_KEY", "part1 part2")
    result = cli("claude", "--provider", "ollama", "--no-launch")
    assert result.exit_code == 0, result.output
    assert _exports(result.output)["ANTHROPIC_AUTH_TOKEN"] == "part1 part2"
    assert {request[3] for request in fake_server.requests} == {"Bearer part1 part2"}


def test_two_urls_launch_through_the_proxy(cli, fake_server_pair, tmp_path, monkeypatch):
    a, b = fake_server_pair
    _openai_pair(a, b)
    set_start_attr(monkeypatch, "_resolve_or_install_agent", lambda name, hint, resolver: name)
    seen = {}

    def fake_run(command, env):
        # The agent is "running": its config points at the proxy, and the proxy reaches both servers.
        config_path = next((tmp_path / "agents" / ".tmp").glob("*/opencode.json"))
        config = json.loads(config_path.read_text())
        provider = config["provider"][opencode_agent._OPENCODE_PROVIDER]
        seen["base"] = provider["options"]["baseURL"][: -len("/v1")]
        seen["models"] = sorted(provider["models"])
        seen["key"] = provider["options"]["apiKey"]
        seen["model"] = config["model"]
        request = urllib.request.Request(
            f"{seen['base']}/v1/chat/completions",
            data = json.dumps({"model": "b:qwen3", "messages": []}).encode(),
            headers = {"Authorization": f"Bearer {seen['key']}"},
        )
        with urllib.request.urlopen(request, timeout = 10) as response:
            seen["answer"] = json.loads(response.read())["choices"][0]["message"]["content"]
        return SimpleNamespace(returncode = 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cli("opencode", "--url", f"a:{a.base}", "--url", f"b:{b.base}")
    assert result.exception is None, result.exception
    assert result.exit_code == 0, result.output
    assert "agent-switch proxy ready at http://127.0.0.1:" in result.output
    assert f"a: OpenAI-compatible server at {a.base}" in result.output
    assert f"b: OpenAI-compatible server at {b.base}" in result.output
    assert seen["model"] == "agent-switch/a:qwen3"
    assert seen["base"].startswith("http://127.0.0.1:")
    assert seen["models"] == ["a:qwen3", "b:qwen3"]
    assert seen["answer"] == "ok"
    # The prefixed id reached b as b's own id, and a was never asked to infer.
    assert [payload["model"] for _, _, payload, _ in b.requests if payload] == ["qwen3"]
    assert not any(method == "POST" for method, *_ in a.requests)


def test_two_urls_positional_model_names_the_server(cli, fake_server_pair, tmp_path, monkeypatch):
    a, b = fake_server_pair
    _openai_pair(a, b)
    set_start_attr(monkeypatch, "_resolve_or_install_agent", lambda name, hint, resolver: name)
    seen = {}

    def fake_run(command, env):
        config_path = next((tmp_path / "agents" / ".tmp").glob("*/opencode.json"))
        seen["model"] = json.loads(config_path.read_text())["model"]
        return SimpleNamespace(returncode = 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cli("opencode", "--url", f"a:{a.base}", "--url", f"b:{b.base}", "b:qwen3")
    assert result.exception is None, result.exception
    assert result.exit_code == 0, result.output
    # The leading NAME:ID positional routes to --model, so server b is the one picked.
    assert seen["model"] == "agent-switch/b:qwen3"


def test_two_urls_take_their_own_api_key_and_remember_them(cli, fake_server_pair, tmp_path, monkeypatch):
    a, b = fake_server_pair
    _openai_pair(a, b)
    set_start_attr(monkeypatch, "_resolve_or_install_agent", lambda name, hint, resolver: name)
    seen = {}

    def fake_run(command, env):
        # The agent is "running": one inference through the proxy shows which key reaches b.
        config_path = next((tmp_path / "agents" / ".tmp").glob("*/opencode.json"))
        provider = json.loads(config_path.read_text())["provider"][opencode_agent._OPENCODE_PROVIDER]
        base = provider["options"]["baseURL"][: -len("/v1")]
        request = urllib.request.Request(
            f"{base}/v1/chat/completions",
            data = json.dumps({"model": "b:qwen3", "messages": []}).encode(),
            headers = {"Authorization": f"Bearer {provider['options']['apiKey']}"},
        )
        with urllib.request.urlopen(request, timeout = 10) as response:
            seen["answer"] = json.loads(response.read())["choices"][0]["message"]["content"]
        return SimpleNamespace(returncode = 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    first = cli(
        "opencode",
        "--url",
        f"a:{a.base}",
        "--url",
        f"b:{b.base}",
        "--api-key",
        "a:KA",
        "--api-key",
        "b:KB",
    )
    assert first.exit_code == 0, first.output
    assert seen["answer"] == "ok"
    # Every server only ever sees its own key, including for the request the proxy routed to b.
    assert {request[3] for request in a.requests} == {"Bearer KA"}
    assert {request[3] for request in b.requests} == {"Bearer KB"}

    a.requests.clear()
    b.requests.clear()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode = 0))
    second = cli("opencode", "--url", f"a:{a.base}", "--url", f"b:{b.base}")
    assert second.exit_code == 0, second.output
    # Both keys were remembered per server, so the next session needs neither flag.
    assert {request[3] for request in a.requests} == {"Bearer KA"}
    assert {request[3] for request in b.requests} == {"Bearer KB"}


def test_two_urls_on_one_base_use_their_own_key_everywhere(cli, fake_server, tmp_path, monkeypatch):
    # One base behind two --url: the listing says which key asked, so one key used twice shows up.
    def listing(_payload):
        asked = fake_server.header_logs[-1][2].get("Authorization")
        model = "alpha" if asked == "Bearer KA" else "beta"
        return 200, {"object": "list", "data": [{"id": model, "context_length": 8192}]}

    fake_server.route("GET", "/v1/models", listing)
    fake_server.route(
        "POST", "/v1/chat/completions", body = {"choices": [{"message": {"content": "ok"}}]}
    )
    core_storage._remember_key(core_storage._provider_key_cache_path(), fake_server.base, "KA")
    set_start_attr(monkeypatch, "_resolve_or_install_agent", lambda name, hint, resolver: name)
    seen = {}

    def fake_run(command, env):
        config_path = next((tmp_path / "agents" / ".tmp").glob("*/opencode.json"))
        config = json.loads(config_path.read_text())
        provider = config["provider"][opencode_agent._OPENCODE_PROVIDER]
        base = provider["options"]["baseURL"][: -len("/v1")]
        headers = {"Authorization": f"Bearer {provider['options']['apiKey']}"}
        seen["models"] = sorted(provider["models"])
        listing_request = urllib.request.Request(f"{base}/v1/models", headers = headers)
        with urllib.request.urlopen(listing_request, timeout = 10) as response:
            seen["listing"] = sorted(item["id"] for item in json.loads(response.read())["data"])
        request = urllib.request.Request(
            f"{base}/v1/chat/completions",
            data = json.dumps({"model": "a:alpha", "messages": []}).encode(),
            headers = headers,
        )
        with urllib.request.urlopen(request, timeout = 10) as response:
            seen["answer"] = json.loads(response.read())["choices"][0]["message"]["content"]
        seen["alpha_auth"] = [
            auth
            for _method, path, _payload, auth in fake_server.requests
            if path == "/v1/chat/completions"
        ][-1]
        return SimpleNamespace(returncode = 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cli(
        "opencode",
        "--url",
        f"a:{fake_server.base}",
        "--url",
        f"b:{fake_server.base}",
        "--api-key",
        "b:KB",
    )
    assert result.exception is None, result.exception
    assert result.exit_code == 0, result.output
    # What OpenCode lists is what the proxy routes, and both came from the key each server got.
    assert seen["models"] == seen["listing"] == ["a:alpha", "b:beta"]
    assert seen["answer"] == "ok"
    # a/alpha goes out with the key remembered for that base, not with b's.
    assert seen["alpha_auth"] == "Bearer KA"