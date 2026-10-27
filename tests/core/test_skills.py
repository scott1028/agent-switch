# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""Session skill parsing, isolated materialization and owned-entry cleanup."""

import json
import os
from pathlib import Path

import pytest
import typer

from agent_switch.core import skills as skills_core


def _write_skill(path, text = "Skill body.\n"):
    path.mkdir(parents = True, exist_ok = True)
    (path / "SKILL.md").write_bytes(text.encode("utf-8"))
    return path


def _expect_fail(call, capsys):
    with pytest.raises(typer.Exit) as caught:
        call()
    assert caught.value.exit_code == 1
    return capsys.readouterr().err


@pytest.mark.parametrize("values", [None, []])
def test_no_values_load_nothing(values):
    assert skills_core.load_skills(values) is None
    assert skills_core.load_skills(values, as_subagent = True) is None


def test_plain_and_named_paths_keep_flag_order(tmp_path):
    first = _write_skill(tmp_path / "folder", "---\nname: from-frontmatter\n---\nBody.\n")
    second = _write_skill(tmp_path / "second", "---\nname: old-name\n---\nBody.\n")
    third = _write_skill(tmp_path / "basename")
    skills = skills_core.load_skills([str(first), f"renamed:{second}", str(third)])
    assert list(skills.items()) == [
        ("from-frontmatter", first), ("renamed", second), ("basename", third)
    ]


def test_home_and_relative_paths_are_absolute(tmp_path, monkeypatch):
    home_skill = _write_skill(tmp_path / "home-skill")
    relative_skill = _write_skill(tmp_path / "relative-skill")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert skills_core.load_skills(["~/home-skill", "./relative-skill"]) == {
        "home-skill": home_skill, "relative-skill": relative_skill
    }


@pytest.mark.parametrize("kind", ["missing", "file", "no-manifest", "manifest-is-dir"])
def test_invalid_source_fails_naming_path(tmp_path, capsys, kind):
    source = tmp_path / "source"
    if kind == "file":
        source.write_text("not a folder")
    elif kind != "missing":
        source.mkdir()
        if kind == "manifest-is-dir":
            (source / "SKILL.md").mkdir()
    error = _expect_fail(lambda: skills_core.load_skills([str(source)]), capsys)
    assert str(source) in error


@pytest.mark.parametrize("name", ["Bad_Name", "", "a" * 65, "-bad", "bad--name", "bad name"])
def test_invalid_inferred_name_suggests_prefix(tmp_path, capsys, name):
    source = _write_skill(tmp_path / "source", f"---\nname: {json.dumps(name)}\n---\nBody.\n")
    error = _expect_fail(lambda: skills_core.load_skills([str(source)]), capsys)
    assert "NAME:" in error
    assert name in error


def test_invalid_folder_name_suggests_prefix(tmp_path, capsys):
    source = _write_skill(tmp_path / "Bad_Folder")
    error = _expect_fail(lambda: skills_core.load_skills([str(source)]), capsys)
    assert "Bad_Folder" in error and "NAME:" in error


@pytest.mark.parametrize("name", ["Bad_Name", "a" * 65, "-bad"])
def test_invalid_explicit_name_fails(tmp_path, capsys, name):
    source = _write_skill(tmp_path / "source")
    error = _expect_fail(lambda: skills_core.load_skills([f"{name}:{source}"]), capsys)
    assert name in error


def test_name_can_be_64_characters(tmp_path):
    source = _write_skill(tmp_path / "source")
    name = "a" * 64
    assert skills_core.load_skills([f"{name}:{source}"]) == {name: source}


@pytest.mark.parametrize("explicit", [False, True])
def test_duplicate_names_fail(tmp_path, capsys, explicit):
    sources = [_write_skill(tmp_path / folder, "---\nname: repeated\n---\n")
               for folder in ["first", "second"]]
    values = [f"repeated:{source}" if explicit else str(source) for source in sources]
    assert "repeated" in _expect_fail(lambda: skills_core.load_skills(values), capsys)


def test_subagent_refusal_precedes_source_validation(capsys):
    error = _expect_fail(
        lambda: skills_core.load_skills(["missing"], as_subagent = True), capsys
    )
    assert "--skill cannot be combined with --as-subagent." in error


