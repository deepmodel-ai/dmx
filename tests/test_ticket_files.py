"""GH-84: each ticket's spec.md, tasks.md and activeContext.md live in its job folder."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from dmx.exceptions import AmbiguousActiveRun
from dmx.loop_state import (
    find_active_run,
    jobs_for_branch,
    make_pending_job_id,
    rename_job,
    resolve_job_id,
    ticket_file,
    write_initial_state,
)
from dmx.validators import check_plan_complete, check_pr_ready, check_spec_complete

if TYPE_CHECKING:
    from pathlib import Path

_BRANCH = "feature-gh-84"
_SPEC = (
    "---\nticket: {ticket}\nbranch: {branch}\n---\n"
    "Q: What?\nA: A widget.\n\n"
    "## Technical Approach\nAdd one function and a test that calls it.\n\n"
    "## Scope\n- Add the widget\n"
)
_TASKS = "## Phase 1: Widget\n- [x] Implement the widget\n"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _init_main(root: Path) -> None:
    _git(root, "init", "-b", "main", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / ".dmx").mkdir()
    (root / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")


def _job_spec(root: Path, job_id: str, ticket: str, branch: str) -> Path:
    path = ticket_file(root, job_id, "spec.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SPEC.format(ticket=ticket, branch=branch), encoding="utf-8")
    return path


def _root_spec(root: Path, ticket: str, branch: str) -> None:
    (root / ".dmx" / "spec.md").write_text(
        _SPEC.format(ticket=ticket, branch=branch), encoding="utf-8"
    )


async def _call(root: Path, tool: str, **arguments: object) -> str:
    from fastmcp import Client

    from dmx.server import create_app

    async with Client(create_app()) as client:
        result = await client.call_tool(tool, {"workspace_root": str(root), **arguments})
    return str(result.data)


class TestResolveJobIdFromTheBranch:
    def test_the_job_whose_spec_names_the_branch(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _job_spec(tmp_path, "GH-84", "GH-84", _BRANCH)
        _job_spec(tmp_path, "GH-83", "GH-83", "bug-gh-83-other")

        assert jobs_for_branch(tmp_path, _BRANCH) == ["GH-84"]
        assert resolve_job_id(tmp_path) == "GH-84"

    def test_no_matching_spec_falls_back_to_the_branch(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _job_spec(tmp_path, "GH-83", "GH-83", "bug-gh-83-other")

        assert resolve_job_id(tmp_path) == _BRANCH

    def test_a_pending_folder_is_not_an_identity(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _job_spec(tmp_path, make_pending_job_id("abcdef12-0000"), "GH-84", _BRANCH)

        assert resolve_job_id(tmp_path) == _BRANCH

    def test_several_matches_prefer_the_one_with_a_live_run(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _job_spec(tmp_path, "GH-80", "GH-80", _BRANCH)
        _job_spec(tmp_path, "GH-84", "GH-84", _BRANCH)
        write_initial_state(tmp_path, "dev", "GH-84", "task-1", ["commit"], {"status": "running"})

        assert resolve_job_id(tmp_path) == "GH-84"

    def test_several_matches_without_one_live_run_are_ambiguous(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _job_spec(tmp_path, "GH-80", "GH-80", _BRANCH)
        _job_spec(tmp_path, "GH-84", "GH-84", _BRANCH)

        with pytest.raises(AmbiguousActiveRun, match="2 job folders name branch"):
            resolve_job_id(tmp_path)

    def test_a_05_root_spec_still_names_the_job(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _root_spec(tmp_path, "GH-84", _BRANCH)

        assert resolve_job_id(tmp_path) == "GH-84"

    def test_a_job_folder_spec_wins_over_a_stale_root_spec(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _root_spec(tmp_path, "GH-1", "bug-gh-1-old")
        _job_spec(tmp_path, "GH-84", "GH-84", _BRANCH)

        assert resolve_job_id(tmp_path) == "GH-84"


class TestRenameJobMovesEveryFile:
    def test_ticket_files_and_artifacts_move_with_the_state(self, tmp_path: Path) -> None:
        pending = make_pending_job_id("abcdef12-0000")
        write_initial_state(tmp_path, "spec", pending, "task-1", ["create-ticket"])
        _job_spec(tmp_path, pending, "GH-84", _BRANCH)
        ticket_file(tmp_path, pending, "activeContext.md").write_text("notes\n", encoding="utf-8")
        report = ticket_file(tmp_path, pending, "validation-report.json")
        report.write_text('{"commit": "abc"}', encoding="utf-8")

        rename_job(tmp_path, pending, "GH-84")

        assert not (tmp_path / ".dmx" / "jobs" / pending).exists()
        assert ticket_file(tmp_path, "GH-84", "spec.md").is_file()
        assert ticket_file(tmp_path, "GH-84", "activeContext.md").read_text() == "notes\n"
        assert ticket_file(tmp_path, "GH-84", "validation-report.json").read_text() == (
            '{"commit": "abc"}'
        )
        assert find_active_run(tmp_path, "GH-84") == ("spec", "task-1")

    def test_an_existing_file_in_the_target_is_kept(self, tmp_path: Path) -> None:
        pending = make_pending_job_id("abcdef12-0000")
        _job_spec(tmp_path, pending, "GH-84", _BRANCH)
        existing = _job_spec(tmp_path, "GH-84", "GH-84", "keep-me")

        rename_job(tmp_path, pending, "GH-84")

        assert "keep-me" in existing.read_text(encoding="utf-8")
        assert ticket_file(tmp_path, pending, "spec.md").is_file()


class TestSpecLoopWritesIntoItsPendingFolder:
    @pytest.mark.asyncio
    async def test_promotion_carries_the_spec_and_notes(self, tmp_path: Path) -> None:
        _init_main(tmp_path)

        started = await _call(tmp_path, "run_loop", name="spec")
        pending = next(
            path.name for path in (tmp_path / ".dmx" / "jobs").iterdir() if path.is_dir()
        )
        assert f"Ticket files: `.dmx/jobs/{pending}/spec.md`" in started
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _job_spec(tmp_path, pending, "GH-84", _BRANCH)
        ticket_file(tmp_path, pending, "activeContext.md").write_text(
            "## Open Learnings\n- keep this\n", encoding="utf-8"
        )

        await _call(tmp_path, "loop_advance", output="created ticket", skill="create-ticket")

        assert not (tmp_path / ".dmx" / "jobs" / pending).exists()
        assert ticket_file(tmp_path, "GH-84", "spec.md").is_file()
        assert "keep this" in ticket_file(tmp_path, "GH-84", "activeContext.md").read_text()
        assert not (tmp_path / ".dmx" / "spec.md").exists()


class TestA05RepoMidTicket:
    def _mid_ticket(self, root: Path) -> None:
        _init_main(root)
        _git(root, "checkout", "-q", "-b", _BRANCH)
        _root_spec(root, "GH-84", _BRANCH)
        (root / ".dmx" / "tasks.md").write_text(_TASKS, encoding="utf-8")
        (root / ".dmx" / "activeContext.md").write_text("## Open Learnings\n", encoding="utf-8")
        write_initial_state(
            root,
            "dev",
            "GH-84",
            "task-1",
            ["implement-next-phase", "commit"],
            {"status": "running"},
        )

    @pytest.mark.asyncio
    async def test_the_next_write_moves_the_root_files_and_says_so(self, tmp_path: Path) -> None:
        self._mid_ticket(tmp_path)

        message = await _call(
            tmp_path, "loop_advance", output="implemented", skill="implement-next-phase"
        )

        assert (
            "Moved `spec.md`, `tasks.md`, `activeContext.md` from `.dmx/` into "
            "`.dmx/jobs/GH-84/` (ticket files live in the job folder since 0.6)."
        ) in message
        for name in ("spec.md", "tasks.md", "activeContext.md"):
            assert not (tmp_path / ".dmx" / name).exists(), name
            assert ticket_file(tmp_path, "GH-84", name).is_file(), name

    @pytest.mark.asyncio
    async def test_loop_status_changes_nothing(self, tmp_path: Path) -> None:
        self._mid_ticket(tmp_path)
        before = sorted(str(path) for path in (tmp_path / ".dmx").rglob("*"))

        message = await _call(tmp_path, "loop_status")

        assert "Moved" not in message
        assert sorted(str(path) for path in (tmp_path / ".dmx").rglob("*")) == before

    @pytest.mark.asyncio
    async def test_a_stale_root_spec_from_another_ticket_stays(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _root_spec(tmp_path, "GH-1", "bug-gh-1-merged")
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)

        await _call(tmp_path, "run_loop", name="dev")

        assert (tmp_path / ".dmx" / "spec.md").is_file()
        assert not ticket_file(tmp_path, _BRANCH, "spec.md").exists()

    @pytest.mark.asyncio
    async def test_a_05_tasks_file_written_after_the_move_follows(self, tmp_path: Path) -> None:
        # A plan skill made for 0.5 writes the root tasks.md after spec.md moved.
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _job_spec(tmp_path, "GH-84", "GH-84", _BRANCH)
        write_initial_state(tmp_path, "plan", "GH-84", "task-1", ["plan"], {"status": "running"})
        (tmp_path / ".dmx" / "tasks.md").write_text(_TASKS, encoding="utf-8")

        await _call(tmp_path, "loop_advance", output="planned", skill="plan")

        assert not (tmp_path / ".dmx" / "tasks.md").exists()
        assert ticket_file(tmp_path, "GH-84", "tasks.md").read_text() == _TASKS


class TestValidatorsReadTheJobFolder:
    def _context(self, root: Path) -> dict[str, str]:
        return {
            "spec_path": str(ticket_file(root, "GH-84", "spec.md")),
            "tasks_path": str(ticket_file(root, "GH-84", "tasks.md")),
            "active_context_path": str(ticket_file(root, "GH-84", "activeContext.md")),
        }

    def test_spec_and_plan_checks_use_the_paths_they_are_given(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _job_spec(tmp_path, "GH-84", "GH-84", _BRANCH)
        ticket_file(tmp_path, "GH-84", "tasks.md").write_text(_TASKS, encoding="utf-8")

        assert check_spec_complete.run(tmp_path, self._context(tmp_path))["pass"] is True
        assert check_plan_complete.run(tmp_path, self._context(tmp_path))["pass"] is True
        # Without the paths, a hand-run check reads the 0.5 root files.
        assert check_spec_complete.run(tmp_path)["pass"] is False
        assert check_plan_complete.run(tmp_path)["pass"] is False

    def test_a_commit_of_only_the_spec_is_not_a_memory_update(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        _job_spec(tmp_path, "GH-84", "GH-84", _BRANCH)
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-q", "-m", "spec only")

        passed, message = check_pr_ready._memory_updated(tmp_path, self._context(tmp_path))

        assert passed is False, message

    def test_a_commit_of_the_jobs_inbox_is_a_memory_update(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        inbox = ticket_file(tmp_path, "GH-84", "activeContext.md")
        inbox.parent.mkdir(parents=True)
        inbox.write_text("## Open Learnings\n- a learning\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-q", "-m", "learning")

        passed, message = check_pr_ready._memory_updated(tmp_path, self._context(tmp_path))

        assert passed is True, message

    def test_a_commit_of_repo_wide_memory_is_a_memory_update(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        (tmp_path / ".dmx" / "techContext.md").write_text("# Tech\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-q", "-m", "memory")

        passed, message = check_pr_ready._memory_updated(tmp_path, self._context(tmp_path))

        assert passed is True, message


class TestParallelTicketsMerge:
    def test_two_tickets_merge_in_either_order(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        for ticket, branch in (("GH-1", "feature-gh-1"), ("GH-2", "feature-gh-2")):
            _git(tmp_path, "checkout", "-q", "-b", branch, "main")
            _job_spec(tmp_path, ticket, ticket, branch)
            ticket_file(tmp_path, ticket, "tasks.md").write_text(_TASKS, encoding="utf-8")
            ticket_file(tmp_path, ticket, "activeContext.md").write_text(
                f"- note from {ticket}\n", encoding="utf-8"
            )
            _git(tmp_path, "add", ".")
            _git(tmp_path, "commit", "-q", "-m", ticket)

        for first, second in (("feature-gh-1", "feature-gh-2"), ("feature-gh-2", "feature-gh-1")):
            _git(tmp_path, "checkout", "-q", "-B", f"merge-{first}", "main")
            _git(tmp_path, "merge", "-q", "--no-edit", first)
            merged = subprocess.run(
                ["git", "merge", "--no-edit", second],
                cwd=tmp_path,
                capture_output=True,
                text=True,
            )
            assert merged.returncode == 0, merged.stdout + merged.stderr
