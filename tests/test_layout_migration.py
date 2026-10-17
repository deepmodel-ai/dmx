"""GH-84: ``/dmx/upgrade``'s move from the 0.5 layout, and branches that span it."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from dmx.layout_migration import (
    GITATTRIBUTES_LINE,
    apply_layout_migration,
    plan_layout_migration,
)
from dmx.loop_state import ticket_file, write_initial_state

if TYPE_CHECKING:
    from pathlib import Path

_SPEC = "---\nticket: {ticket}\nbranch: {branch}\n---\n# Spec {ticket}\n{body}\n"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _root_files(root: Path, ticket: str, branch: str, body: str = "body") -> None:
    dmx = root / ".dmx"
    (dmx / "spec.md").write_text(
        _SPEC.format(ticket=ticket, branch=branch, body=body), encoding="utf-8"
    )
    (dmx / "tasks.md").write_text(f"## Phase 1: {ticket}\n- [ ] work\n", encoding="utf-8")
    (dmx / "activeContext.md").write_text(
        f"## Open Learnings\n- learned on {ticket}\n\n## Open Decisions\n\n## Session Notes\n",
        encoding="utf-8",
    )


def _repo_on_05(root: Path) -> None:
    """A 0.5 repo: main carries GH-1's leftover root ticket files."""
    _git(root, "init", "-b", "main", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / ".dmx").mkdir()
    (root / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
    _root_files(root, "GH-1", "feature-gh-1")
    _commit(root, "GH-1 merged on 0.5")


class TestPlan:
    def test_refused_on_the_integration_branch(self, tmp_path: Path) -> None:
        _repo_on_05(tmp_path)

        plan = plan_layout_migration(tmp_path)

        assert plan.blocked is not None
        assert "`main` is a protected branch" in plan.blocked
        assert apply_layout_migration(tmp_path).blocked == plan.blocked
        assert (tmp_path / ".dmx" / "spec.md").is_file()

    def test_refused_on_a_detached_head(self, tmp_path: Path) -> None:
        _repo_on_05(tmp_path)
        _git(tmp_path, "checkout", "-q", "--detach")

        assert "HEAD is detached" in (plan_layout_migration(tmp_path).blocked or "")

    def test_an_earlier_tickets_leftovers_are_deleted(self, tmp_path: Path) -> None:
        _repo_on_05(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", "chore-dmx-upgrade")

        plan = plan_layout_migration(tmp_path)

        assert plan.blocked is None
        assert plan.moves == ()
        assert plan.deletes == (".dmx/spec.md", ".dmx/tasks.md", ".dmx/activeContext.md")
        assert "learned on GH-1" in plan.open_items
        assert plan.adds_gitattributes is True

    def test_this_branchs_files_move_into_its_job_folder(self, tmp_path: Path) -> None:
        _repo_on_05(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", "feature-gh-2")
        _root_files(tmp_path, "GH-2", "feature-gh-2")

        plan = plan_layout_migration(tmp_path)

        assert plan.deletes == ()
        assert plan.moves == (
            (".dmx/spec.md", ".dmx/jobs/GH-2/spec.md"),
            (".dmx/tasks.md", ".dmx/jobs/GH-2/tasks.md"),
            (".dmx/activeContext.md", ".dmx/jobs/GH-2/activeContext.md"),
        )
        assert plan.open_items == ""

    def test_a_file_the_job_folder_already_has_is_kept(self, tmp_path: Path) -> None:
        _repo_on_05(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", "feature-gh-2")
        _root_files(tmp_path, "GH-2", "feature-gh-2")
        existing = ticket_file(tmp_path, "GH-2", "tasks.md")
        existing.parent.mkdir(parents=True)
        existing.write_text("## Phase 1: already here\n", encoding="utf-8")

        plan = plan_layout_migration(tmp_path)

        assert plan.kept == (".dmx/tasks.md",)
        assert (".dmx/tasks.md", ".dmx/jobs/GH-2/tasks.md") not in plan.moves

    def test_custom_code_naming_the_old_paths_is_listed(self, tmp_path: Path) -> None:
        _repo_on_05(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", "chore-dmx-upgrade")
        (tmp_path / "validators").mkdir()
        (tmp_path / "validators" / "check_spec.py").write_text(
            'spec = root / ".dmx" / "spec.md"\nok = True\n', encoding="utf-8"
        )
        (tmp_path / ".dmx" / "skills").mkdir()
        (tmp_path / ".dmx" / "skills" / "review.md").write_text(
            "Read the plan.\nOpen `.dmx/tasks.md` first.\n", encoding="utf-8"
        )
        vendored = tmp_path / ".dmx" / "vendor" / "acme" / "loops"
        vendored.mkdir(parents=True)
        (vendored / "ship.yaml").write_text(
            "# uses .dmx/vendor/acme/skills\nname: ship\n", encoding="utf-8"
        )

        plan = plan_layout_migration(tmp_path)

        assert plan.references == (
            'validators/check_spec.py:1: spec = root / ".dmx" / "spec.md"',
            ".dmx/skills/review.md:2: Open `.dmx/tasks.md` first.",
            ".dmx/vendor/acme/loops/ship.yaml:1: # uses .dmx/vendor/acme/skills",
        )

    def test_the_gitattributes_line_is_added_once(self, tmp_path: Path) -> None:
        _repo_on_05(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", "chore-dmx-upgrade")
        (tmp_path / ".gitattributes").write_text("*.png binary", encoding="utf-8")

        apply_layout_migration(tmp_path)

        assert (tmp_path / ".gitattributes").read_text(encoding="utf-8") == (
            f"*.png binary\n{GITATTRIBUTES_LINE}\n"
        )
        assert plan_layout_migration(tmp_path).adds_gitattributes is False


class TestApply:
    def test_moves_and_deletes_then_nothing_is_left(self, tmp_path: Path) -> None:
        _repo_on_05(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", "feature-gh-2")
        _root_files(tmp_path, "GH-2", "feature-gh-2", body="the GH-2 spec")

        apply_layout_migration(tmp_path)

        for name in ("spec.md", "tasks.md", "activeContext.md"):
            assert not (tmp_path / ".dmx" / name).exists(), name
        assert "the GH-2 spec" in ticket_file(tmp_path, "GH-2", "spec.md").read_text()
        again = plan_layout_migration(tmp_path)
        assert not again.has_changes

    @pytest.mark.asyncio
    async def test_the_tool_only_changes_files_when_dry_run_is_false(self, tmp_path: Path) -> None:
        _repo_on_05(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", "chore-dmx-upgrade")

        from fastmcp import Client

        from dmx.server import create_app

        async with Client(create_app()) as client:
            dry = await client.call_tool("migrate_dmx_layout", {"workspace_root": str(tmp_path)})
            assert (tmp_path / ".dmx" / "spec.md").is_file()
            done = await client.call_tool(
                "migrate_dmx_layout", {"dry_run": False, "workspace_root": str(tmp_path)}
            )

        assert dry.data["applied"] is False
        assert dry.data["deletes"] == [".dmx/spec.md", ".dmx/tasks.md", ".dmx/activeContext.md"]
        assert done.data["applied"] is True
        assert not (tmp_path / ".dmx" / "spec.md").exists()


class TestABranchThatSpansTheUpgrade:
    """A branch still on the 0.5 layout while main migrates."""

    def _main_upgraded_and_gh2_on_05(self, root: Path) -> None:
        _repo_on_05(root)
        _git(root, "checkout", "-q", "-b", "feature-gh-2")
        _root_files(root, "GH-2", "feature-gh-2", body="the GH-2 spec")
        _commit(root, "GH-2 on the 0.5 layout")
        _git(root, "checkout", "-q", "-b", "chore-dmx-upgrade", "main")
        apply_layout_migration(root)
        _commit(root, "upgrade")
        _git(root, "checkout", "-q", "main")
        _git(root, "merge", "-q", "--no-edit", "chore-dmx-upgrade")
        _git(root, "checkout", "-q", "feature-gh-2")

    @pytest.mark.asyncio
    async def test_merging_main_conflicts_visibly_and_resolves_in_one_step(
        self, tmp_path: Path
    ) -> None:
        self._main_upgraded_and_gh2_on_05(tmp_path)
        write_initial_state(
            tmp_path, "dev", "GH-2", "task-1", ["implement-next-phase"], {"status": "running"}
        )
        _commit(tmp_path, "GH-2 run")

        merged = subprocess.run(
            ["git", "merge", "--no-edit", "main"], cwd=tmp_path, capture_output=True, text=True
        )
        assert merged.returncode != 0
        assert "CONFLICT (modify/delete)" in merged.stdout
        # Nothing landed in another ticket's folder.
        assert not (tmp_path / ".dmx" / "jobs" / "GH-1").exists()

        # The documented step: keep this branch's version of each ticket file.
        _git(tmp_path, "add", ".dmx/spec.md", ".dmx/tasks.md", ".dmx/activeContext.md")
        _git(tmp_path, "commit", "-q", "--no-edit")

        from tests.test_ticket_files import _call

        message = await _call(
            tmp_path, "loop_advance", output="implemented", skill="implement-next-phase"
        )

        assert "Moved `spec.md`, `tasks.md`, `activeContext.md`" in message
        assert "the GH-2 spec" in ticket_file(tmp_path, "GH-2", "spec.md").read_text()
        assert not (tmp_path / ".dmx" / "spec.md").exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("integrate", ["merge", "rebase"])
    async def test_files_a_merge_put_in_another_tickets_folder_are_moved_back(
        self, tmp_path: Path, integrate: str
    ) -> None:
        # Main got GH-1's root files as a rename into jobs/GH-1 (for example
        # through a merge of a 0.5-era move). Merging or rebasing GH-2's 0.5
        # branch onto that puts GH-2's edits in jobs/GH-1 with no conflict.
        _repo_on_05(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", "feature-gh-2")
        _root_files(tmp_path, "GH-2", "feature-gh-2", body="the GH-2 spec")
        _commit(tmp_path, "GH-2 on the 0.5 layout")
        _git(tmp_path, "checkout", "-q", "main")
        (tmp_path / ".dmx" / "jobs" / "GH-1").mkdir(parents=True)
        for name in ("spec.md", "tasks.md", "activeContext.md"):
            _git(tmp_path, "mv", f".dmx/{name}", f".dmx/jobs/GH-1/{name}")
        _git(tmp_path, "commit", "-q", "-m", "rename GH-1's files")
        _git(tmp_path, "checkout", "-q", "feature-gh-2")
        write_initial_state(
            tmp_path, "dev", "GH-2", "task-1", ["implement-next-phase"], {"status": "running"}
        )
        _commit(tmp_path, "GH-2 run")
        if integrate == "merge":
            _git(tmp_path, "merge", "-q", "--no-edit", "main")
        else:
            _git(tmp_path, "rebase", "-q", "main")
        misplaced = ticket_file(tmp_path, "GH-1", "spec.md").read_text(encoding="utf-8")
        assert "the GH-2 spec" in misplaced  # the silent case

        from tests.test_ticket_files import _call

        message = await _call(
            tmp_path, "loop_advance", output="implemented", skill="implement-next-phase"
        )

        assert (
            "A merge had put this branch's `spec.md`, `tasks.md`, `activeContext.md` in "
            "`.dmx/jobs/GH-1/`. Moved them to `.dmx/jobs/GH-2/` and restored "
            "`.dmx/jobs/GH-1/` from `main`. Commit both folders."
        ) in message
        assert "No active loop run found" not in message
        assert "the GH-2 spec" in ticket_file(tmp_path, "GH-2", "spec.md").read_text()
        restored = ticket_file(tmp_path, "GH-1", "spec.md").read_text(encoding="utf-8")
        assert restored == _git(tmp_path, "show", "main:.dmx/jobs/GH-1/spec.md") + "\n"

    @pytest.mark.asyncio
    async def test_a_misplaced_file_its_own_folder_already_has_is_left_for_the_developer(
        self, tmp_path: Path
    ) -> None:
        _repo_on_05(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", "feature-gh-2")
        _root_files(tmp_path, "GH-2", "feature-gh-2", body="the GH-2 spec")
        _commit(tmp_path, "GH-2 on the 0.5 layout")
        _git(tmp_path, "checkout", "-q", "main")
        (tmp_path / ".dmx" / "jobs" / "GH-1").mkdir(parents=True)
        for name in ("spec.md", "tasks.md", "activeContext.md"):
            _git(tmp_path, "mv", f".dmx/{name}", f".dmx/jobs/GH-1/{name}")
        _git(tmp_path, "commit", "-q", "-m", "rename GH-1's files")
        _git(tmp_path, "checkout", "-q", "feature-gh-2")
        _git(tmp_path, "merge", "-q", "--no-edit", "main")
        own_spec = ticket_file(tmp_path, "GH-2", "spec.md")
        own_spec.parent.mkdir(parents=True, exist_ok=True)
        own_spec.write_text(
            _SPEC.format(ticket="GH-2", branch="feature-gh-2", body="a newer GH-2 spec"),
            encoding="utf-8",
        )
        write_initial_state(
            tmp_path, "dev", "GH-2", "task-1", ["implement-next-phase"], {"status": "running"}
        )

        from tests.test_ticket_files import _call

        message = await _call(
            tmp_path, "loop_advance", output="implemented", skill="implement-next-phase"
        )

        assert (
            "`.dmx/jobs/GH-1/` also has this branch's `spec.md`, but `.dmx/jobs/GH-2/` "
            "already has different ones. Merge them into `.dmx/jobs/GH-2/` by hand"
        ) in message
        assert "a newer GH-2 spec" in own_spec.read_text(encoding="utf-8")
        assert "the GH-2 spec" in ticket_file(tmp_path, "GH-1", "spec.md").read_text()
        # The files with no copy in GH-2's folder still moved.
        assert ticket_file(tmp_path, "GH-2", "tasks.md").is_file()

    def test_moving_first_then_merging_keeps_each_ticket_in_its_folder(
        self, tmp_path: Path
    ) -> None:
        self._main_upgraded_and_gh2_on_05(tmp_path)

        apply_layout_migration(tmp_path)  # /dmx/sync-branch Step 4b
        _commit(tmp_path, "chore: move ticket files into the job folder")
        merged = subprocess.run(
            ["git", "merge", "--no-edit", "main"], cwd=tmp_path, capture_output=True, text=True
        )
        if merged.returncode != 0:
            conflicted = _git(tmp_path, "diff", "--name-only", "--diff-filter=U").splitlines()
            assert all(path.startswith(".dmx/") for path in conflicted), conflicted
            _git(tmp_path, "add", *conflicted)
            _git(tmp_path, "commit", "-q", "--no-edit")

        assert not (tmp_path / ".dmx" / "jobs" / "GH-1").exists()
        assert "the GH-2 spec" in ticket_file(tmp_path, "GH-2", "spec.md").read_text()
        for name in ("spec.md", "tasks.md", "activeContext.md"):
            assert not (tmp_path / ".dmx" / name).exists(), name