@pytest.mark.parametrize(("text", "name"), [
    ("---\nname: named\ndescription: Keep.\n---\nBody.\n", "named"),
    ("---\r\nname: named\r\n---\r\nBody.\r\n", "named"),
    ("Body.\n", None),
    ("Body.\n---\nname: ignored\n---\n", None),
    ("---\nname: unterminated\n", None),
    ("---\ndescription: Only.\n---\n", None),
    ("---\nname: 123\n---\n", None),
    ("---\nname: false\n---\n", None),
    ("---\n- name\n---\n", None),
    ("---\nname: foo\ndescription: Use when: X\n---\n", "foo"),
    ("\ufeff---\nname: foo\n---\nBody.\n", "foo"),
])
def test_get_skill_name_reads_only_leading_frontmatter(tmp_path, text, name):
    assert skills_core.get_skill_name(_write_skill(tmp_path / "source", text)) == name


def test_frontmatter_still_invalid_after_colon_retry_fails(tmp_path, capsys):
    source = _write_skill(tmp_path / "source", "---\nname: [unclosed\ndescription: Use when: X\n---\n")
    error = _expect_fail(lambda: skills_core.load_skills([str(source)]), capsys)
    assert "Could not parse skill frontmatter" in error and str(source) in error


@pytest.mark.parametrize("text", ["Body.\n", "---\nname: linked\n---\nBody.\n"])
def test_write_skills_links_source_and_exposes_live_edits(tmp_path, text):
    source = _write_skill(tmp_path / "source", text)
    root = tmp_path / "session" / "skills"
    assert skills_core.write_skills(root, {"linked": source}) == {"linked": root / "linked"}
    assert (root / "linked").is_symlink()
    assert (root / "linked").resolve() == source
    assert (source / "SKILL.md").read_bytes() == text.encode("utf-8")
    (source / "SKILL.md").write_text("Live edit.\n")
    assert (root / "linked" / "SKILL.md").read_text() == "Live edit.\n"
    manifest = root / ".agent-switch-skills.json"
    assert json.loads(manifest.read_text()) == {"entries": ["linked"]}
    if os.name != "nt":
        assert manifest.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("skills", [None, {}])
@pytest.mark.parametrize("has_root", [False, True])
def test_empty_write_without_manifest_changes_nothing(tmp_path, skills, has_root):
    root = tmp_path / "session"
    if has_root:
        root.mkdir()
        (root / "user-owned.txt").write_text("Keep me.\n")
    assert skills_core.write_skills(root, skills) == {}
    assert root.exists() is has_root
    if has_root:
        assert list(root.iterdir()) == [root / "user-owned.txt"]
        assert (root / "user-owned.txt").read_text() == "Keep me.\n"


def test_rename_changes_only_top_level_name_line_and_links_siblings(tmp_path):
    text = ("---\r\n# Keep this comment.\r\nname: old-name # Old label.\r\n"
            "description: café\r\nnested:\r\n  name: nested-name\r\n---\r\n"
            "name: body-name\r\nBody without final newline.")
    source = _write_skill(tmp_path / "source", text)
    (source / "scripts").mkdir()
    (source / "scripts" / "run.py").write_text("print('hello')\n")
    (source / "notes.txt").write_text("Notes.\n")
    root = tmp_path / "session"
    entry = skills_core.write_skills(root, {"renamed": source})["renamed"]
    assert entry.is_dir() and not entry.is_symlink()
    assert (entry / "SKILL.md").read_bytes() == text.replace(
        "name: old-name # Old label.", "name: renamed", 1
    ).encode("utf-8")
    assert (source / "SKILL.md").read_bytes() == text.encode("utf-8")
    for sibling in ["scripts", "notes.txt"]:
        assert (entry / sibling).is_symlink()
        assert (entry / sibling).resolve() == source / sibling
    if os.name != "nt":
        assert entry.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(("prefix", "text", "expected"), [
    ("renamed:", "---\ndescription: Keep.\n---\nBody.\n", "---\nname: renamed\ndescription: Keep.\n---\nBody.\n"),
    ("renamed:", "---\n---\nBody.\n", "---\nname: renamed\n---\nBody.\n"),
    ("", "---\r\ndescription: Keep.\r\n---\r\n", "---\r\nname: source\r\ndescription: Keep.\r\n---\r\n"),
    ("", "---\nname: 123\nnested:\n  name: inner\n---\n", "---\nname: source\nnested:\n  name: inner\n---\n"),
])
def test_write_skills_writes_missing_frontmatter_name(tmp_path, prefix, text, expected):
    source = _write_skill(tmp_path / "source", text)
    skills = skills_core.load_skills([f"{prefix}{source}"])
    name = next(iter(skills))
    entry = skills_core.write_skills(tmp_path / "session", skills)[name]
    assert not entry.is_symlink()
    assert (entry / "SKILL.md").read_bytes() == expected.encode("utf-8")
    assert skills_core.get_skill_name(entry) == name


