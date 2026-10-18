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

For rework (GH-115), a failed ``tests_pass`` lists ``findings``: each failed
run and the lines that name a failing test. When nothing failed because of
the code (no test command, a tool not on PATH, a component with no ``test``,
a timeout), it is marked ``reworkable: false`` instead.

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
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Bundled validators run under dmx's interpreter and may import dmx. See #12.
from dmx.component_tests import KNOWN_FAILURES_KEY, TestRun, plan_tests
from dmx.components import COMPONENT_FILE, COMPONENTS_DIR, has_components
from dmx.group_timeout import run_with_group_timeout
from dmx.ticket_routing import read_config_list
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
        "checks": [{"name": "tests_pass", "pass": False, "reworkable": False}],
    }


# Lines that name a failing test: pytest, jest/vitest and go test (GH-115).
_FAILING_TEST_RE = re.compile(r"^(?:FAILED\b|ERROR\b|FAIL\b|--- FAIL:)")


def _failing_tests(output: str) -> list[str]:
    """The lines of *output* that name a failing test."""
    return [line.strip() for line in output.splitlines() if _FAILING_TEST_RE.match(line.strip())]


_KNOWN_FAILURES_HINT = (
    "A test that fails on the base branch too, or only on some machines, can be listed under "
    "`known_failures` in the component's `test` entry (or `test_known_failures` in "
    "`.dmx/config.md`). Only list failures this change didn't cause; `dmx check` shows them."
)

# A run that passed, or failed only on listed known failures (GH-118).
_PASSING = frozenset({"passed", "known failures"})

# pytest exits 5 when it collected nothing (GH-117).
_NO_TESTS_EXIT = 5


def _set_test(component_id: str) -> str:
    """How to give *component_id* a test command."""
    path = f"{COMPONENTS_DIR}/{component_id}/{COMPONENT_FILE}"
    return "set `test: {cmd: ..., cwd: ...}` in " + path


def _nothing_collected(proc: subprocess.CompletedProcess[str]) -> bool:
    return proc.returncode == _NO_TESTS_EXIT and "no tests ran" in (proc.stdout + proc.stderr)


def _judge(
    proc: subprocess.CompletedProcess[str], known: tuple[str, ...]
) -> tuple[str, list[str], list[str], list[str]]:
    """``(status, unlisted failing lines, known ids that failed, known ids that didn't)``.

    A failed run whose failing-test lines all name a known failure passes as
    ``known failures`` (GH-118). One with no recognisable lines still fails.
    """
    failing = _failing_tests(proc.stdout + proc.stderr) if proc.returncode != 0 else []
    unlisted = [line for line in failing if not any(k in line for k in known)]
    seen = [k for k in known if any(k in line for line in failing)]
    unseen = [k for k in known if k not in seen]
    if proc.returncode == 0:
        return "passed", [], [], unseen
    if failing and not unlisted:
        return "known failures", [], seen, unseen
    return "failed", unlisted, seen, unseen


def _known_notes(seen: list[str], unseen: list[str]) -> list[str]:
    notes: list[str] = []
    if seen:
        notes.append(f"Known failures: {', '.join(seen)}")
    if unseen:
        notes.append(
            f"Listed known failures that didn't fail: {', '.join(unseen)} "
            "(remove them from known_failures if they pass now)"
        )
    return notes


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
    where = f" in {test.cwd}" if test.cwd != "." else ""
    if _nothing_collected(proc):
        return {
            **record,
            "status": "no tests",
            "exit": proc.returncode,
            "line": f"{test.command}{where} collected no tests (exit 5)",
        }
    status, failing, seen, unseen = _judge(proc, test.known_failures)
    only = ", known failures only" if status == "known failures" else ""
    return {
        **record,
        "status": status,
        "exit": proc.returncode,
        "line": f"{test.command}{where} (exit {proc.returncode}{only})",
        "output": "" if status != "failed" else _tail(proc),
        "failing": failing,
        "known": seen,
        "unseen": unseen if status != "failed" else [],
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
    notes: list[str] = []
    for record in runs:
        notes.extend(_known_notes(record.get("known") or [], record.get("unseen") or []))
    for record in runs:
        mark = "✓" if record["status"] in _PASSING else "✗"
        label = ", ".join(record["components"])
        lines.append(f"{mark} {label} — {record['line']}")
        if record["status"] == "no tests":
            fixes = "; ".join(_set_test(cid) for cid in record["components"])
            failures.append(f"{label} — {record['line']}: {fixes}")
        elif record["status"] not in _PASSING:
            detail = f"{label} — {record['line']}"
            output = record.get("output")
            failures.append(f"{detail}:\n{output}" if output else detail)
    for component_id in plan.unconfigured:
        note = f"not configured: {_set_test(component_id)}"
        lines.append(f"✗ {component_id} — {note}")
        failures.append(f"{component_id} — {note}")

    findings: list[str] = []
    for record in runs:
        if record["status"] == "failed":
            findings.append(f"{', '.join(record['components'])} — {record['line']}")
            findings.extend(record.get("failing") or [])
    # A missing toolchain, an unconfigured component or a timeout isn't a
    # code task, so rework leaves the whole check alone (GH-115).
    reworkable = not plan.unconfigured and all(
        record["status"] in {*_PASSING, "failed"} for record in runs
    )

    failed = sum(1 for record in runs if record["status"] not in _PASSING) + len(plan.unconfigured)
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
    if any(record.get("failing") for record in runs):
        lines.append(_KNOWN_FAILURES_HINT)
    check: dict[str, Any] = {"name": "tests_pass", "pass": passed, "message": "\n\n".join(failures)}
    if not passed:
        if reworkable and findings:
            check["findings"] = findings
        else:
            check["reworkable"] = False
    return {
        "pass": passed,
        "message": "\n".join([header, *lines, *notes]),
        "checks": [check],
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
            "checks": [{"name": "tests_pass", "pass": False, "reworkable": False}],
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
            "checks": [{"name": "tests_pass", "pass": False, "reworkable": False}],
        }
    except subprocess.TimeoutExpired:
        return _timeout_result(cmd_str, inner, timeout_seconds, loop_name)

    if _nothing_collected(proc):
        return {
            "pass": False,
            "message": f"Tests failed — `{cmd_str}` collected no tests (exit 5)",
            "checks": [{"name": "tests_pass", "pass": False, "reworkable": False}],
        }
    known = tuple(read_config_list(workspace_root, KNOWN_FAILURES_KEY) or ())
    status, failing, seen, unseen = _judge(proc, known)
    passed = status in _PASSING
    only = ", known failures only" if status == "known failures" else ""
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-20:])
    notes = _known_notes(seen, unseen if passed else [])

    result: dict[str, Any] = {
        "pass": passed,
        "message": "\n".join(
            [
                f"{'Tests passed' if passed else 'Tests failed'} — ran `{cmd_str}` "
                f"(exit {proc.returncode}{only})",
                *notes,
            ]
        ),
        "checks": [{"name": "tests_pass", "pass": passed, "message": tail}],
    }
    if not passed:
        result["checks"][0]["findings"] = [f"`{cmd_str}` (exit {proc.returncode})", *failing]
    return result


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
