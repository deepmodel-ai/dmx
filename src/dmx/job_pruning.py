"""Remove finished job folders before a PR opens (GH-84).

Every ticket keeps its ``spec.md``, ``tasks.md``, ``activeContext.md`` and
loop state in ``.dmx/jobs/<job>/``, and the folder merges with its PR, so
``.dmx/jobs/`` grows by one folder per ticket. The bundled ``release`` loop
runs ``prune-jobs`` before ``create-pr``; ``dmx prune-jobs`` runs it by hand.

A job is removed when:

- every loop run in its folder has finished (``complete`` or ``failed``);
- the folder is at the tip of ``branch_base``, so its PR merged;
- its newest run finished more than ``older_than`` ago (a folder with no
  runs uses the date of the last commit on ``branch_base`` that touched it).

It is never removed when it is the current ticket, when its
``activeContext.md`` still has Open Learnings or Open Decisions, or when it
has uncommitted changes. The removals are one commit,
"chore: remove N finished jobs", so reviewers can skip it. Git history
keeps the files.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.exceptions import AmbiguousActiveRun, DmxError, WorkspaceRootInvalid
from dmx.learnings import has_waiting
from dmx.loop_state import (
    ACTIVE_CONTEXT_FILE,
    LoopStatus,
    current_branch,
    jobs_for_branch,
    resolve_job_id,
)
from dmx.loop_tools import _read_config_value  # noqa: PLC2701 — same package
from dmx.workspace import resolve_workspace_root

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DEFAULT_OLDER_THAN_DAYS",
    "PruneError",
    "PrunePlan",
    "plan_prune",
    "prune_jobs",
    "register_job_pruning_tools",
    "removal_message",
]

DEFAULT_OLDER_THAN_DAYS = 30

_JOBS_DIR = ".dmx/jobs"

_FINISHED = frozenset({LoopStatus.complete.value, LoopStatus.failed.value})


class PruneError(DmxError):
    """Git refused to remove or commit the finished jobs."""


@dataclass(frozen=True)
class PrunePlan:
    """Which jobs go, which stay and why, or why nothing can run here."""

    blocked: str | None
    removes: tuple[str, ...] = ()
    kept: tuple[tuple[str, str], ...] = ()
    commit: str | None = None

    @property
    def message(self) -> str | None:
        return removal_message(len(self.removes)) if self.removes else None

    def as_dict(self, *, applied: bool) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "applied": applied,
            "removes": list(self.removes),
            "kept": [{"job": job, "reason": reason} for job, reason in self.kept],
            "commit": self.commit,
            "commit_message": self.message,
        }


def removal_message(count: int) -> str:
    """The removal commit's message, e.g. ``chore: remove 3 finished jobs``."""
    return f"chore: remove {count} finished job{'' if count == 1 else 's'}"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _blocked(root: Path) -> str | None:
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return "Not a git repository. Nothing was removed."
    branch = current_branch(root)
    if branch is None:
        return "HEAD is detached. Check out the ticket's branch, then run it again."
    base = _read_config_value(root, "branch_base")
    if base is None:
        return "`branch_base` is not set in `.dmx/config.md`. Run /dmx/init to set it."
    if branch in {base, _read_config_value(root, "production_branch")}:
        return (
            f"`{branch}` is a protected branch; finished jobs are removed through a PR. "
            "Run this on a feature branch."
        )
    return None


def _base_ref(root: Path) -> str | None:
    base = _read_config_value(root, "branch_base")
    if base is None:
        return None
    for ref in (f"origin/{base}", base):
        if _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0:
            return ref
    return None


def _current_jobs(root: Path) -> set[str]:
    branch = current_branch(root)
    if branch is None:
        return set()
    current = {branch, *jobs_for_branch(root, branch)}
    with contextlib.suppress(AmbiguousActiveRun):
        current.add(resolve_job_id(root))
    return current


def _jobs_on(root: Path, ref: str) -> set[str]:
    """Job folder names at the tip of *ref*, in one git call."""
    result = _git(root, "ls-tree", "-d", "--name-only", "--full-tree", f"{ref}:./{_JOBS_DIR}")
    return set(result.stdout.splitlines()) if result.returncode == 0 else set()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _runs_verdict(folder: Path) -> tuple[str | None, datetime | None]:
    """Why the runs keep this job (None when all finished), and the newest finish."""
    newest: datetime | None = None
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return f"`{path.name}` can't be read", None
        if not isinstance(data, dict) or not {"loop_name", "task_id", "status"} <= data.keys():
            continue
        if data["status"] not in _FINISHED:
            return f"the {data['loop_name']} loop is {data['status']}", None
        finished = _parse_time(data.get("updated_at")) or _parse_time(data.get("timestamp"))
        if finished is not None and (newest is None or finished > newest):
            newest = finished
    return None, newest


def _last_commit_time(root: Path, ref: str, rel: str) -> datetime | None:
    result = _git(root, "log", "-1", "--format=%cI", ref, "--", rel)
    return _parse_time(result.stdout.strip()) if result.returncode == 0 else None


