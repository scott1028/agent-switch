# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""Bash completion setup: one ~/.bashrc line that regenerates the script in every new shell."""

import os
import shlex
import sys
from pathlib import Path

import click
import typer
from typer._completion_shared import _get_shell_name  # the detection Typer's install uses

from agent_switch.core.options import _fail

_COMPLETION_MARKER = "# agent-switch completion"


def _get_launcher() -> list[str]:
    """The command that runs this agent-switch, for the ~/.bashrc line."""
    appimage, appdir = os.environ.get("APPIMAGE"), os.environ.get("APPDIR")
    # A parent AppImage (easy-herdr) leaks both vars, so trust them only when this code is in
    # APPDIR. Not sys.executable: python-appimage sets it to the AppImage file, outside APPDIR.
    if appimage and appdir and Path(__file__).is_relative_to(appdir):
        return [appimage]
    script = Path(sys.argv[0])
    if script.suffix != ".py" and script.is_file() and os.access(script, os.X_OK):
        return [str(script.absolute())]
    return [sys.executable, "-m", "agent_switch"]


def _remove_completion(home: Path) -> list[str]:
    """Remove our ~/.bashrc line and the known older setups; return what was removed."""
    removed = []
    rc_path = home / ".bashrc"
    legacy_script = home / ".bash_completions" / "agent-switch.sh"
    if rc_path.is_file():
        # Typer's own install writes this line for its static script.
        legacy_line = f"source '{legacy_script}'"
        lines = rc_path.read_text(encoding = "utf-8").splitlines(keepends = True)
        kept = []
        for line in lines:
            if line.rstrip().endswith(_COMPLETION_MARKER) or line.rstrip() == legacy_line:
                removed.append(f"{rc_path}: {line.rstrip()}")
            else:
                kept.append(line)
        if len(kept) != len(lines):
            # Trailing blank lines go too, so repeated install/uninstall never piles them up.
            text = "".join(kept).rstrip()
            rc_path.write_text(f"{text}\n" if text else "", encoding = "utf-8")
    for path in (legacy_script, home / ".bashrc.d" / "agent-switch-completion"):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return removed


def _install_bash(**_typer_args) -> Path:
    """Replaces Typer's install_bash: a ~/.bashrc line instead of a static script."""
    home = Path.home()
    for item in _remove_completion(home):
        typer.echo(f"Removed old completion setup: {item}")
    launcher = _get_launcher()
    # -x keeps new shells quiet once agent-switch is uninstalled.
    line = (
        f"[[ $- == *i* && -x {shlex.quote(launcher[0])} ]] && "
        f"source <({shlex.join(launcher)} --show-completion)  {_COMPLETION_MARKER}"
    )
    rc_path = home / ".bashrc"
    text = rc_path.read_text(encoding = "utf-8").rstrip() if rc_path.is_file() else ""
    rc_path.write_text(f"{text}\n\n{line}\n" if text else f"{line}\n", encoding = "utf-8")
    return rc_path


def _uninstall_completion(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    shell = _get_shell_name()
    if shell != "bash":
        _fail(f"--uninstall-completion supports only bash; this shell is {shell or 'unknown'}.")
    removed = _remove_completion(Path.home())
    if not removed:
        typer.echo("No agent-switch bash completion found.")
        raise typer.Exit()
    for item in removed:
        typer.echo(f"Removed: {item}")
    typer.echo("Completion will be gone once you restart the terminal")
    raise typer.Exit()
