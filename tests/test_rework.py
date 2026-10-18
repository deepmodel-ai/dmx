"""Rework after a failed loop (GH-115).

Driven through the MCP client like ``test_loop_integration.py``. Each
validator is an app-repo stub that prints whatever ``stub-results.json`` at
the workspace root says for it, and passes every declared check otherwise.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

from dmx.loop_schema import LoopConfig, OnFailureConfig, load_loop
from dmx.loop_state import find_active_run, write_initial_state, write_state
from dmx.rework import (
    ReworkCheck,
    append_rework_phase,
    failed_required_checks,
    rework_tasks,
    unchecked_in_phase,
)
from dmx.server import create_app

if TYPE_CHECKING:
    from pathlib import Path

JOB = "GH-1"

_DEFAULT_CHECKS = {
    "run_tests": ["tests_pass"],
    "spec_adherence": ["scope_matches_spec", "edge_cases_addressed", "no_regressions"],
    "check_pr_ready": ["pr_exists", "ticket_transitioned", "memory_updated"],
    "challenge_check": ["holds_up"],
    "fixer_check": ["fixed"],
}

_STUB = """\
import json, pathlib, sys
contract = json.loads(sys.stdin.read() or "{{}}")
root = pathlib.Path((contract.get("loop_context") or {{}}).get("workspace_root") or ".")
plan_path = root / "stub-results.json"
plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {{}}
result = plan.get({tool!r})
if result == "crash":
    print("not json")
    sys.exit(1)
if result is None:
    result = {{"pass": True, "message": "stub", "checks": [
        {{"name": n, "pass": True}} for n in {checks!r}
    ]}}
print(json.dumps(result))
sys.exit(0 if result.get("pass") else 1)
"""


def _install_stubs(root: Path) -> None:
    validators = root / "validators"
    validators.mkdir(parents=True, exist_ok=True)
    for tool, checks in _DEFAULT_CHECKS.items():
        (validators / f"{tool}.py").write_text(
            _STUB.format(tool=tool, checks=checks), encoding="utf-8"
        )


def _set_results(root: Path, **results: Any) -> None:
    (root / "stub-results.json").write_text(json.dumps(results), encoding="utf-8")


def _fail(check: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"pass": False, "message": message, "checks": [{"name": check, "pass": False, **extra}]}


_SCOPE_FAIL = {
    "pass": False,
    "message": "Spec adherence check failed — see checks for details",
    "checks": [
        {
            "name": "scope_matches_spec",
            "pass": False,
            "message": "Scope item(s) with no evidence in the diff: Refund endpoint",
            "findings": ["Missing scope item: Refund endpoint"],
        },
        {"name": "edge_cases_addressed", "pass": True},
        {"name": "no_regressions", "pass": True},
    ],
}


def _job_dir(root: Path) -> Path:
    return root / ".dmx" / "jobs" / JOB


def _tasks(root: Path) -> str:
    return (_job_dir(root) / "tasks.md").read_text(encoding="utf-8")


def _state(root: Path, loop: str, task_id: str) -> dict[str, Any]:
    return json.loads((_job_dir(root) / f"{loop}-{task_id}.json").read_text(encoding="utf-8"))


def _runs(root: Path, loop: str) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(_job_dir(root).glob(f"{loop}-*.json"))
    ]


def _check_all_boxes(root: Path) -> None:
    path = _job_dir(root) / "tasks.md"
    path.write_text(path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _install_stubs(tmp_path)
    dmx = tmp_path / ".dmx"
    dmx.mkdir()
    (dmx / "config.md").write_text("branch_base: main\n", encoding="utf-8")
    (dmx / "spec.md").write_text(
        "---\nticket: GH-1\nbranch: feature-gh-1\n---\n# Spec\n", encoding="utf-8"
    )
    monkeypatch.setattr("dmx.loop_tools.current_branch", lambda _root: "feature-gh-1")
    monkeypatch.setattr("dmx.loop_state.current_branch", lambda _root: "feature-gh-1")
    _job_dir(tmp_path).mkdir(parents=True)
    (_job_dir(tmp_path) / "tasks.md").write_text(
        "# GH-1 tasks\n\n## Phase 1: Build it\n\n- [x] Build the thing\n", encoding="utf-8"
    )
    return tmp_path


def _write_loop(root: Path, name: str, body: str) -> None:
    loops = root / ".dmx" / "loops"
    loops.mkdir(parents=True, exist_ok=True)
    (loops / f"{name}.yaml").write_text(body, encoding="utf-8")


_CHALLENGE = """\
name: challenge
skills:
  - challenge
