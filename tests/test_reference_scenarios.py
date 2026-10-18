"""Every work item's scenario since 0.7, on the reference monorepo (#81).

Each class is one work item, driven through the MCP tools the agent calls
where it can, so the scenario covers the wiring as well as the parts. What an
agent decides (which owner a learning belongs to, what to write in memory) is
done by the test in the agent's place, then checked through the tools.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

from dmx.component_tests import plan_tests
from dmx.job_pruning import plan_prune
from dmx.server import create_app
from tests.test_loop_integration import _call as _loop_call
from tests.test_loop_integration import _install_passing_validators

if TYPE_CHECKING:
    from pathlib import Path

_LINE = {
    "ledger": "ledger (.dmx/components/ledger/)",
    "payments": "payments (.dmx/components/payments/)",
    "billing-ui": "billing-ui (.dmx/components/billing-ui/)",
}


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _touch(root: Path, rel: str) -> None:
    with (root / rel).open("a", encoding="utf-8") as handle:
        handle.write("\n// changed\n")


def _ticket(root: Path, job: str, branch: str, components: str) -> None:
    _git(root, "checkout", "-q", "-b", branch)
    _write(
        root,
        f".dmx/jobs/{job}/spec.md",
        f"---\nticket: {job}\nbranch: {branch}\ncomponents: {components}\n---\n# {job}\n",
    )


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _merge_to_main(root: Path, branch: str) -> None:
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "-m", f"merge {branch}", branch)


async def _tool(client: Client, tool: str, root: Path, **kwargs: Any) -> Any:
    result = await client.call_tool(tool, {"workspace_root": str(root), **kwargs})
    return result.data


class TestComponents:
    """#86: a ticket's components, from its spec, its changes, or the IDE folder."""

    @pytest.mark.asyncio
    async def test_a_chat_in_a_service_folder(self, reference_monorepo: Path) -> None:
        _git(reference_monorepo, "checkout", "-q", "-b", "feature-x")

        async with Client(create_app()) as client:
            status = await client.call_tool(
                "loop_status",
                {"workspace_root": str(reference_monorepo / "services" / "payments")},
            )

        assert status.data.splitlines()[0] == f"Components: {_LINE['payments']}"

    @pytest.mark.asyncio
    async def test_spec_and_changed_files(self, reference_monorepo: Path) -> None:
        _ticket(reference_monorepo, "GH-1", "feature-gh-1", "[ledger]")
        _touch(reference_monorepo, "packages/billing-ui/test/format.test.js")

        async with Client(create_app()) as client:
            status = await _tool(client, "loop_status", reference_monorepo)
            context = await _tool(client, "get_context", reference_monorepo)

        assert status.splitlines()[0] == (f"Components: {_LINE['billing-ui']}, {_LINE['ledger']}")
        assert [(c["id"], c["reason"]) for c in context["components"]] == [
            ("billing-ui", "changed files"),
            ("ledger", "spec"),
        ]


class TestAddComponent:
    """#87: one folder becomes a component, and its tests run."""

    @pytest.mark.asyncio
    async def test_a_go_service_in_the_root_module(self, reference_monorepo: Path) -> None:
        _write(reference_monorepo, "services/fraud/fraud.go", "package fraud\n")
        _write(
            reference_monorepo,
            "services/fraud/fraud_test.go",
            'package fraud\n\nimport "testing"\n\nfunc TestNothing(t *testing.T) {}\n',
        )

        async with Client(create_app()) as client:
            added = await _tool(
                client, "add_component", reference_monorepo, path="services/fraud", dry_run=False
            )
        _commit(reference_monorepo, "add fraud")
        _git(reference_monorepo, "checkout", "-q", "-b", "feature-fraud")
        _touch(reference_monorepo, "services/fraud/fraud.go")

        assert (added["id"], added["language"]) == ("fraud", "go")
        assert [(r.components, r.command, r.cwd) for r in plan_tests(reference_monorepo).runs] == [
            (("fraud",), "go test ./services/fraud/...", ".")
        ]


