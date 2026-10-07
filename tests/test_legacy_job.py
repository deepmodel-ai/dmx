"""A 0.4.2 run filed under jobs/none/ is adopted into the branch folder."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from dmx.loop_state import read_state, state_path, write_initial_state, write_state

if TYPE_CHECKING:
    from pathlib import Path

_BRANCH = "feature-upgrade"
_REMOTE_BRANCH = "feature-new"
_RELEASE_PATH = ".dmx/jobs/none/release-old-release.json"
_NOTE = (
    "Moved the in-progress `spec` run from `.dmx/jobs/none/` "
    f"to `.dmx/jobs/{_BRANCH}/` (job ids changed in 0.5.0)."
)
_REMOTE_NOTE = (
    "Moved the in-progress `spec` run from `.dmx/jobs/none/` "
    f"to `.dmx/jobs/{_REMOTE_BRANCH}/` (job ids changed in 0.5.0)."
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _spec(root: Path, ticket: str, branch: str) -> None:
    (root / ".dmx").mkdir(exist_ok=True)
    (root / ".dmx" / "spec.md").write_text(
        f"---\nticket: {ticket}\nbranch: {branch}\n---\n# Spec\n",
        encoding="utf-8",
    )


def _init_main(root: Path, *, branch_base: str | None = "main") -> None:
    _git(root, "init", "-b", "main", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / ".dmx").mkdir()
    if branch_base is None:
        (root / "README.md").write_text("hi\n", encoding="utf-8")
    else:
        (root / ".dmx" / "config.md").write_text(
            f"branch_base: {branch_base}\n",
            encoding="utf-8",
        )
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")


def _feature(root: Path, branch: str = _BRANCH) -> None:
    _git(root, "checkout", "-q", "-b", branch)
    _spec(root, "none", branch)


def _running(root: Path, loop: str, task: str) -> None:
    write_initial_state(root, loop, "none", task, ["ship"], {"status": "running"})


def _commit_jobs(root: Path, message: str) -> None:
    _git(root, "add", ".dmx/jobs")
    _git(root, "commit", "-q", "-m", message)


def _paused_spec(root: Path, job_id: str = "none", task_id: str = "task-1") -> None:
    write_initial_state(
        root,
        "spec",
        job_id,
        task_id,
        ["create-ticket", "plan"],
        {"status": "paused", "current_skill_index": 1},
    )


def _merged_releases(root: Path, tasks: list[str]) -> None:
    """Commit running release runs on main, then check out the feature branch."""
    _init_main(root)
    for task in tasks:
        _running(root, "release", task)
    _commit_jobs(root, "merged releases")
    _feature(root)


def _on_ref(repo: Path, ref: str, path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"],
        cwd=repo,
        capture_output=True,
    )
    return result.returncode == 0


def _developer_behind_origin(tmp_path: Path) -> Path:
    """A clone whose local main is behind the release pushed to origin/main."""
    bare = tmp_path / "origin.git"
    publisher = tmp_path / "publisher"
    developer = tmp_path / "developer"
    publisher.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    _init_main(publisher)
    _git(publisher, "remote", "add", "origin", str(bare))
    _git(publisher, "push", "-q", "-u", "origin", "HEAD")
    _git(tmp_path, "clone", "-q", str(bare), str(developer))
    _git(developer, "config", "user.email", "test@example.com")
    _git(developer, "config", "user.name", "Test")
    _running(publisher, "release", "old-release")
    _commit_jobs(publisher, "merged release")
    _git(publisher, "push", "-q", "origin", "HEAD")
    _git(developer, "fetch", "-q", "origin")
    _git(developer, "checkout", "-q", "-b", _REMOTE_BRANCH, "origin/main")
    _spec(developer, "none", _REMOTE_BRANCH)
    return developer


async def _call(root: Path, tool: str, **arguments: object) -> str:
    from fastmcp import Client

    from dmx.server import create_app

    app = create_app()
    async with Client(app) as client:
        result = await client.call_tool(tool, {"workspace_root": str(root), **arguments})
    return str(result.data)


class TestLegacyJobAdoption:
    @pytest.mark.asyncio
    async def test_paused_run_moves_and_continue_says_so(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _feature(tmp_path)
        _paused_spec(tmp_path)
        write_initial_state(
            tmp_path,
            "dev",
            "none",
            "task-old",
            ["commit"],
            {"status": "complete", "outcome": "success"},
        )

        message = await _call(tmp_path, "loop_continue")

        assert _NOTE in message
        assert message.count(_NOTE) == 1
        assert "plan" in message
        moved = read_state(tmp_path, _BRANCH, "spec", "task-1")
        assert moved["job_id"] == _BRANCH
        assert moved["status"] == "running"
        assert not state_path(tmp_path, "none", "spec", "task-1").exists()
        history = read_state(tmp_path, "none", "dev", "task-old")
        assert history["job_id"] == "none"
        assert history["status"] == "complete"

    @pytest.mark.asyncio
    async def test_branch_mismatch_moves_nothing(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _feature(tmp_path)
        _spec(tmp_path, "none", "other-branch")
        _paused_spec(tmp_path)

        message = await _call(tmp_path, "loop_continue")

        assert "No active loop run found" in message
        assert "Moved the in-progress" not in message
        assert state_path(tmp_path, "none", "spec", "task-1").is_file()

    @pytest.mark.asyncio
    async def test_ticketed_repo_is_unchanged(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _feature(tmp_path)
        _spec(tmp_path, "GH-7", _BRANCH)
        _paused_spec(tmp_path, job_id="GH-7")
        write_initial_state(
            tmp_path,
            "dev",
            "none",
            "task-old",
            ["commit"],
            {"status": "complete", "outcome": "success"},
        )

        message = await _call(tmp_path, "loop_continue")

        assert "Moved the in-progress" not in message
        assert read_state(tmp_path, "GH-7", "spec", "task-1")["status"] == "running"
        assert read_state(tmp_path, "none", "dev", "task-old")["job_id"] == "none"
        assert not state_path(tmp_path, _BRANCH, "spec", "task-1").exists()

    @pytest.mark.asyncio
    async def test_run_loop_does_not_start_beside_the_moved_run(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _feature(tmp_path)
        _paused_spec(tmp_path)

        message = await _call(tmp_path, "run_loop", name="dev")

        assert _NOTE in message
        assert "already in progress" in message
        assert list((tmp_path / ".dmx" / "jobs" / _BRANCH).glob("*.json")) == [
            state_path(tmp_path, _BRANCH, "spec", "task-1")
        ]

    @pytest.mark.asyncio
    async def test_snapshot_moves_on_the_feature_branch(self, tmp_path: Path) -> None:
        _init_main(tmp_path)
        _feature(tmp_path)
        write_initial_state(tmp_path, "spec", "none", "task-snap", ["create-pr"])
        write_state(
            tmp_path,
            "none",
            "spec",
            "task-snap",
            {"status": "complete", "outcome": None, "validator_results": []},
        )

        message = await _call(tmp_path, "loop_status")

        assert "Moved the in-progress `spec` run from `.dmx/jobs/none/`" in message
        assert read_state(tmp_path, _BRANCH, "spec", "task-snap")["job_id"] == _BRANCH
        assert not state_path(tmp_path, "none", "spec", "task-snap").exists()

    def test_two_non_terminal_runs_stay_put(self, tmp_path: Path) -> None:
        from dmx.loop_tools import _find_active

        _init_main(tmp_path)
        _feature(tmp_path)
        _paused_spec(tmp_path, task_id="task-1")
        _paused_spec(tmp_path, task_id="task-2")

        assert _find_active(tmp_path) is None
        assert state_path(tmp_path, "none", "spec", "task-1").is_file()
        assert state_path(tmp_path, "none", "spec", "task-2").is_file()

    @pytest.mark.asyncio
    async def test_in_progress_run_is_adopted_beside_a_merged_release(self, tmp_path: Path) -> None:
        _merged_releases(tmp_path, ["old-release"])
        _paused_spec(tmp_path)
        _commit_jobs(tmp_path, "in progress spec")

        message = await _call(tmp_path, "loop_continue")

        assert _NOTE in message
        assert "plan" in message
        assert read_state(tmp_path, _BRANCH, "spec", "task-1")["status"] == "running"
        assert not state_path(tmp_path, "none", "spec", "task-1").exists()
        history = read_state(tmp_path, "none", "release", "old-release")
        assert history["job_id"] == "none"
        assert history["status"] == "running"

    @pytest.mark.asyncio
    async def test_merged_release_alone_is_not_adopted(self, tmp_path: Path) -> None:
        _merged_releases(tmp_path, ["old-release"])

        status = await _call(tmp_path, "loop_status")
        started = await _call(tmp_path, "run_loop", name="validate")

        assert "Moved the in-progress" not in status
        assert "Moved the in-progress" not in started
        assert "2 non-terminal" not in started
        history = read_state(tmp_path, "none", "release", "old-release")
        assert history["job_id"] == "none"
        assert history["status"] == "running"
        assert not state_path(tmp_path, _BRANCH, "release", "old-release").exists()

    @pytest.mark.asyncio
    async def test_two_merged_releases_leave_the_in_progress_run(self, tmp_path: Path) -> None:
        _merged_releases(tmp_path, ["old-release", "older-release"])
        _paused_spec(tmp_path)
        _commit_jobs(tmp_path, "in progress spec")

        message = await _call(tmp_path, "loop_continue")

        assert _NOTE in message
        assert read_state(tmp_path, _BRANCH, "spec", "task-1")["job_id"] == _BRANCH
        assert read_state(tmp_path, "none", "release", "old-release")["job_id"] == "none"
        assert read_state(tmp_path, "none", "release", "older-release")["job_id"] == "none"

    @pytest.mark.asyncio
    async def test_missing_branch_base_adopts_nothing(self, tmp_path: Path) -> None:
        _init_main(tmp_path, branch_base=None)
        _feature(tmp_path)
        _paused_spec(tmp_path)

        message = await _call(tmp_path, "loop_continue")

        assert "Moved the in-progress" not in message
        assert state_path(tmp_path, "none", "spec", "task-1").is_file()

    @pytest.mark.asyncio
    async def test_unknown_branch_base_adopts_nothing(self, tmp_path: Path) -> None:
        _init_main(tmp_path, branch_base="no-such-branch")
        _feature(tmp_path)
        _paused_spec(tmp_path)

        message = await _call(tmp_path, "loop_continue")

        assert "Moved the in-progress" not in message
        assert state_path(tmp_path, "none", "spec", "task-1").is_file()

    @pytest.mark.asyncio
    async def test_without_git_adopts_nothing(self, tmp_path: Path) -> None:
        _spec(tmp_path, "none", _BRANCH)
        _paused_spec(tmp_path)

        message = await _call(tmp_path, "loop_continue")

        assert "Moved the in-progress" not in message
        assert state_path(tmp_path, "none", "spec", "task-1").is_file()

    @pytest.mark.asyncio
    async def test_behind_origin_does_not_adopt_the_merged_release(self, tmp_path: Path) -> None:
        developer = _developer_behind_origin(tmp_path)
        assert not _on_ref(developer, "main", _RELEASE_PATH)
        assert _on_ref(developer, "origin/main", _RELEASE_PATH)

        status = await _call(developer, "loop_status")
        continued = await _call(developer, "loop_continue")

        assert "Moved the in-progress" not in status
        assert "Moved the in-progress" not in continued
        history = read_state(developer, "none", "release", "old-release")
        assert history["job_id"] == "none"
        assert history["status"] == "running"
        assert not state_path(developer, _REMOTE_BRANCH, "release", "old-release").exists()

    @pytest.mark.asyncio
    async def test_behind_origin_still_adopts_the_in_progress_run(self, tmp_path: Path) -> None:
        developer = _developer_behind_origin(tmp_path)
        _paused_spec(developer)
        _commit_jobs(developer, "in progress spec")

        message = await _call(developer, "loop_continue")

        assert _REMOTE_NOTE in message
        assert "plan" in message
        assert read_state(developer, _REMOTE_BRANCH, "spec", "task-1")["status"] == "running"
        assert not state_path(developer, "none", "spec", "task-1").exists()
        history = read_state(developer, "none", "release", "old-release")
        assert history["job_id"] == "none"
        assert history["status"] == "running"
