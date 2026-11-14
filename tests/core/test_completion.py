# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""`--install-completion` / `--uninstall-completion`: the ~/.bashrc line and old setups."""

import os
import shutil
import subprocess
import sys

import pytest
from typer.testing import CliRunner

import agent_switch.start as start
from agent_switch.core import completion


def _invoke(*args):
    return CliRunner().invoke(start.start_app, list(args), prog_name = "agent-switch")


def _install(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Typer reads this while building the command, so `--install-completion bash` takes a value.
    monkeypatch.setenv("_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION", "1")
    result = _invoke("--install-completion", "bash")
    assert result.exit_code == 0, result.output
    return result


def test_install_is_repeatable_and_clears_old_setups(tmp_path, monkeypatch):
    legacy_script = tmp_path / ".bash_completions" / "agent-switch.sh"
    other_script = tmp_path / ".bash_completions" / "recall-engine.sh"
    manual_setup = tmp_path / ".bashrc.d" / "agent-switch-completion"
    for path in (legacy_script, other_script, manual_setup):
        path.parent.mkdir(exist_ok = True)
        path.write_text("complete -F _x x\n", encoding = "utf-8")
    rc = tmp_path / ".bashrc"
    # Typer leaves its source line plus one more blank line per run.
    rc.write_text(f"export FOO=1\n\nsource '{legacy_script}'\n\n\n", encoding = "utf-8")

    first = _install(tmp_path, monkeypatch)
    _install(tmp_path, monkeypatch)
    _install(tmp_path, monkeypatch)

    assert f"Removed old completion setup: {manual_setup}" in first.output
    text = rc.read_text(encoding = "utf-8")
    assert [line.endswith(completion._COMPLETION_MARKER) for line in text.splitlines()].count(True) == 1
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert text.startswith("export FOO=1\n")
    assert "source '" not in text
    assert not legacy_script.exists() and not manual_setup.exists()
    assert other_script.exists()


def test_uninstall_restores_the_bashrc(tmp_path, monkeypatch):
    rc = tmp_path / ".bashrc"
    rc.write_text("export FOO=1\n", encoding = "utf-8")
    _install(tmp_path, monkeypatch)
    monkeypatch.setattr(completion, "_get_shell_name", lambda: "bash")

    result = _invoke("--uninstall-completion")
    assert result.exit_code == 0, result.output
    assert rc.read_text(encoding = "utf-8") == "export FOO=1\n"
    result = _invoke("--uninstall-completion")
    assert result.exit_code == 0, result.output
    assert "No agent-switch bash completion found." in result.output


def test_uninstall_refuses_other_shells(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    rc = tmp_path / ".bashrc"
    before = rc.read_text(encoding = "utf-8")
    monkeypatch.setattr(completion, "_get_shell_name", lambda: "zsh")

    result = _invoke("--uninstall-completion")
    assert result.exit_code == 1
    assert rc.read_text(encoding = "utf-8") == before


def test_launcher_ignores_a_leaked_appimage(tmp_path, monkeypatch):
    # A parent AppImage (easy-herdr) leaks APPIMAGE/APPDIR into every shell it starts.
    monkeypatch.setenv("APPIMAGE", "/opt/easy-herdr")
    monkeypatch.setenv("APPDIR", str(tmp_path / "mount"))
    assert completion._get_launcher() != ["/opt/easy-herdr"]
    monkeypatch.setattr(sys, "executable", str(tmp_path / "mount" / "usr" / "bin" / "python3"))
    assert completion._get_launcher() == ["/opt/easy-herdr"]


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason = "needs a real bash")
def test_installed_line_loads_the_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(completion, "_get_launcher", lambda: [sys.executable, "-m", "agent_switch"])
    _install(tmp_path, monkeypatch)
    # The line's own --show-completion must detect the shell, not take a value.
    monkeypatch.delenv("_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION")

    result = subprocess.run(
        ["bash", "-ic", "complete -p agent-switch"], capture_output = True, text = True, timeout = 60
    )
    assert "_agent_switch_completion_with_aliases" in result.stdout, result.stderr
