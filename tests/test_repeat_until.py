"""Tests for dmx.repeat_until — deterministic repeat_until evaluation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dmx.repeat_until import evaluate_repeat_until

if TYPE_CHECKING:
    from pathlib import Path


class TestAllPhasesComplete:
    def test_no_tasks_file_is_met(self, tmp_path: Path) -> None:
        assert evaluate_repeat_until("all_phases_complete", tmp_path, "GH-1") is True

    def test_unchecked_task_is_not_met(self, tmp_path: Path) -> None:
        dmx = tmp_path / ".dmx" / "jobs" / "GH-1"
        dmx.mkdir(parents=True)
        (dmx / "tasks.md").write_text(
            "## Phase 1: X\n- [x] Done task\n- [ ] Not done task\n", encoding="utf-8"
        )
        assert evaluate_repeat_until("all_phases_complete", tmp_path, "GH-1") is False

    def test_all_checked_is_met(self, tmp_path: Path) -> None:
        dmx = tmp_path / ".dmx" / "jobs" / "GH-1"
        dmx.mkdir(parents=True)
        (dmx / "tasks.md").write_text(
            "## Phase 1: X\n- [x] Done task\n- [x] Also done\n", encoding="utf-8"
        )
        assert evaluate_repeat_until("all_phases_complete", tmp_path, "GH-1") is True


class TestUnknownCondition:
    def test_unknown_condition_treated_as_met(self, tmp_path: Path) -> None:
        assert evaluate_repeat_until("some_unrecognized_condition", tmp_path, "GH-1") is True