goal_state: the change holds up
validators:
  - tool: challenge_check
    checks:
      - name: holds_up
        required: true
failure_handling: pause
human_gate: true
on_complete:
  on_failure:
{failure}
"""


class _Session:
    def __init__(self, client: Client, root: Path) -> None:
        self.client = client
        self.root = root

    async def call(self, tool: str, **kwargs: Any) -> str:
        kwargs["workspace_root"] = str(self.root)
        message = (await self.client.call_tool(tool, kwargs)).data
        if tool not in {"loop_advance", "loop_continue"} or "Validators are running" not in message:
            return message
        for _ in range(200):
            await asyncio.sleep(0.05)
            message = (
                await self.client.call_tool("loop_status", {"workspace_root": str(self.root)})
            ).data
            if "Validators are running" not in message:
                return message
        raise AssertionError(f"validators did not finish: {message}")

    async def finish(self, skill: str, output: str) -> str:
        """Record the loop's last skill, then pass the human gate to run its validators."""
        gate = await self.call("loop_advance", output=output, skill=skill)
        assert "HUMAN GATE" in gate, gate
        return await self.call("loop_continue")

    async def run_dev_round(self) -> str:
        """Implement and commit the rework phase; return the message after dev's validators."""
        await self.call(
            "loop_advance", output="implemented the rework", skill="implement-next-phase"
        )
        _check_all_boxes(self.root)
        await self.call("loop_continue")
        return await self.finish("commit", "committed")


def _active(root: Path) -> tuple[str, str]:
    found = find_active_run(root, JOB)
    assert found is not None
    return found


# ---------------------------------------------------------------------------
# The bundled validate loop
# ---------------------------------------------------------------------------


