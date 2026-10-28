# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""Session skill folders: parse names, validate sources and materialize isolated entries."""

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import typer
import yaml

from agent_switch.core.install import _probe_env, _which_with_install_dirs
from agent_switch.core.launch import _resolved_launch_command
from agent_switch.core.mcp import _URL_SCHEME_RE, _parse_name_value
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


def _find_skill_dirs(root: Path, *, should_follow_links: bool = False) -> list:
    """List folders holding SKILL.md top-down, so nothing nested inside a skill counts."""
    # os.path.isdir answers False for any OSError, so an unreadable parent keeps the set's message.
    if not os.path.isdir(root):
        return []
    folders = []
    seen = set()
    # An unreadable folder hides skills, so a partial scan must stop with its path named.
    for current, dirnames, filenames in os.walk(
        root,
        onerror = lambda exc: _fail(f"Cannot search skill folder {exc.filename}; "
                                    "fix its permissions, or pass a narrower folder."),
        followlinks = should_follow_links,
    ):
        # A followed link can reach a folder that was already walked, so skip any repeat real path.
        real = os.path.realpath(current)
        if real in seen:
            dirnames.clear()
            continue
        seen.add(real)
        dirnames.sort()
        if "SKILL.md" in filenames:
            folders.append(Path(current))
            dirnames.clear()
    return folders


def _download_skill(url: str, *, is_skill_set: bool = False) -> Path:
    """Fetch a degit URL into a private per-user temp cache, reusing a folder downloaded earlier."""
    label = "skill set" if is_skill_set else "skill"
    flag = "--skill-set" if is_skill_set else "--skill"
    leaf = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    if leaf.endswith(".git"):
        leaf = leaf[:-4]
    # The URL's last path segment is the fallback skill name, kept folder-safe.
    leaf = re.sub(r"[^A-Za-z0-9._-]", "-", leaf).strip(".-") or "skill"
    # A shared /tmp needs one private root per user; the Windows temp dir is already per-user.
    suffix = f"-{os.getuid()}" if hasattr(os, "getuid") else ""
    root = Path(tempfile.gettempdir()) / f"agent-switch-skills{suffix}"
    is_unsafe = False
    try:
        # A root that someone else owns, opened up, is a symlink, or is not a folder at all
        # can carry a planted skill, so verify it before trusting anything under it.
        root.mkdir(mode = 0o700, exist_ok = True)
        if hasattr(os, "getuid"):
            status = os.lstat(root)
            is_unsafe = bool(
                stat.S_ISLNK(status.st_mode)
                or status.st_uid != os.getuid()
                or status.st_mode & 0o077
            )
    except OSError:
        is_unsafe = True
    if is_unsafe:
        _fail(
            f"Cannot use skill cache {root}: it must be a private folder you own. "
            "Remove it, or set TMPDIR to a private folder, then re-run."
        )
    # A skill set hashes under its own key so one URL never shares a cache between the flags.
    cache_key = f"skill-set:{url}" if is_skill_set else url
    target = root / hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:12] / leaf
    if target.is_dir():
        typer.echo(f"Using cached {label} {url} from {target}; delete that folder to download it again.")
        return target
    degit = _which_with_install_dirs("degit")
    if degit is None:
        _fail(f"{flag} {url} needs degit. Install it with `npm install -g degit`, then re-run.")
    target.parent.mkdir(mode = 0o700, exist_ok = True)
    # Download into a fresh empty folder, then rename, so a failed run never looks like a cache.
    partial = Path(tempfile.mkdtemp(dir = target.parent))
    # The shim resolver keeps a raw & in the URL out of cmd.exe, and the probe env carries the
    # augmented PATH, so a degit with node in an install dir runs as _which_with_install_dirs found it.
    env = _probe_env()
    try:
        subprocess.run(_resolved_launch_command(degit, [url, str(partial)], env), env = env, check = True)
    except (OSError, subprocess.CalledProcessError):
        shutil.rmtree(partial, ignore_errors = True)
        _fail(f"degit could not download {label} {url}.")
    if is_skill_set:
        flat = None
        try:
            folders = _find_skill_dirs(partial)
            if not folders or folders[0] == partial:
                _fail(
                    f"degit downloaded {url}, but it holds no nested skill folder. "
                    "Use --skill for a single skill."
                )
            # Flatten every skill folder to one level, so the cache holds only skills and drops
            # the set root's extra files, like README.md.
            flat = Path(tempfile.mkdtemp(dir = target.parent))
            dropped = {}
            landed = {}
            base = url.rstrip("/")
            for folder in folders:
                # Name the download content by its URL path, since the temp folder is deleted.
                source = f"{base}/{folder.relative_to(partial).as_posix()}"
                # Degit can land a broken SKILL.md symlink, so check it like load_skills does.
                if not (folder / "SKILL.md").is_file():
                    _fail(f"Skill path {source} must be a directory holding SKILL.md.")
                # An unreadable SKILL.md must name itself, not the publish step that follows.
                try:
                    name = get_skill_name(folder)
                except OSError:
                    _fail(f"Cannot read {source}/SKILL.md.")
                # Land each skill under the name load_skills will give, so source order decides.
                if name is None:
                    name = folder.name
                if len(name) > 64 or _SKILL_NAME_RE.fullmatch(name) is None:
                    _fail(
                        f"Invalid skill name {name!r}: use lowercase letters, digits and "
                        "single hyphens, with at most 64 characters. Rename it upstream."
                    )
                key = name.casefold()
                if key in landed:
                    # The later skill of the same name wins: the sorted source-path walk decides.
                    shutil.rmtree(flat / landed[key][0], ignore_errors = True)
                    dropped.setdefault(name, []).append(landed[key][1])
                landed[key] = (name, folder.relative_to(partial))
                folder.replace(flat / name)
            flat.replace(target)
            # The first download is the only run that knows these drops; cache runs show nothing.
            for name, losers in dropped.items():
                losers_text = ", ".join(f"{base}/{loser.as_posix()}" for loser in reversed(losers))
                typer.echo(
                    f"agent-switch: skill set {url}: skill {name} uses {target / name}; "
                    f"dropped {losers_text}.",
                    err = True,
                )
        except OSError:
            # A concurrent run may have published the same URL first; its cache wins.
            if not target.is_dir():
                _fail(f"Cannot publish the downloaded skill set {url} under {target}.")
        finally:
            for tree in (partial, flat):
                if tree is not None:
                    shutil.rmtree(tree, ignore_errors = True)
        return target
    if not (partial / "SKILL.md").is_file():
        shutil.rmtree(partial, ignore_errors = True)
        _fail(f"degit downloaded {url}, but the folder has no SKILL.md.")
    partial.replace(target)
    return target


