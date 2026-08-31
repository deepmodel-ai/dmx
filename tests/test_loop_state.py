"""Tests for dmx.loop_state — state persistence and job ID resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dmx.exceptions import AmbiguousActiveRun
from dmx.loop_state import (
    LoopStatus,
    find_active_run,
    find_pending_run,
    is_pending_job_id,
    make_pending_job_id,
    make_task_id,
    read_state,
    rename_job,
    resolve_job_id,
    state_path,
    write_initial_state,
    write_state,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# make_task_id
# ---------------------------------------------------------------------------


def test_make_task_id_is_uuid4_format() -> None:
    tid = make_task_id()
    assert len(tid) == 36
    parts = tid.split("-")
    assert len(parts) == 5
    assert parts[2][0] == "4"  # UUID version 4


def test_make_task_id_is_unique() -> None:
    assert make_task_id() != make_task_id()


# ---------------------------------------------------------------------------
# resolve_job_id
# ---------------------------------------------------------------------------


class TestResolveJobId:
    def test_reads_ticket_from_spec_md(self, tmp_path: Path) -> None:
        # spec.md frontmatter uses the key `ticket` (see dmx-create-ticket.md,
        # dmx-derive-ticket.md, dmx-hotfix.md) — not `ticket_id`.
        dmx = tmp_path / ".dmx"
        dmx.mkdir()
        spec = dmx / "spec.md"
        spec.write_text(
            "---\nticket: PAY-1234\ntitle: My ticket\n---\n\n# Spec",
            encoding="utf-8",
        )
        assert resolve_job_id(tmp_path) == "PAY-1234"

    def test_quoted_ticket(self, tmp_path: Path) -> None:
        dmx = tmp_path / ".dmx"
        dmx.mkdir()
        spec = dmx / "spec.md"
        spec.write_text(
            '---\nticket: "GH-42"\n---\n',
            encoding="utf-8",
        )
        assert resolve_job_id(tmp_path) == "GH-42"

    def test_falls_back_to_branch_name(self, tmp_path: Path) -> None:
        # No spec.md — use branch. tmp_path is not a git repo, falls back to "unknown".
        result = resolve_job_id(tmp_path)
        # Should not raise; returns a string.
        assert isinstance(result, str)

    def test_unknown_fallback_when_no_git(self, tmp_path: Path) -> None:
        result = resolve_job_id(tmp_path)
        # Not a git repo, no spec.md → "unknown" fallback.
        assert result == "unknown"


# ---------------------------------------------------------------------------
# write_initial_state / read_state
# ---------------------------------------------------------------------------


class TestStateIO:
    def _workspace(self, tmp_path: Path) -> Path:
        (tmp_path / ".dmx").mkdir()
        return tmp_path

    def test_write_creates_state_file(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        p = write_initial_state(root, "spec", "PAY-1", "task-uuid", ["dmx-create-ticket"])
        assert p.exists()

    def test_initial_state_fields(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "spec", "PAY-1", "task-uuid", ["skill-a", "skill-b"])
        state = read_state(root, "PAY-1", "spec", "task-uuid")
        assert state["loop_name"] == "spec"
        assert state["job_id"] == "PAY-1"
        assert state["task_id"] == "task-uuid"
        assert state["status"] == LoopStatus.pending.value
        assert state["current_skill_index"] == 0
        assert state["skills"] == ["skill-a", "skill-b"]
        assert state["skills_completed"] == []
        assert state["skill_outputs"] == {}
        assert state["validator_results"] == []
        assert state["outcome"] is None

    def test_state_path_structure(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        p = state_path(root, "PAY-1", "spec", "abc")
        assert p == root / ".dmx" / "jobs" / "PAY-1" / "spec-abc.json"

    def test_write_state_merges(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "spec", "JOB", "TASK", ["s"])
        updated = write_state(
            root,
            "JOB",
            "spec",
            "TASK",
            {
                "status": LoopStatus.running.value,
                "current_skill_index": 1,
            },
        )
        assert updated["status"] == LoopStatus.running.value
        assert updated["current_skill_index"] == 1
        # Ensure other fields preserved.
        assert updated["loop_name"] == "spec"

    def test_multiple_runs_same_job(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "spec", "PAY-1", "task-1", ["s"])
        write_initial_state(root, "dev", "PAY-1", "task-2", ["s"])
        # Both state files exist under the same job directory.
        job_dir = root / ".dmx" / "jobs" / "PAY-1"
        files = list(job_dir.glob("*.json"))
        assert len(files) == 2
        names = {f.stem for f in files}
        assert "spec-task-1" in names
        assert "dev-task-2" in names


# ---------------------------------------------------------------------------
# make_pending_job_id / is_pending_job_id
# ---------------------------------------------------------------------------


class TestPendingJobId:
    def test_make_pending_job_id_uses_short_task_id(self) -> None:
        job_id = make_pending_job_id("abcd1234-5678-90ab-cdef-1234567890ab")
        assert job_id == "_pending-abcd1234"

    def test_is_pending_job_id(self) -> None:
        assert is_pending_job_id("_pending-abcd1234") is True
        assert is_pending_job_id("PAY-1234") is False
        assert is_pending_job_id("main") is False


# ---------------------------------------------------------------------------
# find_active_run — scan-based lookup, replaces the old active-pointer file
# (see GH-9: a single mutable pointer file doesn't survive branch switches)
# ---------------------------------------------------------------------------


class TestFindActiveRun:
    def _workspace(self, tmp_path: Path) -> Path:
        (tmp_path / ".dmx").mkdir()
        return tmp_path

    def test_no_job_dir_returns_none(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        assert find_active_run(root, "PAY-1") is None

    def test_finds_running_run(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "dev", "PAY-1", "task-1", ["s"])
        write_state(root, "PAY-1", "dev", "task-1", {"status": LoopStatus.running.value})
        assert find_active_run(root, "PAY-1") == ("dev", "task-1")

    def test_finds_paused_run(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "dev", "PAY-1", "task-1", ["s"])
        write_state(root, "PAY-1", "dev", "task-1", {"status": LoopStatus.paused.value})
        assert find_active_run(root, "PAY-1") == ("dev", "task-1")

    def test_finds_iterating_run(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "dev", "PAY-1", "task-1", ["s"])
        write_state(root, "PAY-1", "dev", "task-1", {"status": LoopStatus.iterating.value})
        assert find_active_run(root, "PAY-1") == ("dev", "task-1")

    def test_ignores_complete_run(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "dev", "PAY-1", "task-1", ["s"])
        write_state(root, "PAY-1", "dev", "task-1", {"status": LoopStatus.complete.value})
        assert find_active_run(root, "PAY-1") is None

    def test_ignores_failed_run(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "dev", "PAY-1", "task-1", ["s"])
        write_state(root, "PAY-1", "dev", "task-1", {"status": LoopStatus.failed.value})
        assert find_active_run(root, "PAY-1") is None

    def test_finds_the_one_active_run_among_completed_history(self, tmp_path: Path) -> None:
        """A job accumulates one completed file per loop across a ticket's
        lifecycle (spec -> plan -> dev -> validate -> release) — only the
        current one should be non-terminal at any given time."""
        root = self._workspace(tmp_path)
        write_initial_state(root, "spec", "PAY-1", "task-1", ["s"])
        write_state(root, "PAY-1", "spec", "task-1", {"status": LoopStatus.complete.value})
        write_initial_state(root, "plan", "PAY-1", "task-2", ["s"])
        write_state(root, "PAY-1", "plan", "task-2", {"status": LoopStatus.paused.value})
        assert find_active_run(root, "PAY-1") == ("plan", "task-2")

    def test_multiple_non_terminal_runs_raises_ambiguous(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "spec", "PAY-1", "task-1", ["s"])
        write_initial_state(root, "plan", "PAY-1", "task-2", ["s"])
        with pytest.raises(AmbiguousActiveRun, match="PAY-1"):
            find_active_run(root, "PAY-1")

    def test_ignores_malformed_state_file(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        job_dir = root / ".dmx" / "jobs" / "PAY-1"
        job_dir.mkdir(parents=True)
        (job_dir / "dev-broken.json").write_text("not json", encoding="utf-8")
        assert find_active_run(root, "PAY-1") is None


# ---------------------------------------------------------------------------
# find_pending_run — resuming a loop before its real job id is resolvable
# ---------------------------------------------------------------------------


class TestFindPendingRun:
    def _workspace(self, tmp_path: Path) -> Path:
        (tmp_path / ".dmx").mkdir()
        return tmp_path

    def test_no_jobs_dir_returns_none(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        assert find_pending_run(root) is None

    def test_no_pending_jobs_returns_none(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "dev", "PAY-1", "task-1", ["s"])
        assert find_pending_run(root) is None

    def test_finds_pending_job(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        pending_id = make_pending_job_id("task-1")
        write_initial_state(root, "spec", pending_id, "task-1", ["s"])
        assert find_pending_run(root) == (pending_id, "spec", "task-1")

    def test_ignores_completed_pending_job(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        pending_id = make_pending_job_id("task-1")
        write_initial_state(root, "spec", pending_id, "task-1", ["s"])
        write_state(root, pending_id, "spec", "task-1", {"status": LoopStatus.complete.value})
        assert find_pending_run(root) is None

    def test_multiple_pending_jobs_raises_ambiguous(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "spec", make_pending_job_id("task-1"), "task-1", ["s"])
        write_initial_state(root, "spec", make_pending_job_id("task-2"), "task-2", ["s"])
        with pytest.raises(AmbiguousActiveRun, match="pending"):
            find_pending_run(root)


# ---------------------------------------------------------------------------
# rename_job — promoting a pending job once its real identity is known
# ---------------------------------------------------------------------------


class TestRenameJob:
    def _workspace(self, tmp_path: Path) -> Path:
        (tmp_path / ".dmx").mkdir()
        return tmp_path

    def test_moves_state_files_and_rewrites_job_id(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        pending_id = make_pending_job_id("task-1")
        write_initial_state(root, "spec", pending_id, "task-1", ["s"])

        rename_job(root, pending_id, "gh-42")

        assert not (root / ".dmx" / "jobs" / pending_id).exists()
        state = read_state(root, "gh-42", "spec", "task-1")
        assert state["job_id"] == "gh-42"

    def test_noop_when_old_dir_missing(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        rename_job(root, "_pending-doesnotexist", "gh-42")
        assert not (root / ".dmx" / "jobs" / "gh-42").exists()

    def test_merges_into_existing_destination(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "plan", "gh-42", "task-existing", ["s"])
        pending_id = make_pending_job_id("task-new")
        write_initial_state(root, "spec", pending_id, "task-new", ["s"])

        rename_job(root, pending_id, "gh-42")

        job_dir = root / ".dmx" / "jobs" / "gh-42"
        names = {f.stem for f in job_dir.glob("*.json")}
        assert names == {"plan-task-existing", "spec-task-new"}
