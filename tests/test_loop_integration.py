"""Integration tests for the full loop runtime pipeline.

Exercises the registered MCP tools (``run_loop``, ``loop_advance``,
``loop_continue``) end-to-end through an in-process FastMCP ``Client`` — the
same interface a coding agent uses — rather than calling internal helpers
directly. This is the one place all the pieces (config loading, state
persistence, human gate, the validator subprocess runner, the policy engine,
``repeat_until``, ``on_complete`` chaining, and memory hooks) are exercised
together across a full run.

Every bundled validator is overridden at the app-repo level (``validators/``
at the workspace root) with a small deterministic stub that always passes
the checks declared for it. This is the same override mechanism a real app
repo uses (see ``resolve_validator_path``) — it keeps these tests focused on
runtime orchestration rather than on the fuzziness of the bundled heuristic
validators, which are covered separately in ``test_validators.py``.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client

from dmx.loop_state import (
    find_active_run,
    find_pending_run,
    resolve_job_id,
    write_initial_state,
    write_state,
)
from dmx.server import create_app

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Validator stubs
# ---------------------------------------------------------------------------

# Every check declared across the bundled spec/plan/dev/validate/release
# loop configs, keyed by the validator tool name that reports it.
_STUB_CHECKS = {
    "check_spec_complete": [
        "spec_exists",
        "qa_answered",
        "technical_approach_filled",
        "scope_defined",
        "spec_identity_matches_branch",
    ],
    "check_plan_complete": [
        "tasks_file_exists",
        "phases_defined",
        "tasks_have_descriptions",
    ],
    "run_tests": ["tests_pass", "coverage_threshold"],
    "spec_adherence": ["scope_matches_spec", "edge_cases_addressed", "no_regressions"],
    "check_pr_ready": ["pr_exists", "ticket_transitioned", "memory_updated"],
}


def _stub_validator_source(check_names: list[str], *, passing: bool = True) -> str:
    """Build validator script source that prints a fixed JSON result.

    Note: the checks list is embedded via ``repr()`` (valid Python literal:
    ``True``/``False``), then serialized to JSON at *runtime* by the script
    itself — using ``json.dumps()`` here instead would embed lowercase
    ``true``/``false``, which is not valid Python source.
    """
    checks_literal = repr([{"name": n, "pass": passing} for n in check_names])
    return (
        "import json, sys\n"
        f"print(json.dumps({{'pass': {passing!r}, 'message': 'stub', "
        f"'checks': {checks_literal}}}))\n"
        f"sys.exit({0 if passing else 1})\n"
    )


def _install_passing_validators(workspace_root: Path) -> Path:
    """Write a passing stub for every bundled validator into `validators/`."""
    validators_dir = workspace_root / "validators"
    validators_dir.mkdir(parents=True, exist_ok=True)
    for name, checks in _STUB_CHECKS.items():
        (validators_dir / f"{name}.py").write_text(_stub_validator_source(checks), encoding="utf-8")
    return validators_dir


async def _call(
    client: Client, tool: str, workspace_root: Path, *, wait: bool = True, **kwargs: str
) -> str:
    kwargs["workspace_root"] = str(workspace_root)
    result = await client.call_tool(tool, kwargs)
    message = result.data
    if (
        not wait
        or tool not in {"loop_advance", "loop_continue"}
        or "Validators are running" not in message
    ):
        return message
    last = message
    for _ in range(100):
        await asyncio.sleep(0.05)
        status = await client.call_tool("loop_status", {"workspace_root": str(workspace_root)})
        last = status.data
        if "Validators are running" not in last:
            return last
    raise AssertionError(f"validators did not finish; last message: {last}")


class _MockBranch:
    """Mutable current-branch stand-in for tests that don't have a real git
    repo. Call ``checkout(name)`` to simulate switching branches — this also
    swaps ``.dmx/spec.md`` in and out (it's a real tracked file, so a real
    ``git checkout`` would change its contents along with the branch).

    Patches both ``dmx.loop_tools.current_branch`` (used by the
    ``require_branch`` guard) and ``dmx.loop_state.current_branch`` (used by
    ``resolve_job_id``'s branch fallback) — separate imported bindings of
    the same underlying function.
    """

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, workspace_root: Path, initial: str = "main"
    ) -> None:
        self._root = workspace_root
        self._name = initial
        self._spec_snapshots: dict[str, str | None] = {}
        monkeypatch.setattr("dmx.loop_tools.current_branch", lambda _root: self._name)
        monkeypatch.setattr("dmx.loop_state.current_branch", lambda _root: self._name)

    def checkout(self, name: str) -> None:
        spec_path = self._root / ".dmx" / "spec.md"
        self._spec_snapshots[self._name] = (
            spec_path.read_text(encoding="utf-8") if spec_path.exists() else None
        )
        self._name = name
        restored = self._spec_snapshots.get(name)
        if restored is not None:
            spec_path.write_text(restored, encoding="utf-8")
        elif spec_path.exists():
            spec_path.unlink()


def _write_config(workspace_root: Path, branch_base: str = "main") -> None:
    dmx_dir = workspace_root / ".dmx"
    dmx_dir.mkdir(parents=True, exist_ok=True)
    (dmx_dir / "config.md").write_text(f"branch_base: {branch_base}\n", encoding="utf-8")


def _write_spec_md(workspace_root: Path, ticket: str, branch: str) -> None:
    (workspace_root / ".dmx" / "spec.md").write_text(
        f"---\nticket: {ticket}\nbranch: {branch}\n---\n# Spec\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Full pipeline: spec -> plan -> dev -> validate -> release
# ---------------------------------------------------------------------------


class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_spec_to_release_chains_through_all_loops(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_passing_validators(tmp_path)
        _write_config(tmp_path)
        branch = _MockBranch(monkeypatch, tmp_path)
        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            # --- spec (1 skill), started from the configured base branch ---
            msg = await call("run_loop", name="spec")
            assert "get_skill_definition" in msg
            assert "create-ticket" in msg

            # Simulate what create-ticket actually does: switch to the new
            # feature branch, then write spec.md with the real ticket id.
            branch.checkout("bug-gh-1-example")
            _write_spec_md(tmp_path, "GH-1", "bug-gh-1-example")

            msg = await call(
                "loop_advance",
                output="created ticket, spec.md filled in",
                skill="create-ticket",
            )
            assert "paused" in msg.lower()

            msg = await call("loop_continue")
            assert "chaining automatically to **plan**" in msg.lower()
            assert "get_skill_definition" in msg
            assert "plan" in msg

            # --- plan (1 skill) ---
            msg = await call("loop_advance", output="tasks.md created with 2 phases", skill="plan")
            assert "paused" in msg.lower()

            msg = await call("loop_continue")
            assert "chaining automatically to **dev**" in msg.lower()
            assert "get_skill_definition" in msg
            assert "implement-next-phase" in msg

            # --- dev (2 skills; no tasks.md -> repeat_until treated as met) ---
            msg = await call(
                "loop_advance", output="implemented phase 1", skill="implement-next-phase"
            )
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "get_skill_definition" in msg
            assert "commit" in msg

            msg = await call("loop_advance", output="committed", skill="commit")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "chaining automatically to **validate**" in msg.lower()
            assert "get_skill_definition" in msg
            assert "validate" in msg

            # --- validate (1 skill) ---
            msg = await call("loop_advance", output="all checks green", skill="validate")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "chaining automatically to **release**" in msg.lower()
            assert "get_skill_definition" in msg
            assert "create-pr" in msg

            # --- release (1 skill; on_complete has no trigger_loop) ---
            # create-pr does its own memory-bank sync + commit (Steps 4-5) —
            # update-memory is intentionally NOT chained after it, since that
            # left dangling uncommitted .dmx/ changes after the PR was
            # already opened (see GH-15).
            msg = await call(
                "loop_advance", output="opened PR #42, memory bank synced", skill="create-pr"
            )
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "release loop — complete" in msg.lower()
            assert "chaining" not in msg.lower()

        # Terminal: no loop left active, and the ticket identity established
        # by create-ticket stuck for the whole pipeline (never fell back to
        # a per-loop-restart "unknown").
        assert resolve_job_id(tmp_path) == "GH-1"
        assert find_active_run(tmp_path, "GH-1") is None

        # Memory hooks: a breadcrumb was written for each of the 5 loops.
        active_context = (tmp_path / ".dmx" / "activeContext.md").read_text(encoding="utf-8")
        assert active_context.count("loop completed (outcome: success)") == 5
        assert "chained to plan" in active_context
        assert "chained to dev" in active_context
        assert "chained to validate" in active_context
        assert "chained to release" in active_context


# ---------------------------------------------------------------------------
# repeat_until: dev loop iterates until tasks.md has no unchecked items
# ---------------------------------------------------------------------------


class TestRepeatUntilIntegration:
    @pytest.mark.asyncio
    async def test_dev_loop_iterates_then_chains_to_validate(self, tmp_path: Path) -> None:
        _install_passing_validators(tmp_path)
        dmx_dir = tmp_path / ".dmx"
        dmx_dir.mkdir(parents=True)
        tasks_path = dmx_dir / "tasks.md"
        tasks_path.write_text("## Phase 1: X\n- [ ] Not done yet\n", encoding="utf-8")

        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            msg = await call("run_loop", name="dev")
            assert "get_skill_definition" in msg
            assert "implement-next-phase" in msg

            await call(
                "loop_advance",
                output="implemented phase 1 partially",
                skill="implement-next-phase",
            )
            await call("loop_continue")  # -> commit
            await call("loop_advance", output="committed wip", skill="commit")
            msg = await call("loop_continue")  # all skills done, repeat_until not met

            assert "iterating (round 1)" in msg.lower()
            assert "get_skill_definition" in msg
            assert "implement-next-phase" in msg

            active = find_active_run(tmp_path, "unknown")
            assert active is not None
            assert active[0] == "dev"
            _loop_name, task_id = active
            round_state = _read_json(tmp_path / ".dmx" / "jobs" / "unknown" / f"dev-{task_id}.json")
            assert round_state["status"] == "running"
            assert round_state["iteration_count"] == 1

            # Second pass: mark the phase complete before finishing.
            tasks_path.write_text("## Phase 1: X\n- [x] Done now\n", encoding="utf-8")

            await call(
                "loop_advance", output="implemented phase 1 fully", skill="implement-next-phase"
            )
            await call("loop_continue")  # -> commit
            await call("loop_advance", output="committed final", skill="commit")
            msg = await call("loop_continue")  # repeat_until met -> chain to validate

            assert "chaining automatically to **validate**" in msg.lower()

        active = find_active_run(tmp_path, "unknown")
        assert active is not None
        assert active[0] == "validate"

        content = (tmp_path / ".dmx" / "activeContext.md").read_text(encoding="utf-8")
        assert "iterating (round 1)" in content


# ---------------------------------------------------------------------------
# Validator failure -> pause -> retry succeeds
# ---------------------------------------------------------------------------


class TestValidatorFailureRetryIntegration:
    @pytest.mark.asyncio
    async def test_spec_loop_pauses_on_failure_then_chains_on_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_config(tmp_path)
        _MockBranch(monkeypatch, tmp_path)  # stays on "main" throughout — never checks out
        validators_dir = tmp_path / "validators"
        validators_dir.mkdir(parents=True)
        failing_checks_literal = repr(
            [
                {"name": "spec_exists", "pass": True},
                {"name": "qa_answered", "pass": False},
                {"name": "technical_approach_filled", "pass": True},
                {"name": "scope_defined", "pass": True},
            ]
        )
        (validators_dir / "check_spec_complete.py").write_text(
            "import json, sys\n"
            f"print(json.dumps({{'pass': False, 'message': 'spec incomplete', "
            f"'checks': {failing_checks_literal}}}))\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            await call("run_loop", name="spec")
            await call("loop_advance", output="ticket created", skill="create-ticket")
            msg = await call("loop_continue")

            assert "paused (validation failed)" in msg.lower()
            assert "qa_answered" in msg

            # spec.md was never actually written (create-ticket's output is
            # simulated) and the branch never changed — the job stays under
            # its temp/pending id rather than being promoted prematurely.
            pending = find_pending_run(tmp_path)
            assert pending is not None
            assert pending[1] == "spec"

            # Fix the underlying issue: swap in a validator that now passes.
            (validators_dir / "check_spec_complete.py").write_text(
                _stub_validator_source(_STUB_CHECKS["check_spec_complete"]), encoding="utf-8"
            )

            msg = await call("loop_continue")  # retry — re-runs validators

        assert "chaining automatically to **plan**" in msg.lower()
        active = find_active_run(tmp_path, "main")
        assert active is not None
        assert active[0] == "plan"


# ---------------------------------------------------------------------------
# Memory hooks surfaced through the MCP tool, not just the internal helper
# ---------------------------------------------------------------------------


class TestMemoryContextIntegration:
    @pytest.mark.asyncio
    async def test_run_loop_surfaces_open_learnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_config(tmp_path)
        _MockBranch(monkeypatch, tmp_path)
        active_context = tmp_path / ".dmx" / "activeContext.md"
        active_context.parent.mkdir(parents=True, exist_ok=True)
        active_context.write_text(
            "## Open Learnings\n- CI requires `uv run pytest -q`\n\n"
            "## Open Decisions\n\n## Session Notes\n",
            encoding="utf-8",
        )
        app = create_app()

        async with Client(app) as client:
            result = await client.call_tool(
                "run_loop", {"name": "spec", "workspace_root": str(tmp_path)}
            )

        assert "Memory context" in result.data
        assert "CI requires `uv run pytest -q`" in result.data


# ---------------------------------------------------------------------------
# GH-9: spec loop workspace isolation + per-branch loop state
# ---------------------------------------------------------------------------


class TestLoopStateIsolationIntegration:
    @pytest.mark.asyncio
    async def test_spec_loop_rejects_start_from_feature_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_config(tmp_path)
        _MockBranch(monkeypatch, tmp_path, initial="bug-gh-1-in-progress")
        app = create_app()

        async with Client(app) as client:
            msg = await _call(client, "run_loop", tmp_path, name="spec")

        assert "cannot start" in msg.lower()
        assert "main" in msg
        assert "get_skill_definition" not in msg
        # Nothing was written — no leftover job folders from a rejected start.
        jobs_dir = tmp_path / ".dmx" / "jobs"
        assert not jobs_dir.exists() or not list(jobs_dir.iterdir())

    @pytest.mark.asyncio
    async def test_two_tickets_in_sequence_get_isolated_job_folders(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact GH-9 repro: finish ticket 1 (leaving its spec.md/branch
        behind, as close-ticket does), then start a fresh spec loop for
        ticket 2 from main. Ticket 2's state must never land in ticket 1's
        job folder."""
        _install_passing_validators(tmp_path)
        _write_config(tmp_path)
        branch = _MockBranch(monkeypatch, tmp_path)
        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            # --- Ticket 1: spec -> plan (stop early, close-ticket doesn't
            # touch .dmx/, so GH-1's spec.md is still sitting there) ---
            await call("run_loop", name="spec")
            branch.checkout("bug-gh-1-first-ticket")
            _write_spec_md(tmp_path, "GH-1", "bug-gh-1-first-ticket")
            await call("loop_advance", output="created ticket GH-1", skill="create-ticket")
            msg = await call("loop_continue")
            assert "chaining automatically to **plan**" in msg.lower()

            await call("loop_advance", output="tasks.md created", skill="plan")
            msg = await call("loop_continue")
            assert "release loop — complete" not in msg.lower()

            # Simulate returning to main after GH-1's PR merged: close-ticket
            # never cleans .dmx/ on main, and the merge itself brought GH-1's
            # spec.md forward — main now has it as a leftover, stale file.
            branch.checkout("main")
            _write_spec_md(tmp_path, "GH-1", "bug-gh-1-first-ticket")

            # --- Ticket 2: fresh spec loop from main ---
            msg = await call("run_loop", name="spec")
            assert "get_skill_definition" in msg
            assert "create-ticket" in msg

            branch.checkout("bug-gh-2-second-ticket")
            _write_spec_md(tmp_path, "GH-2", "bug-gh-2-second-ticket")
            msg = await call("loop_advance", output="created ticket GH-2", skill="create-ticket")
            assert "paused" in msg.lower()

        jobs_dir = tmp_path / ".dmx" / "jobs"
        assert {p.name for p in jobs_dir.iterdir()} == {"GH-1", "GH-2"}

        gh1_state = next((jobs_dir / "GH-1").glob("*.json"))
        assert "GH-1" in gh1_state.read_text(encoding="utf-8")

        gh2_active = find_active_run(tmp_path, "GH-2")
        assert gh2_active is not None
        assert gh2_active[0] == "spec"

    @pytest.mark.asyncio
    async def test_resuming_paused_work_after_switching_branches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pause ticket A mid-dev, switch to ticket B's branch, then switch
        back — ticket A's paused state must still be there and resumable,
        unaffected by any work done on B."""
        _install_passing_validators(tmp_path)
        (tmp_path / ".dmx").mkdir(parents=True, exist_ok=True)
        branch = _MockBranch(monkeypatch, tmp_path)
        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            # Ticket A: dev loop paused after its first skill.
            branch.checkout("feature-gh-a")
            _write_spec_md(tmp_path, "GH-A", "feature-gh-a")
            msg = await call("run_loop", name="dev")
            assert "implement-next-phase" in msg
            msg = await call(
                "loop_advance", output="implemented phase 1 on A", skill="implement-next-phase"
            )
            assert "paused" in msg.lower()

            # Switch to ticket B, run its own independent dev loop.
            branch.checkout("feature-gh-b")
            _write_spec_md(tmp_path, "GH-B", "feature-gh-b")
            msg = await call("run_loop", name="dev")
            assert "implement-next-phase" in msg
            msg = await call(
                "loop_advance", output="implemented phase 1 on B", skill="implement-next-phase"
            )
            assert "paused" in msg.lower()

            # Switch back to A — its paused run resumes untouched.
            branch.checkout("feature-gh-a")
            msg = await call("loop_continue")
            assert "commit" in msg
            assert "get_skill_definition" in msg

        a_active = find_active_run(tmp_path, "GH-A")
        assert a_active is not None
        a_state = _read_json(tmp_path / ".dmx" / "jobs" / "GH-A" / f"dev-{a_active[1]}.json")
        assert a_state["skills_completed"] == ["implement-next-phase"]

        b_active = find_active_run(tmp_path, "GH-B")
        assert b_active is not None
        b_state = _read_json(tmp_path / ".dmx" / "jobs" / "GH-B" / f"dev-{b_active[1]}.json")
        assert b_state["status"] == "paused"
        assert b_state["skills_completed"] == ["implement-next-phase"]


def _read_json(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


class TestGetSkillDefinitionFolderShaped:
    """GH-27 phase 4: the {name}/SKILL.md folder shape, end-to-end through
    the actual get_skill_definition MCP tool (not the internal helper)."""

    @pytest.mark.asyncio
    async def test_folder_shaped_skill_includes_root_path_and_dependencies(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / ".dmx" / "shared-sources.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            'shared_sources:\n  - name: acme\n    source: "git::https://x//?ref=v1"\n',
            encoding="utf-8",
        )
        skill_dir = tmp_path / ".dmx" / "vendor" / "acme" / "skills" / "custom-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            '---\ndependencies: ["requests", "pyyaml"]\n---\n\nRun `scripts/run.py`.\n',
            encoding="utf-8",
        )

        app = create_app()
        async with Client(app) as client:
            msg = await _call(client, "get_skill_definition", tmp_path, name="custom-skill")

        assert "`.dmx/vendor/acme/skills/custom-skill/`" in msg
        assert "requests" in msg
        assert "pyyaml" in msg
        assert "Run `scripts/run.py`." in msg

    @pytest.mark.asyncio
    async def test_folder_shaped_skill_without_dependencies_omits_the_note(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / ".dmx" / "shared-sources.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            'shared_sources:\n  - name: acme\n    source: "git::https://x//?ref=v1"\n',
            encoding="utf-8",
        )
        skill_dir = tmp_path / ".dmx" / "vendor" / "acme" / "skills" / "no-deps-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("Just a body, no frontmatter deps.\n", encoding="utf-8")

        app = create_app()
        async with Client(app) as client:
            msg = await _call(client, "get_skill_definition", tmp_path, name="no-deps-skill")

        assert "`.dmx/vendor/acme/skills/no-deps-skill/`" in msg
        assert "Declared dependencies" not in msg

    @pytest.mark.asyncio
    async def test_flat_skill_gets_no_root_path_prefix(self, tmp_path: Path) -> None:
        skill_path = tmp_path / ".dmx" / "skills" / "flat-skill.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text("---\ntitle: Flat\n---\n\nJust do the thing.\n", encoding="utf-8")

        app = create_app()
        async with Client(app) as client:
            msg = await _call(client, "get_skill_definition", tmp_path, name="flat-skill")

        assert msg.strip() == "Just do the thing."
        assert "Skill root:" not in msg


class TestReleaseSnapshot:
    """GH-49: create-pr records the release run as complete, and a second
    release on the same ticket can still advance."""

    @pytest.mark.asyncio
    async def test_snapshot_then_merge_before_continue_leaves_a_complete_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_passing_validators(tmp_path)
        _write_config(tmp_path)
        _MockBranch(monkeypatch, tmp_path, initial="feature-gh-1")
        _write_spec_md(tmp_path, "GH-1", "feature-gh-1")
        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            await call("run_loop", name="release")
            await call("snapshot_loop_for_pr")
            message = await call("loop_advance", output="opened PR #42", skill="create-pr")

        assert "paused" in message.lower()
        assert not message.startswith("Error:")
        state = _read_json(next((tmp_path / ".dmx" / "jobs" / "GH-1").glob("release-*.json")))
        assert state["status"] == "complete"
        assert state["outcome"] is None
        assert state["validator_results"] == []
        assert state["skills_completed"] == ["create-pr"]

    @pytest.mark.asyncio
    async def test_loop_continue_records_check_pr_ready(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_passing_validators(tmp_path)
        _write_config(tmp_path)
        _MockBranch(monkeypatch, tmp_path, initial="feature-gh-1")
        _write_spec_md(tmp_path, "GH-1", "feature-gh-1")
        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            await call("run_loop", name="release")
            await call("snapshot_loop_for_pr")
            await call("loop_advance", output="opened PR #42", skill="create-pr")
            message = await call("loop_continue")

        assert "release loop — complete" in message.lower()
        state = _read_json(next((tmp_path / ".dmx" / "jobs" / "GH-1").glob("release-*.json")))
        assert state["status"] == "complete"
        assert state["outcome"] == "success"
        assert state["validator_results"]

    @pytest.mark.asyncio
    async def test_second_release_after_a_skipped_continue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_passing_validators(tmp_path)
        _write_config(tmp_path)
        _MockBranch(monkeypatch, tmp_path, initial="feature-gh-1")
        _write_spec_md(tmp_path, "GH-1", "feature-gh-1")
        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            await call("run_loop", name="release")
            await call("snapshot_loop_for_pr")
            await call("loop_advance", output="opened PR #42", skill="create-pr")

            await call("run_loop", name="validate")
            await call("loop_advance", output="all checks green", skill="validate")
            chained = await call("loop_continue")
            assert "create-pr" in chained

            await call("snapshot_loop_for_pr")
            message = await call("loop_advance", output="opened PR #43", skill="create-pr")

        assert not message.startswith("Error:")
        snapshots = [
            _read_json(path)
            for path in (tmp_path / ".dmx" / "jobs" / "GH-1").glob("release-*.json")
            if _read_json(path).get("outcome") is None
        ]
        assert len(snapshots) == 1
        assert snapshots[0]["status"] == "complete"


def _write_slow_loop(root: Path, *, name: str, human_gate: bool) -> None:
    loops = root / ".dmx" / "loops"
    loops.mkdir(parents=True, exist_ok=True)
    (loops / f"{name}.yaml").write_text(
        f"name: {name}\n"
        "skills:\n"
        "  - do-thing\n"
        "goal_state: done\n"
        f"human_gate: {str(human_gate).lower()}\n"
        "validators:\n"
        "  - tool: slow_check\n"
        "    checks:\n"
        "      - name: slept\n"
        "        required: true\n"
        "on_optional_failure: warn\n"
        "failure_handling: pause\n",
        encoding="utf-8",
    )
    validators = root / "validators"
    validators.mkdir(parents=True, exist_ok=True)
    (validators / "slow_check.py").write_text(
        "import json, sys, time\n"
        "time.sleep(1.5)\n"
        "print(json.dumps({'pass': True, 'message': 'slept', "
        "'checks': [{'name': 'slept', 'pass': True}]}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )


class TestAsyncValidators:
    """GH-47: validators run off the MCP request, and a repeated advance is ignored."""

    @pytest.mark.asyncio
    async def test_advance_returns_while_a_slow_validator_is_still_running(
        self, tmp_path: Path
    ) -> None:
        _write_slow_loop(tmp_path, name="quickfinish", human_gate=False)
        app = create_app()

        async with Client(app) as client:
            await _call(client, "run_loop", tmp_path, name="quickfinish")
            started = time.perf_counter()
            message = await _call(
                client,
                "loop_advance",
                tmp_path,
                wait=False,
                output="done",
                skill="do-thing",
            )
            elapsed = time.perf_counter() - started
            assert elapsed < 1
            assert message == "Validators are running. Call `loop_status` to see the result."

            status_task = asyncio.create_task(_call(client, "loop_status", tmp_path))
            await asyncio.sleep(0.05)
            other_started = time.perf_counter()
            other = await _call(client, "get_skill_definition", tmp_path, name="does-not-exist")
            assert time.perf_counter() - other_started < 1
            assert "not found" in other.lower()

            repeated = await _call(
                client,
                "loop_advance",
                tmp_path,
                wait=False,
                output="done again",
                skill="do-thing",
            )
            assert "Nothing was advanced" in repeated or "Validators are running" in repeated

            outcome = await status_task

        assert "quickfinish loop — complete" in outcome
        state = _read_json(next((tmp_path / ".dmx" / "jobs").glob("*/*.json")))
        assert state["skills_completed"] == ["do-thing"]
        assert state["validator_results"]

    @pytest.mark.asyncio
    async def test_continue_returns_while_a_slow_validator_is_still_running(
        self, tmp_path: Path
    ) -> None:
        _write_slow_loop(tmp_path, name="gated", human_gate=True)
        app = create_app()

        async with Client(app) as client:
            await _call(client, "run_loop", tmp_path, name="gated")
            paused = await _call(client, "loop_advance", tmp_path, output="done", skill="do-thing")
            assert "paused" in paused.lower()

            started = time.perf_counter()
            message = await _call(client, "loop_continue", tmp_path, wait=False)
            assert time.perf_counter() - started < 1
            assert "Validators are running" in message

            again = await _call(client, "loop_continue", tmp_path, wait=False)
            assert again == "Validators are running. Call `loop_status` to see the result."

            outcome = await _call(client, "loop_status", tmp_path)

        assert "gated loop — complete" in outcome

    @pytest.mark.asyncio
    async def test_repeating_loop_advance_does_not_record_the_skill_twice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_passing_validators(tmp_path)
        _write_config(tmp_path)
        _MockBranch(monkeypatch, tmp_path)
        app = create_app()

        async with Client(app) as client:
            await _call(client, "run_loop", tmp_path, name="spec")
            first = await _call(
                client,
                "loop_advance",
                tmp_path,
                output="created ticket",
                skill="create-ticket",
            )
            assert "paused" in first.lower()
            second = await _call(
                client,
                "loop_advance",
                tmp_path,
                output="created ticket",
                skill="create-ticket",
            )

        assert "already recorded" in second
        assert "Nothing was advanced" in second
        pending = find_pending_run(tmp_path)
        assert pending is not None
        job_id, _loop_name, task_id = pending
        state = _read_json(tmp_path / ".dmx" / "jobs" / job_id / f"spec-{task_id}.json")
        assert state["skills_completed"] == ["create-ticket"]
        assert state["current_skill_index"] == 1

    @pytest.mark.asyncio
    async def test_loop_status_reports_running_when_validation_outlasts_the_wait(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("dmx.loop_tools._STATUS_WAIT_SECONDS", 0.2)
        _write_slow_loop(tmp_path, name="quickfinish", human_gate=False)
        app = create_app()

        async with Client(app) as client:
            await _call(client, "run_loop", tmp_path, name="quickfinish")
            await _call(
                client,
                "loop_advance",
                tmp_path,
                wait=False,
                output="done",
                skill="do-thing",
            )
            started = time.perf_counter()
            during = await _call(client, "loop_status", tmp_path)
            elapsed = time.perf_counter() - started
            assert "Validators are running" in during
            assert elapsed < 1
            monkeypatch.setattr("dmx.loop_tools._STATUS_WAIT_SECONDS", 25)
            outcome = await _call(client, "loop_status", tmp_path)

        assert "quickfinish loop — complete" in outcome

    @pytest.mark.asyncio
    async def test_validating_without_a_worker_is_interrupted(self, tmp_path: Path) -> None:
        _write_slow_loop(tmp_path, name="gated", human_gate=True)
        (tmp_path / "validators" / "slow_check.py").write_text(
            "import json, sys\n"
            "print(json.dumps({'pass': True, 'message': 'ok', "
            "'checks': [{'name': 'slept', 'pass': True}]}))\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        write_initial_state(tmp_path, "gated", "unknown", "task-47", ["do-thing"])
        write_state(
            tmp_path,
            "unknown",
            "gated",
            "task-47",
            {
                "status": "validating",
                "current_skill_index": 1,
                "skills_completed": ["do-thing"],
                "skill_outputs": {"do-thing": "done"},
                "validation_started_at": "2026-10-01T00:00:00Z",
            },
        )
        app = create_app()

        async with Client(app) as client:
            started = time.perf_counter()
            status = await _call(client, "loop_status", tmp_path)
            assert time.perf_counter() - started < 1
            assert status == "Validation was interrupted; call `loop_continue` to re-run it."

            advance = await _call(
                client, "loop_advance", tmp_path, wait=False, output="again", skill="do-thing"
            )
            assert advance == status

            restarted = await _call(client, "loop_continue", tmp_path, wait=False)
            assert restarted == "Validators are running. Call `loop_status` to see the result."
            outcome = await _call(client, "loop_status", tmp_path)

        assert "gated loop — complete" in outcome
        state = _read_json(tmp_path / ".dmx" / "jobs" / "unknown" / "gated-task-47.json")
        assert state["skills_completed"] == ["do-thing"]

    @pytest.mark.asyncio
    async def test_run_loop_refuses_while_a_run_is_validating(self, tmp_path: Path) -> None:
        _write_slow_loop(tmp_path, name="quickfinish", human_gate=False)
        app = create_app()

        async with Client(app) as client:
            await _call(client, "run_loop", tmp_path, name="quickfinish")
            await _call(
                client,
                "loop_advance",
                tmp_path,
                wait=False,
                output="done",
                skill="do-thing",
            )
            refused = await _call(client, "run_loop", tmp_path, name="other")
            outcome = await _call(client, "loop_status", tmp_path)

        assert "still validating" in refused
        assert "quickfinish loop — complete" in outcome
        jobs = tmp_path / ".dmx" / "jobs"
        assert len(list(jobs.glob("*/*.json"))) == 1

    @pytest.mark.asyncio
    async def test_repeating_advance_on_a_later_skill_does_not_move_the_index(
        self, tmp_path: Path
    ) -> None:
        loops = tmp_path / ".dmx" / "loops"
        loops.mkdir(parents=True)
        (loops / "two.yaml").write_text(
            "name: two\nskills:\n  - first\n  - second\nhuman_gate: true\nvalidators: []\n",
            encoding="utf-8",
        )
        app = create_app()

        async with Client(app) as client:
            await _call(client, "run_loop", tmp_path, name="two")
            first = await _call(client, "loop_advance", tmp_path, output="one", skill="first")
            assert "paused" in first.lower()
            second = await _call(client, "loop_advance", tmp_path, output="one", skill="first")

        assert "already recorded" in second
        assert "Current skill is `second`" in second
        state = _read_json(next((tmp_path / ".dmx" / "jobs").glob("*/*.json")))
        assert state["skills_completed"] == ["first"]
        assert state["current_skill_index"] == 1


class TestStaleSpecPromotion:
    """GH-50: a stale spec.md must not file the new spec run under the previous ticket."""

    @pytest.mark.asyncio
    async def test_pending_run_promotes_only_after_spec_matches_the_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_config(tmp_path)
        branch = _MockBranch(monkeypatch, tmp_path)
        validators_dir = tmp_path / "validators"
        validators_dir.mkdir()
        # Fails the identity check on purpose. A stub that passes every check
        # would let loop_continue chain to plan while the spec is still stale.
        checks = repr(
            [
                {"name": "spec_exists", "pass": True},
                {"name": "qa_answered", "pass": True},
                {"name": "technical_approach_filled", "pass": True},
                {"name": "scope_defined", "pass": True},
                {"name": "spec_identity_matches_branch", "pass": False},
            ]
        )
        (validators_dir / "check_spec_complete.py").write_text(
            "import json, sys\n"
            f"print(json.dumps({{'pass': False, 'message': 'branch mismatch', "
            f"'checks': {checks}}}))\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            await call("run_loop", name="spec")
            branch.checkout("feature-gh-2")
            _write_spec_md(tmp_path, "GH-1", "feature-gh-1")
            advanced = await call(
                "loop_advance", output="created the branch", skill="create-ticket"
            )

            assert "paused" in advanced.lower()
            assert not advanced.startswith("Error:")
            pending = find_pending_run(tmp_path)
            assert pending is not None
            pending_id, loop_name, task_id = pending
            assert loop_name == "spec"
            state = _read_json(tmp_path / ".dmx" / "jobs" / pending_id / f"spec-{task_id}.json")
            assert state["status"] == "paused"
            assert not (tmp_path / ".dmx" / "jobs" / "GH-1").exists()

            _write_spec_md(tmp_path, "GH-2", "feature-gh-2")
            continued = await call("loop_continue")

        assert "spec_identity_matches_branch" in continued
        assert "chaining" not in continued.lower()
        assert find_pending_run(tmp_path) is None
        promoted = find_active_run(tmp_path, "GH-2")
        assert promoted == ("spec", task_id)
        assert not (tmp_path / ".dmx" / "jobs" / "GH-1").exists()


def _write_interrupted_finish(root: Path, status: str) -> None:
    """A one-skill loop whose last advance was saved and whose finish never started."""
    loops = root / ".dmx" / "loops"
    loops.mkdir(parents=True)
    (loops / "finishme.yaml").write_text(
        "name: finishme\n"
        "skills:\n"
        "  - do-thing\n"
        "human_gate: false\n"
        "validators:\n"
        "  - tool: finish_check\n"
        "    checks:\n"
        "      - name: ok\n"
        "        required: true\n",
        encoding="utf-8",
    )
    validators = root / "validators"
    validators.mkdir(parents=True)
    (validators / "finish_check.py").write_text(
        "import json, sys\n"
        "print(json.dumps({'pass': True, 'message': 'ok', "
        "'checks': [{'name': 'ok', 'pass': True}]}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    write_initial_state(root, "finishme", "unknown", "task-48", ["do-thing"])
    write_state(
        root,
        "unknown",
        "finishme",
        "task-48",
        {
            "status": status,
            "current_skill_index": 1,
            "skills_completed": ["do-thing"],
            "skill_outputs": {"do-thing": "done"},
        },
    )


class TestFinishPending:
    """GH-48: skills recorded, finish never started, status still running or iterating."""

    @pytest.mark.parametrize("status", ["running", "iterating"])
    @pytest.mark.asyncio
    async def test_continue_finishes_a_run_whose_skills_were_already_recorded(
        self, tmp_path: Path, status: str
    ) -> None:
        _write_interrupted_finish(tmp_path, status)
        app = create_app()
        recovery = (
            "All skills for this run are complete; the finish step did not complete. "
            "Call `loop_continue` to re-run validators."
        )

        async with Client(app) as client:
            advance = await _call(
                client,
                "loop_advance",
                tmp_path,
                wait=False,
                output="again",
                skill="do-thing",
            )
            assert advance == recovery
            assert await _call(client, "loop_status", tmp_path) == recovery
            state = _read_json(tmp_path / ".dmx" / "jobs" / "unknown" / "finishme-task-48.json")
            assert state["status"] == status
            assert state["current_skill_index"] == 1
            assert state["skills_completed"] == ["do-thing"]

            outcome = await _call(client, "loop_continue", tmp_path)

        assert "finishme loop — complete" in outcome
