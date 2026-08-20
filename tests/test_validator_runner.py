"""Tests for dmx.validator_runner — subprocess resolution and policy engine."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from dmx.loop_schema import LoopConfig
from dmx.loop_state import LoopOutcome, LoopStatus
from dmx.validator_runner import (
    ValidatorRunError,
    evaluate_validator_results,
    resolve_validator_path,
    run_validator,
    run_validators,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_validator(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


PASSING_VALIDATOR = """\
    import json, sys
    contract = json.loads(sys.stdin.read())
    result = {
        "pass": True,
        "message": "all good",
        "checks": [{"name": "check_a", "pass": True}],
    }
    print(json.dumps(result))
    sys.exit(0)
"""

FAILING_VALIDATOR = """\
    import json, sys
    result = {
        "pass": False,
        "message": "nope",
        "checks": [{"name": "check_a", "pass": False}, {"name": "check_b", "pass": True}],
    }
    print(json.dumps(result))
    sys.exit(1)
"""

MALFORMED_VALIDATOR = """\
    import sys
    print("not json")
    sys.exit(0)
"""

CRASHING_VALIDATOR = """\
    import sys
    sys.exit(2)
"""

ECHO_CONTRACT_VALIDATOR = """\
    import json, sys
    contract = json.loads(sys.stdin.read())
    result = {
        "pass": True,
        "message": "echo",
        "checks": [{"name": "check_a", "pass": True}],
        "received": contract,
    }
    print(json.dumps(result))
    sys.exit(0)
