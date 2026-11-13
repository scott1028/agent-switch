# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""Manifest-owned native config overlays with optional isolated skill injection."""

import contextlib
import os
import re
import shutil
from pathlib import Path
from typing import Optional

import typer

from agent_switch.core import platform as core_platform
from agent_switch.core.options import _fail
from agent_switch.core.skills import write_skills_overlay
from agent_switch.core.storage import _read_json_object, _write_private_json


# Where link_user_store moves history it found in a store's place.
_MOVED_ASIDE = re.compile(r".+\.agent-switch-old(?:-[0-9]+)?")


def _move_aside(target: Path) -> None:
    """Rename history found at target to <name>.agent-switch-old[-N]; never delete it."""
    moved = target.with_name(f"{target.name}.agent-switch-old")
    index = 0
    while os.path.lexists(moved):
        index += 1
        moved = target.with_name(f"{target.name}.agent-switch-old-{index}")
    try:
        os.rename(target, moved)
    except OSError as error:
        _fail(
            f"Couldn't move {target} aside ({error}); close any agent still running from it "
            "and re-run."
        )
    typer.echo(f"Moved earlier session data from {target} to {moved}.", err = True)


def link_user_store(source: Path, target: Path, *, is_file: bool = False) -> bool:
    """Point target at the user's own store at source, so a session reads and writes the
    user's history. Never copies; False when no link could be made."""
    with contextlib.suppress(OSError):
        # samefile follows a symlink or junction and also matches a hard link.
        if os.path.samefile(source, target):
            return True
    if not source.exists():
        # Create it now, so even the first run writes into the user's store.
        try:
            source.parent.mkdir(parents = True, exist_ok = True, mode = 0o700)
            if is_file:
                os.close(os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            else:
                source.mkdir(mode = 0o700)
        except OSError:
            return False
    if target.is_symlink() or core_platform._is_junction(target):
        core_platform._remove_overlay_entry(target)
    elif target.is_dir() and not any(target.iterdir()):
        target.rmdir()
    elif target.is_file() and target.stat().st_size == 0:
        target.unlink()
    elif os.path.lexists(target):
        # History an earlier run kept here.
        _move_aside(target)
    try:
        target.symlink_to(source, target_is_directory = not is_file)
        return True
    except OSError:
        pass
    if not is_file:
        return core_platform._create_directory_junction(source, target)
    try:
        os.link(source, target)
        return True
    except OSError:
        return False


def _link_overlay_entry(source: Path, target: Path, *, should_copy_directory: bool) -> bool:
    core_platform._remove_overlay_entry(target)
    try:
        target.symlink_to(source, target_is_directory = source.is_dir())
        return True
    except OSError:
        if source.is_file():
            shutil.copy2(source, target)
            return True
        if source.is_dir():
            if core_platform._create_directory_junction(source, target):
                return True
            if should_copy_directory:
                shutil.copytree(source, target)
                return True
    return False


def remove_native_overlay(overlay: Path, *, shared: tuple = ()) -> None:
    """Remove a native overlay, keeping its history: real history at a shared name is moved
    aside first, everything else goes, and the overlay stays only while history moved aside
    is in it."""
    if overlay.is_dir() and not overlay.is_symlink() and not core_platform._is_junction(overlay):
        for name in shared:
            target = overlay / name
            if target.is_symlink() or core_platform._is_junction(target):
                continue
            if (target.is_dir() and any(target.iterdir())) or (
                target.is_file() and target.stat().st_size
            ):
                _move_aside(target)
        for entry in overlay.iterdir():
            if not _MOVED_ASIDE.fullmatch(entry.name):
                core_platform._remove_overlay_entry(entry)
        if any(overlay.iterdir()):
            return
    core_platform._remove_overlay_entry(overlay)


def _get_is_kept_entry(name: str, shared_names: set) -> bool:
    # The user's own stores and the history moved aside for them are never the manifest's.
    return name in shared_names or _MOVED_ASIDE.fullmatch(name) is not None


def write_native_overlay(
    overlay: Path,
    source: Path,
    *,
    manifest_name: str = ".agent-switch-native-overlay.json",
    excluded: tuple = (),
    copy_directories: Optional[set] = None,
    skills: Optional[dict] = None,
    should_copy_skills: bool = False,
    shared: tuple = (),
    shared_files: tuple = (),
) -> Path:
    """Refresh owned source links while retaining unrelated state from the same source. The
    shared dirs and files are the user's own session stores: always linked, never copied, and
    real history found in their place is moved aside and kept from then on."""
    overlay.mkdir(parents = True, exist_ok = True, mode = 0o700)
    manifest_path = overlay / manifest_name
    previous = _read_json_object(manifest_path)
    source_key = str(source.resolve(strict = False))
    shared_names = {*shared, *shared_files}
    if isinstance(previous, dict) and previous.get("source_home") == source_key:
        managed = previous.get("entries", [])
        for name in managed if isinstance(managed, list) else []:
            if (
                isinstance(name, str) and name not in {"", ".", ".."} and Path(name).name == name
                and not _get_is_kept_entry(name, shared_names)
            ):
                core_platform._remove_overlay_entry(overlay / name)
    else:
        # Switching source homes must not retain another user's credentials or config.
        for target in overlay.iterdir():
            if not _get_is_kept_entry(target.name, shared_names):
                core_platform._remove_overlay_entry(target)
    entries = []
    if source.is_dir():
        for child in source.iterdir():
            if (
                child.name == manifest_name or child.name in excluded
                or _get_is_kept_entry(child.name, shared_names)
                or (skills is not None and child.name == "skills")
            ):
                continue
            if _link_overlay_entry(
                child, overlay / child.name,
                should_copy_directory = copy_directories is None or child.name in copy_directories,
            ):
                entries.append(child.name)
    for name in (*shared, *shared_files):
        if not link_user_store(source / name, overlay / name, is_file = name in shared_files):
            _fail(
                f"Couldn't link your {source / name} at {overlay / name}, and agent-switch "
                "never copies session history; fix that and re-run."
            )
    if skills is not None:
        write_skills_overlay(
            overlay / "skills", source / "skills", skills, should_copy = should_copy_skills,
        )
        entries.append("skills")
    _write_private_json(manifest_path, {"source_home": source_key, "entries": sorted(entries)})
    return overlay