class TestValidateRework:
    @pytest.mark.asyncio
    async def test_failure_writes_a_phase_and_waits_for_approval(self, workspace: Path) -> None:
        _set_results(workspace, spec_adherence=_SCOPE_FAIL)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="validate")
            message = await s.finish("validate", "report written")

        assert "rework" in message.lower()
        assert "round 1 of 2" in message
        assert "Fix `scope_matches_spec` (spec_adherence)" in message
        assert "loop_continue" in message
        tasks = _tasks(workspace)
        assert "## Phase 2: Rework — validate, round 1" in tasks
        assert (
            "- [ ] Fix `scope_matches_spec` (spec_adherence): "
            "Scope item(s) with no evidence in the diff: Refund endpoint"
        ) in tasks
        assert "  - Missing scope item: Refund endpoint" in tasks
        assert tasks.count("- [ ]") == 1
        loop, task_id = _active(workspace)
        state = _state(workspace, loop, task_id)
        assert loop == "validate"
        assert state["status"] == "paused"
        assert state["rework"]["stage"] == "awaiting_approval"
        assert state["rework"]["round"] == 1
        assert state["rework"]["max_rounds"] == 2
        assert state["rework"]["loop"] == "dev"
        assert _runs(workspace, "dev") == []

    @pytest.mark.asyncio
    async def test_full_cycle_returns_to_validate_and_chains_on_success(
        self, workspace: Path
    ) -> None:
        _set_results(workspace, spec_adherence=_SCOPE_FAIL)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="validate")
            await s.finish("validate", "report written")
            _loop, parent_id = _active(workspace)

            started = await s.call("loop_continue")
            assert "implement-next-phase" in started
            assert "validate — rework round 1 of 2 (dev), then back to validate" in started
            dev_loop, dev_id = _active(workspace)
            assert dev_loop == "dev"
            assert _state(workspace, "validate", parent_id)["status"] == "reworking"
            assert _state(workspace, "dev", dev_id)["rework_parent"] == {
                "loop_name": "validate",
                "task_id": parent_id,
                "round": 1,
                "max_rounds": 2,
            }
            status = await s.call("loop_status")
            assert "validate — rework round 1 of 2 (dev), then back to validate" in status

            back = await s.run_dev_round()
            assert "Back to **validate**" in back
            assert "get_skill_definition" in back
            assert "`validate`" in back
            assert "chaining automatically to **validate**" not in back.lower()
            assert _active(workspace) == ("validate", parent_id)
            parent = _state(workspace, "validate", parent_id)
            assert parent["status"] == "running"
            assert parent["current_skill_index"] == 0
            assert parent["skills_completed"] == []
            assert parent["rework"]["stage"] == "rechecking"
            dev_state = _state(workspace, "dev", dev_id)
            assert dev_state["status"] == "complete"
            assert len(_runs(workspace, "validate")) == 1

            _set_results(workspace)
            done = await s.finish("validate", "report rewritten")

        assert "chaining automatically to **release**" in done.lower()
        parent = _state(workspace, "validate", parent_id)
        assert parent["outcome"] == "success"
        assert parent["rework"]["stage"] == "resolved"
        assert [entry["round"] for entry in parent["rework"]["history"]] == [1]
        assert parent["rework"]["history"][0]["rework_task_id"] == dev_id
        assert parent["rework"]["history"][0]["checks"] == ["scope_matches_spec"]

    @pytest.mark.asyncio
    async def test_stops_after_max_rounds_with_history(self, workspace: Path) -> None:
        _set_results(workspace, spec_adherence=_SCOPE_FAIL)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="validate")
            await s.finish("validate", "report")
            _loop, parent_id = _active(workspace)
            for expected_round in (1, 2):
                started = await s.call("loop_continue")
                assert f"rework round {expected_round} of 2" in started
                await s.run_dev_round()
                message = await s.finish("validate", "report")
                if expected_round == 1:
                    assert "round 2 of 2" in message
                    assert "## Phase 3: Rework — validate, round 2" in _tasks(workspace)

        assert "2 rework rounds" in message
        assert "Round 1" in message
        assert "Round 2" in message
        assert "scope_matches_spec" in message
        assert "Phase 4" not in _tasks(workspace)
        parent = _state(workspace, "validate", parent_id)
        assert parent["status"] == "paused"
        assert parent["rework"]["stage"] == "exhausted"
        assert len(parent["rework"]["history"]) == 2
        assert len(_runs(workspace, "dev")) == 2
        assert _active(workspace) == ("validate", parent_id)

    @pytest.mark.asyncio
    async def test_continue_after_exhaustion_re_runs_validators_as_today(
        self, workspace: Path
    ) -> None:
        _set_results(workspace, spec_adherence=_SCOPE_FAIL)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="validate")
            await s.finish("validate", "report")
            for _round in (1, 2):
                await s.call("loop_continue")
                await s.run_dev_round()
                await s.finish("validate", "report")
            again = await s.call("loop_continue")

        assert "paused (validation failed)" in again
        assert "Phase 4" not in _tasks(workspace)
        assert len(_runs(workspace, "dev")) == 2

    @pytest.mark.asyncio
    async def test_skipped_tasks_re_run_validate_without_a_rework_run(
        self, workspace: Path
    ) -> None:
        _set_results(workspace, spec_adherence=_SCOPE_FAIL)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="validate")
            await s.finish("validate", "report")
            _loop, parent_id = _active(workspace)
            _check_all_boxes(workspace)
            message = await s.call("loop_continue")

        assert "get_skill_definition" in message
        assert "`validate`" in message
        assert _runs(workspace, "dev") == []
        parent = _state(workspace, "validate", parent_id)
        assert parent["status"] == "running"
        assert parent["current_skill_index"] == 0
        assert parent["rework"]["stage"] == "rechecking"

    @pytest.mark.asyncio
    async def test_failing_tests_become_one_task_with_findings(self, workspace: Path) -> None:
        _set_results(
            workspace,
            run_tests=_fail(
                "tests_pass",
                "Tests failed — ran `uv run pytest -q` (exit 1)",
                findings=["FAILED tests/test_refund.py::test_partial - AssertionError"],
            ),
            spec_adherence=_SCOPE_FAIL,
        )
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="validate")
            await s.finish("validate", "report")

        tasks = _tasks(workspace)
        assert tasks.count("- [ ]") == 2
        assert "- [ ] Fix `tests_pass` (run_tests): Tests failed" in tasks
        assert "  - FAILED tests/test_refund.py::test_partial - AssertionError" in tasks