def test_rename_keeps_leading_byte_order_mark(tmp_path):
    text = "\ufeff---\nname: old-name\n---\nBody.\n"
    source = _write_skill(tmp_path / "source", text)
    entry = skills_core.write_skills(tmp_path / "session", {"renamed": source})["renamed"]
    assert (entry / "SKILL.md").read_bytes() == text.replace("old-name", "renamed").encode("utf-8")
    assert skills_core.get_skill_name(entry) == "renamed"


@pytest.mark.parametrize("cleared", [None, {}])
def test_repeated_writes_remove_only_owned_entries_and_clear(tmp_path, cleared):
    first = _write_skill(tmp_path / "first")
    second = _write_skill(tmp_path / "second", "---\nname: original\n---\nBody.\n")
    root = tmp_path / "session"
    skills_core.write_skills(root, {"first": first})
    unlisted = root / "user-owned.txt"
    unlisted.write_text("Keep me.\n")
    assert skills_core.write_skills(root, {"second": second}) == {"second": root / "second"}
    assert not os.path.lexists(root / "first")
    assert unlisted.read_text() == "Keep me.\n"
    assert skills_core.write_skills(root, cleared) == {}
    assert not os.path.lexists(root / "second")
    assert unlisted.read_text() == "Keep me.\n"
    assert (first / "SKILL.md").is_file() and (second / "SKILL.md").is_file()
    assert json.loads((root / ".agent-switch-skills.json").read_text()) == {"entries": []}


@pytest.mark.parametrize("kind", ["file", "directory", "dangling-link"])
def test_unlisted_clash_fails_without_touching_entry(tmp_path, capsys, kind):
    source = _write_skill(tmp_path / "source")
    root = tmp_path / "session"
    root.mkdir()
    target = root / "occupied"
    if kind == "directory":
        target.mkdir()
    elif kind == "dangling-link":
        target.symlink_to(tmp_path / "missing")
    else:
        target.write_text("Keep me.\n")
    error = _expect_fail(lambda: skills_core.write_skills(root, {"occupied": source}), capsys)
    assert "occupied" in error
    assert os.path.lexists(target)
    if kind == "file":
        assert target.read_text() == "Keep me.\n"


@pytest.mark.parametrize("renamed", [False, True])
def test_symlink_failure_copies_directory_and_file_siblings(tmp_path, monkeypatch, renamed):
    source = _write_skill(tmp_path / "source", "---\nname: original\n---\nBody.\n")
    (source / "scripts").mkdir()
    (source / "scripts" / "run.py").write_text("print('hello')\n")
    (source / "notes.txt").write_text("Notes.\n")

    def refuse_symlink(*args, **kwargs):
        raise OSError("Symlink unavailable.")

    monkeypatch.setattr(Path, "symlink_to", refuse_symlink)
    monkeypatch.setattr(skills_core, "_create_directory_junction", lambda *args: False)
    name = "renamed" if renamed else "original"
    entry = skills_core.write_skills(tmp_path / "session", {name: source})[name]
    assert not entry.is_symlink() and not (entry / "scripts").is_symlink()
    assert (entry / "scripts" / "run.py").read_text() == "print('hello')\n"
    assert (entry / "notes.txt").read_text() == "Notes.\n"
    assert skills_core.get_skill_name(entry) == name
    assert skills_core.get_skill_name(source) == "original"


