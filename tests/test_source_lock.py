"""GH-85: shared sources are locked in shared-sources.lock and cached, not vendored."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from dmx.cli import main
from dmx.layout_migration import GITIGNORE_LINE, apply_layout_migration, plan_layout_migration
from dmx.shared_sources import (
    LockEntry,
    read_lock,
    read_shared_sources,
    source_root,
    vendor_dir,
    write_lock,
)
from dmx.source_cache import (
    SharedSourceFetchError,
    cache_home,
    cached_tree,
    remove_tree,
    tree_hash,
)
from dmx.sync_runner import (
    compare_link,
    fetch_shared_sources,
    summary_markdown,
    update_shared_sources,
)

if TYPE_CHECKING:
    from pathlib import Path


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _source_repo(root: Path, files: dict[str, str]) -> str:
    """A local repo usable as a shared source, on branch ``main``. Returns HEAD."""
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    return _commit_files(root, files, "initial")


def _commit_files(root: Path, files: dict[str, str | None], message: str) -> str:
    for rel, content in files.items():
        path = root / rel
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _declare(workspace: Path, sources: dict[str, str]) -> None:
    """Write shared-sources.yaml: name -> source address."""
    lines = "".join(f"  - name: {name}\n    source: {addr}\n" for name, addr in sources.items())
    path = workspace / ".dmx" / "shared-sources.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"shared_sources:\n{lines}", encoding="utf-8")


def _source(workspace: Path, name: str):  # noqa: ANN202 — test helper
    return next(s for s in read_shared_sources(workspace) if s.name == name)


_SKILL = "---\ndescription: {text}\n---\n{text}\n"


class TestUpdateWritesTheLock:
    def test_locks_each_source_and_vendors_nothing(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        head = _source_repo(repo, {"skills/reconcile.md": _SKILL.format(text="v1")})
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})

        report = update_shared_sources(workspace)

        assert [change.name for change in report.changes] == ["acme"]
        text = (workspace / ".dmx" / "shared-sources.lock").read_text(encoding="utf-8")
        assert text.startswith("# Written by /dmx/update-shared-sources.")
        entry = read_lock(workspace)["acme"]
        assert entry.commit == head
        assert entry.hash == tree_hash(cached_tree(workspace, str(repo), head))
        assert not (workspace / ".dmx" / "vendor").exists()
        root = source_root(workspace, _source(workspace, "acme"))
        assert (root / "skills" / "reconcile.md").read_text(encoding="utf-8").endswith("v1\n")

    def test_a_floating_ref_moves_only_when_asked(self, tmp_path: Path) -> None:
        first_repo = tmp_path / "first"
        second_repo = tmp_path / "second"
        first_v1 = _source_repo(first_repo, {"skills/a.md": _SKILL.format(text="a1")})
        second_v1 = _source_repo(second_repo, {"skills/b.md": _SKILL.format(text="b1")})
        workspace = tmp_path / "app"
        _declare(
            workspace,
            {"first": f"git::{first_repo}?ref=main", "second": f"git::{second_repo}?ref=main"},
        )
        update_shared_sources(workspace)
        first_v2 = _commit_files(first_repo, {"skills/a.md": _SKILL.format(text="a2")}, "a2")
        second_v2 = _commit_files(second_repo, {"skills/b.md": _SKILL.format(text="b2")}, "b2")

        quiet = update_shared_sources(workspace)
        assert quiet.changes == [] and not quiet.wrote_lock
        assert {n: e.commit for n, e in read_lock(workspace).items()} == {
            "first": first_v1,
            "second": second_v1,
        }

        one = update_shared_sources(workspace, names=["first"])
        assert [change.name for change in one.changes] == ["first"]
        assert read_lock(workspace)["first"].commit == first_v2
        assert read_lock(workspace)["second"].commit == second_v1

        update_shared_sources(workspace, update_all=True)
        assert read_lock(workspace)["second"].commit == second_v2

    def test_a_changed_address_is_looked_up_again(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        v1 = _source_repo(repo, {"skills/a.md": _SKILL.format(text="a1")})
        _git(repo, "tag", "v1")
        v2 = _commit_files(repo, {"skills/a.md": _SKILL.format(text="a2")}, "a2")
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": f"git::{repo}?ref=v1"})
        update_shared_sources(workspace)
        assert read_lock(workspace)["acme"].commit == v1

        _declare(workspace, {"acme": f"git::{repo}?ref=main"})
        report = update_shared_sources(workspace)

        assert [change.name for change in report.changes] == ["acme"]
        assert read_lock(workspace)["acme"].commit == v2

    def test_an_edited_address_keeps_the_locked_tree_until_updated(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        _source_repo(repo, {"skills/a.md": _SKILL.format(text="locked")})
        other = tmp_path / "other-repo"
        _source_repo(other, {"skills/b.md": _SKILL.format(text="other")})
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})
        update_shared_sources(workspace)

        _declare(workspace, {"acme": f"git::{other}?ref=main"})  # not updated yet
        root = source_root(workspace, _source(workspace, "acme"))

        assert (root / "skills" / "a.md").is_file()
        lines, ok = fetch_shared_sources(workspace)
        assert not ok and "not in `.dmx/shared-sources.lock`" in lines[0]

    def test_a_source_no_longer_declared_is_dropped(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        _source_repo(repo, {"skills/a.md": _SKILL.format(text="a")})
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": f"git::{repo}?ref=main", "old": f"git::{repo}?ref=main"})
        update_shared_sources(workspace)
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})

        report = update_shared_sources(workspace)

        assert report.removed == ["old"]
        assert set(read_lock(workspace)) == {"acme"}

    def test_a_failed_source_keeps_its_entry(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        _source_repo(repo, {"skills/a.md": _SKILL.format(text="a")})
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})
        update_shared_sources(workspace)
        before = read_lock(workspace)["acme"]
        moved = tmp_path / "moved-away"
        repo.rename(moved)
        remove_tree(cache_home() / "git")

        report = update_shared_sources(workspace, update_all=True)

        assert len(report.failures) == 1
        assert "Could not fetch shared source `acme`" in report.failures[0]
        assert "gh auth login" in report.failures[0]
        assert read_lock(workspace)["acme"] == before


class TestTheCache:
    def test_a_fresh_clone_fetches_on_first_use(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        _source_repo(repo, {"skills/a.md": _SKILL.format(text="a")})
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})
        update_shared_sources(workspace)
        remove_tree(workspace / ".dmx" / ".cache")
        remove_tree(cache_home() / "git")

        root = source_root(workspace, _source(workspace, "acme"))

        assert (root / "skills" / "a.md").is_file()

    def test_two_names_for_one_repo_share_one_tree(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        _source_repo(repo, {"skills/a.md": _SKILL.format(text="a"), "loops/l.yaml": "name: l\n"})
        workspace = tmp_path / "app"
        _declare(workspace, {"one": f"git::{repo}?ref=main", "two": f"git::{repo}?ref=main"})

        update_shared_sources(workspace)

        trees = [p for p in (workspace / ".dmx" / ".cache").iterdir() if p.is_dir()]
        assert len(trees) == 1
        assert source_root(workspace, _source(workspace, "one")) == source_root(
            workspace, _source(workspace, "two")
        )

    def test_the_cache_is_never_committed(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        _source_repo(repo, {"skills/a.md": _SKILL.format(text="a")})
        workspace = tmp_path / "app"
        workspace.mkdir()
        _git(workspace, "init", "-q", "-b", "main")
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})
        update_shared_sources(workspace)

        _git(workspace, "add", ".dmx/")  # what create-pr runs
        staged = _git(workspace, "diff", "--cached", "--name-only").splitlines()

        assert ".dmx/shared-sources.lock" in staged
        assert not [path for path in staged if path.startswith(".dmx/.cache")]

    def test_a_tampered_cache_is_not_hashed_into_the_lock(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        head = _source_repo(repo, {"skills/a.md": _SKILL.format(text="genuine")})
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})
        update_shared_sources(workspace)
        genuine = read_lock(workspace)["acme"].hash
        tree = cached_tree(workspace, str(repo), head)
        (tree / "skills" / "a.md").write_text(_SKILL.format(text="tampered"), encoding="utf-8")

        update_shared_sources(workspace, update_all=True)

        assert read_lock(workspace)["acme"].hash == genuine
        assert "genuine" in (tree / "skills" / "a.md").read_text(encoding="utf-8")

    def test_a_tampered_tree_is_fetched_again(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        _source_repo(repo, {"skills/a.md": _SKILL.format(text="genuine")})
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})
        update_shared_sources(workspace)
        source = _source(workspace, "acme")
        cached = source_root(workspace, source) / "skills" / "a.md"
        cached.write_text(_SKILL.format(text="tampered"), encoding="utf-8")

        root = source_root(workspace, source)

        assert "genuine" in (root / "skills" / "a.md").read_text(encoding="utf-8")

    def test_content_that_does_not_match_the_lock_is_refused(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        head = _source_repo(repo, {"skills/a.md": _SKILL.format(text="a")})
        workspace = tmp_path / "app"
        address = f"git::{repo}?ref=main"
        _declare(workspace, {"acme": address})
        write_lock(
            workspace,
            [LockEntry(name="acme", source=address, commit=head, hash="sha256:" + "0" * 64)],
        )

        with pytest.raises(SharedSourceFetchError, match="does not match"):
            source_root(workspace, _source(workspace, "acme"))
        assert not cached_tree(workspace, str(repo), head).exists()

    def test_an_unlocked_05_source_still_resolves_to_its_vendored_copy(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": "git::https://example.com/acme/skills.git?ref=v1"})
        (vendor_dir(workspace, "acme") / "skills").mkdir(parents=True)

        assert source_root(workspace, _source(workspace, "acme")) == vendor_dir(workspace, "acme")


class TestPrSummary:
    def test_lists_changes_and_flags_code(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        _source_repo(
            repo,
            {
                "skills/keep.md": _SKILL.format(text="keep"),
                "skills/edit.md": _SKILL.format(text="v1"),
                "validators/check.py": "print(1)\n",
            },
        )
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})
        update_shared_sources(workspace)
        _commit_files(
            repo,
            {
                "skills/edit.md": _SKILL.format(text="v2"),
                "skills/new.md": _SKILL.format(text="new"),
                "validators/check.py": "print(2)\n",
                "loops/ship.yaml": "name: ship\n",
            },
            "bump",
        )

        report = update_shared_sources(workspace, update_all=True)
        summary = summary_markdown(report)

        assert "**`acme`**: `" in summary and "` → `" in summary
        assert "- Skills: added `new`; changed `edit`" in summary
        assert "- **Loops (code to review)**: added `ship`" in summary
        assert "- **Validators (code to review)**: changed `check`" in summary

    def test_compare_links_for_github(self) -> None:
        assert compare_link("https://github.com/acme/skills.git", "a1", "b2") == (
            "https://github.com/acme/skills/compare/a1...b2"
        )
        assert compare_link("git@github.com:acme/skills.git", "a1", "b2") == (
            "https://github.com/acme/skills/compare/a1...b2"
        )
        assert compare_link("https://gitlab.com/acme/skills.git", "a1", "b2") is None


class TestFetchSharedSources:
    def test_fetches_locked_sources_and_reports_unlocked_ones(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        _source_repo(repo, {"skills/a.md": _SKILL.format(text="a")})
        workspace = tmp_path / "app"
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})
        update_shared_sources(workspace)
        remove_tree(workspace / ".dmx" / ".cache")
        lock_before = (workspace / ".dmx" / "shared-sources.lock").read_text(encoding="utf-8")

        lines, ok = fetch_shared_sources(workspace)
        assert ok and lines[0].startswith("✓ `acme` at `")
        assert (workspace / ".dmx" / ".cache").is_dir()
        lock_after = (workspace / ".dmx" / "shared-sources.lock").read_text(encoding="utf-8")
        assert lock_after == lock_before

        _declare(
            workspace,
            {"acme": f"git::{repo}?ref=main", "later": f"git::{repo}?ref=main"},
        )
        (workspace / ".git").mkdir()
        (workspace / "packages" / "billing").mkdir(parents=True)
        (workspace / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            ["fetch-shared-sources", "--workspace-root", str(workspace / "packages" / "billing")],
        )

        assert result.exit_code == 1
        assert "✓ `acme`" in result.output
        assert "✗ `later`: not in `.dmx/shared-sources.lock`" in result.output


class TestMigratingVendoredSources:
    def _vendored_05(self, tmp_path: Path) -> tuple[Path, Path, str, str]:
        repo = tmp_path / "skills-repo"
        head = _source_repo(repo, {"skills/a.md": _SKILL.format(text="a")})
        workspace = tmp_path / "app"
        workspace.mkdir()
        _git(workspace, "init", "-q", "-b", "main")
        _git(workspace, "config", "user.email", "test@example.com")
        _git(workspace, "config", "user.name", "Test")
        address = f"git::{repo}?ref=main"
        _declare(workspace, {"acme": address})
        (workspace / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        vendored = vendor_dir(workspace, "acme")
        (vendored / "skills").mkdir(parents=True)
        (vendored / "skills" / "a.md").write_text(_SKILL.format(text="a"), encoding="utf-8")
        (vendored / ".lock.json").write_text(
            json.dumps({"name": "acme", "source": address, "resolved_sha": head}),
            encoding="utf-8",
        )
        _git(workspace, "add", "-A")
        _git(workspace, "commit", "-q", "-m", "0.5 with vendored acme")
        _git(workspace, "checkout", "-q", "-b", "chore-dmx-upgrade")
        return repo, workspace, address, head

    def test_vendored_sources_are_locked_and_vendor_removed(self, tmp_path: Path) -> None:
        repo, workspace, _address, head = self._vendored_05(tmp_path)

        plan = plan_layout_migration(workspace)
        assert plan.locks == (("acme", head),)
        assert plan.removes_vendor and plan.adds_gitignore

        applied = apply_layout_migration(workspace)

        assert applied.lock_failures == ()
        assert read_lock(workspace)["acme"].commit == head
        assert read_lock(workspace)["acme"].hash == tree_hash(
            cached_tree(workspace, str(repo), head)
        )
        assert not (workspace / ".dmx" / "vendor").exists()
        gitignore = (workspace / ".gitignore").read_text(encoding="utf-8")
        assert GITIGNORE_LINE in gitignore.splitlines()
        root = source_root(workspace, _source(workspace, "acme"))
        assert (root / "skills" / "a.md").is_file()

    def test_a_commit_that_cannot_be_fetched_keeps_the_vendored_copy(self, tmp_path: Path) -> None:
        _repo, workspace, address, _head = self._vendored_05(tmp_path)
        (vendor_dir(workspace, "acme") / ".lock.json").write_text(
            json.dumps({"name": "acme", "source": address, "resolved_sha": "f" * 40}),
            encoding="utf-8",
        )

        applied = apply_layout_migration(workspace)

        assert len(applied.lock_failures) == 1
        assert "`acme`" in applied.lock_failures[0]
        assert (workspace / ".dmx" / "vendor" / "acme").is_dir()
        assert "acme" not in read_lock(workspace)
        assert source_root(workspace, _source(workspace, "acme")) == vendor_dir(workspace, "acme")


class TestTheTool:
    @pytest.mark.asyncio
    async def test_a_failed_update_is_not_reported_as_up_to_date(self, tmp_path: Path) -> None:
        workspace = tmp_path / "app"
        workspace.mkdir()
        _git(workspace, "init", "-q", "-b", "feature")
        _declare(workspace, {"acme": f"git::{tmp_path / 'missing'}?ref=main"})

        from fastmcp import Client

        from dmx.server import create_app

        async with Client(create_app()) as client:
            result = await client.call_tool(
                "update_shared_sources", {"workspace_root": str(workspace)}
            )

        assert str(result.data).startswith("**Shared source update failed:**")
        assert "nothing changed" not in str(result.data)

    @pytest.mark.asyncio
    async def test_returns_the_pr_summary_and_refuses_on_main(self, tmp_path: Path) -> None:
        repo = tmp_path / "skills-repo"
        _source_repo(repo, {"skills/a.md": _SKILL.format(text="a")})
        workspace = tmp_path / "app"
        workspace.mkdir()
        _git(workspace, "init", "-q", "-b", "main")
        _git(workspace, "config", "user.email", "test@example.com")
        _git(workspace, "config", "user.name", "Test")
        _declare(workspace, {"acme": f"git::{repo}?ref=main"})
        (workspace / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        _git(workspace, "add", "-A")
        _git(workspace, "commit", "-q", "-m", "init")

        from fastmcp import Client

        from dmx.server import create_app

        async with Client(create_app()) as client:
            refused = await client.call_tool(
                "update_shared_sources", {"workspace_root": str(workspace)}
            )
            _git(workspace, "checkout", "-q", "-b", "chore-update")
            done = await client.call_tool(
                "update_shared_sources", {"workspace_root": str(workspace)}
            )

        assert "Cannot run `/dmx/update-shared-sources` from `main`" in str(refused.data)
        assert "**Shared sources updated:**" in str(done.data)
        assert "**Summary for the PR description:**" in str(done.data)
        assert "git add .dmx/shared-sources.lock" in str(done.data)


class TestRepoSlug:
    @pytest.mark.parametrize(
        ("url", "slug"),
        [
            ("https://github.com/acme/skills.git", "github.com-acme-skills"),
            ("git@github.com:acme/skills", "github.com-acme-skills"),
        ],
    )
    def test_short_addresses_keep_their_readable_name(self, url: str, slug: str) -> None:
        from dmx.source_cache import repo_slug

        assert repo_slug(url) == slug

    def test_a_long_address_is_capped_and_stays_unique(self) -> None:
        from dmx.source_cache import repo_slug

        deep = "C:/Users/runneradmin/AppData/Local/Temp/pytest-of-runneradmin/pytest-0/"
        first = repo_slug(deep + "test_one/source")
        second = repo_slug(deep + "test_two/source")

        # Short enough for the mirror and cache folders to fit Windows' path limit.
        assert len(first) <= 48
        assert first != second
        assert "source" in first


def test_remove_tree_removes_read_only_files(tmp_path: Path) -> None:
    import stat as stat_module

    from dmx.source_cache import remove_tree

    folder = tmp_path / "objects"
    folder.mkdir()
    locked = folder / "ab"
    locked.write_text("x", encoding="utf-8")
    locked.chmod(stat_module.S_IREAD)

    remove_tree(folder)

    assert not folder.exists()
