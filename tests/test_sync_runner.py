"""Tests for dmx.sync_runner — GH-27 phase 2: clone/vendor mechanics.

Uses local git repos as fixtures (``git clone`` against a local filesystem
path exercises the exact same codepath as a real remote URL) so nothing
here depends on real network access.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from dmx.shared_sources import SharedSource, vendor_dir, vendor_lock_path
from dmx.sync_runner import (
    SyncError,
    detect_collisions,
    require_non_base_branch_error,
    sync_source,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _init_source_repo(root: Path, files: dict[str, str], tag: str = "v1") -> str:
    """Create a local git repo usable as a shared source's ``url``.

    Writes *files* (relative path -> content), commits, tags *tag*, and
    returns the resolved SHA of that tag.
    """
    root.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q"], root)
    _run(["git", "config", "user.email", "test@example.com"], root)
    _run(["git", "config", "user.name", "Test"], root)
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _run(["git", "add", "."], root)
    _run(["git", "commit", "-q", "-m", "initial"], root)
    _run(["git", "tag", tag], root)
    result = subprocess.run(
        ["git", "rev-parse", tag], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _source(name: str, url: Path, ref: str = "v1", subdir: str | None = None) -> SharedSource:
    address = f"git::{url}"
    if subdir:
        address += f"//{subdir}"
    address += f"?ref={ref}"
    return SharedSource(name=name, source=address, url=str(url), subdir=subdir, ref=ref)


# ---------------------------------------------------------------------------
# sync_source
# ---------------------------------------------------------------------------


class TestSyncSource:
    def test_vendors_files_and_writes_lock(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        sha = _init_source_repo(source_repo, {"loops/custom.yaml": "name: custom\n"})
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        result = sync_source(_source("acme", source_repo), workspace)

        assert result.name == "acme"
        assert result.resolved_sha == sha
        vendored = vendor_dir(workspace, "acme") / "loops" / "custom.yaml"
        assert vendored.read_text(encoding="utf-8") == "name: custom\n"

    def test_lock_file_contents(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        sha = _init_source_repo(source_repo, {"skills/custom-skill.md": "# hi\n"})
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        source = _source("acme", source_repo)
        sync_source(source, workspace)

        lock = json.loads(vendor_lock_path(workspace, "acme").read_text(encoding="utf-8"))
        assert lock == {"name": "acme", "source": source.source, "resolved_sha": sha}

    def test_vendored_tree_has_no_git_directory(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        _init_source_repo(source_repo, {"validators/v.py": "# v\n"})
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        sync_source(_source("acme", source_repo), workspace)

        assert not (vendor_dir(workspace, "acme") / ".git").exists()

    def test_resyncing_replaces_previous_contents(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        _init_source_repo(source_repo, {"loops/old.yaml": "name: old\n"}, tag="v1")
        workspace = tmp_path / "consumer"
        workspace.mkdir()
        sync_source(_source("acme", source_repo, ref="v1"), workspace)
        assert (vendor_dir(workspace, "acme") / "loops" / "old.yaml").exists()

        # Bump the source: new commit, new tag, old file removed.
        (source_repo / "loops" / "old.yaml").unlink()
        (source_repo / "loops" / "new.yaml").write_text("name: new\n", encoding="utf-8")
        _run(["git", "add", "-A"], source_repo)
        _run(["git", "commit", "-q", "-m", "bump"], source_repo)
        _run(["git", "tag", "v2"], source_repo)

        sync_source(_source("acme", source_repo, ref="v2"), workspace)

        assert not (vendor_dir(workspace, "acme") / "loops" / "old.yaml").exists()
        assert (vendor_dir(workspace, "acme") / "loops" / "new.yaml").exists()

    def test_subdir_is_respected(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        _init_source_repo(
            source_repo,
            {
                "unrelated/README.md": "not a shared source root\n",
                "shared/loops/custom.yaml": "name: custom\n",
            },
        )
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        sync_source(_source("acme", source_repo, subdir="shared"), workspace)

        assert (vendor_dir(workspace, "acme") / "loops" / "custom.yaml").exists()
        assert not (vendor_dir(workspace, "acme") / "unrelated").exists()

    def test_clone_failure_raises_with_actionable_message(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "does-not-exist"
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        with pytest.raises(SyncError, match="Could not clone"):
            sync_source(_source("acme", nonexistent), workspace)

    def test_missing_ref_raises_with_actionable_message(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        _init_source_repo(source_repo, {"loops/custom.yaml": "name: custom\n"})
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        with pytest.raises(SyncError, match="not found in shared source"):
            sync_source(_source("acme", source_repo, ref="does-not-exist"), workspace)

    def test_missing_subdir_raises(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        _init_source_repo(source_repo, {"loops/custom.yaml": "name: custom\n"})
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        with pytest.raises(SyncError, match="does not exist at ref"):
            sync_source(_source("acme", source_repo, subdir="nonexistent-subdir"), workspace)

    def test_wrong_shaped_source_raises(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        _init_source_repo(source_repo, {"README.md": "just a normal repo, not a dmx source\n"})
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        with pytest.raises(SyncError, match="doesn't look like a dmx shared source"):
            sync_source(_source("acme", source_repo), workspace)

    def test_accepts_skills_only_source(self, tmp_path: Path) -> None:
        # Shape check only requires *one* of loops/skills/validators, not all three.
        source_repo = tmp_path / "source"
        _init_source_repo(source_repo, {"skills/custom-skill.md": "# hi\n"})
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        result = sync_source(_source("acme", source_repo), workspace)
        assert result.name == "acme"

    def test_accepts_folder_shaped_skill(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        _init_source_repo(
            source_repo, {"skills/custom-skill/SKILL.md": "---\ntitle: X\n---\nbody\n"}
        )
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        result = sync_source(_source("acme", source_repo), workspace)

        assert result.name == "acme"
        assert (vendor_dir(workspace, "acme") / "skills" / "custom-skill" / "SKILL.md").exists()

    def test_rejects_folder_shaped_skill_missing_skill_md(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        _init_source_repo(source_repo, {"skills/custom-skill/scripts/run.py": "print(1)\n"})
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        with pytest.raises(SyncError, match="malformed entries under skills/"):
            sync_source(_source("acme", source_repo), workspace)

    def test_rejects_stray_non_markdown_file_under_skills(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        _init_source_repo(source_repo, {"skills/notes.txt": "not a skill\n"})
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        with pytest.raises(SyncError, match="malformed entries under skills/"):
            sync_source(_source("acme", source_repo), workspace)

    def test_hidden_files_under_skills_are_not_flagged_as_malformed(self, tmp_path: Path) -> None:
        # .gitkeep/.gitignore/.DS_Store etc. are common, harmless repo-hygiene
        # artifacts at the top of any real directory — must not fail an
        # otherwise well-formed source just for containing one.
        source_repo = tmp_path / "source"
        _init_source_repo(
            source_repo,
            {
                "skills/custom-skill.md": "# hi\n",
                "skills/.gitkeep": "",
                "skills/.gitignore": "*.log\n",
            },
        )
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        result = sync_source(_source("acme", source_repo), workspace)

        assert result.name == "acme"
        assert (vendor_dir(workspace, "acme") / "skills" / "custom-skill.md").exists()

    def test_uninitialized_submodule_directory_is_vendored_empty(self, tmp_path: Path) -> None:
        # Git itself refuses to track any path literally named ".git" at any
        # depth (confirmed: `git add -A` on a manually-created nested `.git/`
        # is a silent no-op), so a real nested-.git-in-a-tracked-tree
        # scenario can't actually occur through normal git usage — the only
        # realistic case is an *uninitialized* submodule, which git clone
        # (without --recurse-submodules, which sync_source deliberately
        # doesn't pass) leaves as an empty directory with no .git at all.
        # This just confirms that case vendors cleanly.
        source_repo = tmp_path / "source"
        _init_source_repo(source_repo, {"skills/custom-skill/SKILL.md": "body\n"})
        submodule_repo = tmp_path / "submodule-target"
        _init_source_repo(submodule_repo, {"README.md": "sub\n"})
        _run(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(submodule_repo),
                "sub",
            ],
            source_repo,
        )
        _run(["git", "commit", "-q", "-m", "add submodule"], source_repo)
        _run(["git", "tag", "-f", "v1"], source_repo)
        workspace = tmp_path / "consumer"
        workspace.mkdir()

        sync_source(_source("acme", source_repo), workspace)

        vendored = vendor_dir(workspace, "acme")
        assert (vendored / "skills" / "custom-skill" / "SKILL.md").exists()
        # The submodule directory exists (git always creates it) but is
        # empty — no nested .git, since --recurse-submodules was never used.
        assert not (vendored / "sub" / ".git").exists()


# ---------------------------------------------------------------------------
# require_non_base_branch_error
# ---------------------------------------------------------------------------


def _write_config(root: Path, branch_base: str = "main") -> None:
    config_path = root / ".dmx" / "config.md"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(f"branch_base: {branch_base}\n", encoding="utf-8")


class TestRequireNonBaseBranchError:
    def test_blocks_on_branch_base(self, tmp_path: Path) -> None:
        _run(["git", "init", "-q", "-b", "main"], tmp_path)
        _run(["git", "config", "user.email", "test@example.com"], tmp_path)
        _run(["git", "config", "user.name", "Test"], tmp_path)
        (tmp_path / "f.txt").write_text("x", encoding="utf-8")
        _run(["git", "add", "."], tmp_path)
        _run(["git", "commit", "-q", "-m", "initial"], tmp_path)
        _write_config(tmp_path, branch_base="main")

        error = require_non_base_branch_error(tmp_path)

        assert error is not None
        assert "sync" in error.lower()
        assert "main" in error

    def test_allows_a_feature_branch(self, tmp_path: Path) -> None:
        _run(["git", "init", "-q", "-b", "main"], tmp_path)
        _run(["git", "config", "user.email", "test@example.com"], tmp_path)
        _run(["git", "config", "user.name", "Test"], tmp_path)
        (tmp_path / "f.txt").write_text("x", encoding="utf-8")
        _run(["git", "add", "."], tmp_path)
        _run(["git", "commit", "-q", "-m", "initial"], tmp_path)
        _run(["git", "checkout", "-q", "-b", "chore/sync-shared-sources"], tmp_path)
        _write_config(tmp_path, branch_base="main")

        assert require_non_base_branch_error(tmp_path) is None

    def test_no_config_does_not_block(self, tmp_path: Path) -> None:
        assert require_non_base_branch_error(tmp_path) is None

    def test_not_a_git_repo_does_not_block(self, tmp_path: Path) -> None:
        _write_config(tmp_path, branch_base="main")
        assert require_non_base_branch_error(tmp_path) is None


# ---------------------------------------------------------------------------
# detect_collisions
# ---------------------------------------------------------------------------


def _vendor(root: Path, name: str, category: str, filename: str, content: str = "x") -> None:
    """Simulate an already-vendored file at .dmx/vendor/{name}/{category}/{filename},
    without going through a real clone — detect_collisions only reads from disk."""
    path = root / ".dmx" / "vendor" / name / category / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _vendor_skill_dir(root: Path, source_name: str, skill_name: str) -> None:
    """Simulate an already-vendored folder-shaped skill,
    .dmx/vendor/{source_name}/skills/{skill_name}/SKILL.md."""
    path = root / ".dmx" / "vendor" / source_name / "skills" / skill_name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("body\n", encoding="utf-8")


def _app_file(root: Path, category: str, filename: str, content: str = "x") -> None:
    app_dir = {"loops": ".dmx/loops", "skills": ".dmx/skills", "validators": "validators"}[category]
    path = root / app_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _no_op_source(name: str) -> SharedSource:
    return SharedSource(
        name=name, source=f"git::https://x//{name}?ref=v1", url="https://x", subdir=None, ref="v1"
    )


class TestDetectCollisions:
    def test_no_sources_no_collisions(self, tmp_path: Path) -> None:
        assert detect_collisions([], tmp_path) == []

    def test_no_overlap_no_collisions(self, tmp_path: Path) -> None:
        _vendor(tmp_path, "acme", "loops", "custom.yaml")
        _app_file(tmp_path, "loops", "other.yaml")
        assert detect_collisions([_no_op_source("acme")], tmp_path) == []

    def test_two_shared_sources_collide(self, tmp_path: Path) -> None:
        _vendor(tmp_path, "first", "loops", "spec.yaml")
        _vendor(tmp_path, "second", "loops", "spec.yaml")

        warnings = detect_collisions([_no_op_source("first"), _no_op_source("second")], tmp_path)

        assert len(warnings) == 1
        assert "`spec.yaml`" in warnings[0]
        assert "`first`" in warnings[0]
        assert "`second`" in warnings[0]
        assert "wins per shared_sources order" in warnings[0]

    def test_declared_order_determines_the_stated_winner(self, tmp_path: Path) -> None:
        _vendor(tmp_path, "second", "loops", "spec.yaml")
        _vendor(tmp_path, "first", "loops", "spec.yaml")

        # Declared order is [second, first] here — "second" must be reported
        # as the winner, regardless of alphabetical or vendoring order.
        warnings = detect_collisions([_no_op_source("second"), _no_op_source("first")], tmp_path)

        assert "`second` wins" in warnings[0]

    def test_app_repo_beats_shared_source(self, tmp_path: Path) -> None:
        _app_file(tmp_path, "skills", "code-review.md")
        _vendor(tmp_path, "org-wide", "skills", "code-review.md")

        warnings = detect_collisions([_no_op_source("org-wide")], tmp_path)

        assert len(warnings) == 1
        assert "exists both locally" in warnings[0]
        assert "`org-wide`" in warnings[0]
        assert "the local copy wins" in warnings[0]

    def test_no_collision_when_only_app_repo_has_the_file(self, tmp_path: Path) -> None:
        _app_file(tmp_path, "validators", "check_a.py")
        assert detect_collisions([_no_op_source("acme")], tmp_path) == []

    def test_collisions_checked_independently_per_category(self, tmp_path: Path) -> None:
        # Same filename string, but in different categories — not a collision.
        _vendor(tmp_path, "acme", "loops", "shared.yaml")
        _app_file(tmp_path, "validators", "shared.yaml")
        assert detect_collisions([_no_op_source("acme")], tmp_path) == []

    def test_three_way_collision_lists_all_shadowed_names(self, tmp_path: Path) -> None:
        _app_file(tmp_path, "loops", "spec.yaml")
        _vendor(tmp_path, "first", "loops", "spec.yaml")
        _vendor(tmp_path, "second", "loops", "spec.yaml")

        warnings = detect_collisions([_no_op_source("first"), _no_op_source("second")], tmp_path)

        assert len(warnings) == 1
        assert "exists both locally" in warnings[0]
        assert "`first`" in warnings[0]
        assert "`second`" in warnings[0]

    def test_bundled_skills_are_never_flagged(self, tmp_path: Path) -> None:
        # e.g. "spec.yaml"/"commit.md" exist in the bundled fallback for
        # every repo — that must never surface as a collision.
        _vendor(tmp_path, "acme", "skills", "dmx-commit.md")
        assert detect_collisions([_no_op_source("acme")], tmp_path) == []

    def test_end_to_end_with_real_sync_source(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "source"
        _init_source_repo(source_repo, {"loops/custom.yaml": "name: custom\n"})
        workspace = tmp_path / "consumer"
        workspace.mkdir()
        _app_file(workspace, "loops", "custom.yaml")

        source = _source("acme", source_repo)
        sync_source(source, workspace)

        warnings = detect_collisions([source], workspace)
        assert len(warnings) == 1
        assert "`custom.yaml`" in warnings[0]
        assert "exists both locally" in warnings[0]

    def test_two_folder_shaped_skills_with_the_same_name_collide(self, tmp_path: Path) -> None:
        # GH-27 phase 4's folder shape ({name}/SKILL.md) is a directory, not
        # a "*.md" glob match — this must still be caught, not silently
        # missed the way a naive filename glob would miss it.
        _vendor_skill_dir(tmp_path, "first", "custom-skill")
        _vendor_skill_dir(tmp_path, "second", "custom-skill")

        warnings = detect_collisions([_no_op_source("first"), _no_op_source("second")], tmp_path)

        assert len(warnings) == 1
        assert "`custom-skill`" in warnings[0]
        assert "`first`" in warnings[0]
        assert "`second`" in warnings[0]

    def test_flat_and_folder_shaped_skill_with_the_same_name_collide(self, tmp_path: Path) -> None:
        # One source has the flat form, another has the folder-shaped form
        # of the *same logical skill name* — these still collide at actual
        # _resolve_skill time, so detect_collisions must treat them as the
        # same name too, not as two unrelated, non-colliding entries.
        _vendor(tmp_path, "first", "skills", "custom-skill.md")
        _vendor_skill_dir(tmp_path, "second", "custom-skill")

        warnings = detect_collisions([_no_op_source("first"), _no_op_source("second")], tmp_path)

        assert len(warnings) == 1
        assert "`custom-skill`" in warnings[0]

    def test_dmx_prefixed_and_unprefixed_skill_collide(self, tmp_path: Path) -> None:
        # _resolve_skill treats "commit" and "dmx-commit" as the same
        # logical skill (candidates = [name, f"dmx-{name}"]) — collision
        # detection must normalize the same way.
        _vendor(tmp_path, "first", "skills", "custom-skill.md")
        _vendor(tmp_path, "second", "skills", "dmx-custom-skill.md")

        warnings = detect_collisions([_no_op_source("first"), _no_op_source("second")], tmp_path)

        assert len(warnings) == 1
        assert "`custom-skill`" in warnings[0]

    def test_folder_shaped_skill_missing_skill_md_is_not_counted(self, tmp_path: Path) -> None:
        # A directory that merely happens to share a skill's name but has no
        # SKILL.md inside isn't a skill at all (sync_source's own shape
        # check would already reject vendoring it) — detect_collisions must
        # not mistake it for one either.
        stray_dir = tmp_path / ".dmx" / "vendor" / "acme" / "skills" / "custom-skill"
        stray_dir.mkdir(parents=True)
        (stray_dir / "notes.txt").write_text("not a skill\n", encoding="utf-8")
        _vendor(tmp_path, "other", "skills", "custom-skill.md")

        warnings = detect_collisions([_no_op_source("acme"), _no_op_source("other")], tmp_path)

        assert warnings == []
