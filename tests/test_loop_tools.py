"""Tests for dmx.loop_tools._finish_loop — validator policy + repeat_until wiring.

``_finish_loop`` is a plain function (no MCP ``Context`` dependency), so it
can be exercised directly without mocking the MCP server plumbing.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from dmx.loop_schema import LoopConfig
from dmx.loop_state import (
    LoopStatus,
    find_active_run,
    make_pending_job_id,
    read_state,
    write_initial_state,
)
from dmx.loop_tools import (
    _commit_dmx_state,
    _find_active,
    _finish_loop,
    _maybe_promote_pending_job,
    _resolve_loop,
    _resolve_skill,
    _start_loop,
)
from dmx.shared_sources import SharedSourceError

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

    def test_chaining_into_a_require_branch_loop_that_guard_blocks_reports_it_plainly(
        self, tmp_path: Path
    ) -> None:
        """If a (custom) on_complete config chains into a require_branch loop
        while still off its base branch, the message must not claim
        "chaining automatically" and then immediately contradict it with a
        rejection — report the finished loop's own success plainly instead."""
        config = LoopConfig.model_validate(
            {
                "name": "dev",
                "skills": ["implement-next-phase"],
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
                "on_complete": {"on_success": {"trigger_loop": "spec"}},
            }
        )
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config)
        # No .dmx/config.md — the spec loop's require_branch guard can't
        # resolve branch_base, so it should block rather than start.

        message = _finish_loop(tmp_path, "J", "dev", "T", config, {})

        assert "complete" in message.lower()
        assert "chaining automatically" not in message.lower()
        assert "couldn't start" in message.lower()
        assert "cannot start" in message.lower()
        # The finished (dev) loop's own job must not be left dangling —
        # nothing spurious got created for "spec".
        assert find_active_run(tmp_path, "J") is None


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
        assert "no commits yet" not in message.lower()

    def test_blocks_start_with_specific_message_on_a_zero_commit_repo(self, tmp_path: Path) -> None:
        """A freshly `git init`'d repo (no commits yet) makes
        `git rev-parse --abbrev-ref HEAD` fail (unborn HEAD), even though the
        branch name is perfectly real and resolvable via `git symbolic-ref`.
        This must surface a specific, actionable message rather than the
        generic "could not determine branch" one."""
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        (tmp_path / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

        message = _start_loop(tmp_path, "spec")

        assert "cannot start" in message.lower()
        assert "no commits yet" in message.lower()
        assert "commit something" in message.lower()
        assert "get_skill_definition" not in message

    def test_zero_commit_guard_does_not_fire_outside_a_git_repo(self, tmp_path: Path) -> None:
        """A plain non-git directory must still get the generic message, not
        be misreported as a zero-commit git repo."""
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        (tmp_path / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")

        message = _start_loop(tmp_path, "spec")

        assert "cannot start" in message.lower()
        assert "no commits yet" not in message.lower()

    def test_zero_commit_guard_does_not_fire_once_a_commit_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
        (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)
        _allow_spec_loop_start(tmp_path, monkeypatch, branch="main")

        message = _start_loop(tmp_path, "spec")

        assert "get_skill_definition" in message

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

    def test_second_start_while_a_pending_job_is_already_in_progress_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Starting `spec` twice in a row (e.g. an accidental retry) before
        the first run's identity resolves must not silently create a second
        _pending-* folder — that would only surface later as an opaque
        AmbiguousActiveRun on the next loop_advance/loop_continue call."""
        _allow_spec_loop_start(tmp_path, monkeypatch, branch="main")

        first_message = _start_loop(tmp_path, "spec")
        assert "get_skill_definition" in first_message

        second_message = _start_loop(tmp_path, "spec")

        assert "cannot start" in second_message.lower()
        assert "already in progress" in second_message.lower()
        assert "loop_continue" in second_message

        jobs_dir = tmp_path / ".dmx" / "jobs"
        pending_dirs = [p for p in jobs_dir.iterdir() if p.name.startswith("_pending-")]
        assert len(pending_dirs) == 1


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


def _init_real_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)


def _dmx_dirty(root: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--short", "--", ".dmx/"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _install_rejecting_pre_commit_hook(root: Path) -> None:
    """Install a pre-commit hook that always rejects the commit, so
    `git commit` fails deterministically regardless of the host's git
    identity configuration."""
    hooks_dir = root / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook_path.chmod(0o755)


def _commit_count(root: Path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


class TestCommitDmxState:
    """GH-23: .dmx/jobs/*.json (and other .dmx/ writes made by _finish_loop,
    e.g. the activeContext.md session note) must not be left as an
    uncommitted, local-only change once a loop run has genuinely finished —
    otherwise a terminal loop like `release`, with nothing downstream to
    commit on its behalf, permanently loses that final state the moment
    close-ticket force-deletes the branch."""

    def test_commits_dirty_dmx_changes(self, tmp_path: Path) -> None:
        _init_real_git_repo(tmp_path)
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        (tmp_path / ".dmx" / "activeContext.md").write_text("note\n", encoding="utf-8")
        before = _commit_count(tmp_path)

        result = _commit_dmx_state(tmp_path, "chore: sync loop state")

        assert result is None
        assert _dmx_dirty(tmp_path) == ""
        assert _commit_count(tmp_path) == before + 1

    def test_noop_when_nothing_dirty(self, tmp_path: Path) -> None:
        _init_real_git_repo(tmp_path)
        before = _commit_count(tmp_path)

        result = _commit_dmx_state(tmp_path, "chore: sync loop state")

        assert result is None
        assert _commit_count(tmp_path) == before

    def test_noop_outside_a_git_repo(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        (tmp_path / ".dmx" / "activeContext.md").write_text("note\n", encoding="utf-8")

        result = _commit_dmx_state(tmp_path, "chore: sync loop state")  # must not raise

        assert result is None
        assert not (tmp_path / ".git").exists()

    def test_returns_a_warning_string_when_commit_is_attempted_but_fails(
        self, tmp_path: Path
    ) -> None:
        """A commit can be attempted and still fail (rejected by a
        pre-commit hook, GPG signing failure, disk full, etc.) — that must
        never be swallowed silently, since silent failure here defeats the
        entire point of this function (durability of the loop's final
        state). Forced here via an always-failing pre-commit hook, which
        reliably fails `git commit` regardless of the host's git identity
        configuration (unlike unsetting user.name/email, which a global
        config can silently paper over)."""
        _init_real_git_repo(tmp_path)
        _install_rejecting_pre_commit_hook(tmp_path)
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        (tmp_path / ".dmx" / "activeContext.md").write_text("note\n", encoding="utf-8")

        result = _commit_dmx_state(tmp_path, "chore: sync loop state")

        assert result is not None
        assert "could not auto-commit" in result.lower()
        # The failed commit must leave the change sitting uncommitted, not
        # silently discarded — an operator following the warning's advice
        # to inspect/commit manually still has something to find.
        assert "activeContext.md" in _dmx_dirty(tmp_path)

    def test_release_style_loop_with_no_chain_target_commits_final_state(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: a terminal loop (on_complete -> null, matching the
        bundled `release` loop) leaves .dmx/ clean after _finish_loop
        returns, instead of stranding the job state JSON + session note as
        an uncommitted change nothing downstream will ever pick up."""
        _init_real_git_repo(tmp_path)
        config = LoopConfig.model_validate(
            {
                "name": "release",
                "skills": ["create-pr"],
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            }
        )
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config, job_id="GH-1", task_id="T")
        before = _commit_count(tmp_path)

        message = _finish_loop(tmp_path, "GH-1", "release", "T", config, {})

        assert "complete" in message.lower()
        assert _dmx_dirty(tmp_path) == ""
        assert _commit_count(tmp_path) == before + 1

    def test_release_style_loop_surfaces_a_warning_when_the_commit_attempt_fails(
        self, tmp_path: Path
    ) -> None:
        """A failed auto-commit must be visible in the loop's own response,
        not just a server-side log line an operator has no reason to go
        looking for."""
        _init_real_git_repo(tmp_path)
        _install_rejecting_pre_commit_hook(tmp_path)
        config = LoopConfig.model_validate(
            {
                "name": "release",
                "skills": ["create-pr"],
                "validators": [{"tool": "v", "checks": [{"name": "check_a", "required": True}]}],
            }
        )
        _write_validator(tmp_path, "v", PASSING_VALIDATOR)
        _setup(tmp_path, config, job_id="GH-1", task_id="T")

        message = _finish_loop(tmp_path, "GH-1", "release", "T", config, {})

        assert "complete" in message.lower()
        assert "could not auto-commit" in message.lower()


# ---------------------------------------------------------------------------
# _resolve_loop / _resolve_skill — GH-27 phase 1 shared_sources tier
# ---------------------------------------------------------------------------


def _write_shared_sources_config(root: Path, sources: list[tuple[str, str]]) -> None:
    """Write ``.dmx/shared-sources.yaml`` declaring *sources* as ``(name, source)`` pairs."""
    config_path = root / ".dmx" / "shared-sources.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["shared_sources:"]
    for name, source in sources:
        lines.append(f"  - name: {name}")
        lines.append(f'    source: "{source}"')
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_loop_yaml(root: Path, subdir: Path, loop_name: str) -> Path:
    path = subdir / f"{loop_name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'name: {loop_name}\nskills: ["dmx-commit"]\n', encoding="utf-8")
    return path


class TestResolveLoopSharedSources:
    def test_missing_shared_sources_file_falls_through_to_bundled(self, tmp_path: Path) -> None:
        # No .dmx/shared-sources.yaml at all — behaves exactly as before GH-27.
        config = _resolve_loop("spec", tmp_path)
        assert config.name == "spec"

    def test_shared_source_used_when_no_app_override(self, tmp_path: Path) -> None:
        _write_shared_sources_config(tmp_path, [("acme", "git::https://x//?ref=v1")])
        _write_loop_yaml(tmp_path, tmp_path / ".dmx" / "vendor" / "acme" / "loops", "custom")
        config = _resolve_loop("custom", tmp_path)
        assert config.name == "custom"

    def test_app_repo_beats_shared_source(self, tmp_path: Path) -> None:
        _write_shared_sources_config(tmp_path, [("acme", "git::https://x//?ref=v1")])
        shared_path = _write_loop_yaml(
            tmp_path, tmp_path / ".dmx" / "vendor" / "acme" / "loops", "custom"
        )
        shared_path.write_text('name: custom\nskills: ["shared-skill"]\n', encoding="utf-8")
        app_path = _write_loop_yaml(tmp_path, tmp_path / ".dmx" / "loops", "custom")
        app_path.write_text('name: custom\nskills: ["app-skill"]\n', encoding="utf-8")
        config = _resolve_loop("custom", tmp_path)
        assert config.skills == ["app-skill"]

    def test_declared_order_is_precedence_order(self, tmp_path: Path) -> None:
        _write_shared_sources_config(
            tmp_path,
            [("first", "git::https://x//?ref=v1"), ("second", "git::https://y//?ref=v1")],
        )
        first_path = _write_loop_yaml(
            tmp_path, tmp_path / ".dmx" / "vendor" / "first" / "loops", "custom"
        )
        first_path.write_text('name: custom\nskills: ["first-skill"]\n', encoding="utf-8")
        second_path = _write_loop_yaml(
            tmp_path, tmp_path / ".dmx" / "vendor" / "second" / "loops", "custom"
        )
        second_path.write_text('name: custom\nskills: ["second-skill"]\n', encoding="utf-8")
        config = _resolve_loop("custom", tmp_path)
        assert config.skills == ["first-skill"]

    def test_not_found_anywhere_lists_shared_source_loops_too(self, tmp_path: Path) -> None:
        _write_shared_sources_config(tmp_path, [("acme", "git::https://x//?ref=v1")])
        _write_loop_yaml(tmp_path, tmp_path / ".dmx" / "vendor" / "acme" / "loops", "custom")
        try:
            _resolve_loop("nonexistent", tmp_path)
        except FileNotFoundError as exc:
            assert "custom" in str(exc)
        else:
            raise AssertionError("expected FileNotFoundError")

    def test_malformed_shared_sources_file_raises(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".dmx" / "shared-sources.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("shared_sources: not-a-list\n", encoding="utf-8")
        try:
            _resolve_loop("spec", tmp_path)
        except SharedSourceError:
            pass
        else:
            raise AssertionError("expected SharedSourceError")


class TestResolveSkillSharedSources:
    def test_missing_shared_sources_file_falls_through_to_bundled(self, tmp_path: Path) -> None:
        content = _resolve_skill("commit", tmp_path)
        assert content is not None

    def test_shared_source_used_when_no_app_override(self, tmp_path: Path) -> None:
        _write_shared_sources_config(tmp_path, [("acme", "git::https://x//?ref=v1")])
        skill_path = tmp_path / ".dmx" / "vendor" / "acme" / "skills" / "custom-skill.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text("# Custom skill from acme\n", encoding="utf-8")
        content = _resolve_skill("custom-skill", tmp_path)
        assert content == "# Custom skill from acme\n"

    def test_app_repo_beats_shared_source(self, tmp_path: Path) -> None:
        _write_shared_sources_config(tmp_path, [("acme", "git::https://x//?ref=v1")])
        shared_path = tmp_path / ".dmx" / "vendor" / "acme" / "skills" / "custom-skill.md"
        shared_path.parent.mkdir(parents=True, exist_ok=True)
        shared_path.write_text("# shared\n", encoding="utf-8")
        app_path = tmp_path / ".dmx" / "skills" / "custom-skill.md"
        app_path.parent.mkdir(parents=True, exist_ok=True)
        app_path.write_text("# app\n", encoding="utf-8")
        assert _resolve_skill("custom-skill", tmp_path) == "# app\n"

    def test_dmx_prefixed_candidate_also_checked_in_shared_source(self, tmp_path: Path) -> None:
        _write_shared_sources_config(tmp_path, [("acme", "git::https://x//?ref=v1")])
        skill_path = tmp_path / ".dmx" / "vendor" / "acme" / "skills" / "dmx-custom-skill.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text("# dmx-prefixed shared\n", encoding="utf-8")
        assert _resolve_skill("custom-skill", tmp_path) == "# dmx-prefixed shared\n"

    def test_not_found_returns_none(self, tmp_path: Path) -> None:
        _write_shared_sources_config(tmp_path, [("acme", "git::https://x//?ref=v1")])
        assert _resolve_skill("totally-nonexistent-skill", tmp_path) is None
