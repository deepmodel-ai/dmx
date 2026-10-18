"""Loop runtime state persistence.

State layout::

    .dmx/
      jobs/
        {job_id}/
          {loop_name}-{task_id}.json   — full state for each loop run
          spec.md  tasks.md  activeContext.md   — the ticket's own files (GH-84)

Job ID  = the job folder whose ``spec.md`` frontmatter ``branch`` is the
current branch, or the branch name fallback. A 0.5 repo's root
``.dmx/spec.md`` still counts until its ticket files are moved.
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
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from dmx.exceptions import AmbiguousActiveRun

# How many times to retry a state-file replace that Windows refuses.
_REPLACE_ATTEMPTS = 20

# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------


class LoopStatus(StrEnum):
    pending = "pending"
    running = "running"
    paused = "paused"
    iterating = "iterating"
    validating = "validating"
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

# The files each ticket writes, kept in its job folder since GH-84. Before
# that they sat at the `.dmx/` root and every ticket edited the same copy.
SPEC_FILE = "spec.md"
TASKS_FILE = "tasks.md"
ACTIVE_CONTEXT_FILE = "activeContext.md"
TICKET_FILES = (SPEC_FILE, TASKS_FILE, ACTIVE_CONTEXT_FILE)


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


# ``[ \t]*`` rather than ``\s*``: ``\s`` matches a newline, so a bare
# ``ticket:`` line would take the next line as its value.
_FRONTMATTER_FIELD_RE = r"^{key}[ \t]*:[ \t]*(.+)$"

_NO_TICKET_VALUES = frozenset({"none", "unknown"})


def _frontmatter_field(frontmatter: str, key: str) -> str | None:
    """Return one ``key: value`` from a frontmatter block, or None."""
    match = re.search(_FRONTMATTER_FIELD_RE.format(key=re.escape(key)), frontmatter, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value or None


def usable_ticket(ticket: str | None) -> str | None:
    """Return *ticket* when it is a real ticket id, else None.

    An empty value, ``none`` (ticketing is off), and ``unknown`` are not
    identities. Callers then use the branch name.
    """
    if not ticket or ticket.lower() in _NO_TICKET_VALUES:
        return None
    return ticket


def job_dir(workspace_root: Path, job_id: str) -> Path:
    """The folder for one job: ``.dmx/jobs/{job_id}/``."""
    return workspace_root / ".dmx" / "jobs" / job_id


def ticket_file(workspace_root: Path, job_id: str, name: str) -> Path:
    """Path of one of the job's ticket files (see :data:`TICKET_FILES`)."""
    return job_dir(workspace_root, job_id) / name


def root_ticket_file(workspace_root: Path, name: str) -> Path:
    """Where a 0.5 repo kept a ticket file: the ``.dmx/`` root."""
    return workspace_root / ".dmx" / name


def ticket_path_from_context(
    workspace_root: Path, loop_context: dict[str, Any] | None, key: str, name: str
) -> Path:
    """A validator's path to one ticket file.

    The runner passes ``spec_path``, ``tasks_path`` and
    ``active_context_path`` in ``loop_context`` (GH-84). Without them, for
    example when a validator is run by hand, this is the 0.5 root path.
    """
    value = (loop_context or {}).get(key)
    if isinstance(value, str) and value:
        return Path(value)
    return root_ticket_file(workspace_root, name)


def _spec_identity_in(spec: Path) -> tuple[str | None, str | None]:
    if not spec.is_file():
        return None, None
    try:
        content = spec.read_text(encoding="utf-8")
    except OSError:
        return None, None
    return spec_identity_in_text(content)


def spec_identity_in_text(content: str) -> tuple[str | None, str | None]:
    """``(ticket, branch)`` from the frontmatter of a ``spec.md``'s text."""
    fm_match = _FRONTMATTER_RE.match(content)
    if not fm_match:
        return None, None
    frontmatter = fm_match.group(1)
    return _frontmatter_field(frontmatter, "ticket"), _frontmatter_field(frontmatter, "branch")


def read_spec_identity(
    workspace_root: Path, job_id: str | None = None
) -> tuple[str | None, str | None]:
    """Return ``(ticket, branch)`` from a ``spec.md`` frontmatter.

    With *job_id*, reads that job's ``spec.md``. Without it, reads the 0.5
    root ``.dmx/spec.md``. Either side is None when the file or that key is
    missing. ``branch`` is the frontmatter key of that name, not
    ``from_branch``.
    """
    if job_id is None:
        return _spec_identity_in(root_ticket_file(workspace_root, SPEC_FILE))
    return _spec_identity_in(ticket_file(workspace_root, job_id, SPEC_FILE))