"""


def _loop_config(**overrides) -> LoopConfig:
    base = {
        "name": "test",
        "skills": ["s"],
        "validators": [
            {
                "tool": "v",
                "checks": [
                    {"name": "check_a", "required": True},
                    {"name": "check_b", "required": False},
                ],
            }
        ],
    }
    base.update(overrides)
    return LoopConfig.model_validate(base)


# ---------------------------------------------------------------------------
# resolve_validator_path
# ---------------------------------------------------------------------------


class TestResolveValidatorPath:
    def test_app_repo_takes_precedence(self, tmp_path: Path) -> None:
        app_validator = tmp_path / "validators" / "check_spec_complete.py"
        _write_validator(app_validator, PASSING_VALIDATOR)
        resolved = resolve_validator_path("check_spec_complete", tmp_path)
        assert resolved == app_validator

    def test_falls_back_to_bundled(self, tmp_path: Path) -> None:
        resolved = resolve_validator_path("check_spec_complete", tmp_path)
        assert resolved.name == "check_spec_complete.py"
        assert "dmx" in str(resolved)

    def test_unknown_validator_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValidatorRunError, match="not found"):
            resolve_validator_path("nonexistent_validator_xyz", tmp_path)


# ---------------------------------------------------------------------------
# run_validator
# ---------------------------------------------------------------------------


class TestRunValidator:
    def test_passing_validator(self, tmp_path: Path) -> None:
        _write_validator(tmp_path / "validators" / "v.py", PASSING_VALIDATOR)
        result = run_validator("v", tmp_path, {}, "goal", {"job_id": "J"})
        assert result["pass"] is True
        assert result["tool"] == "v"
        assert result["checks"] == [{"name": "check_a", "pass": True}]

    def test_failing_validator(self, tmp_path: Path) -> None:
        _write_validator(tmp_path / "validators" / "v.py", FAILING_VALIDATOR)
        result = run_validator("v", tmp_path, {}, "goal", {"job_id": "J"})
        assert result["pass"] is False
        assert result["checks"][0]["pass"] is False

    def test_malformed_output_raises(self, tmp_path: Path) -> None:
        _write_validator(tmp_path / "validators" / "v.py", MALFORMED_VALIDATOR)
        with pytest.raises(ValidatorRunError, match="valid JSON"):
            run_validator("v", tmp_path, {}, "goal", {"job_id": "J"})

    def test_unexpected_exit_code_raises(self, tmp_path: Path) -> None:
        _write_validator(tmp_path / "validators" / "v.py", CRASHING_VALIDATOR)
        with pytest.raises(ValidatorRunError, match="unexpected code"):
            run_validator("v", tmp_path, {}, "goal", {"job_id": "J"})

    def test_missing_validator_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValidatorRunError):
            run_validator("does_not_exist", tmp_path, {}, "goal", {"job_id": "J"})

    def test_contract_passed_via_stdin(self, tmp_path: Path) -> None:
        _write_validator(tmp_path / "validators" / "v.py", ECHO_CONTRACT_VALIDATOR)
        result = run_validator(
            "v",
            tmp_path,
            {"skill-a": "output-a"},
            "my goal",
            {"job_id": "J", "task_id": "T"},
        )
        received = result["received"]
        assert received["skill_outputs"] == {"skill-a": "output-a"}
        assert received["goal_state"] == "my goal"
        assert received["loop_context"]["job_id"] == "J"
        assert received["loop_context"]["workspace_root"] == str(tmp_path)


# ---------------------------------------------------------------------------
# run_validators
# ---------------------------------------------------------------------------


class TestRunValidators:
    def test_runs_all_declared_validators(self, tmp_path: Path) -> None:
        _write_validator(tmp_path / "validators" / "run_tests.py", PASSING_VALIDATOR)
        _write_validator(tmp_path / "validators" / "spec_adherence.py", FAILING_VALIDATOR)
        config = LoopConfig.model_validate(
            {
                "name": "dev",
                "skills": ["s"],
                "validators": [
                    {"tool": "run_tests", "checks": [{"name": "check_a", "required": True}]},
                    {"tool": "spec_adherence", "checks": [{"name": "check_a", "required": True}]},
                ],
            }
        )
        results = run_validators(config, tmp_path, {}, {"job_id": "J"})
        assert len(results) == 2
        assert results[0]["tool"] == "run_tests"
        assert results[1]["tool"] == "spec_adherence"

    def test_missing_validator_produces_synthetic_failure(self, tmp_path: Path) -> None:
        config = _loop_config()
        results = run_validators(config, tmp_path, {}, {"job_id": "J"})
        assert len(results) == 1
        assert results[0]["pass"] is False
        assert results[0]["tool"] == "v"
        names = {c["name"] for c in results[0]["checks"]}
        assert names == {"check_a", "check_b"}


# ---------------------------------------------------------------------------
# evaluate_validator_results — policy engine
# ---------------------------------------------------------------------------


class TestEvaluateValidatorResults:
    def test_all_pass_is_success(self) -> None:
        config = _loop_config()
        results = [
            {
                "tool": "v",
                "pass": True,
                "checks": [
                    {"name": "check_a", "pass": True},
                    {"name": "check_b", "pass": True},
                ],
            }
        ]
        decision = evaluate_validator_results(config, results)
        assert decision["outcome"] == LoopOutcome.success.value
        assert decision["next_status"] == LoopStatus.complete.value

    def test_required_failure_with_pause_policy(self) -> None:
        config = _loop_config(failure_handling="pause")
        results = [
            {
                "tool": "v",
                "pass": False,
                "checks": [
                    {"name": "check_a", "pass": False},
                    {"name": "check_b", "pass": True},
                ],
            }
        ]
        decision = evaluate_validator_results(config, results)
        assert decision["outcome"] == LoopOutcome.failure.value
        assert decision["next_status"] == LoopStatus.paused.value
        assert "check_a" in decision["message"]

    def test_required_failure_with_fail_policy(self) -> None:
        config = _loop_config(failure_handling="fail")
        results = [
            {
                "tool": "v",
                "pass": False,
                "checks": [
                    {"name": "check_a", "pass": False},
                    {"name": "check_b", "pass": True},
                ],
            }
        ]
        decision = evaluate_validator_results(config, results)
        assert decision["outcome"] == LoopOutcome.failure.value
        assert decision["next_status"] == LoopStatus.failed.value

    def test_optional_failure_with_warn_policy(self) -> None:
        config = _loop_config(on_optional_failure="warn")
        results = [
            {
                "tool": "v",
                "pass": False,
                "checks": [
                    {"name": "check_a", "pass": True},
                    {"name": "check_b", "pass": False},
                ],
            }
        ]
        decision = evaluate_validator_results(config, results)
        assert decision["outcome"] == LoopOutcome.warning.value
        assert decision["next_status"] == LoopStatus.complete.value

    def test_optional_failure_with_ignore_policy(self) -> None:
        config = _loop_config(on_optional_failure="ignore")
        results = [
            {
                "tool": "v",
                "pass": False,
                "checks": [
                    {"name": "check_a", "pass": True},
                    {"name": "check_b", "pass": False},
                ],
            }
        ]
        decision = evaluate_validator_results(config, results)
        assert decision["outcome"] == LoopOutcome.success.value
        assert decision["next_status"] == LoopStatus.complete.value

    def test_missing_check_in_output_treated_as_failed(self) -> None:
        config = _loop_config()
        # Validator ran but didn't report check_a at all.
        results = [{"tool": "v", "pass": True, "checks": [{"name": "check_b", "pass": True}]}]
        decision = evaluate_validator_results(config, results)
        assert decision["outcome"] == LoopOutcome.failure.value

    def test_required_failure_takes_precedence_over_optional(self) -> None:
        config = _loop_config(failure_handling="fail", on_optional_failure="warn")
        results = [
            {
                "tool": "v",
                "pass": False,
                "checks": [
                    {"name": "check_a", "pass": False},
                    {"name": "check_b", "pass": False},
                ],
            }
        ]
        decision = evaluate_validator_results(config, results)
        assert decision["outcome"] == LoopOutcome.failure.value
        assert decision["next_status"] == LoopStatus.failed.value

    def test_no_validators_declared_is_success(self) -> None:
        config = LoopConfig.model_validate({"name": "x", "skills": ["s"]})
        decision = evaluate_validator_results(config, [])
        assert decision["outcome"] == LoopOutcome.success.value
        assert decision["next_status"] == LoopStatus.complete.value
