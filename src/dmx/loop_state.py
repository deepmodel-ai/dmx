"""Loop runtime state persistence.

State layout::

    .dmx/
      jobs/
        {job_id}/
          {loop_name}-{task_id}.json   — full state for each loop run

Job ID  = ticket ID from ``.dmx/spec.md`` frontmatter, or branch name fallback.
Task ID = UUID4 generated at ``run_loop`` invocation.

There is no separate "active run pointer" file. Which run is active is
derived by scanning a job's state files for the one with a non-terminal
status (``pending``/``running``/``paused``/``iterating``) — see
:func:`find_active_run`. This ties resumption directly to the current git
branch (via :func:`resolve_job_id`) instead of a single mutable file that
every ``run_loop`` call anywhere would overwrite regardless of branch —
see GH-9 for the corruption this caused when pausing work on one branch
and running a loop on another.

A loop that establishes a *brand new* ticket identity (``spec``, via
``require_branch``) can't use ``resolve_job_id`` for its own starting job
id — there's no ticket yet, and any pre-existing ``spec.md``/branch signal
would be stale by definition (e.g. left over from the last ticket merged
into ``main``). Such loops start under a temporary id (see
:func:`make_pending_job_id`) and get renamed to their real job id once the
identity-creating skill completes — see :func:`rename_job` and
:func:`find_pending_run` for resuming one before that rename happens.

State merges to ``main`` with the PR via ``create-pr`` Step 5, which commits
all ``.dmx/`` changes.  Each ticket has its own subdirectory, so merge
conflicts are practically impossible.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from dmx.exceptions import AmbiguousActiveRun

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


_TERMINAL_STATUSES = frozenset({LoopStatus.complete.value, LoopStatus.failed.value})

# Job folders under this prefix hold state for a loop that hasn't yet
# established its real ticket identity — see module docstring.
PENDING_JOB_PREFIX = "_pending-"


# ---------------------------------------------------------------------------
# State file structure
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_task_id() -> str:
    """Generate a UUID4 task ID."""
    return str(uuid4())


def make_pending_job_id(task_id: str) -> str:
    """Build a temporary job id for a loop that hasn't established a real
    ticket identity yet (see module docstring)."""
    return f"{PENDING_JOB_PREFIX}{task_id[:8]}"


def is_pending_job_id(job_id: str) -> bool:
    return job_id.startswith(PENDING_JOB_PREFIX)


# ---------------------------------------------------------------------------
# Job ID resolution
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
# spec.md frontmatter is written with the key `ticket` (see dmx-create-ticket.md,
# dmx-derive-ticket.md, dmx-hotfix.md) — not `ticket_id`.
_TICKET_KEY_RE = re.compile(r"^ticket\s*:\s*(.+)$", re.MULTILINE)


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
    1. ``ticket`` in ``.dmx/spec.md`` YAML frontmatter.
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


def _job_dir(workspace_root: Path, job_id: str) -> Path:
    return workspace_root / ".dmx" / "jobs" / job_id


def write_initial_state(
    workspace_root: Path,
    loop_name: str,
    job_id: str,
    task_id: str,
    skills: list[str],
) -> Path:
    """Write the initial state file for a new loop run.

    Creates ``.dmx/jobs/{job_id}/{loop_name}-{task_id}.json``. There is no
    separate active-run pointer to update — see module docstring.

    Args:
        workspace_root: Absolute path to the repo root.
        loop_name: Name of the loop being run.
        job_id: Job ID (ticket ID, branch name, or a pending/temp id — see
            :func:`make_pending_job_id`).
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


# ---------------------------------------------------------------------------
# Active-run lookup (no separate pointer file — see module docstring)
# ---------------------------------------------------------------------------


def find_active_run(workspace_root: Path, job_id: str) -> tuple[str, str] | None:
    """Find the one non-terminal loop run under *job_id*, if any.

    Returns:
        ``(loop_name, task_id)`` for the run currently
        ``pending``/``running``/``paused``/``iterating``, or ``None`` if
        every run under this job has reached a terminal status (or the job
        has no runs at all).

    Raises:
        AmbiguousActiveRun: If more than one non-terminal run is found.
            Loops chain sequentially — completing or pausing one before the
            next starts — so this should never happen in normal operation.
            Rather than guess which one is "active", this fails loudly so
            it can be investigated.
    """
    job_dir = _job_dir(workspace_root, job_id)
    if not job_dir.exists():
        return None

    candidates: list[tuple[str, str]] = []
    for path in sorted(job_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("status") in _TERMINAL_STATUSES:
            continue
        candidates.append((data.get("loop_name", ""), data.get("task_id", "")))

    if not candidates:
        return None
    if len(candidates) > 1:
        names = ", ".join(f"{loop}-{task[:8]}" for loop, task in candidates)
        raise AmbiguousActiveRun(
            f"job '{job_id}' has {len(candidates)} non-terminal loop runs ({names}) — "
            f"expected at most one. Resolve manually under .dmx/jobs/{job_id}/."
        )
    return candidates[0]


def find_pending_run(workspace_root: Path) -> tuple[str, str, str] | None:
    """Find a non-terminal run under a temp/pending job id, if any.

    Covers the window where a loop that establishes a new ticket identity
    (e.g. ``spec``) has started but not yet completed the skill that makes
    its real job id resolvable — branch-derived lookup can't find a ticket
    that doesn't exist yet.

    Returns:
        ``(job_id, loop_name, task_id)``, or ``None`` if there's no pending
        run.

    Raises:
        AmbiguousActiveRun: If more than one pending job has a non-terminal
            run — at most one legitimately exists at a time per working
            directory (you can only be on one branch, i.e. mid-creation of
            one ticket, at once).
    """
    jobs_dir = workspace_root / ".dmx" / "jobs"
    if not jobs_dir.exists():
        return None

    found: list[tuple[str, str, str]] = []
    for job_dir in sorted(jobs_dir.glob(f"{PENDING_JOB_PREFIX}*")):
        if not job_dir.is_dir():
            continue
        active = find_active_run(workspace_root, job_dir.name)
        if active is not None:
            found.append((job_dir.name, active[0], active[1]))

    if not found:
        return None
    if len(found) > 1:
        names = ", ".join(job_id for job_id, _, _ in found)
        raise AmbiguousActiveRun(
            f"found {len(found)} pending loop runs ({names}) — expected at most one. "
            "Resolve manually under .dmx/jobs/."
        )
    return found[0]


def rename_job(workspace_root: Path, old_job_id: str, new_job_id: str) -> None:
    """Rename a job folder once its real identity is known.

    Moves every state file from ``.dmx/jobs/{old_job_id}/`` to
    ``.dmx/jobs/{new_job_id}/``, rewriting each file's own ``job_id`` field
    to match. If the destination already has files (e.g. a prior run for
    the same ticket), the moved files are added alongside them rather than
    overwriting anything unexpected.

    A no-op if *old_job_id* has no folder.
    """
    old_dir = _job_dir(workspace_root, old_job_id)
    if not old_dir.exists():
        return

    new_dir = _job_dir(workspace_root, new_job_id)
    new_dir.mkdir(parents=True, exist_ok=True)

    for path in old_dir.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["job_id"] = new_job_id
        (new_dir / path.name).write_text(json.dumps(data, indent=2), encoding="utf-8")
        path.unlink()

    # Non-empty (unexpected leftover file) — leave it for inspection.
    with contextlib.suppress(OSError):
        old_dir.rmdir()
