# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See LICENSE

"""Session skill parsing, isolated materialization and owned-entry cleanup."""

import hashlib
import json
import os
import subprocess
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
    assert skills_core.load_skills(values, values) is None


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


_PROBE_ENV_SENTINEL = {"PATH": "probe-env"}


def _fake_degit(
    monkeypatch, tmp_path, *, degit = "/usr/bin/degit", returncode = 0, skill_md = True, files = None,
):
    calls = []

    def fake_run(command, *args, **kwargs):
        calls.append((command, kwargs))
        destination = Path(command[-1])
        if returncode:
            # Leave a partial download at the destination, the way a killed degit would.
            (destination / "partial.txt").write_text("Half a download.\n")
        elif skill_md:
            (destination / "SKILL.md").write_text("Skill body.\n")
        else:
            (destination / "README.md").write_text("No skill here.\n")
        for name, text in (files or {}).items():
            path = destination / name
            path.parent.mkdir(parents = True, exist_ok = True)
            path.write_text(text)
        if returncode and kwargs.get("check"):
            raise subprocess.CalledProcessError(returncode, command)
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(skills_core.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(skills_core, "_which_with_install_dirs", lambda name: degit)
    monkeypatch.setattr(skills_core, "_probe_env", lambda **extra: _PROBE_ENV_SENTINEL)
    monkeypatch.setattr(skills_core.subprocess, "run", fake_run)
    return calls


def _cache_root(tmp_path):
    suffix = f"-{os.getuid()}" if hasattr(os, "getuid") else ""
    return tmp_path / f"agent-switch-skills{suffix}"


def _cached_target(tmp_path, url, leaf):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return _cache_root(tmp_path) / digest / leaf


def _cached_set_target(tmp_path, url, leaf):
    digest = hashlib.sha256(f"skill-set:{url}".encode("utf-8")).hexdigest()[:12]
    return _cache_root(tmp_path) / digest / leaf


def test_url_skill_downloads_once_then_uses_cache(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/remote-skill"
    target = _cached_target(tmp_path, url, "remote-skill")
    calls = _fake_degit(monkeypatch, tmp_path)
    assert skills_core.load_skills([url]) == {"remote-skill": target}
    assert [command[:2] for command, _ in calls] == [["/usr/bin/degit", url]]
    assert calls[0][1]["env"] is _PROBE_ENV_SENTINEL
    assert skills_core.load_skills([url]) == {"remote-skill": target}
    assert len(calls) == 1
    stdout = capsys.readouterr().out
    assert str(target) in stdout and "delete" in stdout


def test_named_url_skill_renames_and_passes_full_url(tmp_path, monkeypatch):
    url = "https://github.com/user/repo/tree/main/skills/remote-skill"
    calls = _fake_degit(monkeypatch, tmp_path)
    skills = skills_core.load_skills([f"renamed:{url}"])
    assert skills == {"renamed": _cached_target(tmp_path, url, "remote-skill")}
    assert [command[:2] for command, _ in calls] == [["/usr/bin/degit", url]]


def test_url_skill_runs_the_resolved_shim_command(tmp_path, monkeypatch):
    url = "https://github.com/user/repo/tree/feat&docs/skills/foo"
    calls = _fake_degit(monkeypatch, tmp_path)
    resolved = []

    def record_resolved(executable, arguments, environment = None):
        resolved.append((executable, arguments, environment))
        return ["node", "degit.js", *arguments]

    monkeypatch.setattr(skills_core, "_resolved_launch_command", record_resolved)
    assert skills_core.load_skills([url]) == {"foo": _cached_target(tmp_path, url, "foo")}
    command, kwargs = calls[0]
    assert command[:3] == ["node", "degit.js", url]
    assert Path(command[-1]).parent == _cached_target(tmp_path, url, "foo").parent
    assert resolved == [("/usr/bin/degit", [url, command[-1]], _PROBE_ENV_SENTINEL)]
    assert kwargs["env"] is _PROBE_ENV_SENTINEL


def test_missing_degit_fails_before_touching_cache(tmp_path, monkeypatch, capsys):
    calls = _fake_degit(monkeypatch, tmp_path, degit = None)
    error = _expect_fail(
        lambda: skills_core.load_skills(["https://github.com/user/repo"]), capsys
    )
    assert "npm install -g degit" in error
    assert calls == []
    # The empty private root may exist; no hash folder may be created.
    root = _cache_root(tmp_path)
    assert not root.exists() or list(root.iterdir()) == []


def test_failed_degit_leaves_no_partial_and_retries(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/remote-skill"
    target = _cached_target(tmp_path, url, "remote-skill")
    calls = _fake_degit(monkeypatch, tmp_path, returncode = 1)
    for _ in range(2):
        error = _expect_fail(lambda: skills_core.load_skills([url]), capsys)
    assert "degit could not download skill" in error
    assert not target.exists()
    assert list(target.parent.iterdir()) == []
    assert len(calls) == 2


def test_unrunnable_degit_fails_cleanly(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/remote-skill"
    target = _cached_target(tmp_path, url, "remote-skill")

    def refuse(command, *args, **kwargs):
        raise OSError("degit is not executable")

    _fake_degit(monkeypatch, tmp_path)
    monkeypatch.setattr(skills_core.subprocess, "run", refuse)
    error = _expect_fail(lambda: skills_core.load_skills([url]), capsys)
    assert "degit could not download skill" in error
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_degit_without_skill_md_never_becomes_cache(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/remote-skill"
    target = _cached_target(tmp_path, url, "remote-skill")
    calls = _fake_degit(monkeypatch, tmp_path, skill_md = False)
    for _ in range(2):
        error = _expect_fail(lambda: skills_core.load_skills([url]), capsys)
    assert "no SKILL.md" in error
    assert not target.exists()
    assert list(target.parent.iterdir()) == []
    assert len(calls) == 2


@pytest.mark.skipif(os.name == "nt", reason = "POSIX cache root ownership")
@pytest.mark.parametrize("kind", [
    "wide-mode", "symlink", "dangling-symlink", "regular-file", "foreign-owner",
])
def test_unsafe_cache_root_stops_and_never_reuses(tmp_path, monkeypatch, capsys, kind):
    url = "https://github.com/user/repo/tree/main/skills/remote-skill"
    uid = os.getuid()
    root = tmp_path / f"agent-switch-skills-{uid + 1 if kind == 'foreign-owner' else uid}"
    cached_root = root
    if kind == "symlink":
        real = tmp_path / "elsewhere"
        real.mkdir()
        os.chmod(real, 0o700)
        root.symlink_to(real, target_is_directory = True)
        cached_root = real
    elif kind == "dangling-symlink":
        root.symlink_to(tmp_path / "missing", target_is_directory = True)
    elif kind == "regular-file":
        root.write_text("Keep me.\n")
    else:
        root.mkdir()
        # Set the mode directly; mkdir mode is subject to the umask.
        os.chmod(root, 0o777 if kind == "wide-mode" else 0o700)
    if kind in ("symlink", "wide-mode", "foreign-owner"):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        _write_skill(cached_root / digest / "remote-skill")
    calls = _fake_degit(monkeypatch, tmp_path)
    if kind == "foreign-owner":
        monkeypatch.setattr(skills_core.os, "getuid", lambda: uid + 1)
    error = _expect_fail(lambda: skills_core.load_skills([url]), capsys)
    assert str(root) in error
    assert calls == []


@pytest.mark.parametrize(("url", "name"), [
    ("https://github.com/user/my-skill.git", "my-skill"),
    ("https://github.com/", "skill"),
    ("https://github.com/user/repo/..", "skill"),
    ("https://github.com/user/repo/..\\..\\x", "x"),
    ("https://github.com/user/repo/-x-.", "x"),
    ("https://github.com/user/repo/a%20b", "a-20b"),
])
def test_url_leaf_is_the_fallback_name(tmp_path, monkeypatch, url, name):
    calls = _fake_degit(monkeypatch, tmp_path)
    assert skills_core.load_skills([url]) == {name: _cached_target(tmp_path, url, name)}
    assert len(calls) == 1


def test_invalid_url_leaf_fails_with_name_hint(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/my.skill_x"
    _fake_degit(monkeypatch, tmp_path)
    target = _cached_target(tmp_path, url, "my.skill_x")
    error = _expect_fail(lambda: skills_core.load_skills([url]), capsys)
    assert "NAME:" in error and "my.skill_x" in error
    assert (target / "SKILL.md").is_file()


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
def test_duplicate_names_last_wins(tmp_path, capsys, explicit):
    sources = [_write_skill(tmp_path / folder, "---\nname: repeated\n---\n")
               for folder in ["first", "second"]]
    values = [f"repeated:{source}" if explicit else str(source) for source in sources]
    assert skills_core.load_skills(values) == {"repeated": sources[1]}
    assert capsys.readouterr().err.splitlines() == [
        f"agent-switch: skill repeated uses {sources[1]}; dropped {sources[0]}."
    ]


def test_subagent_refusal_precedes_source_validation(capsys):
    error = _expect_fail(
        lambda: skills_core.load_skills(["missing"], as_subagent = True), capsys
    )
    assert "--skill cannot be combined with --as-subagent." in error


def test_subagent_refusal_names_skill_set_flag(capsys):
    error = _expect_fail(
        lambda: skills_core.load_skills(None, ["missing"], as_subagent = True), capsys
    )
    assert "--skill-set cannot be combined with --as-subagent." in error


def test_local_skill_set_finds_every_nested_skill(tmp_path):
    root = tmp_path / "set"
    alpha = _write_skill(root / "alpha")
    (alpha / "references").mkdir()
    (alpha / "references" / "SKILL.md").write_text("Part of the skill.\n")
    beta = _write_skill(root / "group" / "beta", "---\nname: beta-name\n---\nBody.\n")
    skills = skills_core.load_skills(None, [str(root)])
    assert list(skills.items()) == [("alpha", alpha), ("beta-name", beta)]


def test_local_skill_set_follows_relative_and_absolute_symlinked_folders(tmp_path):
    _write_skill(tmp_path / "skills" / "alpha", "---\nname: linked-alpha\n---\nBody.\n")
    beta = _write_skill(tmp_path / "elsewhere" / "beta")
    root = tmp_path / "set"
    root.mkdir()
    (root / "alpha-link").symlink_to("../skills/alpha", target_is_directory = True)
    (root / "beta-link").symlink_to(beta, target_is_directory = True)
    skills = skills_core.load_skills(None, [str(root)])
    assert list(skills.items()) == [
        ("linked-alpha", root / "alpha-link"), ("beta-link", root / "beta-link")
    ]


def test_local_skill_set_symlink_loop_finds_each_skill_once(tmp_path):
    root = tmp_path / "set"
    alpha = _write_skill(root / "alpha")
    zeta = _write_skill(root / "zeta")
    (root / "loop").symlink_to(root, target_is_directory = True)
    skills = skills_core.load_skills(None, [str(root)])
    assert list(skills.items()) == [("alpha", alpha), ("zeta", zeta)]


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason = "POSIX folder modes")
def test_local_skill_set_unreadable_folder_fails(tmp_path, capsys):
    root = tmp_path / "set"
    _write_skill(root / "alpha")
    group = root / "group"
    _write_skill(group / "beta")
    group.chmod(0o000)
    try:
        error = _expect_fail(lambda: skills_core.load_skills(None, [str(root)]), capsys)
    finally:
        group.chmod(0o700)
    assert str(group) in error
    assert "fix its permissions, or pass a narrower folder." in error


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason = "POSIX folder modes")
def test_local_skill_set_unreadable_parent_gives_the_normal_message(tmp_path, capsys):
    locked = tmp_path / "locked"
    root = locked / "set"
    _write_skill(root / "alpha")
    locked.chmod(0o000)
    try:
        error = _expect_fail(lambda: skills_core.load_skills(None, [str(root)]), capsys)
    finally:
        locked.chmod(0o700)
    assert "must be a folder holding nested skills" in error
    assert "Traceback" not in error


@pytest.mark.parametrize("kind", ["root-skill", "empty", "missing"])
def test_local_skill_set_without_nested_skills_fails(tmp_path, capsys, kind):
    if kind == "root-skill":
        root = _write_skill(tmp_path / "set")
    elif kind == "empty":
        root = tmp_path / "set"
        root.mkdir()
    else:
        root = tmp_path / "missing"
    error = _expect_fail(lambda: skills_core.load_skills(None, [str(root)]), capsys)
    assert str(root) in error and "use --skill" in error


def test_skill_wins_a_set_name_clash(tmp_path, capsys):
    solo = _write_skill(tmp_path / "alpha")
    nested = _write_skill(tmp_path / "set" / "alpha")
    skills = skills_core.load_skills([str(solo)], [str(tmp_path / "set")])
    assert skills == {"alpha": solo}
    assert (nested / "SKILL.md").is_file()
    assert capsys.readouterr().err.splitlines() == [
        f"agent-switch: skill alpha uses {solo}; dropped {nested}."
    ]


def test_later_skill_set_wins_a_name_clash(tmp_path):
    first = _write_skill(tmp_path / "set-a" / "alpha")
    second = _write_skill(tmp_path / "set-b" / "alpha")
    skills = skills_core.load_skills(None, [str(tmp_path / "set-a"), str(tmp_path / "set-b")])
    assert skills == {"alpha": second}


def test_one_skill_set_later_path_wins_with_notice(tmp_path, capsys):
    root = tmp_path / "root"
    deep = _write_skill(root / "a" / "b" / "c" / "my-skill")
    top = _write_skill(root / "a" / "my-skill")
    far = _write_skill(root / "c" / "d" / "my-skill")
    assert skills_core.load_skills(None, [str(root)]) == {"my-skill": far}
    assert capsys.readouterr().err.splitlines() == [
        f"agent-switch: skill my-skill uses {far}; dropped {top}, {deep}."
    ]


def test_skill_set_invalid_name_suggests_upstream(tmp_path, capsys):
    _write_skill(tmp_path / "set" / "Bad_Name")
    error = _expect_fail(lambda: skills_core.load_skills(None, [str(tmp_path / "set")]), capsys)
    assert "Bad_Name" in error
    assert "Rename it upstream." in error
    assert "NAME:" not in error


def test_skill_set_namespace_prefixes_names(tmp_path):
    set_root = tmp_path / "set"
    alpha = _write_skill(set_root / "alpha", "---\nname: real-alpha\n---\nBody.\n")
    beta = _write_skill(set_root / "beta")
    skills = skills_core.load_skills(None, [f"ns1:{set_root}"])
    assert list(skills.items()) == [("ns1-real-alpha", alpha), ("ns1-beta", beta)]


def test_skill_set_namespace_written_skill_carries_the_prefixed_name(tmp_path):
    alpha = _write_skill(tmp_path / "set" / "alpha", "---\nname: real-alpha\n---\nBody.\n")
    skills = skills_core.load_skills(None, [f"ns1:{tmp_path / 'set'}"])
    entry = skills_core.write_skills(tmp_path / "session", skills)["ns1-real-alpha"]
    assert not entry.is_symlink()
    assert skills_core.get_skill_name(entry) == "ns1-real-alpha"
    assert (alpha / "SKILL.md").read_text() == "---\nname: real-alpha\n---\nBody.\n"


def test_skill_set_prefixed_invalid_name_fails(tmp_path, capsys):
    _write_skill(tmp_path / "set" / "Bad_Name")
    error = _expect_fail(lambda: skills_core.load_skills(None, [f"ns1:{tmp_path / 'set'}"]), capsys)
    assert "ns1-Bad_Name" in error
    assert "Rename it upstream." in error


@pytest.mark.parametrize("namespace", ["My_NS", "ns-"])
def test_skill_set_invalid_namespace_fails_early(tmp_path, capsys, namespace):
    _write_skill(tmp_path / "set" / "alpha")
    error = _expect_fail(
        lambda: skills_core.load_skills(None, [f"{namespace}:{tmp_path / 'set'}"]), capsys
    )
    assert f"Invalid --skill-set namespace {namespace!r}" in error
    assert "use lowercase letters, digits and single hyphens." in error
    assert "Rename it upstream" not in error


@pytest.mark.parametrize("value", ["ns1:", ""])
def test_skill_set_empty_path_fails_without_searching_cwd(tmp_path, monkeypatch, capsys, value):
    _write_skill(tmp_path / "cwd-skill")
    monkeypatch.chdir(tmp_path)
    error = _expect_fail(lambda: skills_core.load_skills(None, [value]), capsys)
    if value:
        assert "--skill-set 'ns1:' needs a folder or URL after the namespace." in error
    else:
        assert "--skill-set '' needs a folder or URL." in error
    assert "cwd-skill" not in error


@pytest.mark.parametrize("length", [62, 63])
def test_skill_set_namespace_length_boundary(tmp_path, capsys, length):
    namespace = "n" * length
    _write_skill(tmp_path / "set" / "alpha", "---\nname: a\n---\nBody.\n")
    if length == 62:
        assert list(skills_core.load_skills(None, [f"{namespace}:{tmp_path / 'set'}"])) == [
            f"{namespace}-a"
        ]
    else:
        error = _expect_fail(
            lambda: skills_core.load_skills(None, [f"{namespace}:{tmp_path / 'set'}"]), capsys
        )
        assert f"Invalid --skill-set namespace {namespace!r}" in error


def test_skill_set_same_folder_twice_prints_no_notice(tmp_path, capsys):
    root = tmp_path / "set"
    alpha = _write_skill(root / "alpha")
    assert skills_core.load_skills(None, [str(root), str(root)]) == {"alpha": alpha}
    assert capsys.readouterr().err == ""


def test_url_skill_set_flattens_the_cache_then_reuses_it(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    calls = _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {
        "README.md": "Set readme.\n",
        "godot-one/SKILL.md": "One.\n",
        "godot-one/references/guide.md": "Guide.\n",
        "nested/godot-two/SKILL.md": "Two.\n",
    })
    expected = {"godot-one": target / "godot-one", "godot-two": target / "godot-two"}
    assert skills_core.load_skills(None, [url]) == expected
    assert [command[:2] for command, _ in calls] == [["/usr/bin/degit", url]]
    assert list(target.parent.iterdir()) == [target]
    assert not (target / "README.md").exists()
    assert (target / "godot-one" / "references" / "guide.md").is_file()
    assert skills_core.load_skills(None, [url]) == expected
    assert len(calls) == 1
    stdout = capsys.readouterr().out
    assert "Using cached skill set" in stdout and str(target) in stdout


def test_url_skill_set_cache_differs_from_the_skill_cache(tmp_path, monkeypatch):
    url = "https://github.com/user/repo/tree/main/skills/remote-set"
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {"inner/SKILL.md": "Inner.\n"})
    set_target = _cached_set_target(tmp_path, url, "remote-set")
    skill_target = _cached_target(tmp_path, url, "remote-set")
    assert skills_core.load_skills(None, [url]) == {"inner": set_target / "inner"}
    _fake_degit(monkeypatch, tmp_path)
    assert skills_core.load_skills([url]) == {"remote-set": skill_target}
    assert set_target.parent != skill_target.parent
    assert (skill_target / "SKILL.md").is_file()


def test_url_skill_set_flatten_keeps_the_later_folder(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {
        "first/godot-one/SKILL.md": "One.\n",
        "second/godot-one/SKILL.md": "Two.\n",
    })
    assert skills_core.load_skills(None, [url]) == {"godot-one": target / "godot-one"}
    assert (target / "godot-one" / "SKILL.md").read_text() == "Two.\n"
    assert list(target.parent.iterdir()) == [target]
    winner = target / "godot-one"
    assert capsys.readouterr().err.splitlines() == [
        f"agent-switch: skill set {url}: skill godot-one uses {winner}; dropped {url}/first/godot-one."
    ]
    assert skills_core.load_skills(None, [url]) == {"godot-one": winner}
    assert capsys.readouterr().err == ""


def test_url_skill_set_flatten_keyed_by_skill_name(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    files = {
        "a/zeta/SKILL.md": "---\nname: shared\n---\nEARLIER\n",
        "z/alpha/SKILL.md": "---\nname: shared\n---\nLATER\n",
    }
    # A local set names the winner by source path, so the URL set must match it.
    _write_skill(tmp_path / "local" / "a" / "zeta", "---\nname: shared\n---\nEARLIER\n")
    _write_skill(tmp_path / "local" / "z" / "alpha", "---\nname: shared\n---\nLATER\n")
    local = skills_core.load_skills(None, [str(tmp_path / "local")])
    assert (local["shared"] / "SKILL.md").read_text().endswith("LATER\n")
    capsys.readouterr()
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = files)
    winner = target / "shared"
    assert skills_core.load_skills(None, [url]) == {"shared": winner}
    assert (winner / "SKILL.md").read_text().endswith("LATER\n")
    assert capsys.readouterr().err.splitlines() == [
        f"agent-switch: skill set {url}: skill shared uses {winner}; dropped {url}/a/zeta."
    ]
    assert skills_core.load_skills(None, [url]) == {"shared": winner}
    assert capsys.readouterr().err == ""


def test_url_skill_set_flatten_invalid_folder_name_fails_like_local(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    local_root = tmp_path / "local"
    _write_skill(local_root / "a" / "Pdf", "EARLIER\n")
    _write_skill(local_root / "b" / "pdf", "LATER\n")
    error = _expect_fail(lambda: skills_core.load_skills(None, [str(local_root)]), capsys)
    assert "Invalid skill name 'Pdf'" in error
    assert "Rename it upstream." in error
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {
        "a/Pdf/SKILL.md": "EARLIER\n",
        "b/pdf/SKILL.md": "LATER\n",
    })
    error = _expect_fail(lambda: skills_core.load_skills(None, [url]), capsys)
    assert "Invalid skill name 'Pdf'" in error
    assert "Rename it upstream." in error
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_url_skill_set_invalid_frontmatter_name_fails_like_local(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    broken = "---\nname: Bad_Name\n---\nEARLIER\n"
    valid = "---\nname: shared\n---\nVALID\n"
    local_root = tmp_path / "local"
    _write_skill(local_root / "a" / "shared", broken)
    _write_skill(local_root / "z" / "shared", valid)
    error = _expect_fail(lambda: skills_core.load_skills(None, [str(local_root)]), capsys)
    assert "Invalid skill name 'Bad_Name'" in error
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {
        "a/shared/SKILL.md": broken,
        "z/shared/SKILL.md": valid,
    })
    error = _expect_fail(lambda: skills_core.load_skills(None, [url]), capsys)
    assert "Invalid skill name 'Bad_Name'" in error
    assert "Rename it upstream." in error
    assert not target.exists()


def test_url_skill_set_broken_frontmatter_fails_like_local(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    broken = "---\nname: [broken\n---\nEARLIER\n"
    valid = "---\nname: shared\n---\nVALID\n"
    local_root = tmp_path / "local"
    _write_skill(local_root / "a" / "shared", broken)
    _write_skill(local_root / "z" / "shared", valid)
    error = _expect_fail(lambda: skills_core.load_skills(None, [str(local_root)]), capsys)
    assert "Could not parse skill frontmatter" in error
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {
        "a/shared/SKILL.md": broken,
        "z/shared/SKILL.md": valid,
    })
    error = _expect_fail(lambda: skills_core.load_skills(None, [url]), capsys)
    assert "Could not parse skill frontmatter" in error
    assert not target.exists()


def test_url_skill_set_flatten_notice_has_no_double_slash(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot/"
    target = _cached_set_target(tmp_path, url, "godot")
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {
        "first/one/SKILL.md": "One.\n",
        "second/one/SKILL.md": "Two.\n",
    })
    winner = target / "one"
    assert skills_core.load_skills(None, [url]) == {"one": winner}
    base = url.rstrip("/")
    assert capsys.readouterr().err.splitlines() == [
        f"agent-switch: skill set {url}: skill one uses {winner}; dropped {base}/first/one."
    ]


def test_url_skill_set_flatten_keeps_distinct_skill_names(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {
        "a/dupe/SKILL.md": "---\nname: first-one\n---\nOne.\n",
        "b/dupe/SKILL.md": "---\nname: second-one\n---\nTwo.\n",
    })
    assert skills_core.load_skills(None, [url]) == {
        "first-one": target / "first-one", "second-one": target / "second-one",
    }
    assert capsys.readouterr().err == ""


def test_url_skill_set_flatten_notice_lists_drops_latest_first(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {
        "a/one/SKILL.md": "One.\n",
        "b/one/SKILL.md": "Two.\n",
        "c/one/SKILL.md": "Three.\n",
    })
    winner = target / "one"
    assert skills_core.load_skills(None, [url]) == {"one": winner}
    assert capsys.readouterr().err.splitlines() == [
        f"agent-switch: skill set {url}: skill one uses {winner}; "
        f"dropped {url}/b/one, {url}/a/one."
    ]


def test_url_skill_set_broken_skill_md_symlink_names_the_skill(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")

    def broken_link(command, *args, **kwargs):
        destination = Path(command[-1])
        skill = destination / "a" / "odd"
        skill.mkdir(parents = True)
        (skill / "SKILL.md").symlink_to(destination / "shared.md")
        return subprocess.CompletedProcess(command, 0)

    _fake_degit(monkeypatch, tmp_path)
    monkeypatch.setattr(skills_core.subprocess, "run", broken_link)
    error = _expect_fail(lambda: skills_core.load_skills(None, [url]), capsys)
    assert f"Skill path {url}/a/odd must be a directory holding SKILL.md." in error
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason = "POSIX file modes")
def test_url_skill_set_unreadable_skill_md_names_the_skill(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")

    def locked_file(command, *args, **kwargs):
        destination = Path(command[-1])
        skill = destination / "a" / "odd"
        skill.mkdir(parents = True)
        path = skill / "SKILL.md"
        path.write_text("Odd.\n")
        path.chmod(0o000)
        return subprocess.CompletedProcess(command, 0)

    _fake_degit(monkeypatch, tmp_path)
    monkeypatch.setattr(skills_core.subprocess, "run", locked_file)
    error = _expect_fail(lambda: skills_core.load_skills(None, [url]), capsys)
    assert f"Cannot read {url}/a/odd/SKILL.md." in error
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_url_skill_set_namespace_prefixes_names(tmp_path, monkeypatch):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {"godot-one/SKILL.md": "One.\n"})
    skills = skills_core.load_skills(None, [f"ns1:{url}"])
    assert skills == {"ns1-godot-one": target / "godot-one"}


def test_url_skill_set_reuses_a_target_published_mid_flatten(tmp_path, monkeypatch):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")

    def publish_first(command, *args, **kwargs):
        destination = Path(command[-1])
        (destination / "alpha").mkdir()
        (destination / "alpha" / "SKILL.md").write_text("Skill body.\n")
        # Simulate the other concurrent run: its cache lands before this flatten publishes.
        (target / "alpha").mkdir(parents = True)
        (target / "alpha" / "SKILL.md").write_text("Skill body.\n")
        return subprocess.CompletedProcess(command, 0)

    _fake_degit(monkeypatch, tmp_path)
    monkeypatch.setattr(skills_core.subprocess, "run", publish_first)
    assert skills_core.load_skills(None, [url]) == {"alpha": target / "alpha"}
    assert list(target.parent.iterdir()) == [target]


def test_url_skill_set_move_failure_leaves_no_cache(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    _fake_degit(monkeypatch, tmp_path, skill_md = False, files = {"alpha/SKILL.md": "Skill body.\n"})
    replace = Path.replace

    def refuse_replace(self, destination):
        if self.name == "alpha":
            raise PermissionError(13, "Permission denied", str(self))
        return replace(self, destination)

    monkeypatch.setattr(Path, "replace", refuse_replace)
    error = _expect_fail(lambda: skills_core.load_skills(None, [url]), capsys)
    assert url in error
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_url_skill_set_never_follows_links_out_of_the_download(tmp_path, monkeypatch):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    victim = tmp_path / "victim"
    _write_skill(victim / "my-skill")
    target = _cached_set_target(tmp_path, url, "godot")

    def link_outside(command, *args, **kwargs):
        destination = Path(command[-1])
        (destination / "ok-skill").mkdir()
        (destination / "ok-skill" / "SKILL.md").write_text("Skill body.\n")
        (destination / "evil").symlink_to(victim, target_is_directory = True)
        return subprocess.CompletedProcess(command, 0)

    _fake_degit(monkeypatch, tmp_path)
    monkeypatch.setattr(skills_core.subprocess, "run", link_outside)
    assert skills_core.load_skills(None, [url]) == {"ok-skill": target / "ok-skill"}
    assert (victim / "my-skill").is_dir()


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason = "POSIX folder modes")
def test_url_skill_set_unreadable_download_folder_leaves_no_cache(tmp_path, monkeypatch, capsys):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")

    def locked_folder(command, *args, **kwargs):
        destination = Path(command[-1])
        (destination / "alpha").mkdir()
        (destination / "alpha" / "SKILL.md").write_text("Skill body.\n")
        locked = destination / "locked"
        locked.mkdir()
        locked.chmod(0o000)
        created.append(locked)
        return subprocess.CompletedProcess(command, 0)

    created = []
    _fake_degit(monkeypatch, tmp_path)
    monkeypatch.setattr(skills_core.subprocess, "run", locked_folder)
    try:
        error = _expect_fail(lambda: skills_core.load_skills(None, [url]), capsys)
    finally:
        for locked in created:
            locked.chmod(0o700)
    assert "locked" in error
    assert not target.exists()


def test_url_skill_set_missing_degit_names_the_flag(tmp_path, monkeypatch, capsys):
    _fake_degit(monkeypatch, tmp_path, degit = None)
    error = _expect_fail(
        lambda: skills_core.load_skills(None, ["https://github.com/user/repo/tree/main/skills/godot"]),
        capsys,
    )
    assert "--skill-set https://github.com/user/repo/tree/main/skills/godot needs degit" in error
    assert "npm install -g degit" in error


@pytest.mark.parametrize("kind", ["no-skill", "root-skill"])
def test_url_skill_set_without_nested_skills_leaves_no_cache(tmp_path, monkeypatch, capsys, kind):
    url = "https://github.com/user/repo/tree/main/skills/godot"
    target = _cached_set_target(tmp_path, url, "godot")
    _fake_degit(
        monkeypatch, tmp_path, skill_md = kind == "root-skill",
        files = {"nested/godot-skill/SKILL.md": "Nested.\n"} if kind == "root-skill" else None,
    )
    error = _expect_fail(lambda: skills_core.load_skills(None, [url]), capsys)
    assert "Use --skill" in error
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


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