def jobs_for_branch(workspace_root: Path, branch: str) -> list[str]:
    """Job ids whose ``spec.md`` frontmatter ``branch`` is *branch*, sorted.

    Pending folders are skipped: their identity is not settled yet.
    """
    jobs_root = workspace_root / ".dmx" / "jobs"
    if not jobs_root.is_dir():
        return []
    found: list[str] = []
    for folder in sorted(jobs_root.iterdir()):
        if not folder.is_dir() or is_pending_job_id(folder.name):
            continue
        _ticket, spec_branch = _spec_identity_in(folder / SPEC_FILE)
        if spec_branch == branch:
            found.append(folder.name)
    return found


def _has_live_run(workspace_root: Path, job_id: str) -> bool:
    try:
        if find_active_run(workspace_root, job_id) is not None:
            return True
    except AmbiguousActiveRun:
        return True
    return find_pr_snapshot_run(workspace_root, job_id) is not None


def resolve_job_id(workspace_root: Path) -> str:
    """Resolve job ID for the current workspace.

    Resolution order:
    1. The job folder whose ``spec.md`` frontmatter ``branch`` equals the
       current git branch. When several do, the one with a live run (or a
       PR snapshot) wins.
    2. ``ticket`` in a 0.5 root ``.dmx/spec.md``, only when it is a real
       ticket id and its ``branch`` equals the current branch. A stale
       ticket, or ``none`` / ``unknown`` / empty, is not an identity.
    3. Current git branch name. A renamed branch therefore resolves to the
       new name, and an in-flight job under the old name is no longer found.
       Detached HEAD has no branch name.
    4. ``unknown`` fallback.

    Args:
        workspace_root: Absolute path to the repo root.

    Returns:
        A string job ID — stable across multiple loop runs on the same ticket.

    Raises:
        AmbiguousActiveRun: Several job folders name the current branch and
            more or fewer than one of them has a live run.
    """
    branch = current_branch(workspace_root)
    if not branch:
        return "unknown"

    matches = jobs_for_branch(workspace_root, branch)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        live = [job_id for job_id in matches if _has_live_run(workspace_root, job_id)]
        if len(live) == 1:
            return live[0]
        names = ", ".join(f"`{job_id}`" for job_id in matches)
        raise AmbiguousActiveRun(
            f"{len(matches)} job folders name branch `{branch}` in their spec.md ({names}). "
            "Resolve manually under .dmx/jobs/: fix the `branch` in the spec.md that is "
            "wrong, or move the old folder aside."
        )

    ticket, spec_branch = read_spec_identity(workspace_root)
    identity = usable_ticket(ticket)
    if identity and spec_branch == branch:
        return identity
    return branch


def root_ticket_files_belong_to(workspace_root: Path, job_id: str) -> bool:
    """True when a 0.5 repo's root ticket files are *job_id*'s (GH-84).

    Either the root ``.dmx/spec.md`` names the current branch and its ticket
    (or that branch) is *job_id*; or there is no root ``spec.md`` and
    *job_id*'s own ``spec.md`` names the current branch, so a root
    ``tasks.md`` or ``activeContext.md`` left beside it is this ticket's.
    """
    branch = current_branch(workspace_root)
    if not branch:
        return False
    if root_ticket_file(workspace_root, SPEC_FILE).is_file():
        ticket, spec_branch = read_spec_identity(workspace_root)
        return spec_branch == branch and (usable_ticket(ticket) or branch) == job_id
    return read_spec_identity(workspace_root, job_id)[1] == branch


def job_has_loop_runs(workspace_root: Path, job_id: str) -> bool:
    """True when ``.dmx/jobs/{job_id}/`` already holds a loop-state file.

    Skill artifacts that lack ``loop_name``, ``task_id``, and ``status``
    do not count. An empty or missing directory does not count.
    """
    job_dir = _job_dir(workspace_root, job_id)
    if not job_dir.exists():
        return False
    for path in job_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and {"loop_name", "task_id", "status"} <= data.keys():
            return True
    return False


# ---------------------------------------------------------------------------
# State file I/O
# ---------------------------------------------------------------------------


def state_path(workspace_root: Path, job_id: str, loop_name: str, task_id: str) -> Path:
    """Return the path for a loop run state file."""
    # A component loop is named `<id>/<name>` (GH-90); a `/` would make a folder.
    stem = loop_name.replace("/", "--")
    return workspace_root / ".dmx" / "jobs" / job_id / f"{stem}-{task_id}.json"


