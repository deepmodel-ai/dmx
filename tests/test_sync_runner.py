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