class TestScopedMemory:
    """#88: a payments ticket's learning about ledger reaches ledger in ledger's next PR."""

    @pytest.mark.asyncio
    async def test_a_learning_waits_for_its_owner_then_folds_in(
        self, reference_monorepo: Path
    ) -> None:
        root = reference_monorepo
        refund = "Refunds post a reversing entry, never a negative amount."

        # PR 1, the payments team: reads ledger's memory, may only write payments'.
        _ticket(root, "GH-1", "feature-gh-1-refunds", "[payments]")
        _touch(root, "services/payments/payments.go")
        _write(
            root,
            ".dmx/jobs/GH-1/activeContext.md",
            f"## Open Learnings\n- [ledger] {refund}\n"
            "- [payments] Refund handlers take an idempotency key.\n\n"
            "## Open Decisions\n\n## Session Notes\n",
        )
        async with Client(create_app()) as client:
            first = await _tool(client, "get_context", root)
        assert ".dmx/components/ledger/systemPatterns.md" in first["memory_files"]
        assert first["editable_memory"] == ["payments"]
        # The agent writes the payments learning into payments' memory and leaves ledger's.
        _write(root, ".dmx/components/payments/systemPatterns.md", "- Refunds take a key.\n")
        _write(
            root,
            ".dmx/jobs/GH-1/activeContext.md",
            f"## Open Learnings\n- [ledger] {refund}\n\n## Open Decisions\n\n## Session Notes\n",
        )
        _commit(root, "GH-1")
        _merge_to_main(root, "feature-gh-1-refunds")

        # PR 2, the ledger team: sees the waiting learning, folds it in, marks it.
        _ticket(root, "GH-2", "feature-gh-2-ledger", "[ledger]")
        _touch(root, "services/ledger/ledger.go")
        later = datetime.now(UTC) + timedelta(days=60)
        async with Client(create_app()) as client:
            second = await _tool(client, "get_context", root)
            assert second["editable_memory"] == ["ledger"]
            assert second["waiting_learnings"] == [
                {"job": "GH-1", "owner": "ledger", "section": "Open Learnings", "text": refund}
            ]
            assert "GH-1" in dict(plan_prune(root, timedelta(days=30), now=later).kept)
            with (root / ".dmx/components/ledger/systemPatterns.md").open("a") as memory:
                memory.write(f"- {refund}\n")
            marked = await _tool(
                client,
                "mark_learnings_folded",
                root,
                job="GH-1",
                items=[refund],
                target="ledger",
                ref="feature-gh-2-ledger",
            )
            third = await _tool(client, "get_context", root)

        assert marked["marked"] == [refund]
        assert third["waiting_learnings"] == []
        assert third["waiting_counts"] == {}
        _commit(root, "GH-2")
        assert "GH-1" in plan_prune(root, timedelta(days=30), now=later).removes


class TestComponentTests:
    """#89: a ledger change runs ledger's tests and its dependents', and nothing else."""

    def test_a_ledger_change(self, reference_monorepo: Path) -> None:
        _ticket(reference_monorepo, "GH-1", "feature-gh-1", "[ledger]")
        _touch(reference_monorepo, "services/ledger/ledger.go")

        plan = plan_tests(reference_monorepo)

        assert [r.components for r in plan.runs] == [("ledger",), ("payments",)]
        assert plan.reasons == {"ledger": "changed files", "payments": "depends on ledger"}