def _keep_reason(
    root: Path,
    job: str,
    *,
    ref: str,
    current: set[str],
    merged: set[str],
    cutoff: datetime,
    now: datetime,
) -> str | None:
    rel = f"{_JOBS_DIR}/{job}"
    folder = root / rel
    if job in current:
        return "the current ticket"
    if job not in merged:
        return f"not on `{ref}`"
    unfinished, finished = _runs_verdict(folder)
    if unfinished is not None:
        return unfinished
    if has_waiting(folder / ACTIVE_CONTEXT_FILE):
        return "open learnings or decisions in activeContext.md"
    finished = finished or _last_commit_time(root, ref, rel)
    if finished is None:
        return "no finish time found"
    if finished > cutoff:
        days = max((now - finished).days, 0)
        return f"finished {days} day{'' if days == 1 else 's'} ago"
    # The git calls last: only jobs that would otherwise go pay for them.
    if _git(root, "status", "--porcelain", "--untracked-files=all", "--", rel).stdout.strip():
        return "uncommitted changes"
    if not _git(root, "ls-files", "--", rel).stdout.strip():
        return "no files tracked by git"
    return None


def plan_prune(root: Path, older_than: timedelta, *, now: datetime | None = None) -> PrunePlan:
    """Work out which finished jobs under *root* to remove, changing nothing."""
    blocked = _blocked(root)
    if blocked is not None:
        return PrunePlan(blocked=blocked)
    ref = _base_ref(root)
    if ref is None:
        base = _read_config_value(root, "branch_base")
        return PrunePlan(blocked=f"`{base}` was not found locally or on `origin`.")
    jobs_root = root / _JOBS_DIR
    if not jobs_root.is_dir():
        return PrunePlan(blocked=None)

    now = now or datetime.now(UTC)
    cutoff = now - older_than
    current = _current_jobs(root)
    merged = _jobs_on(root, ref)
    removes: list[str] = []
    kept: list[tuple[str, str]] = []
    for folder in sorted(jobs_root.iterdir()):
        if not folder.is_dir():
            continue
        reason = _keep_reason(
            root,
            folder.name,
            ref=ref,
            current=current,
            merged=merged,
            cutoff=cutoff,
            now=now,
        )
        if reason is None:
            removes.append(folder.name)
        else:
            kept.append((folder.name, reason))
    return PrunePlan(blocked=None, removes=tuple(removes), kept=tuple(kept))


def prune_jobs(root: Path, older_than: timedelta, *, now: datetime | None = None) -> PrunePlan:
    """Remove the finished jobs :func:`plan_prune` picks, in one commit.

    The commit holds only the removals, even when other changes are staged.

    Raises:
        PruneError: ``git rm`` or ``git commit`` failed. The job folders are
            restored.
    """
    plan = plan_prune(root, older_than, now=now)
    if plan.blocked is not None or not plan.removes:
        return plan
    paths = [f"{_JOBS_DIR}/{job}" for job in plan.removes]
    removed = _git(root, "rm", "-r", "-q", "--", *paths)
    if removed.returncode != 0:
        _restore(root, paths)
        raise PruneError(f"git rm failed: {removed.stderr.strip()}")
    committed = _git(root, "commit", "-q", "-m", removal_message(len(paths)), "--", *paths)
    if committed.returncode != 0:
        _restore(root, paths)
        detail = (committed.stderr or committed.stdout).strip()
        raise PruneError(f"git commit failed: {detail}")
    # Ignored files (a local validation report, say) are not in git, so
    # `git rm` leaves them behind.
    for path in paths:
        shutil.rmtree(root / path, ignore_errors=True)
    sha = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    return PrunePlan(blocked=None, removes=plan.removes, kept=plan.kept, commit=sha)


def _restore(root: Path, paths: list[str]) -> None:
    _git(root, "reset", "-q", "HEAD", "--", *paths)
    _git(root, "checkout", "-q", "HEAD", "--", *paths)


def register_job_pruning_tools(app: FastMCP) -> None:
    """Register ``prune_jobs`` on *app*."""

    @app.tool(name="prune_jobs")
    async def prune_jobs_tool(
        ctx: Context,
        older_than_days: int = DEFAULT_OLDER_THAN_DAYS,
        dry_run: bool = False,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        """Remove finished job folders from ``.dmx/jobs/`` in one commit.

        Called by the ``prune-jobs`` step of the release loop, before
        ``create-pr``. A job is removed when all its loop runs finished, it
        is on ``branch_base``, and its newest run finished more than
        ``older_than_days`` ago. The current ticket, jobs with open
        learnings or decisions, and jobs with uncommitted changes are kept.
        The commit is "chore: remove N finished jobs" and holds only the
        removals. Refuses on ``branch_base``, ``production_branch`` and a
        detached HEAD.

        Args:
            older_than_days: Minimum age in days. Defaults to 30.
            dry_run: When true, change nothing and report the plan.
            workspace_root: Repo root path override. Auto-detected if omitted.

        Returns:
            ``blocked``, ``applied``, ``removes``, ``kept`` (each with a
            ``reason``), ``commit`` and ``commit_message``.
        """
        if older_than_days < 0:
            return PrunePlan(blocked="`older_than_days` can't be negative.").as_dict(applied=False)
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return PrunePlan(blocked=f"Could not resolve a valid workspace root: {exc}").as_dict(
                applied=False
            )
        older_than = timedelta(days=older_than_days)
        if dry_run:
            return plan_prune(root, older_than).as_dict(applied=False)
        try:
            plan = prune_jobs(root, older_than)
        except PruneError as exc:
            return PrunePlan(blocked=str(exc)).as_dict(applied=False)
        return plan.as_dict(applied=plan.blocked is None and bool(plan.removes))
