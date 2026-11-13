# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""Linking the user's own session stores into a session or native overlay."""

import json
import os
import stat
from pathlib import Path

import pytest

from agent_switch.core import overlay as core_overlay
from agent_switch.core.overlay import link_user_store, write_native_overlay


def _refuse_symlinks(monkeypatch):
    def symlink_to(self, target, target_is_directory = False):
        raise PermissionError("no symlinks here")

    monkeypatch.setattr(Path, "symlink_to", symlink_to)


def test_link_user_store_creates_a_missing_source(tmp_path):
    source, target = tmp_path / "user" / "sessions", tmp_path / "session" / "sessions"
    target.parent.mkdir()
    assert link_user_store(source, target)
    # The first run already writes into the user's store, never into the session dir.
    assert source.is_dir() and stat.S_IMODE(source.stat().st_mode) == 0o700
    assert target.is_symlink() and target.resolve() == source
    key, key_target = tmp_path / "user" / "key", tmp_path / "session" / "key"
    assert link_user_store(key, key_target, is_file = True)
    assert key.is_file() and stat.S_IMODE(key.stat().st_mode) == 0o600
    assert key_target.resolve() == key


def test_link_user_store_leaves_a_correct_link_alone(tmp_path, monkeypatch):
    source, target = tmp_path / "sessions", tmp_path / "session-sessions"
    source.mkdir()
    target.symlink_to(source, target_is_directory = True)
    _refuse_symlinks(monkeypatch)
    assert link_user_store(source, target)
    assert target.is_symlink() and target.resolve() == source


def test_link_user_store_moves_real_history_aside_and_drops_an_empty_dir(tmp_path, capsys):
    source = tmp_path / "user" / "sessions"
    source.mkdir(parents = True)
    target = tmp_path / "session" / "sessions"
    (target / "project").mkdir(parents = True)
    (target / "project" / "old.jsonl").write_text("old\n")
    (target.parent / "sessions.agent-switch-old").mkdir()
    assert link_user_store(source, target)
    assert target.resolve() == source
    # The earlier history is kept beside the link, and the user is told where.
    moved = target.parent / "sessions.agent-switch-old-1"
    assert (moved / "project" / "old.jsonl").read_text() == "old\n"
    assert str(moved) in capsys.readouterr().err
    empty = tmp_path / "session" / "blobs"
    empty.mkdir()
    assert link_user_store(tmp_path / "user" / "blobs", empty)
    assert empty.is_symlink()
    assert sorted(path.name for path in target.parent.iterdir()) == [
        "blobs", "sessions", "sessions.agent-switch-old", "sessions.agent-switch-old-1",
    ]


def test_link_user_store_never_copies(tmp_path, monkeypatch):
    source = tmp_path / "sessions"
    (source / "project").mkdir(parents = True)
    target = tmp_path / "session-sessions"
    _refuse_symlinks(monkeypatch)
    monkeypatch.setattr(core_overlay.core_platform, "_create_directory_junction", lambda *args: False)
    assert not link_user_store(source, target)
    assert not os.path.lexists(target)


def test_link_user_store_falls_back_to_a_hard_link_for_a_file(tmp_path, monkeypatch):
    source, target = tmp_path / "history.jsonl", tmp_path / "session-history.jsonl"
    source.write_text("mine\n")
    _refuse_symlinks(monkeypatch)
    assert link_user_store(source, target, is_file = True)
    assert not target.is_symlink() and os.path.samefile(source, target)
    # The same hard link counts as already linked.
    assert link_user_store(source, target, is_file = True)
    assert os.path.samefile(source, target)


def test_link_user_store_falls_back_to_a_junction_for_a_dir(tmp_path, monkeypatch):
    source, target = tmp_path / "sessions", tmp_path / "session-sessions"
    source.mkdir()
    _refuse_symlinks(monkeypatch)
    calls = []

    def create_junction(junction_source, junction_target):
        # Windows makes a junction here; os.symlink stands in for it.
        calls.append((junction_source, junction_target))
        os.symlink(junction_source, junction_target, target_is_directory = True)
        return True

    monkeypatch.setattr(core_overlay.core_platform, "_create_directory_junction", create_junction)
    assert link_user_store(source, target)
    assert calls == [(source, target)] and target.resolve() == source


@pytest.mark.parametrize("source_home", ["same", "other"])
def test_native_overlay_keeps_a_real_sessions_dir(tmp_path, source_home):
    source = tmp_path / "user-agent"
    (source / "sessions").mkdir(parents = True)
    (source / "config.yml").write_text("mine\n")
    overlay = tmp_path / "overlay"
    (overlay / "sessions").mkdir(parents = True)
    (overlay / "sessions" / "new.jsonl").write_text("new\n")
    # A fixed overlay from an earlier run whose agent wrote real sessions, from either source.
    manifest_source = source if source_home == "same" else tmp_path / "other-agent"
    (overlay / ".agent-switch-native-overlay.json").write_text(json.dumps({
        "source_home": str(manifest_source.resolve()), "entries": ["config.yml", "sessions"],
    }))
    write_native_overlay(overlay, source, shared = ("sessions",))
    assert (overlay / "sessions").resolve() == source / "sessions"
    assert (overlay / "sessions.agent-switch-old" / "new.jsonl").read_text() == "new\n"
    assert (overlay / "config.yml").resolve() == source / "config.yml"
    manifest = json.loads((overlay / ".agent-switch-native-overlay.json").read_text())
    assert manifest["entries"] == ["config.yml"]


@pytest.mark.parametrize("source_home", ["same", "other"])
def test_native_overlay_never_removes_history_moved_aside(tmp_path, source_home):
    source = tmp_path / "user-agent"
    (source / "sessions").mkdir(parents = True)
    overlay = tmp_path / "overlay"
    for name in ("sessions.agent-switch-old", "sessions.agent-switch-old-2"):
        (overlay / name).mkdir(parents = True)
        (overlay / name / "old.jsonl").write_text("old\n")
    manifest_source = source if source_home == "same" else tmp_path / "other-agent"
    (overlay / ".agent-switch-native-overlay.json").write_text(json.dumps({
        "source_home": str(manifest_source.resolve()), "entries": ["sessions.agent-switch-old"],
    }))
    write_native_overlay(overlay, source, shared = ("sessions",))
    for name in ("sessions.agent-switch-old", "sessions.agent-switch-old-2"):
        assert (overlay / name / "old.jsonl").read_text() == "old\n"
