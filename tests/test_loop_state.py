"""Tests for dmx.loop_state — state persistence and job ID resolution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dmx.loop_state import (
    LoopStatus,
    active_pointer_path,
    clear_active_pointer,
    make_task_id,
    read_active_pointer,
    read_state,
    resolve_job_id,
    state_path,
    write_initial_state,
    write_state,
)


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
    def test_reads_ticket_id_from_spec_md(self, tmp_path: Path) -> None:
        dmx = tmp_path / ".dmx"
        dmx.mkdir()
        spec = dmx / "spec.md"
        spec.write_text(
            "---\nticket_id: PAY-1234\ntitle: My ticket\n---\n\n# Spec",
            encoding="utf-8",
        )
        assert resolve_job_id(tmp_path) == "PAY-1234"

    def test_quoted_ticket_id(self, tmp_path: Path) -> None:
        dmx = tmp_path / ".dmx"
        dmx.mkdir()
        spec = dmx / "spec.md"
        spec.write_text(
            '---\nticket_id: "GH-42"\n---\n',
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

    def test_active_pointer_written(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "spec", "PAY-1", "task-uuid", ["skill-a"])
        pointer = read_active_pointer(root)
        assert pointer is not None
        assert pointer["active_job_id"] == "PAY-1"
        assert pointer["active_task_id"] == "task-uuid"
        assert pointer["active_loop_name"] == "spec"

    def test_state_path_structure(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        p = state_path(root, "PAY-1", "spec", "abc")
        assert p == root / ".dmx" / "jobs" / "PAY-1" / "spec-abc.json"

    def test_write_state_merges(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "spec", "JOB", "TASK", ["s"])
        updated = write_state(root, "JOB", "spec", "TASK", {
            "status": LoopStatus.running.value,
            "current_skill_index": 1,
        })
        assert updated["status"] == LoopStatus.running.value
        assert updated["current_skill_index"] == 1
        # Ensure other fields preserved.
        assert updated["loop_name"] == "spec"

    def test_clear_active_pointer(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        write_initial_state(root, "spec", "J", "T", ["s"])
        assert active_pointer_path(root).exists()
        clear_active_pointer(root)
        assert not active_pointer_path(root).exists()
        # Idempotent.
        clear_active_pointer(root)

    def test_no_active_pointer_returns_none(self, tmp_path: Path) -> None:
        root = self._workspace(tmp_path)
        assert read_active_pointer(root) is None

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
