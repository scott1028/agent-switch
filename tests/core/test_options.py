# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE
# Ported from unsloth_cli/tests/test_start.py (unsloth commit 8e11ba15e); see NOTICE.md.

"""Option parsing: repo/model token splitting and yolo command maps."""

import pytest
import typer

from agent_switch.core import (
    options as core_options,
)


@pytest.mark.parametrize(
    "model, expected",
    [
        ("org/Qwen3-1.7B-GGUF:UD-Q4_K_XL", ("org/Qwen3-1.7B-GGUF", "UD-Q4_K_XL")),
        ("org/gemma-4-E2B-it-GGUF:Q8_0", ("org/gemma-4-E2B-it-GGUF", "Q8_0")),
        ("org/Qwen3-1.7B-GGUF", ("org/Qwen3-1.7B-GGUF", None)),  # no suffix
        ("/models/local.gguf", ("/models/local.gguf", None)),  # absolute path
        ("./rel.gguf", ("./rel.gguf", None)),  # relative path
        ("C:\\models\\x.gguf", ("C:\\models\\x.gguf", None)),  # Windows drive
        ("repo:with/slash", ("repo:with/slash", None)),  # slash in variant -> not a variant
        ("", ("", None)),
    ],
)
def test_split_repo_variant(model, expected):
    assert core_options._split_repo_variant(model) == expected


@pytest.mark.parametrize(
    "token, expected",
    [
        ("org/gemma-4-E2B-it-GGUF", True),
        ("org/gemma-4-E2B-it-GGUF:UD-Q4_K_XL", True),
        ("some-org/model.name_1", True),
        ("--continue", False),  # flag
        ("resume", False),  # single word, no slash
        ("/models/local.gguf", False),  # absolute path
        ("./rel.gguf", False),  # relative path
        ("C:\\models\\x.gguf", False),  # Windows drive
        ("my models/foo", False),  # has a space
        ("owner/repo/extra", False),  # too many segments
        ("a:qwen3", True),  # NAME:ID server prefix
        ("a:qwen3:8b", True),  # NAME:ID whose id carries a colon
        ("qwen3:8b", True),  # colon id; the known-server check happens later
        ("http://127.0.0.1:11434", False),  # URL, not a model
        ("C:\\tools\\x.exe", False),  # Windows drive path, not a NAME: prefix
    ],
)
def test_looks_like_model(token, expected):
    assert core_options._looks_like_model(token) is expected


def test_consume_positional_model_leading_token():
    # A leading org/name positional routes to --model and is dropped from the passthrough.
    model, rest = core_options._consume_positional_model(None, ["org/Model-GGUF", "--continue"])
    assert model == "org/Model-GGUF"
    assert rest == ["--continue"]


def test_looks_like_model_leaves_existing_local_dir_for_agent(tmp_path, monkeypatch):
    # A relative `owner/repo` that actually exists (e.g. an OpenCode project dir) must
    # stay an agent argument, not be consumed as a model.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "owner" / "repo").mkdir(parents = True)
    assert core_options._looks_like_model("owner/repo") is False
    model, rest = core_options._consume_positional_model(None, ["owner/repo"])
    assert model is None and rest == ["owner/repo"]
    # The same shape, when it does not exist locally, is still treated as a model.
    assert core_options._looks_like_model("owner/absent-repo") is True


def test_consume_positional_model_ignores_non_leading_and_explicit_model():
    # An org/name that is an option value (not leading) is never stolen.
    model, rest = core_options._consume_positional_model(None, ["--profile", "owner/repo"])
    assert model is None and rest == ["--profile", "owner/repo"]
    # An explicit --model always wins; the positional is left untouched.
    model, rest = core_options._consume_positional_model("explicit/model", ["owner/repo"])
    assert model == "explicit/model" and rest == ["owner/repo"]


def test_yolo_command_flags_unmapped_agent_is_empty():
    # Placement-aware/config-based agents (and any typo) must yield no prefix flag.
    assert core_options._yolo_command_flags("opencode", True) == []
    assert core_options._yolo_command_flags("claude", True) == ["--dangerously-skip-permissions"]
    assert core_options._yolo_command_flags("claude", False) == []


def test_parse_server_headers_without_a_prefix_reaches_every_server():
    assert core_options.parse_server_headers(["X-Foo=bar"], ["a", "b"]) == {
        "a": {"X-Foo": "bar"},
        "b": {"X-Foo": "bar"},
    }


def test_parse_server_headers_prefix_scopes_to_one_server():
    assert core_options.parse_server_headers(["a:X-Foo=bar", "X-Tenant=eng"], ["a", "b"]) == {
        "a": {"X-Foo": "bar", "X-Tenant": "eng"},
        "b": {"X-Tenant": "eng"},
    }


def test_parse_server_headers_unknown_server_fails():
    with pytest.raises(typer.Exit):
        core_options.parse_server_headers(["c:X-Foo=bar"], ["a", "b"])


def test_parse_server_headers_invalid_header_name_fails():
    for bad in ("a:X Foo=bar", "a:=bar", "X-Foo"):
        with pytest.raises(typer.Exit):
            core_options.parse_server_headers([bad], ["a"])


def test_parse_server_headers_a_colon_in_the_value_is_not_a_prefix():
    assert core_options.parse_server_headers(["a:Authorization=Bearer x:y"], ["a"]) == {
        "a": {"Authorization": "Bearer x:y"}
    }


def test_parse_server_keys_prefix_scopes_to_one_server():
    assert core_options.parse_server_keys(["a:KA", "b:KB"], ["a", "b"]) == {"a": "KA", "b": "KB"}


def test_parse_server_keys_a_bare_key_goes_to_the_single_server():
    assert core_options.parse_server_keys(["KA"], ["a"]) == {"a": "KA"}
    # One --url without NAME: has no server name to prefix, so the key still lands on it.
    assert core_options.parse_server_keys(["KA"], [None]) == {None: "KA"}


def test_parse_server_keys_a_bare_key_fails_when_several_servers_run():
    with pytest.raises(typer.Exit):
        core_options.parse_server_keys(["KA"], ["a", "b"])


def test_parse_server_keys_an_unknown_prefix_is_part_of_the_key():
    # A colon only names a server when it is a known one, so ids like qwen3:8b and keys
    # carrying : keep their whole value.
    for value in ("qwen3:8b", "abc::"):
        assert core_options.parse_server_keys([value], ["a"]) == {"a": value}


def test_parse_server_keys_the_released_equals_form_is_not_a_prefix():
    # SERVER= no longer names a server, so with several --url it counts as unprefixed and fails.
    assert core_options.parse_server_keys(["a=KA"], ["a"]) == {"a": "a=KA"}
    with pytest.raises(typer.Exit):
        core_options.parse_server_keys(["a=KA"], ["a", "b"])


def test_parse_server_keys_a_later_key_for_the_same_server_wins():
    assert core_options.parse_server_keys(["a:KA", "a:KB"], ["a", "b"]) == {"a": "KB"}
