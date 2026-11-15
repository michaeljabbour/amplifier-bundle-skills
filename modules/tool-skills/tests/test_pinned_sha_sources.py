"""Skill sources pinned to a commit SHA (``git+<url>@<sha>``).

``git clone --branch <sha>`` fails with "Remote branch <sha> not found in
upstream origin" even when the commit exists, so every SHA-pinned skills
source used to fail at session start. These tests pin the fix: SHAs are
fetched by id, while branch and tag refs keep the single shallow clone.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from amplifier_module_tool_skills.sources import (
    _clone_commands,
    _is_commit_sha,
    _resolve_remote_source,
)

SHA1 = "88d9d84149eee914372c1ca8d68e9eb9f4ba3290"

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.mark.parametrize(
    "ref, expected",
    [
        (SHA1, True),
        (SHA1.upper(), True),
        ("a" * 64, True),  # SHA-256 object format
        ("main", False),
        ("v1.2.3", False),
        ("88d9d84", False),  # abbreviated: may be a branch/tag name; servers won't fetch it
        (SHA1[:-1] + "g", False),
        ("a" * 41, False),
    ],
)
def test_is_commit_sha(ref, expected):
    assert _is_commit_sha(ref) is expected


def test_branch_ref_keeps_single_shallow_clone(tmp_path):
    dest = tmp_path / "d"
    assert _clone_commands("https://x/r", "main", dest) == [
        ["git", "clone", "--depth", "1", "--branch", "main", "https://x/r", str(dest)]
    ]


def test_sha_ref_never_uses_branch_flag(tmp_path):
    cmds = _clone_commands("https://x/r", SHA1, tmp_path / "d")
    flat = [arg for cmd in cmds for arg in cmd]
    assert "--branch" not in flat
    assert ["git", "-C", str(tmp_path / "d"), "fetch", "--depth", "1", "--quiet", "origin", SHA1] in cmds
    assert cmds[-1][-1] == "FETCH_HEAD"


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """A local repo with two commits on main and a tag on the first.

    Returns (path, first_sha, second_sha). The first commit is no longer a
    branch tip, which is exactly the case ``--branch`` cannot reach.
    """
    repo = tmp_path / "upstream"
    repo.mkdir()
    _git("init", "--quiet", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    # Real hosts (GitHub etc.) serve any reachable commit by id; a local
    # upload-pack needs this switched on to behave the same way.
    _git("config", "uploadpack.allowReachableSHA1InWant", "true", cwd=repo)
    (repo / "skills" / "demo").mkdir(parents=True)
    (repo / "skills" / "demo" / "SKILL.md").write_text("v1\n")
    _git("add", ".", cwd=repo)
    _git("commit", "--quiet", "-m", "one", cwd=repo)
    first = _git("rev-parse", "HEAD", cwd=repo)
    _git("tag", "v1", cwd=repo)
    (repo / "skills" / "demo" / "SKILL.md").write_text("v2\n")
    _git("commit", "--quiet", "-am", "two", cwd=repo)
    second = _git("rev-parse", "HEAD", cwd=repo)
    return repo, first, second


@requires_git
@pytest.mark.asyncio
async def test_real_clone_of_non_tip_sha(tmp_path, upstream):
    repo, first, _ = upstream
    cache_dir = tmp_path / "cache"
    source = f"git+{repo.as_uri()}@{first}#subdirectory=skills"

    result = await _resolve_remote_source(source, cache_dir)

    assert result is not None
    assert (result / "demo" / "SKILL.md").read_text() == "v1\n"
    meta = json.loads((result.parent / ".amplifier_cache_meta.json").read_text())
    assert meta["commit"] == first
    assert meta["ref"] == first
    assert not list(cache_dir.glob("*.tmp"))


@requires_git
@pytest.mark.asyncio
@pytest.mark.parametrize("ref, content", [("main", "v2\n"), ("v1", "v1\n")])
async def test_real_clone_of_branch_and_tag_still_works(tmp_path, upstream, ref, content):
    repo, _, _ = upstream
    source = f"git+{repo.as_uri()}@{ref}#subdirectory=skills"

    result = await _resolve_remote_source(source, tmp_path / "cache")

    assert result is not None
    assert (result / "demo" / "SKILL.md").read_text() == content


@requires_git
@pytest.mark.asyncio
async def test_unknown_sha_fails_cleanly(tmp_path, upstream):
    repo, _, _ = upstream
    cache_dir = tmp_path / "cache"
    source = f"git+{repo.as_uri()}@{'0' * 40}"

    assert await _resolve_remote_source(source, cache_dir) is None
    assert not list(cache_dir.iterdir())  # no cache dir, no leftover .tmp
