"""GH-84: ``prune-jobs`` removes finished job folders before a PR opens."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from fastmcp import Client

from dmx.cli import main
from dmx.job_pruning import PruneError, plan_prune, prune_jobs, removal_message
from dmx.loop_state import write_initial_state, write_state
from dmx.server import create_app

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
_OLD = (_NOW - timedelta(days=45)).isoformat()
_RECENT = (_NOW - timedelta(days=3)).isoformat()
_MONTH = timedelta(days=30)


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **env} if env else None,
    )
    return result.stdout.strip()


def _commit(root: Path, message: str, *, date: str | None = None) -> None:
    _git(root, "add", "-A")
    env = {"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else None
    _git(root, "commit", "-q", "-m", message, env=env)


def _job(
    root: Path,
    job: str,
    *,
    branch: str | None = None,
    status: str = "complete",
    updated_at: str = _OLD,
    learnings: str = "",
    runs: bool = True,
) -> Path:
    folder = root / ".dmx" / "jobs" / job
    folder.mkdir(parents=True)
    (folder / "spec.md").write_text(
        f"---\nticket: {job}\nbranch: {branch or f'feature-{job.lower()}'}\n---\n# {job}\n",
        encoding="utf-8",
    )
    (folder / "activeContext.md").write_text(
        f"## Open Learnings\n{learnings}\n## Open Decisions\n\n## Session Notes\n- done\n",
        encoding="utf-8",
    )
    if runs:
        write_initial_state(root, "dev", job, "T1", ["implement-next-phase"])
        state = write_state(root, job, "dev", "T1", {"status": status})
        state["updated_at"] = updated_at
        (folder / "dev-T1.json").write_text(json.dumps(state), encoding="utf-8")
    return folder


def _repo(root: Path) -> None:
    """``main`` with config; the caller adds merged jobs, then branches."""
    _git(root, "init", "-b", "main", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / ".dmx").mkdir()
    (root / ".dmx" / "config.md").write_text(
        "branch_base: main\nproduction_branch: release\n", encoding="utf-8"
    )
    _commit(root, "init")


def _merged(root: Path, *jobs: str, **kwargs: object) -> None:
    for job in jobs:
        _job(root, job, **kwargs)  # type: ignore[arg-type]
    _commit(root, f"merge {' '.join(jobs)}")


def _on_ticket(root: Path, branch: str = "feature-gh-9") -> None:
    _git(root, "checkout", "-q", "-b", branch)


def _kept(root: Path) -> dict[str, str]:
    return dict(plan_prune(root, _MONTH, now=_NOW).kept)


class TestRemoval:
    def test_an_old_finished_merged_job_goes_in_its_own_commit(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1", "GH-2")
        _on_ticket(tmp_path)
        (tmp_path / "work.py").write_text("x = 1\n", encoding="utf-8")
        _git(tmp_path, "add", "work.py")

        plan = prune_jobs(tmp_path, _MONTH, now=_NOW)

        assert plan.removes == ("GH-1", "GH-2")
        assert plan.commit == _git(tmp_path, "rev-parse", "--short", "HEAD")
        assert _git(tmp_path, "log", "-1", "--format=%s") == "chore: remove 2 finished jobs"
        changed = _git(tmp_path, "show", "--name-only", "--format=", "HEAD").splitlines()
        assert changed
        assert all(path.startswith((".dmx/jobs/GH-1/", ".dmx/jobs/GH-2/")) for path in changed)
        assert not (tmp_path / ".dmx" / "jobs" / "GH-1").exists()
        assert _git(tmp_path, "diff", "--cached", "--name-only") == "work.py"
        assert _git(tmp_path, "show", "HEAD~1:.dmx/jobs/GH-1/spec.md").startswith("---")

    def test_one_job_has_a_singular_message(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1")
        _on_ticket(tmp_path)

        assert prune_jobs(tmp_path, _MONTH, now=_NOW).message == "chore: remove 1 finished job"
        assert removal_message(3) == "chore: remove 3 finished jobs"

    def test_a_failed_run_counts_as_finished(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1", status="failed")
        _on_ticket(tmp_path)

        assert plan_prune(tmp_path, _MONTH, now=_NOW).removes == ("GH-1",)

    def test_a_job_with_no_runs_uses_its_last_commit_date(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _job(tmp_path, "GH-1", runs=False)
        _commit(tmp_path, "old ticket", date=_OLD)
        _job(tmp_path, "GH-2", runs=False)
        _commit(tmp_path, "new ticket", date=_RECENT)
        _on_ticket(tmp_path)

        plan = plan_prune(tmp_path, _MONTH, now=_NOW)

        assert plan.removes == ("GH-1",)
        assert dict(plan.kept) == {"GH-2": "finished 3 days ago"}

    def test_ignored_leftovers_go_after_the_commit(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        (tmp_path / ".gitignore").write_text("local.log\n", encoding="utf-8")
        _merged(tmp_path, "GH-1")
        (tmp_path / ".dmx" / "jobs" / "GH-1" / "local.log").write_text("x", encoding="utf-8")
        _on_ticket(tmp_path)

        prune_jobs(tmp_path, _MONTH, now=_NOW)

        assert not (tmp_path / ".dmx" / "jobs" / "GH-1").exists()

    def test_a_shorter_age_removes_newer_jobs(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1", updated_at=(_NOW - timedelta(days=10)).isoformat())
        _on_ticket(tmp_path)

        assert plan_prune(tmp_path, _MONTH, now=_NOW).removes == ()
        assert plan_prune(tmp_path, timedelta(days=7), now=_NOW).removes == ("GH-1",)

    def test_a_dmx_folder_below_the_git_top(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        app = tmp_path / "services" / "app"
        (app / ".dmx").mkdir(parents=True)
        (app / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        _job(app, "GH-1")
        _commit(tmp_path, "merge")
        _on_ticket(tmp_path)

        plan = prune_jobs(app, _MONTH, now=_NOW)

        assert plan.removes == ("GH-1",)
        assert not (app / ".dmx" / "jobs" / "GH-1").exists()
        assert _git(tmp_path, "status", "--porcelain") == ""

    def test_a_failed_commit_restores_the_folders(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1")
        _on_ticket(tmp_path)
        hook = tmp_path / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        with pytest.raises(PruneError, match="refused"):
            prune_jobs(tmp_path, _MONTH, now=_NOW)

        assert (tmp_path / ".dmx" / "jobs" / "GH-1" / "spec.md").is_file()
        assert _git(tmp_path, "status", "--porcelain") == ""


class TestKeep:
    def test_the_current_ticket(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-9", branch="feature-gh-9")
        _on_ticket(tmp_path, "feature-gh-9")

        assert _kept(tmp_path) == {"GH-9": "the current ticket"}

    def test_a_folder_named_after_the_branch(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "feature-gh-9", branch="something-else")
        _on_ticket(tmp_path, "feature-gh-9")

        assert _kept(tmp_path) == {"feature-gh-9": "the current ticket"}

    def test_an_unfinished_run(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1", status="paused")
        _on_ticket(tmp_path)

        assert _kept(tmp_path) == {"GH-1": "the dev loop is paused"}

    def test_an_unreadable_run(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _job(tmp_path, "GH-1")
        (tmp_path / ".dmx" / "jobs" / "GH-1" / "dev-T2.json").write_text("{", encoding="utf-8")
        _commit(tmp_path, "merge")
        _on_ticket(tmp_path)

        assert _kept(tmp_path) == {"GH-1": "`dev-T2.json` can't be read"}

    def test_open_learnings(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1", learnings="- the cache needs a lock\n")
        _on_ticket(tmp_path)

        assert _kept(tmp_path) == {"GH-1": "open learnings or decisions in activeContext.md"}

    def test_learnings_that_were_all_folded(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1", learnings="- [ledger] the cache needs a lock → ledger, #12\n")
        _on_ticket(tmp_path)

        assert plan_prune(tmp_path, _MONTH, now=_NOW).removes == ("GH-1",)

    def test_one_learning_still_waiting(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(
            tmp_path,
            "GH-1",
            learnings="- [ledger] folded → ledger, #12\n- [billing-ui] still waiting\n",
        )
        _on_ticket(tmp_path)

        assert _kept(tmp_path) == {"GH-1": "open learnings or decisions in activeContext.md"}

    def test_open_decisions(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        folder = _job(tmp_path, "GH-1")
        (folder / "activeContext.md").write_text(
            "## Open Learnings\n\n## Open Decisions\n- pick a queue\n", encoding="utf-8"
        )
        _commit(tmp_path, "merge")
        _on_ticket(tmp_path)

        assert _kept(tmp_path) == {"GH-1": "open learnings or decisions in activeContext.md"}

    def test_a_job_not_on_the_base_branch(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _on_ticket(tmp_path)
        _merged(tmp_path, "GH-3")

        assert _kept(tmp_path) == {"GH-3": "not on `main`"}

    def test_the_remote_base_branch_wins(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1")
        _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD~1")
        _on_ticket(tmp_path)

        assert _kept(tmp_path) == {"GH-1": "not on `origin/main`"}

    def test_a_newer_job(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1", updated_at=_RECENT)
        _on_ticket(tmp_path)

        assert _kept(tmp_path) == {"GH-1": "finished 3 days ago"}

    def test_uncommitted_changes(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        folder = _job(tmp_path, "GH-1")
        _commit(tmp_path, "merge")
        _on_ticket(tmp_path)
        (folder / "tasks.md").write_text("- [ ] more\n", encoding="utf-8")

        assert _kept(tmp_path) == {"GH-1": "uncommitted changes"}

    def test_a_folder_git_no_longer_tracks(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        (tmp_path / ".gitignore").write_text("local.log\n", encoding="utf-8")
        _merged(tmp_path, "GH-1")
        _on_ticket(tmp_path)
        _git(tmp_path, "rm", "-r", "-q", "--cached", ".dmx/jobs/GH-1")
        (tmp_path / ".gitignore").write_text("local.log\n.dmx/jobs/GH-1/\n", encoding="utf-8")
        _commit(tmp_path, "stop tracking GH-1")

        assert _kept(tmp_path) == {"GH-1": "no files tracked by git"}


class TestRefusal:
    @pytest.mark.parametrize("branch", ["main", "release"])
    def test_a_protected_branch(self, tmp_path: Path, branch: str) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1")
        if branch != "main":
            _git(tmp_path, "checkout", "-q", "-b", branch)

        plan = prune_jobs(tmp_path, _MONTH, now=_NOW)

        assert f"`{branch}` is a protected branch" in (plan.blocked or "")
        assert (tmp_path / ".dmx" / "jobs" / "GH-1").is_dir()

    def test_a_detached_head(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1")
        _git(tmp_path, "checkout", "-q", "--detach")

        assert "HEAD is detached" in (prune_jobs(tmp_path, _MONTH, now=_NOW).blocked or "")

    def test_a_dry_run_changes_nothing(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1")
        _on_ticket(tmp_path)
        head = _git(tmp_path, "rev-parse", "HEAD")

        assert plan_prune(tmp_path, _MONTH, now=_NOW).removes == ("GH-1",)
        assert _git(tmp_path, "rev-parse", "HEAD") == head
        assert (tmp_path / ".dmx" / "jobs" / "GH-1").is_dir()


class TestCli:
    def _invoke(self, root: Path, *args: str) -> tuple[int, str]:
        result = CliRunner().invoke(main, ["prune-jobs", "--workspace-root", str(root), *args])
        return result.exit_code, result.output

    def test_older_than_takes_days(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        recent = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        _merged(tmp_path, "GH-1", updated_at=recent)
        _on_ticket(tmp_path)

        code, output = self._invoke(tmp_path)
        assert code == 0
        assert "GH-1: finished 10 days ago" in output
        assert (tmp_path / ".dmx" / "jobs" / "GH-1").is_dir()

        code, output = self._invoke(tmp_path, "--older-than", "7d")
        assert code == 0
        assert "Removed 1 finished job" in output
        assert _git(tmp_path, "log", "-1", "--format=%s") == "chore: remove 1 finished job"

    def test_dry_run(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1")
        _on_ticket(tmp_path)

        code, output = self._invoke(tmp_path, "--dry-run")

        assert code == 0
        assert "Would remove 1 finished job: GH-1" in output
        assert (tmp_path / ".dmx" / "jobs" / "GH-1").is_dir()

    def test_a_bad_age(self, tmp_path: Path) -> None:
        _repo(tmp_path)

        code, output = self._invoke(tmp_path, "--older-than", "2w")

        assert code != 0
        assert "number of days" in output

    def test_refused_on_the_base_branch(self, tmp_path: Path) -> None:
        _repo(tmp_path)

        code, output = self._invoke(tmp_path)

        assert code == 1
        assert "protected branch" in output

    def test_found_from_a_subfolder(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1")
        _on_ticket(tmp_path)
        (tmp_path / "src").mkdir()
        monkeypatch.chdir(tmp_path / "src")

        result = CliRunner().invoke(main, ["prune-jobs", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Would remove 1 finished job: GH-1" in result.output


class TestReleaseLoop:
    @pytest.mark.asyncio
    async def test_the_bundled_loop_prunes_before_create_pr(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _on_ticket(tmp_path)

        async with Client(create_app()) as client:
            result = await client.call_tool(
                "run_loop", {"name": "release", "workspace_root": str(tmp_path)}
            )

        assert "Next skill: `prune-jobs` (Loop: release | Skill 1/2)" in result.data

    @pytest.mark.asyncio
    async def test_a_repo_release_loop_without_the_step_keeps_its_jobs(
        self, tmp_path: Path
    ) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1")
        loops = tmp_path / ".dmx" / "loops"
        loops.mkdir()
        (loops / "release.yaml").write_text(
            "name: release\nskills:\n  - create-pr\ntrigger:\n  type: on_complete\n"
            'goal_state: "PR created"\nvalidators: []\n',
            encoding="utf-8",
        )
        _commit(tmp_path, "keep job history")
        _on_ticket(tmp_path)

        async with Client(create_app()) as client:
            result = await client.call_tool(
                "run_loop", {"name": "release", "workspace_root": str(tmp_path)}
            )

        assert "Next skill: `create-pr` (Loop: release | Skill 1/1)" in result.data
        assert "prune-jobs" not in result.data
        assert (tmp_path / ".dmx" / "jobs" / "GH-1" / "spec.md").is_file()


class TestTool:
    @pytest.mark.asyncio
    async def test_dry_run_then_remove(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        _merged(tmp_path, "GH-1")
        _merged(tmp_path, "GH-2", learnings="- keep me\n")
        _on_ticket(tmp_path)

        async with Client(create_app()) as client:
            planned = await client.call_tool(
                "prune_jobs", {"dry_run": True, "workspace_root": str(tmp_path)}
            )
            done = await client.call_tool("prune_jobs", {"workspace_root": str(tmp_path)})

        assert planned.data["applied"] is False
        assert planned.data["removes"] == ["GH-1"]
        assert planned.data["kept"] == [
            {"job": "GH-2", "reason": "open learnings or decisions in activeContext.md"}
        ]
        assert done.data["applied"] is True
        assert done.data["commit_message"] == "chore: remove 1 finished job"
        assert done.data["commit"] == _git(tmp_path, "rev-parse", "--short", "HEAD")

    @pytest.mark.asyncio
    async def test_refusals_are_reported_not_raised(self, tmp_path: Path) -> None:
        _repo(tmp_path)

        async with Client(create_app()) as client:
            on_main = await client.call_tool("prune_jobs", {"workspace_root": str(tmp_path)})
            negative = await client.call_tool(
                "prune_jobs", {"older_than_days": -1, "workspace_root": str(tmp_path)}
            )

        assert "protected branch" in on_main.data["blocked"]
        assert on_main.data["applied"] is False
        assert "negative" in negative.data["blocked"]
