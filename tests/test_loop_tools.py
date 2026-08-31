"""Tests for dmx.loop_tools._finish_loop — validator policy + repeat_until wiring.

``_finish_loop`` is a plain function (no MCP ``Context`` dependency), so it
can be exercised directly without mocking the MCP server plumbing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dmx.loop_schema import LoopConfig
from dmx.loop_state import (
    LoopStatus,
    find_active_run,
    make_pending_job_id,
    read_state,
    write_initial_state,
)
from dmx.loop_tools import _find_active, _finish_loop, _maybe_promote_pending_job, _start_loop

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

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


def _allow_spec_loop_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str = "main"
) -> None:
    """Satisfy the spec loop's require_branch guard for tests that call
    _start_loop directly (no real git repo / .dmx/config.md in tmp_path)."""
    (tmp_path / ".dmx").mkdir(exist_ok=True)
    (tmp_path / ".dmx" / "config.md").write_text(f"branch_base: {branch}\n", encoding="utf-8")
    monkeypatch.setattr("dmx.loop_tools.current_branch", lambda _root: branch)


class TestFinishLoopWithoutRepeatUntil:
    def test_all_checks_pass_completes_and_chains(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate(
            {
                "name": "spec",
                "skills": ["create-ticket"],
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
                "on_complete": {"on_success": {"trigger_loop": "plan"}},
            }
        )
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "complete" in message.lower()
        assert "plan" in message
        state = read_state(tmp_path, "J", "spec", "T")
        assert state["status"] == LoopStatus.complete.value
        assert state["outcome"] == "success"

        # on_complete chains automatically — a freshly-started "plan" run is
        # now the active one (job id re-resolves to "unknown": no spec.md
        # or git repo in this bare tmp_path).
        active = find_active_run(tmp_path, "unknown")
        assert active is not None
        assert active[0] == "plan"

    def test_required_failure_with_fail_policy_has_no_active_run(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate(
            {
                "name": "spec",
                "skills": ["create-ticket"],
                "failure_handling": "fail",
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            }
        )
        _write_validator(tmp_path, "v", FAILING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "paused" not in message.lower()
        assert find_active_run(tmp_path, "J") is None
        state = read_state(tmp_path, "J", "spec", "T")
        assert state["status"] == LoopStatus.failed.value

    def test_required_failure_with_pause_policy_stays_active(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate(
            {
                "name": "spec",
                "skills": ["create-ticket"],
                "failure_handling": "pause",
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            }
        )
        _write_validator(tmp_path, "v", FAILING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "paused" in message.lower()
        assert find_active_run(tmp_path, "J") is not None
        state = read_state(tmp_path, "J", "spec", "T")
        assert state["status"] == LoopStatus.paused.value


class TestFinishLoopWithRepeatUntil:
    def _dev_config(self) -> LoopConfig:
        return LoopConfig.model_validate(
            {
                "name": "dev",
                "skills": ["implement-next-phase", "commit"],
                "repeat_until": "all_phases_complete",
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
                "on_complete": {"on_success": {"trigger_loop": "validate"}},
            }
        )

    def test_condition_not_met_iterates(self, tmp_path: Path) -> None:
        config = self._dev_config()
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)
        (tmp_path / ".dmx" / "tasks.md").write_text(
            "## Phase 1: X\n- [x] Done\n## Phase 2: Y\n- [ ] Not done\n", encoding="utf-8"
        )
        # Simulate having advanced past both skills already.
        from dmx.loop_state import write_state

        write_state(
            tmp_path,
            "J",
            "dev",
            "T",
            {
                "current_skill_index": 2,
                "skills_completed": ["implement-next-phase", "commit"],
            },
        )

        message = _finish_loop(tmp_path, "J", "dev", "T", config, {})

        assert "iterating" in message.lower()
        assert "implement-next-phase" in message
        assert find_active_run(tmp_path, "J") is not None
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

        # on_complete chains automatically to the "validate" loop (job id
        # re-resolves to "unknown": no spec.md or git repo in this bare tmp_path).
        active = find_active_run(tmp_path, "unknown")
        assert active is not None
        assert active[0] == "validate"

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

    def test_no_trigger_loop_returns_terminal_message_and_has_no_active_run(
        self, tmp_path: Path
    ) -> None:
        config = LoopConfig.model_validate(
            {
                "name": "spec",
                "skills": ["create-ticket"],
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            }
        )
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "complete" in message.lower()
        assert find_active_run(tmp_path, "J") is None

    def test_trigger_loop_starts_next_loop_with_fresh_task_id(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate(
            {
                "name": "spec",
                "skills": ["create-ticket"],
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
                "on_complete": {"on_success": {"trigger_loop": "plan"}},
            }
        )
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "chaining automatically" in message.lower()
        assert "get_skill_definition" in message
        assert "plan" in message

        # Chained "plan" loop job id re-resolves to "unknown": no spec.md or
        # git repo in this bare tmp_path.
        active = find_active_run(tmp_path, "unknown")
        assert active is not None
        loop_name, task_id = active
        assert loop_name == "plan"
        assert task_id != "T"

        next_state = read_state(tmp_path, "unknown", "plan", task_id)
        assert next_state["status"] == LoopStatus.running.value
        assert next_state["current_skill_index"] == 0

    def test_trigger_loop_applies_to_failure_outcome(self, tmp_path: Path) -> None:
        # trigger_loop targets "validate" rather than "spec": spec declares
        # require_branch, which would reject this chain — that guard is
        # covered separately (see TestBranchGuard), this test is purely
        # about chaining-on-failure mechanics.
        config = LoopConfig.model_validate(
            {
                "name": "dev",
                "skills": ["implement-next-phase"],
                "failure_handling": "fail",
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
                "on_complete": {"on_failure": {"trigger_loop": "validate"}},
            }
        )
        _write_validator(tmp_path, "v", FAILING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "dev", "T", config, {})

        assert "chaining automatically" in message.lower()
        active = find_active_run(tmp_path, "unknown")
        assert active is not None
        assert active[0] == "validate"

    def test_unknown_trigger_loop_surfaces_error_without_crashing(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate(
            {
                "name": "spec",
                "skills": ["create-ticket"],
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
                "on_complete": {"on_success": {"trigger_loop": "does-not-exist"}},
            }
        )
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "spec", "T", config, {})

        assert "error" in message.lower()
        assert "does-not-exist" in message


class TestLoopMemoryHooks:
    """Loop-level memory hooks: read Open Learnings/Decisions before running,
    write a Session Notes breadcrumb when finishing."""

    def test_start_loop_surfaces_open_learnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _allow_spec_loop_start(tmp_path, monkeypatch)
        active_context = tmp_path / ".dmx" / "activeContext.md"
        active_context.write_text(
            "## Open Learnings\n- validators must print JSON on stdout only\n\n"
            "## Open Decisions\n\n## Session Notes\n",
            encoding="utf-8",
        )

        message = _start_loop(tmp_path, "spec")

        assert "Memory context" in message
        assert "validators must print JSON on stdout only" in message
        assert "get_skill_definition" in message
        assert "create-ticket" in message

    def test_start_loop_without_memory_file_has_no_context_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _allow_spec_loop_start(tmp_path, monkeypatch)
        message = _start_loop(tmp_path, "spec")

        assert "Memory context" not in message
        assert "get_skill_definition" in message
        assert "create-ticket" in message

    def test_finish_loop_writes_session_note_on_completion(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate(
            {
                "name": "spec",
                "skills": ["create-ticket"],
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            }
        )
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
        config = LoopConfig.model_validate(
            {
                "name": "spec",
                "skills": ["create-ticket"],
                "failure_handling": "pause",
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            }
        )
        _write_validator(tmp_path, "v", FAILING_VALIDATOR)
        _setup(tmp_path, config)

        _finish_loop(tmp_path, "J", "spec", "T", config, {})

        content = (tmp_path / ".dmx" / "activeContext.md").read_text(encoding="utf-8")
        assert "paused for validator review" in content

    def test_finish_loop_writes_session_note_on_iterating(self, tmp_path: Path) -> None:
        config = LoopConfig.model_validate(
            {
                "name": "dev",
                "skills": ["implement-next-phase", "commit"],
                "repeat_until": "all_phases_complete",
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            }
        )
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)
        (tmp_path / ".dmx" / "tasks.md").write_text(
            "## Phase 1: X\n- [ ] Not done\n", encoding="utf-8"
        )

        _finish_loop(tmp_path, "J", "dev", "T", config, {})

        content = (tmp_path / ".dmx" / "activeContext.md").read_text(encoding="utf-8")
        assert "iterating (round 1)" in content


class TestBranchGuard:
    """GH-9: the spec loop declares require_branch: base — _start_loop must
    reject starting it anywhere else, and must never resolve a real job id
    up front (any pre-existing spec.md/branch is a stale leftover by
    definition for a loop that's about to create a brand new ticket)."""

    def test_blocks_start_from_wrong_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _allow_spec_loop_start(tmp_path, monkeypatch, branch="main")
        monkeypatch.setattr("dmx.loop_tools.current_branch", lambda _root: "feature/gh-1-old-work")

        message = _start_loop(tmp_path, "spec")

        assert "cannot start" in message.lower()
        assert "main" in message
        assert "feature/gh-1-old-work" in message
        assert "get_skill_definition" not in message

    def test_blocks_start_when_branch_base_not_configured(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir(exist_ok=True)

        message = _start_loop(tmp_path, "spec")

        assert "cannot start" in message.lower()
        assert "/dmx/init" in message

    def test_blocks_start_when_branch_unresolvable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _allow_spec_loop_start(tmp_path, monkeypatch, branch="main")
        monkeypatch.setattr("dmx.loop_tools.current_branch", lambda _root: None)

        message = _start_loop(tmp_path, "spec")

        assert "cannot start" in message.lower()

    def test_allows_start_from_configured_base_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _allow_spec_loop_start(tmp_path, monkeypatch, branch="main")

        message = _start_loop(tmp_path, "spec")

        assert "get_skill_definition" in message
        assert "create-ticket" in message

    def test_loops_without_require_branch_are_unaffected(self, tmp_path: Path) -> None:
        # No .dmx/config.md, no branch mocking — "dev" has no require_branch
        # so the guard must not even run.
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        message = _start_loop(tmp_path, "dev")
        assert "cannot start" not in message.lower()
        assert "get_skill_definition" in message

    def test_spec_loop_starts_under_a_pending_job_id_not_resolve_job_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even if a stale spec.md from a previous ticket is still sitting in
        .dmx/ (e.g. main hasn't been cleaned up yet), starting a fresh spec
        loop must not adopt its ticket id — see GH-9."""
        _allow_spec_loop_start(tmp_path, monkeypatch, branch="main")
        (tmp_path / ".dmx" / "spec.md").write_text(
            "---\nticket: OLD-999\n---\n# Stale spec", encoding="utf-8"
        )

        _start_loop(tmp_path, "spec")

        jobs_dir = tmp_path / ".dmx" / "jobs"
        job_names = {p.name for p in jobs_dir.iterdir()}
        assert "OLD-999" not in job_names
        assert any(name.startswith("_pending-") for name in job_names)


class TestFindActiveAndPendingPromotion:
    """_find_active / _maybe_promote_pending_job wiring used by loop_advance
    and loop_continue (see dmx.loop_state for the underlying scan logic)."""

    def test_find_active_falls_back_to_pending_job(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        pending_id = make_pending_job_id("task-1")
        write_initial_state(tmp_path, "spec", pending_id, "task-1", ["create-ticket"])

        found = _find_active(tmp_path)

        assert found == (pending_id, "spec", "task-1")

    def test_find_active_prefers_resolved_job_over_pending(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        (tmp_path / ".dmx" / "spec.md").write_text(
            "---\nticket: GH-7\n---\n# Spec", encoding="utf-8"
        )
        write_initial_state(tmp_path, "plan", "GH-7", "task-real", ["plan"])
        write_initial_state(
            tmp_path, "spec", make_pending_job_id("task-old"), "task-old", ["create-ticket"]
        )

        found = _find_active(tmp_path)

        assert found == ("GH-7", "plan", "task-real")

    def test_find_active_returns_none_when_nothing_active(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        assert _find_active(tmp_path) is None

    def test_promote_renames_once_real_identity_resolvable(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        pending_id = make_pending_job_id("task-1")
        write_initial_state(tmp_path, "spec", pending_id, "task-1", ["create-ticket"])
        (tmp_path / ".dmx" / "spec.md").write_text(
            "---\nticket: GH-9\n---\n# Spec", encoding="utf-8"
        )

        real_job_id = _maybe_promote_pending_job(tmp_path, pending_id)

        assert real_job_id == "GH-9"
        assert not (tmp_path / ".dmx" / "jobs" / pending_id).exists()
        state = read_state(tmp_path, "GH-9", "spec", "task-1")
        assert state["job_id"] == "GH-9"

    def test_promote_is_noop_when_identity_still_unresolvable(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        pending_id = make_pending_job_id("task-1")
        write_initial_state(tmp_path, "spec", pending_id, "task-1", ["create-ticket"])

        result = _maybe_promote_pending_job(tmp_path, pending_id)

        assert result == pending_id
        assert (tmp_path / ".dmx" / "jobs" / pending_id).exists()

    def test_promote_is_noop_for_non_pending_job_id(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        assert _maybe_promote_pending_job(tmp_path, "GH-9") == "GH-9"
