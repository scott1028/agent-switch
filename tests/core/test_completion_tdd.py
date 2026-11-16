# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""The ~/.bashrc launcher when agent-switch runs from its own AppImage."""

import sys

from agent_switch.core import completion


def test_launcher_uses_its_own_appimage(tmp_path, monkeypatch):
    # python-appimage sets sys.executable to the AppImage file, which is outside APPDIR.
    mount = tmp_path / "mount"
    package = mount / "opt" / "python3.12" / "lib" / "python3.12" / "site-packages"
    monkeypatch.setenv("APPIMAGE", "/opt/agent-switch")
    monkeypatch.setenv("APPDIR", str(mount))
    monkeypatch.setattr(sys, "executable", "/opt/agent-switch")
    monkeypatch.setattr(sys, "argv", [str(package / "agent_switch" / "__main__.py")])
    monkeypatch.setattr(completion, "__file__", str(package / "agent_switch" / "core" / "x.py"))
    assert completion._get_launcher() == ["/opt/agent-switch"]