class TestNotReworkable:
    @pytest.mark.asyncio
    async def test_a_check_marked_not_reworkable_pauses_as_today(self, workspace: Path) -> None:
        _set_results(
            workspace,
            run_tests=_fail(
                "tests_pass", "Test command `pytest` not found on PATH", reworkable=False
            ),
            spec_adherence=_SCOPE_FAIL,
        )
        before = _tasks(workspace)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="validate")
            message = await s.finish("validate", "report")

        assert "paused (validation failed)" in message
        assert "Not reworked" in message
        assert "`tests_pass`" in message
        assert _tasks(workspace) == before
        loop, task_id = _active(workspace)
        assert _state(workspace, loop, task_id)["status"] == "paused"
        assert "rework" not in _state(workspace, loop, task_id) or (
            _state(workspace, loop, task_id)["rework"]["stage"] == "stopped"
        )

    @pytest.mark.asyncio
    async def test_validator_error_is_not_reworkable(self, workspace: Path) -> None:
        _set_results(workspace, spec_adherence="crash")
        before = _tasks(workspace)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="validate")
            message = await s.finish("validate", "report")

        assert "paused (validation failed)" in message
        assert "Not reworked" in message
        assert _tasks(workspace) == before

    @pytest.mark.asyncio
    async def test_unreported_required_check_is_not_reworkable(self, workspace: Path) -> None:
        _set_results(
            workspace,
            spec_adherence={
                "pass": False,
                "message": "partial",
                "checks": [{"name": "edge_cases_addressed", "pass": True}],
            },
        )
        before = _tasks(workspace)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="validate")
            message = await s.finish("validate", "report")

        assert "Not reworked" in message
        assert _tasks(workspace) == before


# ---------------------------------------------------------------------------
# Custom loops, settings and the manual command
# ---------------------------------------------------------------------------


