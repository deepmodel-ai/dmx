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

from typing import TYPE_CHECKING

import pytest
from fastmcp import Client

from dmx.loop_state import find_active_run, find_pending_run, resolve_job_id
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


async def _call(client: Client, tool: str, workspace_root: Path, **kwargs: str) -> str:
    kwargs["workspace_root"] = str(workspace_root)
    result = await client.call_tool(tool, kwargs)
    return result.data


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


def _write_spec_md(workspace_root: Path, ticket: str) -> None:
    (workspace_root / ".dmx" / "spec.md").write_text(
        f"---\nticket: {ticket}\n---\n# Spec\n", encoding="utf-8"
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
            _write_spec_md(tmp_path, "GH-1")

            msg = await call("loop_advance", output="created ticket, spec.md filled in")
            assert "paused" in msg.lower()

            msg = await call("loop_continue")
            assert "chaining automatically to **plan**" in msg.lower()
            assert "get_skill_definition" in msg
            assert "plan" in msg

            # --- plan (1 skill) ---
            msg = await call("loop_advance", output="tasks.md created with 2 phases")
            assert "paused" in msg.lower()

            msg = await call("loop_continue")
            assert "chaining automatically to **dev**" in msg.lower()
            assert "get_skill_definition" in msg
            assert "implement-next-phase" in msg

            # --- dev (2 skills; no tasks.md -> repeat_until treated as met) ---
            msg = await call("loop_advance", output="implemented phase 1")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "get_skill_definition" in msg
            assert "commit" in msg

            msg = await call("loop_advance", output="committed")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "chaining automatically to **validate**" in msg.lower()
            assert "get_skill_definition" in msg
            assert "validate" in msg

            # --- validate (1 skill) ---
            msg = await call("loop_advance", output="all checks green")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "chaining automatically to **release**" in msg.lower()
            assert "get_skill_definition" in msg
            assert "create-pr" in msg

            # --- release (2 skills; on_complete has no trigger_loop) ---
            msg = await call("loop_advance", output="opened PR #42")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "get_skill_definition" in msg
            assert "update-memory" in msg

            msg = await call("loop_advance", output="memory bank synced")
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

            await call("loop_advance", output="implemented phase 1 partially")
            await call("loop_continue")  # -> commit
            await call("loop_advance", output="committed wip")
            msg = await call("loop_continue")  # all skills done, repeat_until not met

            assert "iterating (round 1)" in msg.lower()
            assert "get_skill_definition" in msg
            assert "implement-next-phase" in msg

            active = find_active_run(tmp_path, "unknown")
            assert active is not None
            assert active[0] == "dev"

            # Second pass: mark the phase complete before finishing.
            tasks_path.write_text("## Phase 1: X\n- [x] Done now\n", encoding="utf-8")

            await call("loop_advance", output="implemented phase 1 fully")
            await call("loop_continue")  # -> commit
            await call("loop_advance", output="committed final")
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
            await call("loop_advance", output="ticket created")
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
            _write_spec_md(tmp_path, "GH-1")
            await call("loop_advance", output="created ticket GH-1")
            msg = await call("loop_continue")
            assert "chaining automatically to **plan**" in msg.lower()

            await call("loop_advance", output="tasks.md created")
            msg = await call("loop_continue")
            assert "release loop — complete" not in msg.lower()

            # Simulate returning to main after GH-1's PR merged: close-ticket
            # never cleans .dmx/ on main, and the merge itself brought GH-1's
            # spec.md forward — main now has it as a leftover, stale file.
            branch.checkout("main")
            _write_spec_md(tmp_path, "GH-1")

            # --- Ticket 2: fresh spec loop from main ---
            msg = await call("run_loop", name="spec")
            assert "get_skill_definition" in msg
            assert "create-ticket" in msg

            branch.checkout("bug-gh-2-second-ticket")
            _write_spec_md(tmp_path, "GH-2")
            msg = await call("loop_advance", output="created ticket GH-2")
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
            _write_spec_md(tmp_path, "GH-A")
            msg = await call("run_loop", name="dev")
            assert "implement-next-phase" in msg
            msg = await call("loop_advance", output="implemented phase 1 on A")
            assert "paused" in msg.lower()

            # Switch to ticket B, run its own independent dev loop.
            branch.checkout("feature-gh-b")
            _write_spec_md(tmp_path, "GH-B")
            msg = await call("run_loop", name="dev")
            assert "implement-next-phase" in msg
            msg = await call("loop_advance", output="implemented phase 1 on B")
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
