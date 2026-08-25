"""Bundled validator: spec_adherence.

Grades the structured report the ``validate`` skill writes after reviewing
the real diff against ``.dmx/spec.md`` — it does **not** grade free-text
``skill_outputs`` narration. That was the original design (and its bug):
scoring keyword overlap against the agent's self-report is both gameable
(reword the sentence, no code change, check passes) and prone to false
failures (accurate language about a fixed regression trips a "no
regressions" keyword filter just as hard as an introduced one).

Contract
--------
Called by the orchestrator via subprocess. The input contract is written to
stdin as JSON::

    python validators/spec_adherence.py < contract.json

    {
      "skill_outputs": {...},
      "goal_state": "...",
      "loop_context": {
        "job_id": "...", "task_id": "...", "loop_name": "...",
        "branch": "...", "ticket_ref": "...", "workspace_root": "/path/to/repo"
      }
    }

Reads the structured artifact at
``{workspace_root}/.dmx/jobs/{job_id}/validation-report.json``, written by
the ``validate`` skill (see ``dmx-validate.md`` Step 9). Expected shape::

    {
      "commit": "<git rev-parse HEAD at the time the report was written>",
      "scope_items": [
        {"item": str, "verdict": "covered" | "partial" | "missing", "evidence": str}
      ],
      "scope_creep": [{"description": str, "evidence": str}],
      "regressions": [{"description": str, "evidence": str}],
      "edge_cases": [{"description": str, "addressed": bool, "evidence": str}]
    }

Grading policy — ambiguous verdicts warn, only clear-cut evidence blocks:
- ``scope_matches_spec`` fails only if a scope item is ``missing`` (no
  evidence at all) or ``scope_creep`` is non-empty. ``partial`` items pass
  (ambiguous, not a hard block) but are surfaced in the message.
- ``no_regressions`` fails only if ``regressions`` is non-empty.
- ``edge_cases_addressed`` fails only if an edge case is explicitly flagged
  ``addressed: false``.

If the report is missing, malformed, or stale (its recorded ``commit``
doesn't match the current ``HEAD`` — meaning the diff changed since the
report was generated), every check fails with a message telling the agent
to re-run the ``validate`` skill. Silently passing on missing/stale data
would recreate the original bug in a new location.

Exits 0 on pass, 1 on failure. Writes JSON to stdout::

    {
      "pass": true,
      "message": "Spec adherence check passed",
      "checks": [
        {"name": "scope_matches_spec",   "pass": true},
        {"name": "edge_cases_addressed", "pass": true},
        {"name": "no_regressions",       "pass": true}
      ]
    }
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

GIT_TIMEOUT_SECONDS = 15

_RERUN_HINT = "Re-run the `validate` skill to regenerate the report, then retry."


def _report_path(workspace_root: Path, job_id: str) -> Path:
    return workspace_root / ".dmx" / "jobs" / job_id / "validation-report.json"


def _current_head(workspace_root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _load_report(workspace_root: Path, job_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """Load and validate the report artifact.

    Returns ``(report, None)`` on success, or ``(None, error_message)`` if
    the report is missing, malformed, or stale relative to the current
    commit.
    """
    path = _report_path(workspace_root, job_id)
    if not path.exists():
        return None, f"No validation report found at {path}. {_RERUN_HINT}"

    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"Validation report at {path} is not valid JSON ({exc}). {_RERUN_HINT}"

    if not isinstance(report, dict):
        return None, f"Validation report at {path} is not a JSON object. {_RERUN_HINT}"

    current_head = _current_head(workspace_root)
    report_commit = report.get("commit")
    if current_head and report_commit and report_commit != current_head:
        return None, (
            f"Validation report is stale — generated for commit {report_commit!r}, "
            f"current HEAD is {current_head!r}. {_RERUN_HINT}"
        )

    return report, None


def _scope_matches_spec(report: dict[str, Any]) -> tuple[bool, str]:
    scope_items = report.get("scope_items") or []
    scope_creep = report.get("scope_creep") or []

    missing = [i for i in scope_items if i.get("verdict") == "missing"]
    partial = [i for i in scope_items if i.get("verdict") == "partial"]

    if not scope_items and not scope_creep:
        return False, "Report contains no scope_items — cannot verify scope adherence."

    if missing:
        names = ", ".join(i.get("item", "?") for i in missing)
        return False, f"Scope item(s) with no evidence in the diff: {names}"

    if scope_creep:
        names = ", ".join(c.get("description", "?") for c in scope_creep)
        return False, f"Out-of-scope changes detected: {names}"

    covered = len(scope_items) - len(partial)
    if partial:
        names = ", ".join(i.get("item", "?") for i in partial)
        return True, f"{covered}/{len(scope_items)} scope items fully covered; ambiguous: {names}"
    return True, f"All {len(scope_items)} scope item(s) covered in the diff"


def _edge_cases_addressed(report: dict[str, Any]) -> tuple[bool, str]:
    edge_cases = report.get("edge_cases") or []
    if not edge_cases:
        return True, "No edge cases flagged for review"

    unaddressed = [e for e in edge_cases if e.get("addressed") is False]
    if unaddressed:
        names = ", ".join(e.get("description", "?") for e in unaddressed)
        return False, f"Unaddressed edge case(s): {names}"
    return True, f"All {len(edge_cases)} flagged edge case(s) addressed"


def _no_regressions(report: dict[str, Any]) -> tuple[bool, str]:
    regressions = report.get("regressions") or []
    if regressions:
        names = ", ".join(r.get("description", "?") for r in regressions)
        return False, f"Regression(s) found in the diff: {names}"
    return True, "No regressions found in the diff"


def run(workspace_root: Path, job_id: str) -> dict[str, Any]:
    report, error = _load_report(workspace_root, job_id)

    check_names = ["scope_matches_spec", "edge_cases_addressed", "no_regressions"]

    if report is None:
        checks = [{"name": name, "pass": False, "message": error} for name in check_names]
        return {"pass": False, "message": error, "checks": checks}

    checks_raw = [
        ("scope_matches_spec", _scope_matches_spec(report)),
        ("edge_cases_addressed", _edge_cases_addressed(report)),
        ("no_regressions", _no_regressions(report)),
    ]

    checks = [{"name": name, "pass": passed, "message": msg} for name, (passed, msg) in checks_raw]

    overall = all(c["pass"] for c in checks)
    summary_msg = (
        "Spec adherence check passed"
        if overall
        else "Spec adherence check failed — see checks for details"
    )

    return {
        "pass": overall,
        "message": summary_msg,
        "checks": checks,
    }


if __name__ == "__main__":
    contract = json.loads(sys.stdin.read() or "{}")
    loop_context = contract.get("loop_context", {})
    workspace_root = loop_context.get("workspace_root") or "."
    job_id = loop_context.get("job_id") or "unknown"
    result = run(Path(workspace_root), job_id)
    print(json.dumps(result))
    sys.exit(0 if result["pass"] else 1)