class TestCustomLoops:
    @pytest.mark.asyncio
    async def test_challenge_loop_without_approval_starts_rework_at_once(
        self, workspace: Path
    ) -> None:
        _write_loop(
            workspace,
            "challenge",
            _CHALLENGE.format(
                failure="    rework:\n      loop: dev\n      max_rounds: 1\n      approve: false"
            ),
        )
        _set_results(workspace, challenge_check=_fail("holds_up", "Race on refund retries"))
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="challenge")
            started = await s.finish("challenge", "challenged")
            assert "implement-next-phase" in started
            assert "challenge — rework round 1 of 1 (dev), then back to challenge" in started
            assert "## Phase 2: Rework — challenge, round 1" in _tasks(workspace)
            assert "- [ ] Fix `holds_up` (challenge_check): Race on refund retries" in _tasks(
                workspace
            )

            back = await s.run_dev_round()
            assert "Back to **challenge**" in back
            assert "`challenge`" in back
            stopped = await s.finish("challenge", "challenged")

        assert "1 rework round" in stopped
        loop, task_id = _active(workspace)
        assert loop == "challenge"
        assert _state(workspace, loop, task_id)["rework"]["stage"] == "exhausted"

    @pytest.mark.asyncio
    async def test_loop_without_rework_behaves_as_today(self, workspace: Path) -> None:
        _write_loop(workspace, "challenge", _CHALLENGE.format(failure="    trigger_loop: null"))
        _set_results(workspace, challenge_check=_fail("holds_up", "Race on refund retries"))
        before = _tasks(workspace)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="challenge")
            message = await s.finish("challenge", "challenged")

        assert message.startswith("**challenge loop — paused (validation failed)**")
        assert "rework" not in message.lower()
        assert _tasks(workspace) == before
        loop, task_id = _active(workspace)
        assert "rework" not in _state(workspace, loop, task_id)

    @pytest.mark.asyncio
    async def test_optional_check_warning_never_reworks(self, workspace: Path) -> None:
        _write_loop(
            workspace,
            "challenge",
            _CHALLENGE.replace("required: true", "required: false").format(
                failure="    rework:\n      loop: dev"
            ),
        )
        _set_results(workspace, challenge_check=_fail("holds_up", "nit"))
        before = _tasks(workspace)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="challenge")
            message = await s.finish("challenge", "challenged")

        assert "warning" in message.lower()
        assert _tasks(workspace) == before
        assert find_active_run(workspace, JOB) is None

    @pytest.mark.asyncio
    async def test_rework_run_that_fails_puts_the_parent_back_to_paused(
        self, workspace: Path
    ) -> None:
        _write_loop(
            workspace,
            "challenge",
            _CHALLENGE.format(failure="    rework:\n      loop: fixer\n      approve: false"),
        )
        _write_loop(
            workspace,
            "fixer",
            "name: fixer\nskills:\n  - fix\nvalidators:\n  - tool: fixer_check\n"
            "    checks:\n      - name: fixed\n        required: true\n"
            "failure_handling: fail\nhuman_gate: true\n",
        )
        _set_results(
            workspace,
            challenge_check=_fail("holds_up", "Race on refund retries"),
            fixer_check=_fail("fixed", "still broken"),
        )
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="challenge")
            await s.finish("challenge", "challenged")
            _loop, fixer_id = _active(workspace)
            message = await s.finish("fix", "tried")

        assert "rework" in message.lower()
        assert _state(workspace, "fixer", fixer_id)["status"] == "failed"
        loop, task_id = _active(workspace)
        assert loop == "challenge"
        parent = _state(workspace, loop, task_id)
        assert parent["status"] == "paused"
        assert parent["rework"]["stage"] == "stopped"