class TestComponentConfig:
    """#90: a component's own and shared skills, loops and validators."""

    @pytest.mark.asyncio
    async def test_payments_skills_and_loops_from_its_shared_source(
        self, reference_monorepo: Path
    ) -> None:
        _ticket(reference_monorepo, "GH-1", "feature-gh-1", "[payments]")

        async with Client(create_app()) as client:
            skill = await _tool(
                client, "get_skill_definition", reference_monorepo, name="payments/refund-check"
            )
            started = await _tool(
                client, "run_loop", reference_monorepo, name="payments/refund-drill"
            )
            plain = await _tool(
                client, "get_skill_definition", reference_monorepo, name="refund-check"
            )

        assert skill.startswith("Look up the original charge")
        assert "Next skill: `payments/refund-check` (Loop: payments/refund-drill" in started
        assert plain.startswith("Skill 'refund-check' not found")

    @pytest.mark.asyncio
    async def test_a_ledger_ticket_cant_use_payments_skills(self, reference_monorepo: Path) -> None:
        _ticket(reference_monorepo, "GH-1", "feature-gh-1", "[ledger]")

        async with Client(create_app()) as client:
            refused = await _tool(
                client, "get_skill_definition", reference_monorepo, name="payments/refund-check"
            )

        assert refused.startswith("`payments/refund-check` belongs to the `payments` component")

    @pytest.mark.asyncio
    async def test_both_components_validators_gate_the_validate_loop(
        self, reference_monorepo: Path
    ) -> None:
        root = reference_monorepo
        _install_passing_validators(root)
        _ticket(root, "GH-1", "feature-gh-1", "[ledger, payments]")
        _write(root, "services/ledger/rounding.go", "package ledger\n\nvar rate float64\n")

        async with Client(create_app()) as client:
            await _loop_call(client, "run_loop", root, name="validate")
            await _loop_call(client, "loop_advance", root, output="done", skill="validate")
            paused = await _loop_call(client, "loop_continue", root)

        assert "Required checks failed: ledger/no_floats" in paused
        assert "paused for rework approval" in paused
        tasks = (root / ".dmx/jobs/GH-1/tasks.md").read_text(encoding="utf-8")
        assert "Rework — validate, round 1" in tasks
        assert "- [ ] Fix `ledger/no_floats` (ledger/no_float_money)" in tasks
        state = json.loads(
            next((root / ".dmx/jobs/GH-1").glob("validate-*.json")).read_text(encoding="utf-8")
        )
        tools = [result["tool"] for result in state["validator_results"]]
        assert tools[-2:] == ["ledger/no_float_money", "payments/idempotent_refunds"]


class TestRework:
    """#115: a component check fails, rework fixes the code, and validate passes."""

    @pytest.mark.asyncio
    async def test_rework_fixes_a_failed_component_check(self, reference_monorepo: Path) -> None:
        root = reference_monorepo
        _install_passing_validators(root)
        _ticket(root, "GH-1", "feature-gh-1", "[ledger]")
        tasks = root / ".dmx/jobs/GH-1/tasks.md"
        _write(root, ".dmx/jobs/GH-1/tasks.md", "# GH-1\n\n## Phase 1: Rounding\n\n- [x] Round\n")
        _write(root, "services/ledger/rounding.go", "package ledger\n\nvar rate float64\n")
        _commit(root, "feat: rounding")

        async with Client(create_app()) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _loop_call(client, tool, root, **kwargs)

            await call("run_loop", name="validate")
            await call("loop_advance", output="report written", skill="validate")
            proposed = await call("loop_continue")
            assert "paused for rework approval" in proposed
            assert "- [ ] Fix `ledger/no_floats` (ledger/no_float_money)" in tasks.read_text(
                encoding="utf-8"
            )

            started = await call("loop_continue")
            assert "validate — rework round 1 of 2 (dev), then back to validate" in started
            assert "implement-next-phase" in started

            # The agent's part: fix the code and check off the rework task.
            _write(root, "services/ledger/rounding.go", "package ledger\n\nvar rateBp int64\n")
            tasks.write_text(
                tasks.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8"
            )
            await call("loop_advance", output="replaced the float", skill="implement-next-phase")
            await call("loop_continue")
            _commit(root, "fix: keep the rate in basis points")
            await call("loop_advance", output="committed", skill="commit")
            back = await call("loop_continue")
            assert "Back to **validate**" in back

            await call("loop_advance", output="report rewritten", skill="validate")
            done = await call("loop_continue")

        assert "chaining automatically to **release**" in done.lower()
        [validate] = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (root / ".dmx/jobs/GH-1").glob("validate-*.json")
        ]
        assert validate["outcome"] == "success"
        assert validate["rework"]["stage"] == "resolved"
        assert [entry["checks"] for entry in validate["rework"]["history"]] == [
            ["ledger/no_floats"]
        ]