def load_skills(
    values: Optional[list], skill_sets: Optional[list] = None, *, as_subagent: bool = False,
) -> Optional[dict]:
    """Resolve [NAME:]PATH|URL skills and [NS:]PATH|URL skill sets; the later skill wins a name."""
    if not values and not skill_sets:
        return None
    if as_subagent:
        flag = "--skill" if values else "--skill-set"
        _fail(f"{flag} cannot be combined with --as-subagent.")
    sources = []
    # Sets come first, in flag order, so a later set beats an earlier one and every --skill
    # beats them all. Inside one set the sorted walk order decides.
    for value in skill_sets or []:
        if _URL_SCHEME_RE.match(value):
            # A bare URL is the whole path; only NS:URL carries a namespace.
            namespace, path = None, value
        else:
            namespace, path = _parse_name_value(value)
        if not path:
            # A bare NS: or an unset shell variable must never search the current folder.
            if namespace is not None:
                _fail(f"--skill-set {value!r} needs a folder or URL after the namespace.")
            _fail(f"--skill-set {value!r} needs a folder or URL.")
        if namespace is not None and (
            len(namespace) > 62 or _SKILL_NAME_RE.fullmatch(namespace) is None
        ):
            _fail(
                f"Invalid --skill-set namespace {namespace!r}: use lowercase letters, "
                "digits and single hyphens."
            )
        if _URL_SCHEME_RE.match(path):
            root = _download_skill(path, is_skill_set = True)
            folders = _find_skill_dirs(root)
        else:
            root = Path(os.path.abspath(os.path.expanduser(path)))
            # A local set is used where it is, so follow folder links into skills elsewhere.
            folders = _find_skill_dirs(root, should_follow_links = True)
        if not folders or folders[0] == root:
            _fail(f"Skill set {root} must be a folder holding nested skills; use --skill for a single skill.")
        sources.extend((namespace, folder, True) for folder in folders)
    for value in values or []:
        if _URL_SCHEME_RE.match(value):
            # A bare URL is the whole path; only NAME:URL carries a name.
            given_name, path = None, value
        else:
            given_name, path = _parse_name_value(value)
        if _URL_SCHEME_RE.match(path):
            source = _download_skill(path)
        else:
            source = Path(os.path.abspath(os.path.expanduser(path)))
        sources.append((given_name, source, False))
    skills = {}
    dropped = {}
    for given_name, source, from_set in sources:
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            _fail(f"Skill path {source} must be a directory holding SKILL.md.")
        name = given_name if not from_set else None
        if name is None:
            name = get_skill_name(source)
            if name is None:
                name = source.name
        # A set skill keeps its own name; an NS: prefix lands in front of it.
        if from_set and given_name is not None:
            name = f"{given_name}-{name}"
        if len(name) > 64 or _SKILL_NAME_RE.fullmatch(name) is None:
            if from_set:
                # The set keeps failing while it holds the skill, so only an upstream rename helps.
                hint = " Rename it upstream."
            else:
                hint = " Use NAME:PATH to give it a valid name." if given_name is None else ""
            _fail(
                f"Invalid skill name {name!r}: use lowercase letters, digits and single hyphens, "
                f"with at most 64 characters.{hint}"
            )
        if name in skills:
            # The later skill replaces the earlier one whole, never a mix of the two folders.
            dropped.setdefault(name, []).append(skills[name])
        skills[name] = source
    for name, losers in dropped.items():
        winner = os.path.realpath(skills[name])
        # A folder passed twice, or by overlapping sets, is not a real drop.
        losers = [loser for loser in losers if os.path.realpath(loser) != winner]
        if not losers:
            continue
        typer.echo(
            f"agent-switch: skill {name} uses {skills[name]}; "
            f"dropped {', '.join(str(loser) for loser in reversed(losers))}.",
            err = True,
        )
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
