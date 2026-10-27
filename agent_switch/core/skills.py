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


# Skip one leading BOM like Pi (references/pi/packages/coding-agent/src/utils/frontmatter.ts).
_FRONTMATTER_RE = re.compile(r"\A\ufeff?---\r?\n(.*?)^---(?:\r?\n|\Z)", re.MULTILINE | re.DOTALL)


def _sanitize_frontmatter(frontmatter: str) -> str:
    lines = []
    for line in re.split(r"\r?\n", frontmatter):
        entry = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)", line)
        value = entry.group(2).strip() if entry else ""
        if value in ("", ">", "|") or value.startswith(("\"", "'")) or ":" not in value:
            lines.append(line)
        else:
            lines.extend([f"{entry.group(1)}: |-", f"  {value}"])
    return "\n".join(lines)


def get_skill_name(skill_dir: Path, *, should_fail: bool = True) -> Optional[str]:
    """Read a string name from the leading SKILL.md YAML frontmatter, when present."""
    path = skill_dir / "SKILL.md"
    try:
        text = path.read_text(encoding = "utf-8")
    except (OSError, UnicodeDecodeError):
        if should_fail:
            raise
        return None
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        # Agents accept unquoted colons in values; retry them as block scalars like OpenCode's
        # sanitize (references/opencode/packages/core/src/config/markdown.ts).
        try:
            metadata = yaml.safe_load(_sanitize_frontmatter(match.group(1)))
        except yaml.YAMLError:
            if not should_fail:
                return None
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
        # Read bytes to preserve every newline and non-name byte in the copied SKILL.md.
        text = (source / "SKILL.md").read_bytes().decode("utf-8")
        match = _FRONTMATTER_RE.match(text)
        if match is None or get_skill_name(source) == name:
            if should_copy:
                shutil.copytree(source, target)
            else:
                _link_skill_entry(source, target)
        else:
            target.mkdir(mode = 0o700)
            start, end = match.span(1)
            frontmatter, count = re.subn(
                r"^name[ \t]*:[^\r\n]*", f"name: {name}", text[start:end],
                count = 1, flags = re.MULTILINE,
            )
            if count == 0:
                newline = "\r\n" if text[:start].endswith("\r\n") else "\n"
                frontmatter = f"name: {name}{newline}{frontmatter}"
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
            # Like Pi and Codex, a broken user skill only loses its name; it never stops the launch.
            if (
                child.is_dir() and (child / "SKILL.md").is_file()
                and get_skill_name(child, should_fail = False) in skills
            ):
                continue
            _link_skill_entry(child, root / child.name)
    return write_skills(root, skills, should_copy = should_copy)
