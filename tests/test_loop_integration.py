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

from dmx.loop_state import read_active_pointer
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


# ---------------------------------------------------------------------------
# Full pipeline: spec -> plan -> dev -> validate -> release
# ---------------------------------------------------------------------------


class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_spec_to_release_chains_through_all_loops(self, tmp_path: Path) -> None:
        _install_passing_validators(tmp_path)
        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            # --- spec (1 skill) ---
            msg = await call("run_loop", name="spec")
            assert "run /create-ticket now" in msg.lower()

            msg = await call("loop_advance", output="created ticket, spec.md filled in")
            assert "paused" in msg.lower()

            msg = await call("loop_continue")
            assert "chaining automatically to **plan**" in msg.lower()
            assert "run /plan now" in msg.lower()

            # --- plan (1 skill) ---
            msg = await call("loop_advance", output="tasks.md created with 2 phases")
            assert "paused" in msg.lower()

            msg = await call("loop_continue")
            assert "chaining automatically to **dev**" in msg.lower()
            assert "run /implement-next-phase now" in msg.lower()

            # --- dev (2 skills; no tasks.md -> repeat_until treated as met) ---
            msg = await call("loop_advance", output="implemented phase 1")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "run /commit now" in msg.lower()

            msg = await call("loop_advance", output="committed")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "chaining automatically to **validate**" in msg.lower()
            assert "run /validate now" in msg.lower()

            # --- validate (1 skill) ---
            msg = await call("loop_advance", output="all checks green")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "chaining automatically to **release**" in msg.lower()
            assert "run /create-pr now" in msg.lower()

            # --- release (2 skills; on_complete has no trigger_loop) ---
            msg = await call("loop_advance", output="opened PR #42")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "run /update-memory now" in msg.lower()

            msg = await call("loop_advance", output="memory bank synced")
            assert "paused" in msg.lower()
            msg = await call("loop_continue")
            assert "release loop — complete" in msg.lower()
            assert "chaining" not in msg.lower()

        # Terminal: no loop left active.
        assert read_active_pointer(tmp_path) is None

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
            assert "run /implement-next-phase now" in msg.lower()

            await call("loop_advance", output="implemented phase 1 partially")
            await call("loop_continue")  # -> commit
            await call("loop_advance", output="committed wip")
            msg = await call("loop_continue")  # all skills done, repeat_until not met

            assert "iterating (round 1)" in msg.lower()
            assert "run /implement-next-phase now" in msg.lower()

            pointer = read_active_pointer(tmp_path)
            assert pointer is not None
            assert pointer["active_loop_name"] == "dev"

            # Second pass: mark the phase complete before finishing.
            tasks_path.write_text("## Phase 1: X\n- [x] Done now\n", encoding="utf-8")

            await call("loop_advance", output="implemented phase 1 fully")
            await call("loop_continue")  # -> commit
            await call("loop_advance", output="committed final")
            msg = await call("loop_continue")  # repeat_until met -> chain to validate

            assert "chaining automatically to **validate**" in msg.lower()

        pointer = read_active_pointer(tmp_path)
        assert pointer is not None
        assert pointer["active_loop_name"] == "validate"

        content = (tmp_path / ".dmx" / "activeContext.md").read_text(encoding="utf-8")
        assert "iterating (round 1)" in content


# ---------------------------------------------------------------------------
# Validator failure -> pause -> retry succeeds
# ---------------------------------------------------------------------------


class TestValidatorFailureRetryIntegration:
    @pytest.mark.asyncio
    async def test_spec_loop_pauses_on_failure_then_chains_on_retry(self, tmp_path: Path) -> None:
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

            pointer = read_active_pointer(tmp_path)
            assert pointer is not None
            assert pointer["active_loop_name"] == "spec"

            # Fix the underlying issue: swap in a validator that now passes.
            (validators_dir / "check_spec_complete.py").write_text(
                _stub_validator_source(_STUB_CHECKS["check_spec_complete"]), encoding="utf-8"
            )

            msg = await call("loop_continue")  # retry — re-runs validators

        assert "chaining automatically to **plan**" in msg.lower()
        pointer = read_active_pointer(tmp_path)
        assert pointer is not None
        assert pointer["active_loop_name"] == "plan"


# ---------------------------------------------------------------------------
# Memory hooks surfaced through the MCP tool, not just the internal helper
# ---------------------------------------------------------------------------


class TestMemoryContextIntegration:
    @pytest.mark.asyncio
    async def test_run_loop_surfaces_open_learnings(self, tmp_path: Path) -> None:
        active_context = tmp_path / ".dmx" / "activeContext.md"
        active_context.parent.mkdir(parents=True)
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