class TestCheck:
    """#91: dmx check on the reference monorepo, and its plan for a branch."""

    @pytest.mark.asyncio
    async def test_the_reference_setup_has_no_errors(self, reference_monorepo: Path) -> None:
        _ticket(reference_monorepo, "GH-1", "feature-gh-1", "[payments]")

        async with Client(create_app()) as client:
            report = await _tool(client, "check_setup", reference_monorepo)

        assert "✗" not in report
        assert "  ✓ 6 components" in report
        assert "validate: run_tests, spec_adherence, payments/idempotent_refunds" in report


class TestTicketRouting:
    """#92: each component's ticket goes to its own Jira project."""

    @pytest.mark.asyncio
    async def test_component_defaults(self, reference_monorepo: Path) -> None:
        _write(
            reference_monorepo,
            ".dmx/config.md",
            "branch_base: main\nticketing: jira\nproject_key: DM\njira_projects: [LEDG, PAY]\n",
        )

        async with Client(create_app()) as client:
            ledger = await _tool(
                client, "ticket_destination", reference_monorepo, components=["ledger"]
            )
            both = await _tool(
                client,
                "ticket_destination",
                reference_monorepo,
                components=["ledger", "payments"],
            )

        assert (ledger["target"], ledger["confirm"]) == ("LEDG", True)
        assert ledger["question"] == "Create the ticket in LEDG (default for ledger)?"
        assert [option["target"] for option in both["options"]] == ["LEDG", "PAY"]


class TestEndToEnd:
    """Plan, dev, validate and release on a payments ticket, through the loop tools."""

    @pytest.mark.asyncio
    async def test_the_loop_chain(self, reference_monorepo: Path) -> None:
        root = reference_monorepo
        _install_passing_validators(root)
        _ticket(root, "GH-1", "feature-gh-1", "[payments]")
        _write(root, ".dmx/jobs/GH-1/tasks.md", "## Phase 1: Refunds\n\n- [x] Add refunds\n")
        line = f"Components: {_LINE['payments']}"
        messages: list[str] = []

        async with Client(create_app()) as client:

            async def step(tool: str, **kwargs: str) -> str:
                message: str = await _loop_call(client, tool, root, **kwargs)
                messages.append(message)
                return message

            await step("run_loop", name="plan")
            await step("loop_advance", output="planned", skill="plan")
            assert "chaining automatically to **dev**" in (await step("loop_continue")).lower()
            await step("loop_advance", output="phase 1", skill="implement-next-phase")
            await step("loop_continue")
            await step("loop_advance", output="committed", skill="commit")
            chained = await step("loop_continue")
            assert "chaining automatically to **validate**" in chained.lower()
            await step("loop_advance", output="all green", skill="validate")
            assert "chaining automatically to **release**" in (await step("loop_continue")).lower()
            await step("loop_advance", output="nothing to prune", skill="prune-jobs")
            assert "`create-pr`" in await step("loop_continue")

        assert all(message.splitlines()[0] == line for message in messages)
        validate = json.loads(
            next((root / ".dmx/jobs/GH-1").glob("validate-*.json")).read_text(encoding="utf-8")
        )
        assert validate["outcome"] == "success"
        assert "payments/idempotent_refunds" in [r["tool"] for r in validate["validator_results"]]
