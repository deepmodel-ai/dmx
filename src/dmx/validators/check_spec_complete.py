"""Bundled validator: check_spec_complete.

Checks that ``.dmx/spec.md`` exists and has meaningful content in the
required sections.  App repos can override this by placing their own
``validators/check_spec_complete.py`` at the repo root.

Contract
--------
Called by the orchestrator via subprocess. The input contract is written to
stdin as JSON::

    python validators/check_spec_complete.py < contract.json

    {
      "skill_outputs": {...},
      "goal_state": "...",
      "loop_context": {..., "workspace_root": "/path/to/repo"}
    }

Reads ``.dmx/spec.md`` relative to ``loop_context.workspace_root``.

Exits 0 on pass, 1 on failure.
Writes JSON to stdout::

    {
      "pass": true,
      "message": "Spec completeness check passed",
      "checks": [
        {"name": "spec_exists",              "pass": true},
        {"name": "qa_answered",              "pass": true},
        {"name": "technical_approach_filled","pass": true},
        {"name": "scope_defined",            "pass": true}
      ]
    }
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------


def _spec_exists(spec: Path) -> tuple[bool, str]:
    if spec.exists() and spec.stat().st_size > 0:
        return True, "spec.md exists"
    return False, "spec.md not found or empty"


def _qa_answered(content: str) -> tuple[bool, str]:
    """Check that Q&A section has non-empty answers.

    Looks for lines starting with 'Q:' followed by lines with 'A:' that
    contain actual content (not just 'A: TBD' or blank).
    """
    qa_pattern = re.compile(r"^A:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
    tbd_pattern = re.compile(r"^A:\s*(tbd|n/a|todo|\?+)\s*$", re.MULTILINE | re.IGNORECASE)

    answers = qa_pattern.findall(content)
    tbd_count = len(tbd_pattern.findall(content))

    if not answers:
        return False, "No Q&A answers found in spec.md"
    if tbd_count == len(answers):
        return False, "All Q&A answers are placeholders (TBD/N/A)"
    return True, f"Q&A section has {len(answers) - tbd_count} answered question(s)"


def _technical_approach_filled(content: str) -> tuple[bool, str]:
    """Check that Technical Approach section has content beyond a heading."""
    # Find the section (handles ## or ### heading levels).
    match = re.search(
        r"^#{1,4}\s+technical approach.*?\n(.*?)(?=^#{1,4}\s|\Z)",
        content,
        re.MULTILINE | re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return False, "Technical Approach section not found"
    body = match.group(1).strip()
    if not body or body.lower() in {"tbd", "n/a", "todo", ""}:
        return False, "Technical Approach section is empty or placeholder"
    if len(body) < 30:
        return False, "Technical Approach section appears too short to be meaningful"
    return True, "Technical Approach section has content"


def _scope_defined(content: str) -> tuple[bool, str]:
    """Check that a scope / in-scope / out-of-scope section is present."""
    match = re.search(
        r"^#{1,4}\s+(scope|in scope|out of scope).*?\n(.*?)(?=^#{1,4}\s|\Z)",
        content,
        re.MULTILINE | re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return False, "Scope section not found"
    body = match.group(2).strip()
    if not body or body.lower() in {"tbd", "n/a", "todo", ""}:
        return False, "Scope section is empty"
    return True, "Scope section has content"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(workspace_root: Path) -> dict[str, Any]:
    spec = workspace_root / ".dmx" / "spec.md"
    content = spec.read_text(encoding="utf-8") if spec.exists() else ""

    checks_raw = [
        ("spec_exists", _spec_exists(spec)),
        ("qa_answered", _qa_answered(content)),
        ("technical_approach_filled", _technical_approach_filled(content)),
        ("scope_defined", _scope_defined(content)),
    ]

    checks = [{"name": name, "pass": passed, "message": msg} for name, (passed, msg) in checks_raw]

    overall = all(c["pass"] for c in checks)
    summary_msg = (
        "Spec completeness check passed"
        if overall
        else "Spec completeness check failed — see checks for details"
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