class TestManualRework:
    @pytest.mark.asyncio
    async def test_rework_command_on_a_loop_without_the_setting(self, workspace: Path) -> None:
        _write_loop(workspace, "challenge", _CHALLENGE.format(failure="    trigger_loop: null"))
        _set_results(workspace, challenge_check=_fail("holds_up", "Race on refund retries"))
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="challenge")
            await s.finish("challenge", "challenged")
            proposed = await s.call("rework_loop")
            assert "round 1 of 2" in proposed
            assert "## Phase 2: Rework — challenge, round 1" in _tasks(workspace)
            again = await s.call("rework_loop")
            assert "already" in again.lower()
            started = await s.call("loop_continue")
            assert "challenge — rework round 1 of 2 (dev), then back to challenge" in started
            refused = await s.call("rework_loop")

        assert "paused after a failed required check" in refused

    @pytest.mark.asyncio
    async def test_rework_command_refuses_non_reworkable_failures(self, workspace: Path) -> None:
        _write_loop(workspace, "challenge", _CHALLENGE.format(failure="    trigger_loop: null"))
        _set_results(
            workspace,
            challenge_check=_fail("holds_up", "no toolchain", reworkable=False),
        )
        before = _tasks(workspace)
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="challenge")
            await s.finish("challenge", "challenged")
            refused = await s.call("rework_loop")

        assert "Not reworked" in refused
        assert "`holds_up`" in refused
        assert _tasks(workspace) == before

    @pytest.mark.asyncio
    async def test_rework_command_with_no_active_run(self, workspace: Path) -> None:
        async with Client(create_app()) as client:
            message = await _Session(client, workspace).call("rework_loop")
        assert "No active loop run" in message

    @pytest.mark.asyncio
    async def test_rework_command_allows_more_rounds_after_exhaustion(
        self, workspace: Path
    ) -> None:
        _write_loop(
            workspace,
            "challenge",
            _CHALLENGE.format(
                failure="    rework:\n      loop: dev\n      max_rounds: 1\n      approve: false"
            ),
        )
        _set_results(workspace, challenge_check=_fail("holds_up", "Race on refund retries"))
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            await s.call("run_loop", name="challenge")
            await s.finish("challenge", "challenged")
            await s.run_dev_round()
            await s.finish("challenge", "challenged")
            message = await s.call("rework_loop", max_rounds=1)

        assert "round 2 of 2" in message
        assert "## Phase 3: Rework — challenge, round 2" in _tasks(workspace)
        loop, task_id = _active(workspace)
        assert _state(workspace, loop, task_id)["rework"]["stage"] == "awaiting_approval"


# ---------------------------------------------------------------------------
# Active-run lookup and recovery
# ---------------------------------------------------------------------------


def _write_run(root: Path, loop: str, task_id: str, updates: dict[str, Any]) -> None:
    write_initial_state(
        workspace_root=root,
        loop_name=loop,
        job_id=JOB,
        task_id=task_id,
        skills=["validate"] if loop == "validate" else ["implement-next-phase", "commit"],
        updates=updates,
    )


class TestActiveRun:
    def test_reworking_parent_is_skipped_while_its_rework_runs(self, workspace: Path) -> None:
        _write_run(workspace, "validate", "p" * 32, {"status": "reworking"})
        _write_run(workspace, "dev", "c" * 32, {"status": "running"})
        assert find_active_run(workspace, JOB) == ("dev", "c" * 32)

    def test_reworking_parent_is_active_when_nothing_else_is(self, workspace: Path) -> None:
        _write_run(workspace, "validate", "p" * 32, {"status": "reworking"})
        _write_run(workspace, "dev", "c" * 32, {"status": "complete", "outcome": "success"})
        assert find_active_run(workspace, JOB) == ("validate", "p" * 32)

    @pytest.mark.asyncio
    async def test_continue_recovers_a_parent_whose_rework_finished(self, workspace: Path) -> None:
        parent_id, child_id = "p" * 32, "c" * 32
        _write_run(
            workspace,
            "validate",
            parent_id,
            {
                "status": "reworking",
                "current_skill_index": 1,
                "skills_completed": ["validate"],
                "rework": {
                    "loop": "dev",
                    "max_rounds": 2,
                    "approve": True,
                    "round": 1,
                    "stage": "reworking",
                    "phase": "Phase 2: Rework — validate, round 1",
                    "rework_task_id": child_id,
                    "history": [{"round": 1, "checks": ["tests_pass"]}],
                },
            },
        )
        _write_run(
            workspace,
            "dev",
            child_id,
            {
                "status": "complete",
                "outcome": "success",
                "rework_parent": {
                    "loop_name": "validate",
                    "task_id": parent_id,
                    "round": 1,
                    "max_rounds": 2,
                },
            },
        )
        async with Client(create_app()) as client:
            s = _Session(client, workspace)
            advance = await s.call("loop_advance", output="x", skill="validate")
            message = await s.call("loop_continue")

        assert "loop_continue" in advance
        assert "Back to **validate**" in message
        parent = _state(workspace, "validate", parent_id)
        assert parent["status"] == "running"
        assert parent["current_skill_index"] == 0
        assert parent["rework"]["stage"] == "rechecking"

    @pytest.mark.asyncio
    async def test_continue_stops_a_parent_whose_rework_run_is_gone(self, workspace: Path) -> None:
        parent_id = "p" * 32
        _write_run(
            workspace,
            "validate",
            parent_id,
            {
                "status": "reworking",
                "current_skill_index": 1,
                "rework": {
                    "loop": "dev",
                    "max_rounds": 2,
                    "approve": True,
                    "round": 1,
                    "stage": "reworking",
                    "phase": "Phase 2: Rework — validate, round 1",
                    "rework_task_id": "missing",
                    "history": [],
                },
            },
        )
        async with Client(create_app()) as client:
            message = await _Session(client, workspace).call("loop_continue")

        assert "did not finish" in message
        parent = _state(workspace, "validate", parent_id)
        assert parent["status"] == "paused"
        assert parent["rework"]["stage"] == "stopped"


