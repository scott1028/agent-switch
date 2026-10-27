# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""Session skill folders: parse names, validate sources and materialize isolated entries."""

import os
import re
import shutil
from pathlib import Path
from typing import Optional

import yaml

from agent_switch.core.mcp import _parse_name_value
from agent_switch.core.options import _fail
from agent_switch.core.platform import _create_directory_junction, _is_junction, _remove_overlay_entry
from agent_switch.core.storage import _read_json_object, _write_private_json


_SKILL_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)^---(?:\r?\n|\Z)", re.MULTILINE | re.DOTALL)


def get_skill_name(skill_dir: Path) -> Optional[str]:
    """Read a string name from the leading SKILL.md YAML frontmatter, when present."""
    path = skill_dir / "SKILL.md"
    match = _FRONTMATTER_RE.match(path.read_text(encoding = "utf-8"))
    if match is None:
        return None
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        _fail(f"Could not parse skill frontmatter in {path}: {exc}")
    name = metadata.get("name") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) else None


def load_skills(values: Optional[list], *, as_subagent: bool = False) -> Optional[dict]:
    """Resolve repeated [NAME:]PATH values into ordered names and absolute source folders."""
    if not values:
        return None
    if as_subagent:
        _fail("--skill cannot be combined with --as-subagent.")
    skills = {}
    for value in values:
        given_name, path = _parse_name_value(value)
        source = Path(os.path.abspath(os.path.expanduser(path)))
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            _fail(f"Skill path {source} must be a directory holding SKILL.md.")
        name = given_name
        if name is None:
            name = get_skill_name(source)
        if name is None:
            name = source.name
        if len(name) > 64 or _SKILL_NAME_RE.fullmatch(name) is None:
            hint = " Use NAME:PATH to give it a valid name." if given_name is None else ""
            _fail(
                f"Invalid skill name {name!r}: use lowercase letters, digits and single hyphens, "
                f"with at most 64 characters.{hint}"
            )
        if name in skills:
            _fail(f"Two injected skills are both named {name!r}; give each a distinct NAME: prefix.")
        skills[name] = source
    return skills


def _link_skill_entry(source: Path, target: Path) -> None:
    try:
        target.symlink_to(source, target_is_directory = source.is_dir())
    except OSError:
        if source.is_dir():
            if not _create_directory_junction(source, target):
                shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def write_skills(root: Path, skills: Optional[dict], *, should_copy: bool = False) -> dict:
    """Replace only manifest-owned entries with this session's injected skill folders."""
    manifest_path = root / ".agent-switch-skills.json"
    if not skills and not manifest_path.exists():
        return {}
    previous = _read_json_object(manifest_path)
    if previous is None:
        _fail(f"Could not read injected skills manifest {manifest_path}.")
    managed = previous.get("entries", [])
    if not isinstance(managed, list) or any(
        not isinstance(name, str) or _SKILL_NAME_RE.fullmatch(name) is None for name in managed
    ):
        _fail(f"Invalid injected skills manifest {manifest_path}.")
    for name in managed:
        _remove_overlay_entry(root / name)
    root.mkdir(parents = True, exist_ok = True, mode = 0o700)
    for name in skills or {}:
        if os.path.lexists(root / name):
            _fail(f"Cannot inject skill {name!r}: unlisted entry {root / name} already exists.")
    entries = {}
    for name, source in (skills or {}).items():
        target = root / name
        source_name = get_skill_name(source)
        if source_name is None or source_name == name:
            if should_copy:
                shutil.copytree(source, target)
            else:
                _link_skill_entry(source, target)
        else:
            target.mkdir(mode = 0o700)
            # Read bytes to preserve every newline and non-name byte in the copied SKILL.md.
            text = (source / "SKILL.md").read_bytes().decode("utf-8")
            start, end = _FRONTMATTER_RE.match(text).span(1)
            frontmatter = re.sub(
                r"^name[ \t]*:[^\r\n]*", f"name: {name}", text[start:end],
                count = 1, flags = re.MULTILINE,
            )
            (target / "SKILL.md").write_bytes((text[:start] + frontmatter + text[end:]).encode("utf-8"))
            for sibling in source.iterdir():
                if sibling.name != "SKILL.md":
                    if should_copy:
                        if sibling.is_dir():
                            shutil.copytree(sibling, target / sibling.name)
                        else:
                            shutil.copy2(sibling, target / sibling.name)
                    else:
                        _link_skill_entry(sibling, target / sibling.name)
        entries[name] = target
    _write_private_json(manifest_path, {"entries": list(entries)})
    return entries


def write_skills_overlay(
    root: Path, source: Optional[Path], skills: dict, *, should_copy: bool = False,
) -> dict:
    """Rebuild a private skills directory, preserving only non-conflicting user skills."""
    if (
        os.path.lexists(root) and not root.is_symlink() and not _is_junction(root)
        and not (root.is_dir() and (root / ".agent-switch-skills.json").is_file())
    ):
        _fail(f"Cannot rebuild skills overlay {root}: existing entry is not managed by agent-switch.")
    _remove_overlay_entry(root)
    root.mkdir(parents = True, exist_ok = True, mode = 0o700)
    if source is not None and source.is_dir():
        for child in source.iterdir():
            if child.name == ".agent-switch-skills.json" or child.name in skills:
                continue
            if child.is_dir() and (child / "SKILL.md").is_file() and get_skill_name(child) in skills:
                continue
            _link_skill_entry(child, root / child.name)
    return write_skills(root, skills, should_copy = should_copy)
