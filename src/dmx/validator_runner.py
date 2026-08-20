"""Validator subprocess runner and policy engine.

Resolution
----------
Validators are plain Python scripts at ``validators/{name}.py``. The app
repo takes precedence; the bundled ``src/dmx/validators/`` directory is the
fallback. Resolution is always deterministic — no registry, no guessing.

Invocation
----------
Each validator is invoked as a subprocess::

    python validators/{name}.py

The input contract is written to stdin as JSON::

    {
        "skill_outputs": {...},   # keyed by skill name
        "goal_state": "...",
        "loop_context": {
            "job_id": "...", "task_id": "...", "loop_name": "...",
            "branch": "...", "ticket_ref": "...", "workspace_root": "..."
        }
    }

The validator prints its result to stdout as JSON and exits 0 (pass) or 1
(fail)::

    {"pass": bool, "message": str, "checks": [{"name": str, "pass": bool}]}

The orchestrator does not care what happens inside — deterministic tool
calls, LLM calls, or both. Non-determinism lives inside the validator.

Policy engine
-------------
Required vs optional is a loop config concern, not a validator concern.
``evaluate_validator_results`` maps each declared check (from the loop
config) to its reported pass/fail and applies ``failure_handling`` /
``on_optional_failure`` to decide the next state.
"""

from __future__ import annotations

import importlib.resources as pkg
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from dmx.loop_schema import FailureHandling, LoopConfig, OnOptionalFailure
from dmx.loop_state import LoopOutcome, LoopStatus

__all__ = [
    "ValidatorRunError",
    "resolve_validator_path",
    "run_validator",
    "run_validators",
    "evaluate_validator_results",
]

logger = logging.getLogger(__name__)

VALIDATOR_TIMEOUT_SECONDS = 120


class ValidatorRunError(Exception):
    """Raised when a validator cannot be resolved or run correctly."""


def _bundled_validators_dir() -> Path:
    return Path(str(pkg.files("dmx") / "validators"))


def resolve_validator_path(name: str, workspace_root: Path) -> Path:
    """Resolve a validator name to a script path.

    App repo ``validators/{name}.py`` takes precedence over the bundled
    fallback shipped with dmx.

    Raises:
        ValidatorRunError: If the validator is not found in either location.
    """
    app_path = workspace_root / "validators" / f"{name}.py"
    if app_path.exists():
        return app_path

    bundled_path = _bundled_validators_dir() / f"{name}.py"
    if bundled_path.exists():
        return bundled_path

    raise ValidatorRunError(f"Validator '{name}' not found at {app_path} or {bundled_path}")


def run_validator(
    name: str,
    workspace_root: Path,
    skill_outputs: dict[str, str],
    goal_state: str,
    loop_context: dict[str, Any],
) -> dict[str, Any]:
    """Resolve and run a single validator via subprocess.

    Returns:
        The validator's parsed output: ``{"pass", "message", "checks"}``,
        with ``tool`` added to identify which validator produced it.

    Raises:
        ValidatorRunError: If resolution, execution, or parsing fails.
    """
    path = resolve_validator_path(name, workspace_root)

    contract = {
        "skill_outputs": skill_outputs,
        "goal_state": goal_state,
        "loop_context": {**loop_context, "workspace_root": str(workspace_root)},
    }

    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            input=json.dumps(contract),
            capture_output=True,
            text=True,
            timeout=VALIDATOR_TIMEOUT_SECONDS,
            cwd=workspace_root,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidatorRunError(
            f"Validator '{name}' timed out after {VALIDATOR_TIMEOUT_SECONDS}s"
        ) from exc

    if proc.returncode not in (0, 1):
        raise ValidatorRunError(
            f"Validator '{name}' exited with unexpected code {proc.returncode}: "
            f"{proc.stderr.strip()}"
        )

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValidatorRunError(
            f"Validator '{name}' did not return valid JSON on stdout: {proc.stdout[:500]!r}"
        ) from exc

    if not isinstance(result, dict) or "pass" not in result or "checks" not in result:
        raise ValidatorRunError(
            f"Validator '{name}' output missing required 'pass'/'checks' fields: {result!r}"
        )

    result.setdefault("message", "")
    result["tool"] = name
    return result


def run_validators(
    config: LoopConfig,
    workspace_root: Path,
    skill_outputs: dict[str, str],
    loop_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run every validator declared in *config* and return raw results.

    A validator that raises :class:`ValidatorRunError` produces a synthetic
    failing result for each of its declared checks rather than aborting the
    whole run — the policy engine needs a result per declared check to make
    a proceed/pause/fail decision.
    """
    results: list[dict[str, Any]] = []
    for validator_cfg in config.validators:
        try:
            result = run_validator(
                validator_cfg.tool,
                workspace_root,
                skill_outputs,
                config.goal_state,
                loop_context,
            )
        except ValidatorRunError as exc:
            logger.warning("validator '%s' failed to run: %s", validator_cfg.tool, exc)
            result = {
                "tool": validator_cfg.tool,
                "pass": False,
                "message": f"Validator error: {exc}",
                "checks": [{"name": check.name, "pass": False} for check in validator_cfg.checks],
            }
        results.append(result)
    return results


def evaluate_validator_results(
    config: LoopConfig,
    validator_results: list[dict[str, Any]],
) -> dict[str, str]:
    """Apply loop config policy to validator results.

    Required/optional is a loop config concern — the same validator check
    can be required in one loop and optional in another. This function maps
    each check *declared in the config* to its reported pass/fail and
    applies ``failure_handling`` (required checks) or ``on_optional_failure``
    (optional checks) to decide what happens next.

    Returns:
        ``{"outcome": ..., "next_status": ..., "message": ...}`` where
        outcome is a :class:`LoopOutcome` value and next_status is a
        :class:`LoopStatus` value.
    """
    declared_required: dict[str, bool] = {}
    for validator_cfg in config.validators:
        for check in validator_cfg.checks:
            declared_required[check.name] = check.required

    reported: dict[str, bool] = {}
    for result in validator_results:
        for check in result.get("checks", []):
            reported[check["name"]] = bool(check.get("pass"))

    failed_required = sorted(
        name
        for name, required in declared_required.items()
        if required and not reported.get(name, False)
    )
    failed_optional = sorted(
        name
        for name, required in declared_required.items()
        if not required and not reported.get(name, False)
    )

    if failed_required:
        checks_desc = ", ".join(failed_required)
        if config.failure_handling == FailureHandling.fail:
            return {
                "outcome": LoopOutcome.failure.value,
                "next_status": LoopStatus.failed.value,
                "message": f"Required checks failed: {checks_desc}",
            }
        return {
            "outcome": LoopOutcome.failure.value,
            "next_status": LoopStatus.paused.value,
            "message": (
                f"Required checks failed: {checks_desc}. "
                "Review and address, then call loop_continue to re-run validators."
            ),
        }

    if failed_optional:
        checks_desc = ", ".join(failed_optional)
        if config.on_optional_failure == OnOptionalFailure.warn:
            return {
                "outcome": LoopOutcome.warning.value,
                "next_status": LoopStatus.complete.value,
                "message": f"All required checks passed. Optional checks failed: {checks_desc}",
            }
        return {
            "outcome": LoopOutcome.success.value,
            "next_status": LoopStatus.complete.value,
            "message": f"All required checks passed (optional failures ignored: {checks_desc})",
        }

    return {
        "outcome": LoopOutcome.success.value,
        "next_status": LoopStatus.complete.value,
        "message": "All checks passed.",
    }