# ---------------------------------------------------------------------------
# Schema and the pure helpers
# ---------------------------------------------------------------------------


class TestSchema:
    def test_rework_defaults(self) -> None:
        config = OnFailureConfig.model_validate({"rework": {"loop": "dev"}})
        assert config.rework is not None
        assert config.rework.max_rounds == 2
        assert config.rework.approve is True

    def test_rework_and_trigger_loop_are_exclusive(self) -> None:
        with pytest.raises(ValueError, match="rework"):
            OnFailureConfig.model_validate({"trigger_loop": "dev", "rework": {"loop": "dev"}})

    @pytest.mark.parametrize("rounds", [0, -1, "two", True])
    def test_max_rounds_must_be_a_positive_integer(self, rounds: object) -> None:
        with pytest.raises(ValueError):
            OnFailureConfig.model_validate({"rework": {"loop": "dev", "max_rounds": rounds}})

    def test_loop_name_must_not_be_empty(self) -> None:
        with pytest.raises(ValueError):
            OnFailureConfig.model_validate({"rework": {"loop": " "}})

    def test_bundled_validate_turns_rework_on(self) -> None:
        from importlib.resources import files  # noqa: PLC0415

        path = files("dmx") / "loops" / "validate.yaml"
        config = load_loop(path)  # type: ignore[arg-type]
        rework = config.on_complete.on_failure.rework
        assert rework is not None
        assert (rework.loop, rework.max_rounds, rework.approve) == ("dev", 2, True)
        assert config.on_complete.on_failure.trigger_loop is None

    def test_other_bundled_loops_leave_rework_off(self) -> None:
        from importlib.resources import files  # noqa: PLC0415

        for name in ("spec", "plan", "dev", "release"):
            config = load_loop(files("dmx") / "loops" / f"{name}.yaml")  # type: ignore[arg-type]
            assert config.on_complete.on_failure.rework is None


def _config(*checks: tuple[str, bool]) -> LoopConfig:
    return LoopConfig.model_validate(
        {
            "name": "validate",
            "skills": ["validate"],
            "validators": [
                {"tool": "probe", "checks": [{"name": n, "required": r} for n, r in checks]}
            ],
        }
    )


