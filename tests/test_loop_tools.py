"""Tests for dmx.loop_tools._finish_loop — validator policy + repeat_until wiring.

``_finish_loop`` is a plain function (no MCP ``Context`` dependency), so it
can be exercised directly without mocking the MCP server plumbing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dmx.loop_schema import LoopConfig
from dmx.loop_state import LoopStatus, read_active_pointer, read_state, write_initial_state
from dmx.loop_tools import _finish_loop, _start_loop

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
        state = read_state(tmp_path, "J", "spec", "T")
        assert state["status"] == LoopStatus.complete.value
        assert state["outcome"] == "success"

        # on_complete chains automatically — the active pointer now points to
        # the freshly-started "plan" loop rather than being cleared.
        pointer = read_active_pointer(tmp_path)
        assert pointer is not None
        assert pointer["active_loop_name"] == "plan"

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
        state = read_state(tmp_path, "J", "dev", "T")
        assert state["status"] == LoopStatus.complete.value

        # on_complete chains automatically to the "validate" loop.
        pointer = read_active_pointer(tmp_path)
        assert pointer is not None
        assert pointer["active_loop_name"] == "validate"

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


class TestOnCompleteChaining:
    """on_complete.trigger_loop starts the next loop directly — no second
    run_loop round-trip required from the agent."""

    def test_no_trigger_loop_returns_terminal_message_and_clears_pointer(
        self, tmp_path: Path
    ) -> None:
        config = LoopConfig.model_validate({
            "name": "spec",
            "skills": ["create-ticket"],
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
        })
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "complete" in message.lower()
        assert read_active_pointer(tmp_path) is None

    def test_trigger_loop_starts_next_loop_with_fresh_task_id(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate({
            "name": "spec",
            "skills": ["create-ticket"],
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            "on_complete": {"on_success": {"trigger_loop": "plan"}},
        })
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "chaining automatically" in message.lower()
        assert "run /plan now" in message.lower()

        pointer = read_active_pointer(tmp_path)
        assert pointer is not None
        assert pointer["active_loop_name"] == "plan"
        assert pointer["active_task_id"] != "T"

        next_state = read_state(
            tmp_path, pointer["active_job_id"], "plan", pointer["active_task_id"]
        )
        assert next_state["status"] == LoopStatus.running.value
        assert next_state["current_skill_index"] == 0

    def test_trigger_loop_applies_to_failure_outcome(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate({
            "name": "dev",
            "skills": ["implement-next-phase"],
            "failure_handling": "fail",
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            "on_complete": {"on_failure": {"trigger_loop": "spec"}},
        })
        _write_validator(tmp_path, "v", FAILING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "dev", "T", config, {})

        assert "chaining automatically" in message.lower()
        pointer = read_active_pointer(tmp_path)
        assert pointer is not None
        assert pointer["active_loop_name"] == "spec"

    def test_unknown_trigger_loop_surfaces_error_without_crashing(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate({
            "name": "spec",
            "skills": ["create-ticket"],
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            "on_complete": {"on_success": {"trigger_loop": "does-not-exist"}},
        })
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "error" in message.lower()
        assert "does-not-exist" in message


class TestLoopMemoryHooks:
    """Loop-level memory hooks: read Open Learnings/Decisions before running,
    write a Session Notes breadcrumb when finishing."""

    def test_start_loop_surfaces_open_learnings(self, tmp_path: Path) -> None:
        active_context = tmp_path / ".dmx" / "activeContext.md"
        active_context.parent.mkdir(parents=True)
        active_context.write_text(
            "## Open Learnings\n- validators must print JSON on stdout only\n\n"
            "## Open Decisions\n\n## Session Notes\n",
            encoding="utf-8",
        )

        message = _start_loop(tmp_path, "spec")

        assert "Memory context" in message
        assert "validators must print JSON on stdout only" in message
        assert "run /create-ticket now" in message.lower()

    def test_start_loop_without_memory_file_has_no_context_block(self, tmp_path: Path) -> None:
        message = _start_loop(tmp_path, "spec")

        assert "Memory context" not in message
        assert "run /create-ticket now" in message.lower()

    def test_finish_loop_writes_session_note_on_completion(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate({
            "name": "spec",
            "skills": ["create-ticket"],
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
        })
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)

        _finish_loop(tmp_path, "J", "spec", "T", config, {})

        active_context = tmp_path / ".dmx" / "activeContext.md"
        assert active_context.exists()
        content = active_context.read_text(encoding="utf-8")
        assert "## Session Notes" in content
        assert "spec loop completed" in content
        assert "J" in content

    def test_finish_loop_writes_session_note_on_validator_pause(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate({
            "name": "spec",
            "skills": ["create-ticket"],
            "failure_handling": "pause",
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
        })
        _write_validator(tmp_path, "v", FAILING_VALIDATOR)
        _setup(tmp_path, config)

        _finish_loop(tmp_path, "J", "spec", "T", config, {})

        content = (tmp_path / ".dmx" / "activeContext.md").read_text(encoding="utf-8")
        assert "paused for validator review" in content

    def test_finish_loop_writes_session_note_on_iterating(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate({
            "name": "dev",
            "skills": ["implement-next-phase", "commit"],
            "repeat_until": "all_phases_complete",
            "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
        })
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)
        (tmp_path / ".dmx" / "tasks.md").write_text(
            "## Phase 1: X\n- [ ] Not done\n", encoding="utf-8"
        )

        _finish_loop(tmp_path, "J", "dev", "T", config, {})

        content = (tmp_path / ".dmx" / "activeContext.md").read_text(encoding="utf-8")
        assert "iterating (round 1)" in content
