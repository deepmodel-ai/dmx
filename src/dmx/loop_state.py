"""Loop runtime state persistence.

State layout::

    .dmx/
      loop-state.json          — active run pointer (single active loop)
      jobs/
        {job_id}/
          {loop_name}-{task_id}.json   — full state for each loop run

Job ID  = ticket ID from ``.dmx/spec.md`` frontmatter, or branch name fallback.
Task ID = UUID4 generated at ``run_loop`` invocation.

State merges to ``main`` with the PR via ``create-pr`` Step 5, which commits
all ``.dmx/`` changes.  Each ticket has its own subdirectory, so merge
conflicts are practically impossible.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------


class LoopStatus(StrEnum):
    pending = "pending"
    running = "running"
    paused = "paused"
    iterating = "iterating"
    complete = "complete"
    failed = "failed"


class LoopOutcome(StrEnum):
    success = "success"
    failure = "failure"
    warning = "warning"


# ---------------------------------------------------------------------------
# State file structure
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_task_id() -> str:
    """Generate a UUID4 task ID."""
    return str(uuid4())


# ---------------------------------------------------------------------------
# Job ID resolution
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_TICKET_KEY_RE = re.compile(r"^ticket_id\s*:\s*(.+)$", re.MULTILINE)


def current_branch(workspace_root: Path) -> str | None:
    """Return the current git branch name, or None if not resolvable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=workspace_root,
        )
        branch = result.stdout.strip()
        if branch and branch != "HEAD":
            return branch
    except Exception:  # noqa: BLE001
        pass
    return None


def resolve_job_id(workspace_root: Path) -> str:
    """Resolve job ID for the current workspace.

    Resolution order:
    1. ``ticket_id`` in ``.dmx/spec.md`` YAML frontmatter.
    2. Current git branch name.
    3. ``unknown`` fallback.

    Args:
        workspace_root: Absolute path to the repo root.

    Returns:
        A string job ID — stable across multiple loop runs on the same ticket.
    """
    spec = workspace_root / ".dmx" / "spec.md"
    if spec.exists():
        content = spec.read_text(encoding="utf-8")
        fm_match = _FRONTMATTER_RE.match(content)
        if fm_match:
            ticket_match = _TICKET_KEY_RE.search(fm_match.group(1))
            if ticket_match:
                ticket_id = ticket_match.group(1).strip().strip('"').strip("'")
                if ticket_id:
                    return ticket_id

    branch = current_branch(workspace_root)
    if branch:
        return branch

    return "unknown"


# ---------------------------------------------------------------------------
# State file I/O
# ---------------------------------------------------------------------------


def state_path(workspace_root: Path, job_id: str, loop_name: str, task_id: str) -> Path:
    """Return the path for a loop run state file."""
    return workspace_root / ".dmx" / "jobs" / job_id / f"{loop_name}-{task_id}.json"


def active_pointer_path(workspace_root: Path) -> Path:
    """Return the path for the active run pointer file."""
    return workspace_root / ".dmx" / "loop-state.json"


def write_initial_state(
    workspace_root: Path,
    loop_name: str,
    job_id: str,
    task_id: str,
    skills: list[str],
) -> Path:
    """Write the initial state file for a new loop run.

    Creates ``.dmx/jobs/{job_id}/{loop_name}-{task_id}.json`` and updates
    ``.dmx/loop-state.json`` to point to this run.

    Args:
        workspace_root: Absolute path to the repo root.
        loop_name: Name of the loop being run.
        job_id: Job ID (ticket ID or branch name).
        task_id: UUID4 task ID for this run.
        skills: Ordered list of skill names for this loop.

    Returns:
        Path to the written state file.
    """
    state: dict[str, Any] = {
        "loop_name": loop_name,
        "job_id": job_id,
        "task_id": task_id,
        "status": LoopStatus.pending.value,
        "current_skill_index": 0,
        "skills": skills,
        "skills_completed": [],
        "skill_outputs": {},
        "validator_results": [],
        "outcome": None,
        "iteration_count": 0,
        "timestamp": _now_iso(),
        "updated_at": _now_iso(),
    }

    path = state_path(workspace_root, job_id, loop_name, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # Update active pointer.
    pointer: dict[str, str] = {
        "active_job_id": job_id,
        "active_task_id": task_id,
        "active_loop_name": loop_name,
    }
    active_pointer_path(workspace_root).write_text(json.dumps(pointer, indent=2), encoding="utf-8")

    return path


def read_state(
    workspace_root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
) -> dict[str, Any]:
    """Read and return the state dict for a loop run."""
    path = state_path(workspace_root, job_id, loop_name, task_id)
    return dict(json.loads(path.read_text(encoding="utf-8")))


def write_state(
    workspace_root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Merge *updates* into the state file and return the updated state.

    Args:
        workspace_root: Repo root.
        job_id: Job ID.
        loop_name: Loop name.
        task_id: Task ID.
        updates: Dict of fields to merge into the state.

    Returns:
        The full updated state dict.
    """
    state = read_state(workspace_root, job_id, loop_name, task_id)
    state.update(updates)
    state["updated_at"] = _now_iso()
    path = state_path(workspace_root, job_id, loop_name, task_id)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def read_active_pointer(workspace_root: Path) -> dict[str, str] | None:
    """Read the active run pointer, or return None if no active run."""
    p = active_pointer_path(workspace_root)
    if not p.exists():
        return None
    return dict(json.loads(p.read_text(encoding="utf-8")))


def clear_active_pointer(workspace_root: Path) -> None:
    """Remove the active run pointer (loop has reached a terminal state)."""
    p = active_pointer_path(workspace_root)
    if p.exists():
        p.unlink()
