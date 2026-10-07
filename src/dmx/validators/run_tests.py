"""Bundled validator: run_tests.

Detects the project's test command from common markers (``pyproject.toml``
+ ``uv.lock``, ``package.json`` test script, ``Makefile`` test target) and
runs it. App repos with a non-standard test setup should override this by
placing their own ``validators/run_tests.py`` at the repo root — detection
here is intentionally conservative rather than guessing.

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
``coverage_threshold`` check. Loops that declare ``coverage_threshold`` as
an optional check will treat the missing result as a soft failure — apply
the loop's ``on_optional_failure`` policy (typically ``warn``). Override
this validator if coverage measurement is required.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# Bundled validators run under dmx's interpreter and may import dmx. See #12.
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


def run(
    workspace_root: Path,
    timeout_seconds: int | None = None,
    loop_name: str | None = None,
) -> dict[str, Any]:
    if isinstance(timeout_seconds, bool):
        timeout_seconds = None
    cmd = _detect_test_command(workspace_root)
    if cmd is None:
        return {
            "pass": False,
            "message": (
                "No recognized test command found "
                "(pyproject.toml, package.json test script, or Makefile test target)"
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
