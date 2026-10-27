# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""Manifest-owned native config overlays with optional isolated skill injection."""

import shutil
from pathlib import Path
from typing import Optional

from agent_switch.core import platform as core_platform
from agent_switch.core.skills import write_skills_overlay
from agent_switch.core.storage import _read_json_object, _write_private_json


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


def write_native_overlay(
    overlay: Path,
    source: Path,
    *,
    manifest_name: str = ".agent-switch-native-overlay.json",
    excluded: tuple = (),
    copy_directories: Optional[set] = None,
    skills: Optional[dict] = None,
    should_copy_skills: bool = False,
) -> Path:
    """Refresh owned source links while retaining unrelated state from the same source."""
    overlay.mkdir(parents = True, exist_ok = True, mode = 0o700)
    manifest_path = overlay / manifest_name
    previous = _read_json_object(manifest_path)
    source_key = str(source.resolve(strict = False))
    if isinstance(previous, dict) and previous.get("source_home") == source_key:
        managed = previous.get("entries", [])
        for name in managed if isinstance(managed, list) else []:
            if isinstance(name, str) and name not in {"", ".", ".."} and Path(name).name == name:
                core_platform._remove_overlay_entry(overlay / name)
    else:
        # Switching source homes must not retain another user's credentials or config.
        for target in overlay.iterdir():
            core_platform._remove_overlay_entry(target)
    entries = []
    if source.is_dir():
        for child in source.iterdir():
            if (
                child.name == manifest_name or child.name in excluded
                or (skills is not None and child.name == "skills")
            ):
                continue
            if _link_overlay_entry(
                child, overlay / child.name,
                should_copy_directory = copy_directories is None or child.name in copy_directories,
            ):
                entries.append(child.name)
    if skills is not None:
        write_skills_overlay(
            overlay / "skills", source / "skills", skills, should_copy = should_copy_skills,
        )
        entries.append("skills")
    _write_private_json(manifest_path, {"source_home": source_key, "entries": sorted(entries)})
    return overlay
