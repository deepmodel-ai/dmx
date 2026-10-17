"""GH-90: component skills, validators and loops, named ``<id>/<name>``."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

from dmx.component_config import component_validators, expand_reported_checks
from dmx.loop_schema import LoopConfig, ValidatorCheck, ValidatorConfig
from dmx.loop_state import state_path
from dmx.server import create_app
from dmx.validator_runner import evaluate_validator_results, run_validators

if TYPE_CHECKING:
    from pathlib import Path

_BRANCH = "feature-gh-7"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ticket(root: Path, components: str) -> None:
    _git(root, "checkout", "-q", "-b", _BRANCH)
    _write(
        root,
        ".dmx/jobs/GH-7/spec.md",
        f"---\nticket: GH-7\nbranch: {_BRANCH}\ncomponents: {components}\n---\n# Spec\n",
    )


def _validator(root: Path, rel: str, checks: list[tuple[str, bool]], passed: bool) -> None:
    result = {
        "pass": passed,
        "message": f"{rel} ran",
        "checks": [{"name": name, "pass": ok, "message": f"{name} said so"} for name, ok in checks],
    }
    _write(
        root,
        rel,
        f"import json, sys\nsys.stdin.read()\nprint(json.dumps({result!r}))\n"
        f"sys.exit(0 if {passed!r} else 1)\n",
    )


def _ledger_config(root: Path) -> None:
    _write(
        root,
        ".dmx/components/ledger/skills/reconcile.md",
        "---\nname: reconcile\ndescription: Reconcile the ledger\n---\nReconcile it.\n",
    )
    _write(
        root,
        ".dmx/components/ledger/loops/backfill.yaml",
        "name: backfill\nskills:\n  - ledger/reconcile\nhuman_gate: true\n",
    )
    _write(
        root,
        ".dmx/components/ledger/component.yaml",
        "description: Ledger service, balances in minor units\npaths: ['services/ledger/**']\n"
        "validators:\n  validate: [no_float_money]\n",
    )
    _validator(
        root,
        ".dmx/components/ledger/validators/no_float_money.py",
        [("no_floats", True), ("int64_amounts", False)],
        passed=False,
    )
    _write(
        root,
        ".dmx/components/payments/component.yaml",
        "description: Payment processing service\npaths: ['services/payments/**']\n"
        "depends_on: [ledger]\n"
        "validators:\n  validate:\n    - tool: migration_safety\n      checks:\n"
        "        - name: reversible\n          required: true\n"
        "        - name: no_table_locks\n          required: false\n",
    )
    _validator(
        root,
        ".dmx/components/payments/validators/migration_safety.py",
        [("reversible", True), ("no_table_locks", False)],
        passed=False,
    )


async def _call(client: Client, tool: str, root: Path, **kwargs: Any) -> Any:
    result = await client.call_tool(tool, {"workspace_root": str(root), **kwargs})
    return result.data


class TestSkills:
    @pytest.mark.asyncio
    async def test_a_ledger_ticket_gets_ledger_skills(self, reference_monorepo: Path) -> None:
        _ledger_config(reference_monorepo)
        _ticket(reference_monorepo, "[ledger]")

        async with Client(create_app()) as client:
            body = await _call(
                client, "get_skill_definition", reference_monorepo, name="ledger/reconcile"
            )

        assert body.strip() == "Reconcile it."

    @pytest.mark.asyncio
    async def test_another_ticket_is_refused(self, reference_monorepo: Path) -> None:
        _ledger_config(reference_monorepo)
        _ticket(reference_monorepo, "[billing-ui]")

        async with Client(create_app()) as client:
            body = await _call(
                client, "get_skill_definition", reference_monorepo, name="ledger/reconcile"
            )

        assert body == (
            "`ledger/reconcile` belongs to the `ledger` component, and this ticket's "
            "components are billing-ui. Add `ledger` to the `components` list in the "
            "ticket's spec.md to use it."
        )

    @pytest.mark.parametrize("name", ["reconcile", "nope/reconcile", "ledger/../x", "ledger/a/b"])
    @pytest.mark.asyncio
    async def test_other_names_never_reach_a_component(
        self, reference_monorepo: Path, name: str
    ) -> None:
        _ledger_config(reference_monorepo)
        _ticket(reference_monorepo, "[ledger]")

        async with Client(create_app()) as client:
            body = await _call(client, "get_skill_definition", reference_monorepo, name=name)

        assert body.startswith(f"Skill '{name}' not found")

    @pytest.mark.asyncio
    async def test_list_skills_puts_the_tickets_component_skills_first(
        self, reference_monorepo: Path
    ) -> None:
        _ledger_config(reference_monorepo)
        _write(
            reference_monorepo,
            ".dmx/skills/helm.md",
            "---\nname: helm\ndescription: Bump the chart\n---\nDo it.\n",
        )
        _ticket(reference_monorepo, "[ledger]")

        async with Client(create_app()) as client:
            listing = await _call(client, "list_skills", reference_monorepo)

        assert listing.splitlines()[0:2] == [
            "ledger/reconcile (ledger): Reconcile the ledger",
            "helm (app repo): Bump the chart",
        ]

    @pytest.mark.asyncio
    async def test_a_ticket_without_the_component_lists_none(
        self, reference_monorepo: Path
    ) -> None:
        _ledger_config(reference_monorepo)
        _ticket(reference_monorepo, "[billing-ui]")

        async with Client(create_app()) as client:
            listing = await _call(client, "list_skills", reference_monorepo)

        assert "ledger/reconcile" not in listing


class TestLoops:
    @pytest.mark.asyncio
    async def test_a_component_loop_runs_by_name(self, reference_monorepo: Path) -> None:
        _ledger_config(reference_monorepo)
        _ticket(reference_monorepo, "[ledger]")

        async with Client(create_app()) as client:
            started = await _call(client, "run_loop", reference_monorepo, name="ledger/backfill")
            advanced = await _call(
                client,
                "loop_advance",
                reference_monorepo,
                skill="ledger/reconcile",
                output="reconciled",
            )

        assert "Next skill: `ledger/reconcile` (Loop: ledger/backfill | Skill 1/1)" in started
        [state_file] = (reference_monorepo / ".dmx/jobs/GH-7").glob("ledger--backfill-*.json")
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["loop_name"] == "ledger/backfill"
        assert state["skills_completed"] == ["ledger/reconcile"]
        assert not advanced.startswith("Error")

    @pytest.mark.asyncio
    async def test_another_ticket_is_refused(self, reference_monorepo: Path) -> None:
        _ledger_config(reference_monorepo)
        _ticket(reference_monorepo, "[payments]")

        async with Client(create_app()) as client:
            started = await _call(client, "run_loop", reference_monorepo, name="ledger/backfill")

        assert "`ledger/backfill` belongs to the `ledger` component" in started
        assert not list((reference_monorepo / ".dmx/jobs").glob("*/ledger--backfill-*.json"))

    @pytest.mark.asyncio
    async def test_a_standard_loop_cant_be_replaced(self, reference_monorepo: Path) -> None:
        _ledger_config(reference_monorepo)
        _write(
            reference_monorepo,
            ".dmx/components/ledger/loops/dev.yaml",
            "name: dev\nskills:\n  - ledger/reconcile\n",
        )
        _ticket(reference_monorepo, "[ledger]")

        async with Client(create_app()) as client:
            started = await _call(client, "run_loop", reference_monorepo, name="dev")

        assert "Next skill: `implement-next-phase`" in started

    def test_state_paths_never_make_a_folder(self, tmp_path: Path) -> None:
        path = state_path(tmp_path, "GH-7", "ledger/backfill", "T")

        assert path.name == "ledger--backfill-T.json"
        assert path.parent.name == "GH-7"


def _validate_loop() -> LoopConfig:
    return LoopConfig(
        name="validate",
        skills=["validate"],
        validators=[
            ValidatorConfig(tool="repo_check", checks=[ValidatorCheck(name="ok", required=True)])
        ],
    )


class TestValidators:
    def test_a_ticket_for_two_components_runs_both(self, reference_monorepo: Path) -> None:
        _ledger_config(reference_monorepo)
        _ticket(reference_monorepo, "[payments, ledger]")

        extra = component_validators(reference_monorepo, "GH-7", "validate")

        assert [(v.tool, v.owner, v.require_reported) for v in extra] == [
            ("ledger/no_float_money", "ledger", True),
            ("payments/migration_safety", "payments", False),
        ]
        assert [(c.name, c.required) for c in extra[1].checks] == [
            ("payments/reversible", True),
            ("payments/no_table_locks", False),
        ]
        assert component_validators(reference_monorepo, "GH-7", "dev") == []

    def test_a_ticket_without_the_components_runs_none(self, reference_monorepo: Path) -> None:
        _ledger_config(reference_monorepo)
        _ticket(reference_monorepo, "[billing-ui]")

        assert component_validators(reference_monorepo, "GH-7", "validate") == []

    def test_gating_by_shape(self, reference_monorepo: Path) -> None:
        _ledger_config(reference_monorepo)
        _validator(reference_monorepo, "validators/repo_check.py", [("ok", True)], passed=True)
        _ticket(reference_monorepo, "[payments, ledger]")
        config = _validate_loop()
        config = config.model_copy(
            update={
                "validators": [
                    *config.validators,
                    *component_validators(reference_monorepo, "GH-7", "validate"),
                ]
            }
        )

        results = run_validators(config, reference_monorepo, {}, {})
        effective = expand_reported_checks(config, results)
        decision = evaluate_validator_results(effective, results)

        reported = {c["name"] for r in results for c in r["checks"]}
        assert reported == {
            "ok",
            "ledger/no_floats",
            "ledger/int64_amounts",
            "payments/reversible",
            "payments/no_table_locks",
        }
        # The bare name requires every check it reports; the loop-YAML shape
        # leaves no_table_locks optional.
        assert decision["message"].startswith("Required checks failed: ledger/int64_amounts.")

    @pytest.mark.parametrize("passed", [True, False])
    def test_a_bare_validator_reporting_no_checks_is_judged_by_its_pass(
        self, reference_monorepo: Path, passed: bool
    ) -> None:
        _ledger_config(reference_monorepo)
        _validator(
            reference_monorepo,
            ".dmx/components/ledger/validators/no_float_money.py",
            [],
            passed=passed,
        )
        _ticket(reference_monorepo, "[ledger]")
        config = LoopConfig(
            name="validate",
            skills=["validate"],
            validators=component_validators(reference_monorepo, "GH-7", "validate"),
        )

        results = run_validators(config, reference_monorepo, {}, {})
        decision = evaluate_validator_results(expand_reported_checks(config, results), results)

        if passed:
            assert decision["message"] == "All checks passed."
        else:
            assert decision["message"].startswith("Required checks failed: ledger/no_float_money.")

    @pytest.mark.parametrize(
        "entry",
        ["'../escape'", "{tool: x, timeout_seconds: 5}", "{checks: [{name: a}]}"],
        ids=["bad-name", "bad-shape", "no-tool"],
    )
    def test_a_broken_entry_fails_instead_of_vanishing(
        self, reference_monorepo: Path, entry: str
    ) -> None:
        _write(
            reference_monorepo,
            ".dmx/components/ledger/component.yaml",
            f"description: L\npaths: ['services/ledger/**']\nvalidators:\n  validate: [{entry}]\n",
        )
        _ticket(reference_monorepo, "[ledger]")
        config = LoopConfig(
            name="validate",
            skills=["validate"],
            validators=component_validators(reference_monorepo, "GH-7", "validate"),
        )

        results = run_validators(config, reference_monorepo, {}, {})
        decision = evaluate_validator_results(expand_reported_checks(config, results), results)

        assert decision["message"].startswith(
            "Required checks failed: ledger/invalid-validator-entry-1."
        )

    def test_a_component_can_add_a_repo_wide_validator(self, reference_monorepo: Path) -> None:
        _validator(reference_monorepo, "validators/shared_check.py", [("fine", True)], passed=True)
        _write(
            reference_monorepo,
            ".dmx/components/ledger/component.yaml",
            "description: L\npaths: ['services/ledger/**']\n"
            "validators:\n  validate: [shared_check]\n",
        )
        _ticket(reference_monorepo, "[ledger]")
        config = LoopConfig(
            name="validate",
            skills=["validate"],
            validators=component_validators(reference_monorepo, "GH-7", "validate"),
        )

        [result] = run_validators(config, reference_monorepo, {}, {})

        assert result["tool"] == "ledger/shared_check"
        assert result["checks"][0]["name"] == "ledger/fine"

    @pytest.mark.asyncio
    async def test_the_pause_message_names_the_component(self, reference_monorepo: Path) -> None:
        _ledger_config(reference_monorepo)
        for name in ("run_tests", "spec_adherence"):
            checks = (
                [("tests_pass", True)]
                if name == "run_tests"
                else [("scope_matches_spec", True), ("no_regressions", True)]
            )
            _validator(reference_monorepo, f"validators/{name}.py", checks, passed=True)
        _ticket(reference_monorepo, "[ledger]")

        async with Client(create_app()) as client:
            await _call(client, "run_loop", reference_monorepo, name="validate")
            await _call(client, "loop_advance", reference_monorepo, skill="validate", output="done")
            message = await _call(client, "loop_continue", reference_monorepo)
            for _ in range(50):
                if "Validators are running" not in message:
                    break
                message = await _call(client, "loop_status", reference_monorepo)

        assert "Required checks failed: ledger/int64_amounts" in message
        assert "`ledger/no_float_money`" in message
        assert "`ledger/int64_amounts`:" in message


class TestContext:
    @pytest.mark.asyncio
    async def test_get_context_lists_component_skills_and_validators(
        self, reference_monorepo: Path
    ) -> None:
        _ledger_config(reference_monorepo)
        _ticket(reference_monorepo, "[ledger]")

        async with Client(create_app()) as client:
            context = await _call(client, "get_context", reference_monorepo)

        assert context["skills"][0] == {
            "name": "ledger/reconcile",
            "source": "ledger",
            "path": ".dmx/components/ledger/skills/reconcile.md",
            "description": "Reconcile the ledger",
        }
        assert {
            "name": "ledger/no_float_money",
            "loops": ["validate"],
            "path": ".dmx/components/ledger/validators/no_float_money.py",
        } in context["validators"]