def test_directory_junction_is_used_before_copy(tmp_path, monkeypatch):
    source = _write_skill(tmp_path / "source")
    symlink_to = Path.symlink_to
    calls = []

    def refuse_symlink(*args, **kwargs):
        raise OSError("Symlink unavailable.")

    def create_junction(source, target):
        calls.append((source, target))
        symlink_to(target, source, target_is_directory = True)
        return True

    monkeypatch.setattr(Path, "symlink_to", refuse_symlink)
    monkeypatch.setattr(skills_core, "_create_directory_junction", create_junction)
    entry = skills_core.write_skills(tmp_path / "session", {"linked": source})["linked"]
    assert calls == [(source, entry)]
    assert entry.resolve() == source


@pytest.mark.parametrize("manifest", ["not json", '{"entries": ["../source"]}', '{"entries": null}'])
def test_invalid_manifest_never_removes_source_or_unlisted_entries(tmp_path, capsys, manifest):
    source = _write_skill(tmp_path / "source")
    root = tmp_path / "session"
    root.mkdir()
    (root / ".agent-switch-skills.json").write_text(manifest)
    (root / "user-owned.txt").write_text("Keep me.\n")
    error = _expect_fail(lambda: skills_core.write_skills(root, None), capsys)
    assert ".agent-switch-skills.json" in error
    assert (root / ".agent-switch-skills.json").read_text() == manifest
    assert (root / "user-owned.txt").read_text() == "Keep me.\n"
    assert (source / "SKILL.md").read_text() == "Skill body.\n"


