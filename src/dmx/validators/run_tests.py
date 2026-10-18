"""Bundled validator: run_tests.

Detects the project's test command from common markers (``pyproject.toml``
+ ``uv.lock``, ``package.json`` test script, ``Makefile`` test target,
``go.mod``) and runs it. App repos with a non-standard test setup should
override this by placing their own ``validators/run_tests.py`` at the repo
root — detection here is intentionally conservative rather than guessing.

In a repo with ``.dmx/components/`` (GH-89), it runs each touched
component's tests and those of the components that depend on them instead,
as :func:`dmx.component_tests.plan_tests` decides, one after another within
the same time budget, and reports one line per run. The ``tests_pass``
check fails when any run fails or a touched component has no test command.

Contract
--------
Called by the orchestrator via subprocess. The input contract is written to
stdin as JSON::

    python validators/run_tests.py < contract.json

    {
      "skill_outputs": {...},
      "goal_state": "...",
      "loop_context": {
        ...,
        "workspace_root": "/path/to/repo",
        "timeout_seconds": 630,
        "loop_name": "validate"
      }
    }

Exits 0 on pass, 1 on failure.
Writes JSON to stdout::

    {
      "pass": true,
      "message": "Tests passed — ran `uv run pytest -q`",
      "checks": [{"name": "tests_pass", "pass": true}]
    }

Note: this validator does not measure coverage, so it never reports a
``coverage_threshold`` check. A team that measures coverage should override
this validator and declare that check in its own loop YAML.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Bundled validators run under dmx's interpreter and may import dmx. See #12.
from dmx.component_tests import TestRun, plan_tests
from dmx.components import COMPONENT_FILE, COMPONENTS_DIR, has_components
from dmx.group_timeout import run_with_group_timeout
from dmx.validator_runner import _where_to_set_timeout

TEST_TIMEOUT_SECONDS = 600
# Leave the runner time to receive this validator's own timeout result.
TIMEOUT_MARGIN_SECONDS = 30


def _detect_test_command(workspace_root: Path) -> list[str] | None:
    if (workspace_root / "pyproject.toml").exists():
        if (workspace_root / "uv.lock").exists():
            return ["uv", "run", "pytest", "-q"]
        return ["pytest", "-q"]

    package_json = workspace_root / "package.json"
    if package_json.exists():
        try:
            pkg = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pkg = {}
        if "test" in pkg.get("scripts", {}):
            return ["npm", "test", "--silent"]

    makefile = workspace_root / "Makefile"
    if makefile.exists() and "test:" in makefile.read_text(encoding="utf-8"):
        return ["make", "test"]

    # Checked last, so a repo with one of the markers above keeps its command.
    if (workspace_root / "go.mod").exists():
        return ["go", "test", "./..."]

    return None


def _inner_timeout(timeout_seconds: int | None) -> int:
    """Seconds for the test command.

    When the runner passed ``timeout_seconds``, stay strictly under it so
    this timeout is the one that gets reported. With no contract limit,
    keep :data:`TEST_TIMEOUT_SECONDS`.
    """
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds < 1
    ):
        return TEST_TIMEOUT_SECONDS
    if timeout_seconds > TIMEOUT_MARGIN_SECONDS:
        return timeout_seconds - TIMEOUT_MARGIN_SECONDS
    return max(1, timeout_seconds - 1)


def _timeout_result(
    cmd_str: str,
    inner: int,
    timeout_seconds: int | None,
    loop_name: str | None,
) -> dict[str, Any]:
    where = _where_to_set_timeout(loop_name)
    if timeout_seconds is None:
        detail = f"Test command `{cmd_str}` timed out after {inner}s."
    else:
        detail = (
            f"Test command `{cmd_str}` timed out after {inner}s, "
            f"under `timeout_seconds` ({timeout_seconds})."
        )
    return {
        "pass": False,
        "message": f"{detail} {where}",
        "checks": [{"name": "tests_pass", "pass": False}],
    }


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    return "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-20:])


def _run_one(root: Path, test: TestRun, remaining: float) -> dict[str, Any]:
    """Run *test* within *remaining* seconds. ``status`` is passed, failed, missing or timeout."""
    record: dict[str, Any] = {
        "components": list(test.components),
        "command": test.command,
        "cwd": test.cwd,
    }
    try:
        proc = run_with_group_timeout(
            list(test.argv), timeout=max(1, int(remaining)), cwd=str(root / test.cwd)
        )
    except FileNotFoundError:
        line = f"`{test.argv[0]}` not found on PATH"
        if test.argv[0] == "sh" and sys.platform == "win32":
            line += " (`test` and `test_command` run through `sh`: install Git for Windows)"
        return {**record, "status": "missing", "line": line}
    except subprocess.TimeoutExpired:
        limit = max(1, int(remaining))
        return {**record, "status": "timeout", "line": f"{test.command} timed out after {limit}s"}
    status = "passed" if proc.returncode == 0 else "failed"
    where = f" in {test.cwd}" if test.cwd != "." else ""
    return {
        **record,
        "status": status,
        "exit": proc.returncode,
        "line": f"{test.command}{where} (exit {proc.returncode})",
        "output": "" if status == "passed" else _tail(proc),
    }


def run_components(
    workspace_root: Path,
    timeout_seconds: int | None = None,
    loop_name: str | None = None,
) -> dict[str, Any]:
    """Run each touched component's tests and its dependents' (GH-89)."""
    plan = plan_tests(workspace_root)
    deadline = time.monotonic() + _inner_timeout(timeout_seconds)
    runs: list[dict[str, Any]] = []
    for test in plan.runs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            runs.append(
                {
                    "components": list(test.components),
                    "command": test.command,
                    "cwd": test.cwd,
                    "status": "not run",
                    "line": f"{test.command} not run: the time budget was used up",
                }
            )
            continue
        runs.append(_run_one(workspace_root, test, remaining))

    lines: list[str] = []
    failures: list[str] = []
    for record in runs:
        mark = "✓" if record["status"] == "passed" else "✗"
        label = ", ".join(record["components"])
        lines.append(f"{mark} {label} — {record['line']}")
        if record["status"] != "passed":
            detail = f"{label} — {record['line']}"
            output = record.get("output")
            failures.append(f"{detail}:\n{output}" if output else detail)
    for component_id in plan.unconfigured:
        note = f"not configured: set `test` in {COMPONENTS_DIR}/{component_id}/{COMPONENT_FILE}"
        lines.append(f"✗ {component_id} — {note}")
        failures.append(f"{component_id} — {note}")

    failed = sum(1 for record in runs if record["status"] != "passed") + len(plan.unconfigured)
    total = len(runs) + len(plan.unconfigured)
    passed = failed == 0 and total > 0
    if passed:
        header = f"Tests passed — {total} run{'' if total == 1 else 's'}"
    elif total == 0:
        header = "Tests failed — nothing to run"
    else:
        header = f"Tests failed — {failed} of {total} run{'' if total == 1 else 's'} failed"
    if any(record["status"] == "timeout" for record in runs):
        lines.append(_where_to_set_timeout(loop_name))
    return {
        "pass": passed,
        "message": "\n".join([header, *lines]),
        "checks": [{"name": "tests_pass", "pass": passed, "message": "\n\n".join(failures)}],
        "runs": runs,
    }


def run(
    workspace_root: Path,
    timeout_seconds: int | None = None,
    loop_name: str | None = None,
) -> dict[str, Any]:
    if isinstance(timeout_seconds, bool):
        timeout_seconds = None
    if has_components(workspace_root):
        return run_components(workspace_root, timeout_seconds, loop_name)
    cmd = _detect_test_command(workspace_root)
    if cmd is None:
        return {
            "pass": False,
            "message": (
                "No recognized test command found "
                "(pyproject.toml, package.json test script, Makefile test target, or go.mod)"
            ),
            "checks": [{"name": "tests_pass", "pass": False}],
        }

    cmd_str = " ".join(cmd)
    inner = _inner_timeout(timeout_seconds)
    try:
        proc = run_with_group_timeout(
            cmd,
            timeout=inner,
            cwd=str(workspace_root),
        )
    except FileNotFoundError:
        return {
            "pass": False,
            "message": f"Test command `{cmd_str}` not found on PATH",
            "checks": [{"name": "tests_pass", "pass": False}],
        }
    except subprocess.TimeoutExpired:
        return _timeout_result(cmd_str, inner, timeout_seconds, loop_name)

    passed = proc.returncode == 0
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-20:])

    return {
        "pass": passed,
        "message": (
            f"{'Tests passed' if passed else 'Tests failed'} — ran `{cmd_str}` "
            f"(exit {proc.returncode})"
        ),
        "checks": [{"name": "tests_pass", "pass": passed, "message": tail}],
    }


if __name__ == "__main__":
    contract = json.loads(sys.stdin.read() or "{}")
    loop_context = contract.get("loop_context") or {}
    workspace_root = loop_context.get("workspace_root") or "."
    timeout_seconds = loop_context.get("timeout_seconds")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        timeout_seconds = None
    loop_name = loop_context.get("loop_name")
    if not isinstance(loop_name, str) or not loop_name:
        loop_name = None
    result = run(Path(workspace_root), timeout_seconds=timeout_seconds, loop_name=loop_name)
    print(json.dumps(result))
    sys.exit(0 if result["pass"] else 1)
