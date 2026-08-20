"""Tests for dmx.loop_tools._finish_loop — validator policy + repeat_until wiring.

``_finish_loop`` is a plain function (no MCP ``Context`` dependency), so it
can be exercised directly without mocking the MCP server plumbing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dmx.loop_schema import LoopConfig
from dmx.loop_state import LoopStatus, read_active_pointer, read_state, write_initial_state
from dmx.loop_tools import _finish_loop

if TYPE_CHECKING:
    from pathlib import Path

PASSING_VALIDATOR = """\
import json, sys
print(json.dumps({"pass": True, "message": "ok", "checks": [{"name": "check_a", "pass": True}]}))
sys.exit(0)
"""

FAILING_VALIDATOR = """\
import json, sys
print(json.dumps({"pass": False, "message": "bad", "checks": [{"name": "check_a", "pass": False}]}))
sys.exit(1)
"""


def _write_validator(workspace_root: Path, name: str, body: str) -> None:
    path = workspace_root / "validators" / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _setup(tmp_path: Path, config: LoopConfig, job_id: str = "J", task_id: str = "T") -> Path:
    (tmp_path / ".dmx").mkdir(exist_ok=True)
    write_initial_state(tmp_path, config.name, job_id, task_id, config.skills)
    return tmp_path


class TestFinishLoopWithoutRepeatUntil:
    def test_all_checks_pass_completes_and_clears_pointer(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate({
            "name": "spec",
            "skills": ["create-ticket"],
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            "on_complete": {"on_success": {"trigger_loop": "plan"}},
        })
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "complete" in message.lower()
        assert "plan" in message
        assert read_active_pointer(tmp_path) is None
        state = read_state(tmp_path, "J", "spec", "T")
        assert state["status"] == LoopStatus.complete.value
        assert state["outcome"] == "success"

    def test_required_failure_with_fail_policy_clears_pointer(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate({
            "name": "spec",
            "skills": ["create-ticket"],
            "failure_handling": "fail",
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
        })
        _write_validator(tmp_path, "v", FAILING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "paused" not in message.lower()
        assert read_active_pointer(tmp_path) is None
        state = read_state(tmp_path, "J", "spec", "T")
        assert state["status"] == LoopStatus.failed.value

    def test_required_failure_with_pause_policy_keeps_pointer(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate({
            "name": "spec",
            "skills": ["create-ticket"],
            "failure_handling": "pause",
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
        })
        _write_validator(tmp_path, "v", FAILING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "paused" in message.lower()
        assert read_active_pointer(tmp_path) is not None
        state = read_state(tmp_path, "J", "spec", "T")
        assert state["status"] == LoopStatus.paused.value


class TestFinishLoopWithRepeatUntil:
    def _dev_config(self) -> LoopConfig:
        return LoopConfig.model_validate({
            "name": "dev",
            "skills": ["implement-next-phase", "commit"],
            "repeat_until": "all_phases_complete",
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            "on_complete": {"on_success": {"trigger_loop": "validate"}},
        })

    def test_condition_not_met_iterates(self, tmp_path: Path) -> None:
        config = self._dev_config()
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)
        (tmp_path / ".dmx" / "tasks.md").write_text(
            "## Phase 1: X\n- [x] Done\n## Phase 2: Y\n- [ ] Not done\n", encoding="utf-8"
        )
        # Simulate having advanced past both skills already.
        from dmx.loop_state import write_state
        write_state(tmp_path, "J", "dev", "T", {
            "current_skill_index": 2,
            "skills_completed": ["implement-next-phase", "commit"],
        })

        message = _finish_loop(tmp_path, "J", "dev", "T", config, {})

        assert "iterating" in message.lower()
        assert "implement-next-phase" in message
        assert read_active_pointer(tmp_path) is not None
        state = read_state(tmp_path, "J", "dev", "T")
        assert state["status"] == LoopStatus.iterating.value
        assert state["iteration_count"] == 1
        assert state["current_skill_index"] == 0
        assert state["skills_completed"] == []

    def test_condition_met_completes_and_chains(self, tmp_path: Path) -> None:
        config = self._dev_config()
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)
        (tmp_path / ".dmx" / "tasks.md").write_text(
            "## Phase 1: X\n- [x] Done\n## Phase 2: Y\n- [x] Also done\n", encoding="utf-8"
        )

        message = _finish_loop(tmp_path, "J", "dev", "T", config, {})

        assert "iterating" not in message.lower()
        assert "validate" in message
        assert read_active_pointer(tmp_path) is None
        state = read_state(tmp_path, "J", "dev", "T")
        assert state["status"] == LoopStatus.complete.value

    def test_repeated_iteration_increments_count(self, tmp_path: Path) -> None:
        config = self._dev_config()
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)
        (tmp_path / ".dmx" / "tasks.md").write_text("- [ ] Still not done\n", encoding="utf-8")

        _finish_loop(tmp_path, "J", "dev", "T", config, {})
        state_after_first = read_state(tmp_path, "J", "dev", "T")
        assert state_after_first["iteration_count"] == 1

        _finish_loop(tmp_path, "J", "dev", "T", config, {})
        state_after_second = read_state(tmp_path, "J", "dev", "T")
        assert state_after_second["iteration_count"] == 2