class TestFailedRequiredChecks:
    def test_reports_each_failed_required_check(self) -> None:
        config = _config(("a", True), ("b", True), ("c", False))
        results = [
            {
                "tool": "probe",
                "message": "probe failed",
                "checks": [
                    {"name": "a", "pass": False, "message": "a broke\nmore", "findings": ["x"]},
                    {"name": "b", "pass": True},
                    {"name": "c", "pass": False},
                ],
            }
        ]
        found = failed_required_checks(config, results)
        assert found == [
            ReworkCheck(name="a", tool="probe", message="a broke", findings=["x"], reason=None)
        ]

    def test_falls_back_to_the_validator_message(self) -> None:
        config = _config(("a", True))
        results = [{"tool": "probe", "message": "whole run failed", "checks": [{"name": "a"}]}]
        (check,) = failed_required_checks(config, results)
        assert check.message == "whole run failed"
        assert check.reworkable

    def test_marks_unreported_and_errored_checks(self) -> None:
        config = _config(("a", True), ("b", True))
        results = [
            {
                "tool": "probe",
                "message": "Validator error: boom",
                "checks": [{"name": "a", "pass": False}],
            }
        ]
        a, b = failed_required_checks(config, results)
        assert not a.reworkable
        assert "Validator error" in (a.reason or "")
        assert not b.reworkable
        assert "not reported" in (b.reason or "")

    def test_marked_not_reworkable(self) -> None:
        config = _config(("a", True))
        results = [
            {
                "tool": "probe",
                "checks": [{"name": "a", "pass": False, "message": "m", "reworkable": False}],
            }
        ]
        (check,) = failed_required_checks(config, results)
        assert not check.reworkable
        assert check.reason == "m"


class TestPhaseText:
    def test_tasks_are_one_line_and_never_checkboxes(self) -> None:
        check = ReworkCheck(
            name="a",
            tool="probe",
            message="- [ ] sneaky " + "x" * 300,
            findings=[f"finding {i}\nsecond line" for i in range(12)] + ["- [x] box"],
            reason=None,
        )
        (task,) = rework_tasks([check])
        lines = task.splitlines()
        assert lines[0].startswith("- [ ] Fix `a` (probe): sneaky ")
        assert len(lines[0]) <= len("- [ ] Fix `a` (probe): ") + 200
        assert len(lines) == 11
        assert all(line.startswith("  - finding") for line in lines[1:])
        assert "second line" not in task

    def test_phase_numbers_follow_the_highest(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_text("## Phase 1: A\n- [x] a\n\n## Phase 7: B\n- [x] b\n", encoding="utf-8")
        heading = append_rework_phase(tasks, "validate", 1, "abcd1234", ["- [ ] Fix `a` (p): m"])
        assert heading == "Phase 8: Rework — validate, round 1"
        text = tasks.read_text(encoding="utf-8")
        assert text.endswith("- [ ] Fix `a` (p): m\n")
        assert "## Phase 8: Rework — validate, round 1\n" in text
        assert "`abcd1234`" in text
        assert unchecked_in_phase(tasks, heading) == 1

    def test_creates_tasks_md_when_missing(self, tmp_path: Path) -> None:
        tasks = tmp_path / "jobs" / "GH-1" / "tasks.md"
        heading = append_rework_phase(tasks, "challenge", 2, "abcd1234", ["- [ ] Fix `a` (p): m"])
        assert heading == "Phase 1: Rework — challenge, round 2"
        assert tasks.exists()

    def test_unchecked_counts_only_its_own_phase(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_text(
            "## Phase 1: A\n- [ ] open\n\n## Phase 2: Rework — v, round 1\n- [x] done\n"
            "\n## Phase 3: C\n- [ ] later\n",
            encoding="utf-8",
        )
        assert unchecked_in_phase(tasks, "Phase 2: Rework — v, round 1") == 0
        assert unchecked_in_phase(tasks, "Phase 9: gone") == 0
        assert unchecked_in_phase(tmp_path / "missing.md", "Phase 1: A") == 0


def test_rework_state_survives_a_reread(workspace: Path) -> None:
    """``write_state`` merges, so a nested ``rework`` dict is replaced whole."""
    _write_run(workspace, "validate", "p" * 32, {"status": "paused", "rework": {"round": 1}})
    write_state(workspace, JOB, "validate", "p" * 32, {"rework": {"round": 2}})
    assert _state(workspace, "validate", "p" * 32)["rework"] == {"round": 2}
