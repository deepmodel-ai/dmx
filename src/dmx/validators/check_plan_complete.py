"""Bundled validator: check_plan_complete.

Checks that ``.dmx/tasks.md`` exists, defines at least one phase, and that
tasks have concrete descriptions rather than empty checklist items. App
repos can override this by placing their own
``validators/check_plan_complete.py`` at the repo root.

Contract
--------
Called by the orchestrator via subprocess. The input contract is written to
stdin as JSON::

    python validators/check_plan_complete.py < contract.json

    {
      "skill_outputs": {...},
      "goal_state": "...",
      "loop_context": {..., "workspace_root": "/path/to/repo"}
    }

Reads ``.dmx/tasks.md`` relative to ``loop_context.workspace_root``.

Exits 0 on pass, 1 on failure.
Writes JSON to stdout::

    {
      "pass": true,
      "message": "Plan completeness check passed",
      "checks": [
        {"name": "tasks_file_exists",        "pass": true},
        {"name": "phases_defined",           "pass": true},
        {"name": "tasks_have_descriptions",  "pass": true}
      ]
    }
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_PHASE_RE = re.compile(r"^##\s+Phase\s+\d+\s*:.*$", re.MULTILINE)
_TASK_LINE_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*(.*)$", re.MULTILINE)
_MIN_TASK_DESCRIPTION_LENGTH = 8


def _tasks_file_exists(tasks: Path) -> tuple[bool, str]:
    if tasks.exists() and tasks.stat().st_size > 0:
        return True, "tasks.md exists"
    return False, "tasks.md not found or empty"


def _phases_defined(content: str) -> tuple[bool, str]:
    phases = _PHASE_RE.findall(content)
    if not phases:
        return False, "No 'Phase N: {Name}' headings found in tasks.md"
    return True, f"{len(phases)} phase(s) defined"


def _tasks_have_descriptions(content: str) -> tuple[bool, str]:
    task_lines = _TASK_LINE_RE.findall(content)
    if not task_lines:
        return False, "No checklist tasks found in tasks.md"

    vague = [t for t in task_lines if len(t.strip()) < _MIN_TASK_DESCRIPTION_LENGTH]
    if vague:
        return False, f"{len(vague)} task(s) have no meaningful description"
    return True, f"{len(task_lines)} task(s) have concrete descriptions"


def run(workspace_root: Path) -> dict:
    tasks = workspace_root / ".dmx" / "tasks.md"
    content = tasks.read_text(encoding="utf-8") if tasks.exists() else ""

    checks_raw = [
        ("tasks_file_exists", _tasks_file_exists(tasks)),
        ("phases_defined", _phases_defined(content)),
        ("tasks_have_descriptions", _tasks_have_descriptions(content)),
    ]

    checks = [{"name": name, "pass": passed, "message": msg} for name, (passed, msg) in checks_raw]

    overall = all(c["pass"] for c in checks)
    summary_msg = (
        "Plan completeness check passed"
        if overall
        else "Plan completeness check failed — see checks for details"
    )

    return {
        "pass": overall,
        "message": summary_msg,
        "checks": checks,
    }


if __name__ == "__main__":
    contract = json.loads(sys.stdin.read() or "{}")
    workspace_root = contract.get("loop_context", {}).get("workspace_root") or "."
    result = run(Path(workspace_root))
    print(json.dumps(result))
    sys.exit(0 if result["pass"] else 1)