def test_skills_overlay_filters_names_and_rebuilds_without_writing_source(tmp_path):
    source = tmp_path / "user-skills"
    _write_skill(source / "shared", "---\nname: another-name\n---\nUSER\n")
    _write_skill(source / "different-folder", "---\nname: shared\n---\nUSER\n")
    kept = _write_skill(source / "kept")
    (source / "notes.txt").write_text("User notes.\n")
    manifest = source / ".agent-switch-skills.json"
    manifest.write_text('{"entries": ["kept"]}\n')
    before = {str(path): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    injected = _write_skill(tmp_path / "injected", "---\nname: shared\n---\nINJECTED\n")
    root = tmp_path / "overlay"
    root.symlink_to(source, target_is_directory = True)
    assert skills_core.write_skills_overlay(root, source, {"shared": injected}) == {"shared": root / "shared"}
    assert root.is_dir() and not root.is_symlink()
    assert (root / "shared").resolve() == injected
    assert not (root / "different-folder").exists()
    assert (root / "kept").resolve() == kept
    assert (root / "notes.txt").is_symlink()
    assert not (root / ".agent-switch-skills.json").is_symlink()
    (root / "obsolete.txt").write_text("Old session entry.\n")
    skills_core.write_skills_overlay(root, source, {"renamed": injected})
    assert not (root / "obsolete.txt").exists()
    assert (root / "shared").resolve() == source / "shared"
    assert skills_core.get_skill_name(root / "renamed") == "renamed"
    assert {str(path): path.read_bytes() for path in source.rglob("*") if path.is_file()} == before


def test_skills_overlay_links_unparseable_user_skills(tmp_path, capsys):
    source = tmp_path / "user-skills"
    invalid_yaml = _write_skill(source / "invalid-yaml", "---\nname: [unclosed\n---\nUSER\n")
    non_utf8 = source / "non-utf8"
    non_utf8.mkdir()
    (non_utf8 / "SKILL.md").write_bytes(b"---\nname: \xff\n---\nUSER\n")
    injected = _write_skill(tmp_path / "injected")
    root = tmp_path / "overlay"
    assert skills_core.write_skills_overlay(root, source, {"injected": injected}) == {"injected": root / "injected"}
    assert (root / "invalid-yaml").resolve() == invalid_yaml
    assert (root / "non-utf8").resolve() == non_utf8
    assert (root / "injected").resolve() == injected
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("has_source", [False, True])
def test_skills_overlay_accepts_no_source_or_missing_source(tmp_path, has_source):
    source = tmp_path / "missing" if has_source else None
    injected = _write_skill(tmp_path / "injected")
    root = tmp_path / "session" / "skills"
    assert skills_core.write_skills_overlay(root, source, {"injected": injected}) == {"injected": root / "injected"}
    assert (root / "injected").resolve() == injected


@pytest.mark.parametrize("kind", ["empty-dir", "installed-dir", "file"])
def test_skills_overlay_refuses_unowned_entries(tmp_path, capsys, kind):
    source = _write_skill(tmp_path / "injected")
    root = tmp_path / "session-skills"
    if kind == "file":
        root.write_text("Keep this file.\n")
    else:
        root.mkdir()
        if kind == "installed-dir":
            _write_skill(root / "session-installed", "Keep this skill.\n")
    error = _expect_fail(lambda: skills_core.write_skills_overlay(root, None, {"injected": source}), capsys)
    assert str(root) in error
    assert not (root / "injected").exists()
    if kind == "file":
        assert root.read_text() == "Keep this file.\n"
    elif kind == "installed-dir":
        assert (root / "session-installed" / "SKILL.md").read_text() == "Keep this skill.\n"
    else:
        assert list(root.iterdir()) == []


def test_skills_overlay_accepts_a_directory_junction(tmp_path, monkeypatch):
    source = _write_skill(tmp_path / "injected")
    root = tmp_path / "junction"
    root.mkdir()
    monkeypatch.setattr(skills_core, "_is_junction", lambda path: path == root)
    assert skills_core.write_skills_overlay(root, None, {"injected": source}) == {"injected": root / "injected"}


@pytest.mark.parametrize("name", ["original", "renamed"])
def test_copy_skill_includes_all_siblings_and_cleans_only_owned_entries(tmp_path, name):
    source = tmp_path / "source"
    (source / "scripts" / "nested").mkdir(parents = True)
    original = b"---\r\nname: original\r\n---\r\nBody.\r\n"
    (source / "SKILL.md").write_bytes(original)
    (source / "scripts" / "nested" / "run.py").write_text("print('original')\n")
    (source / "notes.txt").write_text("Notes.\n")
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    root = tmp_path / "skills"
    entry = skills_core.write_skills(root, {name: source}, should_copy = True)[name]
    assert entry.is_dir() and not entry.is_symlink()
    assert all(not path.is_symlink() for path in entry.rglob("*"))
    assert (entry / "SKILL.md").read_bytes() == original.replace(b"name: original", f"name: {name}".encode())
    assert (entry / "scripts" / "nested" / "run.py").read_text() == "print('original')\n"
    assert (entry / "notes.txt").read_text() == "Notes.\n"
    assert {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()} == before
    (source / "notes.txt").write_text("Live edit.\n")
    assert (entry / "notes.txt").read_text() == "Notes.\n"
    (root / "user-owned.txt").write_text("Keep.\n")
    skills_core.write_skills(root, {name: source}, should_copy = True)
    assert (entry / "notes.txt").read_text() == "Live edit.\n"
    assert skills_core.write_skills(root, None, should_copy = True) == {}
    assert not entry.exists()
    assert (root / "user-owned.txt").read_text() == "Keep.\n"
    assert json.loads((root / ".agent-switch-skills.json").read_text()) == {"entries": []}
    assert (source / "SKILL.md").read_bytes() == original


def test_copy_overlay_still_links_non_conflicting_user_skills(tmp_path):
    user_skills = tmp_path / "user-skills"
    (user_skills / "user-only").mkdir(parents = True)
    (user_skills / "user-only" / "SKILL.md").write_text("User skill.\n")
    source = tmp_path / "injected"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: injected\n---\nBody.\n")
    root = tmp_path / "session" / "skills"
    skills_core.write_skills_overlay(root, user_skills, {"injected": source}, should_copy = True)
    assert not (root / "injected").is_symlink()
    assert skills_core.get_skill_name(root / "injected") == "injected"
    assert (root / "user-only").is_symlink()
    assert (root / "user-only").resolve() == user_skills / "user-only"
    skills_core.write_skills_overlay(root, user_skills, {"renamed": source}, should_copy = True)
    assert not (root / "injected").exists()
    assert skills_core.get_skill_name(root / "renamed") == "renamed"
    assert skills_core.get_skill_name(source) == "injected"
    assert (user_skills / "user-only" / "SKILL.md").read_text() == "User skill.\n"
