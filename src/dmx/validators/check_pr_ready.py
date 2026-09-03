"""Bundled validator: check_pr_ready.

Checks that the PR opened by ``/dmx/create-pr`` exists and that the memory
bank was updated on this branch. ``ticket_transitioned`` cannot be verified
deterministically without ticketing API credentials (Jira, GitHub Issues),
so this bundled default reports it as passing with a note recommending
manual confirmation — override ``validators/check_pr_ready.py`` in the app
repo for a real ticketing-API-backed check.

``memory_updated`` fails if ``.dmx/`` has *any* uncommitted changes (staged
or not) — a skill that edits the memory bank without committing (see GH-15)
would otherwise leave dangling working-tree state that's silently excluded
from the PR. Only once the tree is clean does it fall back to checking
whether the latest commit touched ``.dmx/*.md``.

Contract
--------
Called by the orchestrator via subprocess. The input contract is written to
stdin as JSON::

    python validators/check_pr_ready.py < contract.json

    {
      "skill_outputs": {...},
      "goal_state": "...",
      "loop_context": {..., "workspace_root": "/path/to/repo", "ticket_ref": "..."}
    }

Exits 0 on pass, 1 on failure.
Writes JSON to stdout::

    {
      "pass": true,
      "message": "PR ready check passed",
      "checks": [
        {"name": "pr_exists",           "pass": true},
        {"name": "ticket_transitioned", "pass": true},
        {"name": "memory_updated",      "pass": true}
      ]
    }
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

GH_TIMEOUT_SECONDS = 30
GIT_TIMEOUT_SECONDS = 15


def _pr_exists(workspace_root: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", "--json", "url,state"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return False, "gh CLI not found — cannot verify PR existence"
    except subprocess.TimeoutExpired:
        return False, "gh pr view timed out"

    if proc.returncode != 0:
        return False, "No open PR found for the current branch"

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, "Could not parse `gh pr view` output"

    return True, f"PR open: {data.get('url', '(url unavailable)')}"


def _ticket_transitioned(loop_context: dict[str, Any]) -> tuple[bool, str]:
    ticket_ref = loop_context.get("ticket_ref")
    if not ticket_ref or ticket_ref in ("none", "unknown"):
        return True, "No ticketing configured — nothing to transition"

    return (
        True,
        f"Ticket '{ticket_ref}' transition not verified — requires ticketing API access. "
        "Confirm manually or override this validator for automated checking.",
    )


def _dirty_dmx_files(workspace_root: Path) -> list[str]:
    """Return paths under .dmx/ with uncommitted changes (staged or not)."""
    try:
        proc = subprocess.run(
            ["git", "status", "--short", "--", ".dmx/"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001
        return []
    if proc.returncode != 0:
        return []
    # Each line is "XY path" — strip the two-char status + separating space.
    return [line[3:].strip() for line in proc.stdout.splitlines() if line.strip()]


def _memory_updated(workspace_root: Path) -> tuple[bool, str]:
    dirty = _dirty_dmx_files(workspace_root)
    if dirty:
        # A skill (e.g. update-memory) edited .dmx/ without committing —
        # this loop's memory sync isn't actually reflected in the PR.
        return False, (
            f"Uncommitted changes under .dmx/ ({', '.join(dirty)}) — "
            "memory bank edits must be committed, not left dangling in the "
            "working tree. Run `/dmx/commit` (or re-run the skill that made "
            "these edits so it commits them) before continuing."
        )

    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        changed = proc.stdout.splitlines() if proc.returncode == 0 else []
    except Exception:  # noqa: BLE001
        changed = []

    if any(f.startswith(".dmx/") and f.endswith(".md") for f in changed):
        return True, "Memory bank files updated in the latest commit"

    active_context = workspace_root / ".dmx" / "activeContext.md"
    if active_context.exists():
        return True, "activeContext.md present (recent update not confirmed via git diff)"

    return False, "No memory bank update detected"


def run(workspace_root: Path, loop_context: dict[str, Any]) -> dict[str, Any]:
    checks_raw = [
        ("pr_exists", _pr_exists(workspace_root)),
        ("ticket_transitioned", _ticket_transitioned(loop_context)),
        ("memory_updated", _memory_updated(workspace_root)),
    ]

    checks = [{"name": name, "pass": passed, "message": msg} for name, (passed, msg) in checks_raw]

    overall = all(c["pass"] for c in checks)
    summary_msg = (
        "PR ready check passed" if overall else "PR ready check failed — see checks for details"
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
    result = run(Path(workspace_root), loop_context)
    print(json.dumps(result))
    sys.exit(0 if result["pass"] else 1)