def _job_dir(workspace_root: Path, job_id: str) -> Path:
    return job_dir(workspace_root, job_id)


def write_initial_state(
    workspace_root: Path,
    loop_name: str,
    job_id: str,
    task_id: str,
    skills: list[str],
    updates: dict[str, Any] | None = None,
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
        updates: Fields merged into that first write. Chaining uses this so
            the new run is never visible as ``pending`` with no
            ``validation_started_at``.

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
    if updates:
        state.update(updates)

    path = state_path(workspace_root, job_id, loop_name, task_id)
    _replace_json(path, state)
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
    _replace_json(state_path(workspace_root, job_id, loop_name, task_id), state)
    return state


def _replace_json(path: Path, payload: dict[str, Any]) -> None:
    """Write *payload* to *path* by replacing the file, not truncating it.

    ``Path.write_text`` empties the destination before the new bytes land.
    A reader on another thread (``loop_status`` while a validator worker
    writes) can then observe a partial file and raise ``JSONDecodeError``.
    Writing a sibling temp file and ``os.replace``-ing it into place is
    atomic on the same filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique per write. The worker thread and a request share a pid, so a
    # pid-only name lets one thread's os.replace move the other's temp file.
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _replace(source: Path, target: Path) -> None:
    """``os.replace``, retried briefly on Windows.

    Windows refuses to replace a file another thread is replacing or reading
    at that instant (``PermissionError``); a moment later it succeeds.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


# ---------------------------------------------------------------------------
# Active-run lookup (no separate pointer file — see module docstring)
# ---------------------------------------------------------------------------


def find_active_run(workspace_root: Path, job_id: str) -> tuple[str, str] | None:
    """Find the one non-terminal loop run under *job_id*, if any.

    Returns:
        ``(loop_name, task_id)`` for the run currently
        ``pending``/``running``/``paused``/``iterating``/``validating``, or ``None`` if
        every run under this job has reached a terminal status (or the job
        has no runs at all).

    Raises:
        AmbiguousActiveRun: If more than one non-terminal run is found.
            Loops chain sequentially — completing or pausing one before the
            next starts — so this should never happen in normal operation.
            Rather than guess which one is "active", this fails loudly so
            it can be investigated.

    Note:
        The job directory can hold non-state JSON artifacts a skill was
        told to write there (e.g. ``validate`` writing
        ``validation-report.json`` — see ``dmx-validate.md`` Step 9 and
        ``validators/spec_adherence.py``). Those files have no
        ``loop_name``/``task_id``/``status`` and are skipped rather than
        misread as a second, permanently-non-terminal run (see GH-36).
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
        if not isinstance(data, dict) or not {"loop_name", "task_id", "status"} <= data.keys():
            # Not a loop-state file (e.g. a skill artifact like
            # validation-report.json) — not a run candidate at all.
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


def is_pr_snapshot(state: dict[str, Any]) -> bool:
    """True when *state* is the pre-validator record committed with a PR.

    ``create-pr`` marks the active loop ``complete`` before it commits
    ``.dmx/``, so the merged file does not stay ``running``. ``outcome`` is
    still null and ``validator_results`` is still empty — ``loop_continue``
    fills those in by running the loop's validators. A genuinely finished
    loop has an outcome, so it does not match.
    """
    results = state.get("validator_results")
    empty_results = results is None or results == []
    return (
        state.get("status") == LoopStatus.complete.value
        and state.get("outcome") is None
        and empty_results
    )


def find_pr_snapshot_run(workspace_root: Path, job_id: str) -> tuple[str, str] | None:
    """Find a PR snapshot under *job_id*, if any.

    Same shape as :func:`find_active_run`, but matches :func:`is_pr_snapshot`
    instead of a non-terminal status. When more than one snapshot is still
    unfinished, the one with the latest ``updated_at`` wins so a doubled job
    does not stop ``loop_advance``. ``complete`` is terminal for the
    loop-mode rule, so this lookup is separate and callers decide whether
    the current branch should still resume the run.
    """
    job_dir = _job_dir(workspace_root, job_id)
    if not job_dir.exists():
        return None

    candidates: list[tuple[str, str, str]] = []
    for path in sorted(job_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or not {"loop_name", "task_id", "status"} <= data.keys():
            continue
        if not is_pr_snapshot(data):
            continue
        candidates.append(
            (data.get("loop_name", ""), data.get("task_id", ""), str(data.get("updated_at", "")))
        )

    if not candidates:
        return None
    # A second create-pr on the same ticket leaves the earlier snapshot in
    # place until it is superseded. The newest one is the run to resume.
    loop_name, task_id, _updated = max(candidates, key=lambda item: item[2])
    return loop_name, task_id


def list_open_runs(
    workspace_root: Path,
    job_id: str,
    *,
    include_snapshots: bool = False,
) -> list[tuple[str, str]]:
    """Every non-terminal run under *job_id*, plus PR snapshots when asked.

    A folder with several open runs returns all of them. This does not raise
    :class:`AmbiguousActiveRun`. A snapshot is ``complete``, so it is not
    also listed as non-terminal.
    """
    job_dir = _job_dir(workspace_root, job_id)
    if not job_dir.exists():
        return []

    found: list[tuple[str, str]] = []
    for path in sorted(job_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or not {"loop_name", "task_id", "status"} <= data.keys():
            continue
        loop_name = data.get("loop_name", "")
        task_id = data.get("task_id", "")
        if not isinstance(loop_name, str) or not isinstance(task_id, str):
            continue
        if not loop_name or not task_id:
            continue
        if data.get("status") not in _TERMINAL_STATUSES or (
            include_snapshots and is_pr_snapshot(data)
        ):
            found.append((loop_name, task_id))
    return found


def supersede_pr_snapshots(workspace_root: Path, job_id: str, *, keep_task_id: str) -> None:
    """Close every PR snapshot in *job_id* except *keep_task_id*.

    A later ``create-pr`` replaces an unfinished one. The closed file stays
    ``complete`` and gains an outcome, so it is no longer a snapshot and
    :func:`find_pr_snapshot_run` will not treat it as the active run.
    """
    job_dir = _job_dir(workspace_root, job_id)
    if not job_dir.exists():
        return
    for path in sorted(job_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or not is_pr_snapshot(data):
            continue
        task_id = str(data.get("task_id", ""))
        if task_id == keep_task_id:
            continue
        write_state(
            workspace_root,
            job_id,
            str(data.get("loop_name", "")),
            task_id,
            {
                "outcome": LoopOutcome.warning.value,
                "validator_results": [
                    {
                        "tool": "check_pr_ready",
                        "pass": False,
                        "message": (
                            "Superseded by a later create-pr before check_pr_ready finished."
                        ),
                    }
                ],
            },
        )


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

    Moves everything in ``.dmx/jobs/{old_job_id}/`` to
    ``.dmx/jobs/{new_job_id}/``: the state files, with each file's own
    ``job_id`` field rewritten to match, and the ticket's ``spec.md``,
    ``tasks.md``, ``activeContext.md`` and any other file. If the
    destination already has a file of the same name, the source file stays
    where it is rather than overwriting it.

    A no-op if *old_job_id* has no folder.
    """
    old_dir = _job_dir(workspace_root, old_job_id)
    if not old_dir.exists():
        return

    new_dir = _job_dir(workspace_root, new_job_id)
    new_dir.mkdir(parents=True, exist_ok=True)

    for path in sorted(old_dir.iterdir()):
        target = new_dir / path.name
        if target.exists():
            continue
        if path.suffix == ".json" and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = None
            if isinstance(data, dict) and {"loop_name", "task_id", "status"} <= data.keys():
                data["job_id"] = new_job_id
                target.write_text(json.dumps(data, indent=2), encoding="utf-8")
                path.unlink()
                continue
        shutil.move(str(path), str(target))

    # Non-empty (unexpected leftover file) — leave it for inspection.
    with contextlib.suppress(OSError):
        old_dir.rmdir()


def move_one_run(
    workspace_root: Path,
    old_job_id: str,
    new_job_id: str,
    loop_name: str,
    task_id: str,
) -> bool:
    """Move one state file and set its ``job_id`` to *new_job_id*.

    Other files in the old job directory stay where they are. Returns
    ``False`` when the source is missing or the destination already exists.
    """
    source = state_path(workspace_root, old_job_id, loop_name, task_id)
    if not source.is_file():
        return False
    destination = state_path(workspace_root, new_job_id, loop_name, task_id)
    if destination.exists():
        return False
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return False
    data["job_id"] = new_job_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    _replace_json(destination, data)
    source.unlink()
    with contextlib.suppress(OSError):
        source.parent.rmdir()
    return True
