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
      "loop_context": {..., "workspace_root": "/path/to/repo"}
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

TEST_TIMEOUT_SECONDS = 600


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


def run(workspace_root: Path) -> dict[str, Any]:
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
    try:
        proc = subprocess.run(
            cmd,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return {
            "pass": False,
            "message": f"Test command `{cmd_str}` not found on PATH",
            "checks": [{"name": "tests_pass", "pass": False}],
        }
    except subprocess.TimeoutExpired:
        return {
            "pass": False,
            "message": f"Test command `{cmd_str}` timed out after {TEST_TIMEOUT_SECONDS}s",
            "checks": [{"name": "tests_pass", "pass": False}],
        }

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
    workspace_root = contract.get("loop_context", {}).get("workspace_root") or "."
    result = run(Path(workspace_root))
    print(json.dumps(result))
    sys.exit(0 if result["pass"] else 1)
