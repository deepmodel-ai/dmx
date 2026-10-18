"""Loop runtime MCP tools: run_loop, loop_advance, loop_continue, get_skill_definition, list_skills.

These three tools expose the dmx loop runtime through the existing MCP server.
The agent is always the executor — dmx never runs skills directly.  Each tool
returns a plain-English instruction that the agent follows.

Tool contracts
--------------

``run_loop(name)``
    - Reads ``.dmx/loops/{name}.yaml`` (app repo first, bundled fallback).
    - If the loop declares ``require_branch: base``, rejects starting it
      from any branch other than the configured integration branch, and
      generates a temporary job id instead of resolving one — see
      ``_start_loop`` for why.
    - Otherwise generates job_id (ticket ID or branch) and task_id (UUID4).
    - Writes initial state to ``.dmx/jobs/{job_id}/{name}-{task_id}.json``.
    - Returns: instruction to run the first skill, then call ``loop_advance``.

``loop_advance(output, skill)``
    - Finds the active run by scanning ``.dmx/jobs/`` (no separate pointer
      file — see ``dmx.loop_state`` module docstring).
    - Ignores the call when ``skill`` is not the run's current skill, so a
      retry cannot record the same output against the next skill. Skill
      names in a loop are matched by name and must be unique.
    - Persists the skill output.
    - If more skills remain and ``human_gate: true``: pauses, returns pause msg.
    - If more skills remain and ``human_gate: false``: returns next instruction.
    - If all skills complete: marks the run ``validating`` and runs validators
      on a worker thread. The tool returns immediately. ``loop_status``
      waits for the outcome. A ``validating`` run with no live worker was
      interrupted; ``loop_continue`` runs the validators again.

``loop_status()``
    - Read-only, except one adoption: a single in-progress run left in
      ``.dmx/jobs/none/`` or ``unknown/`` by dmx 0.4.2 is moved into the
      branch folder. A file already committed on ``branch_base`` or its
      upstream stays put.
    - Waits up to 25 seconds for a live validator worker and returns as soon
      as it finishes. If the worker is still going, reports that. If the run
      is ``validating`` but this process has no worker, tells the agent to
      call ``loop_continue``.

``loop_continue()``
    - Finds the active run the same way as ``loop_advance``.
    - Advances ``current_skill_index`` past the last completed skill.
    - Returns the next skill instruction.

``list_skills()``
    - Read-only. Lists local and shared skills by the name
      ``get_skill_definition`` accepts, with the description and the source
      that won. Bundled skills are omitted.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import importlib.resources as pkg
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter
from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.component_config import (
    component_dirs,
    component_skill_catalog,
    component_validators,
    expand_reported_checks,
    scope_refusal,
    split_component_name,
    ticket_component_ids,
)
from dmx.components import has_components
from dmx.exceptions import AmbiguousActiveRun, WorkspaceRootInvalid
from dmx.ide.rules_refresh import rules_reminder
from dmx.loop_memory import append_session_note, read_memory_context
from dmx.loop_schema import LoopConfig, RequireBranch, load_loop, load_loops_dir
from dmx.loop_state import (
    ACTIVE_CONTEXT_FILE,
    SPEC_FILE,
    TASKS_FILE,
    TICKET_FILES,
    LoopOutcome,
    LoopStatus,
    _now_iso,
    current_branch,
    find_active_run,
    find_pending_run,
    find_pr_snapshot_run,
    is_pending_job_id,
    is_pr_snapshot,
    job_has_loop_runs,
    jobs_for_branch,
    list_open_runs,
    make_pending_job_id,
    make_task_id,
    move_one_run,
    read_spec_identity,
    read_state,
    rename_job,
    resolve_job_id,
    root_ticket_file,
    root_ticket_files_belong_to,
    spec_identity_in_text,
    supersede_pr_snapshots,
    ticket_file,
    usable_ticket,
    write_initial_state,
    write_state,
)
from dmx.repeat_until import evaluate_repeat_until
from dmx.rework import (
    ReworkCheck,
    append_rework_phase,
    failed_required_checks,
    not_reworkable_text,
    rework_line,
    rework_tasks,
    unchecked_in_phase,
)
from dmx.shared_sources import SharedSourceError, read_shared_sources, source_root
from dmx.ticket_components import current_job, with_components_line
from dmx.validator_runner import evaluate_validator_results, run_validators
from dmx.workspace import ide_folder, resolve_workspace_root, with_skipped_dmx_note

__all__ = ["register_loop_tools"]

logger = logging.getLogger(__name__)

# Set when a 0.4.2 run is moved into the branch job folder during this call.
_LEGACY_MOVE_NOTE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dmx_legacy_job_move", default=None
)


# Set when a 0.5 repo's root ticket files move into the job folder (GH-84).
_TICKET_FILES_MOVE_NOTE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dmx_ticket_files_move", default=None
)
# Set when a pending job folder is renamed to its real id (GH-121).
_JOB_PROMOTED_NOTE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dmx_job_promoted", default=None
)
# Set when a merge had filed this branch's ticket files under another ticket.
_MISPLACED_FILES_NOTE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dmx_misplaced_ticket_files", default=None
)


def _with_legacy_note(message: str) -> str:
    """Append the job-move and ticket-file lines once, then clear them."""
    for var in (
        _LEGACY_MOVE_NOTE,
        _MISPLACED_FILES_NOTE,
        _TICKET_FILES_MOVE_NOTE,
        _JOB_PROMOTED_NOTE,
    ):
        note = var.get()
        if not note:
            continue
        var.set(None)
        if note not in message:
            message = f"{message}\n\n{note}"
    return message


def _with_rules_reminder(root: Path, message: str) -> str:
    """Append the stale-rules line once. A read error leaves *message* as it is.

    The rule files are read where the IDE opened, which is where
    ``setup_ide_rules`` writes them, not the repo root a subfolder resolved to.
    """
    try:
        reminder = rules_reminder(ide_folder() or root)
    except OSError:
        return message
    if not reminder or reminder in message:
        return message
    return f"{message}\n\n{reminder}"


# ---------------------------------------------------------------------------
# Bundled loops directory
# ---------------------------------------------------------------------------


def _bundled_loops_dir() -> Path:
    return Path(str(pkg.files("dmx") / "loops"))


def _bundled_skills_dir() -> Path:
    return Path(str(pkg.files("dmx") / "skills"))


# `name` (and the shared-source config's own `name`/`subdir` fields — see
# shared_sources._NAME_RE) becomes a literal path segment below `.dmx/skills/`,
# a shared source's `skills/`, or the bundled skills dir. Restricting it to a
# plain slug (no `/`, no `..`, no leading `-`) rules out escaping those
# directories via a `../`-laden or absolute skill name passed to
# `get_skill_definition` — see GH-40's review for the concrete reproduction.
_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$")


@dataclass(frozen=True)
class ResolvedSkill:
    """A skill found by :func:`_resolve_skill`.

    Attributes:
        raw: Full file content, including frontmatter — the caller decides
            whether/how to strip it.
        root_path: Workspace-relative path to the skill's own directory,
            set only for the folder-shaped ``{name}/SKILL.md`` form found in
            a shared source (GH-27 phase 4). ``None`` for every other case
            (dmx's own flat ``{name}.md``, wherever it's found) — a flat
            skill has no directory of its own for ``scripts/``/
            ``references/``/``assets/`` to resolve against, so there's
            nothing for the agent to need this for.
    """

    raw: str
    root_path: str | None = None


def _find_skill_in_dir(
    skills_dir: Path,
    candidates: list[str],
    workspace_root: Path,
    *,
    recursive: bool,
) -> ResolvedSkill | None:
    """Look up *candidates* under *skills_dir*: dmx's flat ``{name}.md``
    first, then the folder-shaped ``{name}/SKILL.md`` convention (the
    agentskills.io / Claude Code ecosystem shape, with optional
    ``scripts/``/``references/``/``assets/`` — see GH-27 phase 4) as a
    fallback. Checked in this order for every candidate before moving on —
    flat always wins over folder-shaped for the same logical name.

    Args:
        recursive: ``True`` to search anywhere under *skills_dir* (needed
            for the bundled skills directory, which nests skills under
            category subdirectories, e.g. ``workflow/0-init/dmx-init.md``).
            ``False`` to only look directly inside *skills_dir* (the flat,
            single-level convention used by ``.dmx/skills/`` and every
            shared source's ``skills/`` directory).
    """
    for candidate in candidates:
        if recursive:
            matches = list(skills_dir.rglob(f"{candidate}.md"))
            if matches:
                return ResolvedSkill(raw=matches[0].read_text(encoding="utf-8"))
        else:
            path = skills_dir / f"{candidate}.md"
            if path.exists():
                return ResolvedSkill(raw=path.read_text(encoding="utf-8"))

    for candidate in candidates:
        if recursive:
            matches = list(skills_dir.rglob(f"{candidate}/SKILL.md"))
            if not matches:
                continue
            path = matches[0]
            skill_dir = path.parent
        else:
            skill_dir = skills_dir / candidate
            path = skill_dir / "SKILL.md"
            if not path.exists():
                continue
        try:
            root_path = skill_dir.relative_to(workspace_root).as_posix()
        except ValueError:
            # Outside workspace_root — only possible for the bundled
            # skills directory (installed with the package, not vendored
            # into the workspace). Fall back to an absolute path so the
            # agent still has something resolvable.
            root_path = skill_dir.as_posix()
        return ResolvedSkill(raw=path.read_text(encoding="utf-8"), root_path=root_path)

    return None


def _resolve_skill(name: str, workspace_root: Path) -> ResolvedSkill | None:
    """Find a skill by name.

    Search order — each tier tries the flat ``{name}.md``/``dmx-{name}.md``
    form first, then the folder-shaped ``{name}/SKILL.md`` form as a
    fallback (see :func:`_find_skill_in_dir`):

    1. ``{workspace_root}/.dmx/skills/`` (project-specific override)
    2. For each declared ``shared_sources`` entry, in declared order:
       ``.dmx/vendor/{source}/skills/``
    3. The bundled ``skills/`` directory shipped with dmx

    ``root_path`` is set on the result whenever the folder-shaped form
    matched, regardless of which tier — a skill needs it any time it has
    its own directory for ``scripts/``/``references/``/``assets/`` to
    resolve against, not just when it came from a shared source.

    Returns ``None`` if the skill is not found in any location, or if
    *name* isn't a plain slug (see ``_SKILL_NAME_RE``) — a `/`, `..`, or
    absolute-path-shaped name is never a real skill, only ever a path
    traversal attempt, so it's rejected before touching the filesystem.
    """
    split = split_component_name(name)
    if split is not None:
        component_id, item = split
        for skills_dir in component_dirs(workspace_root, component_id, "skills"):
            resolved = _find_skill_in_dir(
                skills_dir, [item, f"dmx-{item}"], workspace_root, recursive=False
            )
            if resolved is not None:
                return resolved
        return None
    if not _SKILL_NAME_RE.match(name):
        return None

    candidates = [name, f"dmx-{name}"]

    project_skills = workspace_root / ".dmx" / "skills"
    resolved = _find_skill_in_dir(project_skills, candidates, workspace_root, recursive=False)
    if resolved is not None:
        return resolved

    for source in read_shared_sources(workspace_root):
        source_skills = source_root(workspace_root, source) / "skills"
        resolved = _find_skill_in_dir(source_skills, candidates, workspace_root, recursive=False)
        if resolved is not None:
            return resolved

    bundled = _bundled_skills_dir()
    return _find_skill_in_dir(bundled, candidates, workspace_root, recursive=True)


def list_skills(workspace_root: Path) -> str:
    """List local and shared skills the loader would run.

    One line per logical name, sorted by name:
    ``name (source): description``. ``source`` is ``app repo`` or the
    shared-source name. The description is last, so a description that
    contains `` — `` stays one field. The first tier that has the name
    wins, using the same flat-then-folder and ``dmx-`` prefix rules as
    :func:`_resolve_skill`. Bundled skills are omitted. A name that also
    exists as a bundled skill is marked as an override. A missing
    description is empty.

    This is not the ``dmx list-skills`` CLI command, which prints bundled
    skills only.

    Returns the shared-sources error string when ``.dmx/shared-sources.yaml``
    is malformed, and one line when nothing is listed.
    """
    try:
        entries = _skill_catalog(workspace_root)
    except SharedSourceError as exc:
        return f"Error reading .dmx/shared-sources.yaml: {exc}"
    if has_components(workspace_root):
        ids = ticket_component_ids(workspace_root, current_job(workspace_root))
        entries = component_skill_catalog(workspace_root, ids) + entries
    if not entries:
        return "No local or shared skills."
    bundled = _bundled_logical_names()
    lines = [
        _skill_line(name, description, source, bundled)
        for name, description, source, _path in entries
    ]
    return "\n".join(lines)


def _skill_line(name: str, description: str, source: str, bundled: set[str]) -> str:
    note = ""
    if name in bundled:
        note = f" (overrides bundled /dmx/{name} for loops and get_skill_definition)"
    if description:
        return f"{name} ({source}){note}: {description}"
    return f"{name} ({source}){note}:"


def _skill_file(skills_dir: Path, name: str) -> Path | None:
    """The file :func:`_find_skill_in_dir` reads for *name*, flat form first."""
    for candidate in (name, f"dmx-{name}"):
        if (skills_dir / f"{candidate}.md").exists():
            return skills_dir / f"{candidate}.md"
    for candidate in (name, f"dmx-{name}"):
        if (skills_dir / candidate / "SKILL.md").exists():
            return skills_dir / candidate / "SKILL.md"
    return None


def _skill_catalog(workspace_root: Path) -> list[tuple[str, str, str, Path | None]]:
    """``(name, description, source, file)`` for each local and shared skill."""
    # sync_runner imports this module at load time, so this import stays here.
    from dmx.sync_runner import _skill_names

    tiers: list[tuple[str, Path]] = [("app repo", workspace_root / ".dmx" / "skills")]
    for source in read_shared_sources(workspace_root):
        tiers.append((source.name, source_root(workspace_root, source) / "skills"))

    seen: set[str] = set()
    found: list[tuple[str, str, str, Path | None]] = []
    for source_label, skills_dir in tiers:
        for name in _skill_names(skills_dir):
            if name in seen or not _SKILL_NAME_RE.match(name):
                continue
            resolved = _find_skill_in_dir(
                skills_dir,
                [name, f"dmx-{name}"],
                workspace_root,
                recursive=False,
            )
            if resolved is None:
                continue
            seen.add(name)
            found.append(
                (
                    name,
                    _frontmatter_description(resolved.raw),
                    source_label,
                    _skill_file(skills_dir, name),
                )
            )
    found.sort(key=lambda item: item[0])
    return found


def _bundled_logical_names() -> set[str]:
    """Logical names of bundled skills.

    The bundled tree nests skills under category directories, so this walks
    those directories. Within each one it uses the same rules as
    ``sync_runner._skill_names``: a flat ``{name}.md`` or ``dmx-{name}.md``,
    or a ``{name}/SKILL.md`` folder, collapsed to one logical name.
    """
    names: set[str] = set()

    def walk(directory: Path) -> None:
        if not directory.is_dir():
            return
        for entry in directory.iterdir():
            if entry.is_file() and entry.suffix == ".md":
                logical = entry.stem.removeprefix("dmx-")
            elif entry.is_dir() and (entry / "SKILL.md").is_file():
                logical = entry.name.removeprefix("dmx-")
            elif entry.is_dir():
                walk(entry)
                continue
            else:
                continue
            if _SKILL_NAME_RE.match(logical):
                names.add(logical)

    walk(_bundled_skills_dir())
    return names


def _frontmatter_description(raw: str) -> str:
    try:
        post = frontmatter.loads(raw)
    except Exception:  # noqa: BLE001 — a bad header still leaves the skill findable by name
        return ""
    description = post.metadata.get("description", "")
    if description is None:
        return ""
    return " ".join(str(description).split())


def _dependencies_note(raw: str) -> str | None:
    """Return a one-line note if *raw*'s frontmatter declares ``dependencies``.

    Folder-shaped skills (GH-27 phase 4) may declare packages their
    ``scripts/`` need in frontmatter. dmx has no auto-install mechanism of
    its own, so this surfaces the declaration as an explicit, agent-visible
    step rather than silently dropping it the way :func:`_strip_frontmatter`
    otherwise would — see "Resolving `{name}/SKILL.md`'s bundled resources"
    in GH-27.
    """
    try:
        post = frontmatter.loads(raw)
    except Exception:  # noqa: BLE001 — malformed frontmatter isn't this helper's problem
        return None
    deps = post.metadata.get("dependencies")
    if not deps:
        return None
    deps_str = ", ".join(str(d) for d in deps) if isinstance(deps, list) else str(deps)
    return (
        f"Declared dependencies (not auto-installed — install before running any "
        f"scripts/): {deps_str}"
    )


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter (the leading ``---`` block) from a skill file."""
    stripped = content.strip()
    if not stripped.startswith("---"):
        return stripped
    end = stripped.find("\n---", 3)
    if end == -1:
        return stripped
    return stripped[end + 4 :].lstrip("\n")


# ---------------------------------------------------------------------------
# Branch guard (require_branch: base)
# ---------------------------------------------------------------------------

_CONFIG_VALUE_RE = re.compile(r"^([A-Za-z0-9_]+)\s*:\s*([^\s#]+)", re.MULTILINE)


def _read_config_value(workspace_root: Path, key: str) -> str | None:
    """Read one ``key: value`` line from ``.dmx/config.md``, or None.

    Mirrors the "fall back to reading .dmx/config.md" convention every
    skill uses when project config isn't injected into agent context —
    this runs in the deterministic MCP tool layer, which never has agent
    context, so config.md is the only source available here.
    """
    config_path = workspace_root / ".dmx" / "config.md"
    if not config_path.exists():
        return None
    for match in _CONFIG_VALUE_RE.finditer(config_path.read_text(encoding="utf-8")):
        if match.group(1) != key:
            continue
        value = match.group(2).strip()
        return value if value and value != "{REQUIRED}" else None
    return None


def _read_branch_base(workspace_root: Path) -> str | None:
    """Read ``branch_base`` from ``.dmx/config.md``, or None if unavailable."""
    return _read_config_value(workspace_root, "branch_base")


def _on_protected_branch(root: Path) -> bool:
    """True when HEAD is ``branch_base`` or ``production_branch``.

    Those branches receive loop state through a merged PR. A direct commit
    here would land the run's bookkeeping without review.
    """
    branch = current_branch(root)
    if not branch:
        return False
    protected = {
        value
        for value in (
            _read_config_value(root, "branch_base"),
            _read_config_value(root, "production_branch"),
        )
        if value
    }
    return branch in protected


def _repo_has_no_commits(root: Path) -> bool:
    """True if *root* is a git repo with an unborn HEAD (zero commits).

    ``git rev-parse --abbrev-ref HEAD`` — what :func:`current_branch` uses —
    fails on a freshly ``git init``'d repo before its first commit, even
    though the branch name (e.g. ``main``/``master``) is perfectly
    resolvable via ``git symbolic-ref``. This distinguishes that specific,
    fixable case (make a commit) from other reasons ``current_branch``
    might return ``None`` (not a git repo at all, detached HEAD, etc.),
    which don't have an actionable one-line fix.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=root,
        )
    except Exception:  # noqa: BLE001
        return False
    if result.returncode != 0 or result.stdout.strip() != "true":
        return False  # not a git repo at all — a different problem

    try:
        verify = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
        )
    except Exception:  # noqa: BLE001
        return False
    return verify.returncode != 0


def _branch_guard_error(root: Path, config: LoopConfig) -> str | None:
    """Return an error message if *config* declares ``require_branch`` and
    the current branch doesn't satisfy it, else None."""
    if config.require_branch != RequireBranch.base:
        return None

    branch_base = _read_branch_base(root)
    if branch_base is None:
        return (
            f"Cannot start the `{config.name}` loop: could not determine the integration "
            "branch (`branch_base`) from `.dmx/config.md`. Run `/dmx/init` to configure "
            "this project."
        )

    branch = current_branch(root)
    if branch is None:
        if _repo_has_no_commits(root):
            return (
                f"Cannot start the `{config.name}` loop: this repository has no commits yet. "
                f"`{config.name}` creates a new branch from `{branch_base}` on GitHub, which "
                "requires at least one commit to exist first. Commit something (e.g. the "
                "`.dmx/` files `/dmx/init` just wrote) and push to origin, then try again."
            )
        return (
            f"Cannot start the `{config.name}` loop: could not determine the current git "
            f"branch. Make sure you're in a git repository checked out to `{branch_base}`."
        )
    if branch != branch_base:
        return (
            f"Cannot start the `{config.name}` loop from `{branch}` — it must be started "
            f"from `{branch_base}` (the configured integration branch), since this loop "
            "establishes a new ticket and branch. Switch back with "
            f"`git checkout {branch_base}` and run `run_loop` again."
        )
    return None


# ---------------------------------------------------------------------------
# Active-run lookup (no separate pointer file — see dmx.loop_state)
# ---------------------------------------------------------------------------


def _find_active(root: Path) -> tuple[str, str, str] | None:
    """Find the currently active (non-terminal) loop run for this workspace.

    Resolves job_id from the current branch/spec.md as normal and looks for
    a non-terminal state file there. Falls back to scanning temp/pending
    job folders if none is found — covers the window where a loop that
    establishes a new ticket identity (``require_branch``) has started but
    hasn't yet completed the skill that makes its real job_id resolvable.

    When that still finds nothing, one in-progress run left in
    ``.dmx/jobs/none/`` or ``.dmx/jobs/unknown/`` by dmx 0.4.2 is moved into
    the branch folder, unless that file is already committed on
    ``branch_base`` or its upstream. See :func:`_adopt_legacy_job`.

    Returns:
        ``(job_id, loop_name, task_id)``, or ``None`` if nothing is active.
    """
    job_id = resolve_job_id(root)
    found = _lookup_job_run(root, job_id)
    if found is not None:
        loop_name, task_id = found
        return job_id, loop_name, task_id
    pending = find_pending_run(root)
    if pending is not None:
        return pending
    adopted = _adopt_legacy_job(root, job_id)
    if adopted is None:
        return None
    loop_name, task_id = adopted
    return job_id, loop_name, task_id


def _lookup_job_run(root: Path, job_id: str) -> tuple[str, str] | None:
    """Non-terminal run under *job_id*, or a PR snapshot off a protected branch."""
    active = find_active_run(root, job_id)
    if active is not None:
        return active
    # A PR snapshot is ``complete`` so the merged file is not a live loop.
    # On the feature branch it still needs ``loop_continue`` to run
    # validators. On the integration branch it is history.
    if _on_protected_branch(root):
        return None
    return find_pr_snapshot_run(root, job_id)


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run git in *root*. ``None`` when the command cannot be run."""
    try:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _ref_exists(root: Path, ref: str) -> bool | None:
    """Whether *ref* resolves. ``None`` when git cannot answer."""
    result = _run_git(root, "rev-parse", "--verify", "--quiet", ref)
    if result is None:
        return None
    return result.returncode == 0


def _merged_history_refs(root: Path, branch_base: str) -> list[str] | None:
    """Local *branch_base* plus its upstream, or ``origin/<branch_base>``.

    ``None`` when git cannot answer. An empty list means none of those refs
    exist. ``create-ticket`` branches from the remote, and the local
    integration branch is often left behind.
    """
    refs: list[str] = []
    local = _ref_exists(root, branch_base)
    if local is None:
        return None
    if local:
        refs.append(branch_base)

    upstream = _run_git(root, "rev-parse", "--abbrev-ref", f"{branch_base}@{{upstream}}")
    if upstream is None:
        return None
    remote = upstream.stdout.strip() if upstream.returncode == 0 else ""
    if not remote or remote == branch_base:
        fallback = f"origin/{branch_base}"
        exists = _ref_exists(root, fallback)
        if exists is None:
            return None
        remote = fallback if exists else ""
    if remote and remote not in refs:
        refs.append(remote)
    return refs


def _committed_legacy_paths(root: Path, ticket: str) -> set[str] | None:
    """Paths under ``.dmx/jobs/<ticket>/`` that are merged history.

    Merged history is the union of the local ``branch_base`` and its
    upstream (``origin/<branch_base>`` when no upstream is set). ``None``
    when git cannot answer or none of those refs exist: callers then adopt
    nothing. A ref that exists but cannot be listed also adopts nothing.
    An in-progress run is absent from all of them.
    """
    branch_base = _read_branch_base(root)
    if not branch_base:
        return None
    refs = _merged_history_refs(root, branch_base)
    if not refs:
        return None
    found: set[str] = set()
    prefix = f".dmx/jobs/{ticket}"
    for ref in refs:
        result = _run_git(root, "ls-tree", "-r", "--name-only", ref, "--", prefix)
        if result is None or result.returncode != 0:
            return None
        found.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return found


def _adopt_legacy_job(root: Path, job_id: str) -> tuple[str, str] | None:
    """Move one 0.4.2 run from ``none`` or ``unknown`` into *job_id*.

    The raw ``ticket`` must be a value :func:`usable_ticket` rejects, and
    the spec ``branch`` must be the current branch. Completed and failed
    runs stay in the old folder. A run whose state file is already committed
    on ``branch_base`` or its upstream stays too: before job ids changed, a
    release merged to the integration branch could still be marked
    ``running``. Only one remaining candidate is moved. A PR snapshot is a
    candidate only off a protected branch. If git cannot say what those refs
    contain, nothing is moved.

    Sets :data:`_LEGACY_MOVE_NOTE` when a file moves.
    """
    ticket, spec_branch = read_spec_identity(root)
    branch = current_branch(root)
    if not ticket or not branch or not spec_branch or spec_branch != branch:
        return None
    if usable_ticket(ticket) is not None or ticket == job_id:
        return None
    if ticket.lower() not in {"none", "unknown"}:
        return None
    try:
        if find_active_run(root, job_id) is not None:
            return None
    except AmbiguousActiveRun:
        return None
    committed = _committed_legacy_paths(root, ticket)
    if committed is None:
        return None
    candidates = [
        (loop_name, task_id)
        for loop_name, task_id in list_open_runs(
            root, ticket, include_snapshots=not _on_protected_branch(root)
        )
        if f".dmx/jobs/{ticket}/{loop_name}-{task_id}.json" not in committed
    ]
    if len(candidates) != 1:
        return None
    loop_name, task_id = candidates[0]
    if not move_one_run(root, ticket, job_id, loop_name, task_id):
        return None
    logger.info(
        "moved in-progress %s run from jobs/%s to jobs/%s",
        loop_name,
        ticket,
        job_id,
    )
    _LEGACY_MOVE_NOTE.set(
        f"Moved the in-progress `{loop_name}` run from `.dmx/jobs/{ticket}/` "
        f"to `.dmx/jobs/{job_id}/` (job ids changed in 0.5.0)."
    )
    return loop_name, task_id


class PendingJobPromotionError(Exception):
    """A pending job must not be renamed into the target folder."""


def _maybe_promote_pending_job(root: Path, job_id: str) -> str:
    """Rename a temp/pending job folder to its real id once resolvable.

    Called right after a skill completes (``loop_advance``) or a paused
    loop resumes (``loop_continue``) — the natural points where an
    identity-creating skill (e.g. ``create-ticket``) may have just made the
    real ticket/branch resolvable. A no-op once the job is already real, or
    if the real identity still isn't resolvable yet.

    The real identity is the frontmatter ``ticket`` when it is a real ticket
    id and its ``branch`` matches the current branch. ``none``, ``unknown``,
    and an empty ticket are not ids: when the frontmatter ``branch`` matches,
    the job is promoted to that branch name. A stale ``spec.md`` (previous
    ticket, or a ``branch`` that does not match) leaves the pending folder
    in place so the next call can retry.

    ``branch_base`` means ``create-ticket`` hasn't actually switched to the
    new feature branch yet.

    Raises:
        PendingJobPromotionError: The target job directory already holds
            loop runs. The pending folder is left unchanged.
    """
    if not is_pending_job_id(job_id):
        return job_id
    ticket, spec_branch = read_spec_identity(root, job_id)
    # A create-ticket skill written for 0.5 still writes the root spec.md.
    from_root = spec_branch is None
    if from_root:
        ticket, spec_branch = read_spec_identity(root)
    branch = current_branch(root)
    if not branch or not spec_branch or spec_branch != branch:
        return job_id
    real_job_id = usable_ticket(ticket) or branch
    if (
        is_pending_job_id(real_job_id)
        or real_job_id == "unknown"
        or real_job_id == _read_branch_base(root)
    ):
        return job_id
    if job_has_loop_runs(root, real_job_id):
        raise PendingJobPromotionError(
            f"Cannot promote `{job_id}` into `{real_job_id}`: "
            f".dmx/jobs/{real_job_id}/ already has loop runs. "
            "If `.dmx/spec.md` names the wrong ticket, update the frontmatter and retry. "
            f"If `{real_job_id}` was reopened, move or delete "
            f"`.dmx/jobs/{real_job_id}/` and retry."
        )
    rename_job(root, job_id, real_job_id)
    logger.info("promoted pending job %s -> %s", job_id, real_job_id)
    _JOB_PROMOTED_NOTE.set(
        f"The job folder moved from `.dmx/jobs/{job_id}/` to `.dmx/jobs/{real_job_id}/`. "
        f"Edit the ticket files there from now on."
    )
    if from_root:
        _move_root_ticket_files(root, real_job_id)
    return real_job_id


def _move_root_ticket_files(root: Path, job_id: str) -> list[str]:
    """Move a 0.5 repo's root ticket files into *job_id*'s folder (GH-84).

    Only when the root files belong to *job_id*: the root ``.dmx/spec.md``
    names the current branch and its ticket (or that branch) is *job_id*;
    or there is no root ``spec.md`` and *job_id*'s own ``spec.md`` names
    the current branch, so a root ``tasks.md`` or ``activeContext.md``
    written by a skill made for 0.5 is this ticket's. A stale root spec
    from an earlier ticket stays for ``/dmx/upgrade``. A file the job folder
    already has is not overwritten. Called from the tools that write anyway
    (``run_loop``, ``loop_advance``, ``loop_continue``), never from a
    read-only call. Sets :data:`_TICKET_FILES_MOVE_NOTE` when something
    moved.

    Returns:
        The names of the files moved.
    """
    if not root_ticket_files_belong_to(root, job_id):
        return []
    moved: list[str] = []
    for name in TICKET_FILES:
        source = root_ticket_file(root, name)
        target = ticket_file(root, job_id, name)
        if not source.is_file() or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        moved.append(name)
    if moved:
        listed = ", ".join(f"`{name}`" for name in moved)
        logger.info("moved root ticket files %s into jobs/%s", moved, job_id)
        _TICKET_FILES_MOVE_NOTE.set(
            f"Moved {listed} from `.dmx/` into `.dmx/jobs/{job_id}/` "
            "(ticket files live in the job folder since 0.6)."
        )
    return moved


def _file_on_ref(root: Path, ref: str, rel: str) -> str | None:
    """Text of *rel* at *ref*, or None when git cannot show it."""
    shown = _run_git(root, "show", f"{ref}:{rel}")
    if shown is None or shown.returncode != 0:
        return None
    return shown.stdout


def _base_ticket_file(root: Path, base: str, job_id: str, name: str) -> str | None:
    rel = f".dmx/jobs/{job_id}/{name}"
    for ref in (base, f"origin/{base}"):
        text = _file_on_ref(root, ref, rel)
        if text is not None:
            return text
    return None


def _repair_misplaced_ticket_files(root: Path) -> None:
    """Put this branch's ticket files back where a merge moved them (GH-84).

    When the integration branch moved a merged ticket's root files into that
    ticket's job folder, git treats it as a rename. A branch still on the
    0.5 layout that merges or rebases onto it gets its own root edits applied
    to the renamed files, so its ``spec.md`` lands in the other ticket's
    folder with no conflict reported.

    Detected as a job folder whose ``spec.md`` names the current branch but
    belongs to another ticket (its ticket or branch is not the folder name),
    where the same folder on ``branch_base`` names a different branch. The
    branch's changed files move into its own job folder, and the other
    ticket's files are restored from ``branch_base``. Called before the
    writing tools look up the active run; never on a protected branch. Sets
    :data:`_MISPLACED_FILES_NOTE` when something moved.
    """
    branch = current_branch(root)
    base = _read_branch_base(root)
    if not branch or not base or _on_protected_branch(root):
        return
    for job_id in jobs_for_branch(root, branch):
        ticket, _spec_branch = read_spec_identity(root, job_id)
        own_job = usable_ticket(ticket) or branch
        if own_job == job_id:
            continue
        base_spec = _base_ticket_file(root, base, job_id, SPEC_FILE)
        if base_spec is None:
            continue
        _base_ticket, base_branch = spec_identity_in_text(base_spec)
        if base_branch in (None, branch):
            continue
        moved: list[str] = []
        left: list[str] = []
        for name in TICKET_FILES:
            here = ticket_file(root, job_id, name)
            original = _base_ticket_file(root, base, job_id, name)
            if here.is_file() and (text := here.read_text(encoding="utf-8")) != original:
                target = ticket_file(root, own_job, name)
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    here.replace(target)
                    moved.append(name)
                elif target.read_text(encoding="utf-8") == text:
                    here.unlink()
                    moved.append(name)
                else:
                    left.append(name)
                    continue
            if original is not None and not here.exists():
                here.parent.mkdir(parents=True, exist_ok=True)
                here.write_text(original, encoding="utf-8")
        if moved or left:
            logger.info("misplaced ticket files in jobs/%s: moved %s, left %s", job_id, moved, left)
            parts = []
            if moved:
                listed = ", ".join(f"`{name}`" for name in moved)
                parts.append(
                    f"A merge had put this branch's {listed} in `.dmx/jobs/{job_id}/`. "
                    f"Moved them to `.dmx/jobs/{own_job}/` and restored `.dmx/jobs/{job_id}/` "
                    f"from `{base}`. Commit both folders."
                )
            if left:
                listed = ", ".join(f"`{name}`" for name in left)
                parts.append(
                    f"`.dmx/jobs/{job_id}/` also has this branch's {listed}, but "
                    f"`.dmx/jobs/{own_job}/` already has different ones. Merge them into "
                    f"`.dmx/jobs/{own_job}/` by hand and restore `.dmx/jobs/{job_id}/` "
                    f"from `{base}`."
                )
            _MISPLACED_FILES_NOTE.set(" ".join(parts))


# ---------------------------------------------------------------------------
# Loop config resolution
# ---------------------------------------------------------------------------


def _resolve_loop(name: str, workspace_root: Path) -> LoopConfig:
    """Load a loop config: app repo, then declared ``shared_sources`` in
    order, then bundled fallback.

    Args:
        name: Loop name (must match filename stem).
        workspace_root: Repo root.

    Returns:
        Validated :class:`LoopConfig`.

    Raises:
        FileNotFoundError: If the loop is not found in any location.
    """
    split = split_component_name(name)
    if split is not None:
        component_id, item = split
        for loops_dir in component_dirs(workspace_root, component_id, "loops"):
            path = loops_dir / f"{item}.yaml"
            if path.exists():
                return load_loop(path)
        raise FileNotFoundError(
            f"Loop '{name}' not found in .dmx/components/{component_id}/loops/."
        )

    app_path = workspace_root / ".dmx" / "loops" / f"{name}.yaml"
    if app_path.exists():
        return load_loop(app_path)

    shared_sources = read_shared_sources(workspace_root)
    for source in shared_sources:
        source_path = source_root(workspace_root, source) / "loops" / f"{name}.yaml"
        if source_path.exists():
            return load_loop(source_path)

    bundled_path = _bundled_loops_dir() / f"{name}.yaml"
    if bundled_path.exists():
        return load_loop(bundled_path)

    # List available loops for a helpful error.
    app_loops = load_loops_dir(workspace_root / ".dmx" / "loops")
    shared_loops: set[str] = set()
    for source in shared_sources:
        shared_loops |= set(load_loops_dir(source_root(workspace_root, source) / "loops"))
    bundled_loops = load_loops_dir(_bundled_loops_dir())
    available = sorted(set(app_loops) | shared_loops | set(bundled_loops))
    raise FileNotFoundError(f"Loop '{name}' not found. Available loops: {available or ['(none)']}")


# ---------------------------------------------------------------------------
# Skill instruction builder
# ---------------------------------------------------------------------------


def _skill_instruction(
    skill_name: str,
    loop_name: str,
    index: int,
    total: int,
    job_id: str,
    task_id: str,
    description: str | None = None,
) -> str:
    """Return the agent instruction for running a single skill.

    Always carries ``job_id``/``task_id`` — skills that persist ticket-scoped
    artifacts (e.g. ``validate`` writing ``.dmx/jobs/{job_id}/validation-report.json``)
    need this to know where to write without re-deriving it themselves.
    """
    short_task = task_id[:8]
    desc_block = (
        f"\nContext for this skill: {description.strip().splitlines()[0]}\n"
        if description and index == 0
        else ""
    )
    return (
        f"LOOP RUNTIME — do not suggest workflow commands or alternative paths.\n\n"
        f"Next skill: `{skill_name}` (Loop: {loop_name} | Skill {index + 1}/{total}) | "
        f"Job: `{job_id}` | Task: `{short_task}`{desc_block}\n"
        f"Ticket files: `.dmx/jobs/{job_id}/{SPEC_FILE}`, `{TASKS_FILE}`, "
        f"`{ACTIVE_CONTEXT_FILE}` (in that folder).\n\n"
        f"REQUIRED: Call `get_skill_definition` with name=`{skill_name}` to fetch the skill "
        f"instructions, then execute them exactly as written.\n\n"
        f"REQUIRED: When the skill finishes, you MUST immediately call the "
        f"`loop_advance` MCP tool with `skill`=`{skill_name}` and the skill's full "
        f"output as the `output` argument. "
        f"Do not wait for user input. Do not suggest next steps. Call loop_advance."
    )


def _pause_message(
    loop_name: str,
    job_id: str,
    task_id: str,
    completed: int,
    total: int,
) -> str:
    """Return the human-gate pause message shown to the developer."""
    short_task = task_id[:8]
    return (
        f"**{loop_name} loop — paused** ✋\n\n"
        f"Job: `{job_id}` | Task: `{short_task}` | "
        f"Progress: {completed}/{total} skills complete\n\n"
        f"HUMAN GATE — your turn is done. Do not call loop_continue or any other tool.\n"
        f"Output this message to the developer and stop. "
        f"The developer must review and manually run `/loop-continue` when ready."
    )


def _complete_message(
    loop_name: str,
    job_id: str,
    outcome: str,
) -> str:
    """Return the terminal message for a loop with no ``on_complete`` chain."""
    icon = {"success": "✓", "failure": "✗", "warning": "⚠"}.get(outcome, "?")
    return f"**{loop_name} loop — complete {icon}**\n\nJob: `{job_id}` | Outcome: `{outcome}`"


_WHY_LIMIT = 2000
_WHY_MARK = "…"


def _failed_check_names(
    config: LoopConfig,
    validator_results: list[dict[str, Any]],
    *,
    required: bool,
) -> list[str]:
    """Declared checks that failed, required or optional as asked."""
    declared = {
        check.name: check.required
        for validator_cfg in config.validators
        for check in validator_cfg.checks
    }
    reported: dict[str, bool] = {}
    for result in validator_results:
        checks = result.get("checks")
        if not isinstance(checks, list):
            continue
        for check in checks:
            if isinstance(check, dict) and isinstance(check.get("name"), str):
                reported[check["name"]] = bool(check.get("pass"))
    return sorted(
        name
        for name, is_required in declared.items()
        if is_required is required and not reported.get(name, False)
    )


def _not_reported_line(tool: str, check_name: str) -> str:
    return (
        f"`{check_name}`: not reported by `{tool}`. "
        "It is declared in the loop config, so it counts as failed."
    )


def _shorten_line(line: str, budget: int) -> str:
    """Keep the end of one line within *budget*, marked when it is cut."""
    if budget <= 0:
        return ""
    if len(line) <= budget:
        return line
    mark = _WHY_MARK
    content = line.removeprefix(mark)
    room = budget - len(mark)
    if room <= 0:
        return mark[:budget]
    return mark + content[-room:]


def _drop_one(lines: list[str], *, keep_first: bool) -> list[str] | None:
    """Drop the earliest trimmable line. The last line, and a kept first line, stay."""
    if keep_first:
        if len(lines) <= 2:
            return None
        if lines[1] == _WHY_MARK:
            if len(lines) <= 3:
                return None
            return [lines[0], lines[1], *lines[3:]]
        return [lines[0], _WHY_MARK, *lines[2:]]
    if len(lines) <= 1:
        return None
    if lines[0] == _WHY_MARK:
        if len(lines) <= 2:
            return None
        return [lines[0], *lines[2:]]
    return [_WHY_MARK, *lines[1:]]


def _cap_section(text: str, limit: int) -> str:
    """Cut *text* to *limit*, keeping its start and its last line."""
    if len(text) <= limit:
        return text
    last = text.splitlines()[-1] if text.splitlines() else ""
    mark = _WHY_MARK
    if len(last) + len(mark) + 1 > limit:
        return _shorten_line(last or text, limit)
    tail = f"\n{mark}\n{last}"
    head = text[: limit - len(tail)].rstrip("\n")
    if not head:
        fitted = f"{mark}\n{last}"
        return fitted if len(fitted) <= limit else _shorten_line(last, limit)
    return f"{head}{tail}"


def _why_groups(
    config: LoopConfig,
    validator_results: list[dict[str, Any]],
    failed: list[str],
) -> list[tuple[str, str, list[tuple[str, bool, str]]]]:
    """Failed checks grouped by validator, sorted by validator then check."""
    tool_for = {
        check.name: validator_cfg.tool
        for validator_cfg in config.validators
        for check in validator_cfg.checks
    }
    result_for: dict[str, dict[str, Any]] = {}
    for result in validator_results:
        tool = result.get("tool")
        if isinstance(tool, str):
            result_for[tool] = result
    grouped: dict[str, list[tuple[str, bool, str]]] = {}
    messages: dict[str, str] = {}
    for name in failed:
        tool = tool_for.get(name, "validator")
        result = result_for.get(tool, {})
        raw_message = result.get("message")
        messages[tool] = raw_message if isinstance(raw_message, str) else ""
        reported = False
        check_message = ""
        checks = result.get("checks")
        if isinstance(checks, list):
            for check in checks:
                if isinstance(check, dict) and check.get("name") == name:
                    reported = True
                    raw = check.get("message")
                    check_message = raw if isinstance(raw, str) else ""
                    break
        grouped.setdefault(tool, []).append((name, reported, check_message))
    return [(tool, messages.get(tool, ""), sorted(grouped[tool])) for tool in sorted(grouped)]


def _validator_body(tool: str, message: str) -> tuple[list[str], bool]:
    """Lines for one validator. The first line is kept when later lines are cut."""
    if not message:
        return [f"`{tool}`"], True
    parts = message.splitlines() or [message]
    return [f"`{tool}`: {parts[0]}", *parts[1:]], True


def _assemble_why(
    groups: list[tuple[str, str, list[tuple[str, bool, str]]]],
    bodies: dict[str, list[str]],
) -> str:
    lines = ["Why:"]
    for tool, _validator_message, checks in groups:
        lines.extend(bodies[f"v:{tool}"])
        for name, reported, message in checks:
            if not reported:
                lines.append(_not_reported_line(tool, name))
                continue
            if not message:
                lines.append(f"`{name}`")
                continue
            lines.append(f"`{name}`:")
            lines.extend(bodies[f"c:{tool}:{name}"])
    return "\n".join(lines)


def _largest_droppable(
    bodies: dict[str, list[str]],
    keep_first: set[str],
) -> str | None:
    candidates = [
        key
        for key, lines in bodies.items()
        if _drop_one(lines, keep_first=key in keep_first) is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda key: len("\n".join(bodies[key])))


def _shorten_longest(bodies: dict[str, list[str]], overflow: int) -> bool:
    """Shorten the longest line from the start. Returns whether it changed."""
    where: tuple[str, int] | None = None
    longest = 0
    for key, lines in bodies.items():
        for index, line in enumerate(lines):
            if len(line) > longest:
                longest = len(line)
                where = (key, index)
    if where is None or longest <= len(_WHY_MARK):
        return False
    key, index = where
    lines = list(bodies[key])
    target = max(len(_WHY_MARK), len(lines[index]) - max(1, overflow))
    shortened = _shorten_line(lines[index], target)
    if shortened == lines[index]:
        return False
    lines[index] = shortened
    bodies[key] = lines
    return True


def _bounded_why(groups: list[tuple[str, str, list[tuple[str, bool, str]]]]) -> str:
    """Render *groups* and keep the section within ``_WHY_LIMIT`` characters."""
    bodies: dict[str, list[str]] = {}
    keep_first: set[str] = set()
    for tool, validator_message, checks in groups:
        lines, first = _validator_body(tool, validator_message)
        bodies[f"v:{tool}"] = lines
        if first:
            keep_first.add(f"v:{tool}")
        for name, _reported, message in checks:
            if not message:
                continue
            bodies[f"c:{tool}:{name}"] = message.splitlines() or [message]
    rendered = _assemble_why(groups, bodies)
    while len(rendered) > _WHY_LIMIT:
        key = _largest_droppable(bodies, keep_first)
        if key is not None:
            updated = _drop_one(bodies[key], keep_first=key in keep_first)
            if updated is None:
                break
            bodies[key] = updated
            rendered = _assemble_why(groups, bodies)
            continue
        if _shorten_longest(bodies, len(rendered) - _WHY_LIMIT):
            rendered = _assemble_why(groups, bodies)
            continue
        return _cap_section(rendered, _WHY_LIMIT)
    return rendered


def _validation_why(
    config: LoopConfig,
    validator_results: list[dict[str, Any]],
    decision: dict[str, str],
) -> str:
    """Why a required check paused the loop, or an optional check warned.

    The validator's message is shown once, then every failed check by name.
    A check the validator did not report says so. Long text is cut from the
    start, at a line boundary when it has more than one line, and marked.
    The validator's first line stays when it fits, and the end of the text
    stays, including the end of a single long line. The section never
    exceeds ``_WHY_LIMIT`` characters. Empty when the outcome is not a
    pause or a warning.
    """
    if decision.get("next_status") == LoopStatus.paused.value:
        required = True
    elif decision.get("outcome") == LoopOutcome.warning.value:
        required = False
    else:
        return ""
    failed = _failed_check_names(config, validator_results, required=required)
    if not failed:
        return ""
    groups = _why_groups(config, validator_results, failed)
    return _bounded_why(groups)


def _with_why(message: str, why: str) -> str:
    """Append *why* when a pause or warning has something to explain."""
    if not why:
        return message
    return f"{message.rstrip()}\n\n{why}"


def _validator_failure_message(
    loop_name: str,
    job_id: str,
    task_id: str,
    message: str,
    why: str = "",
) -> str:
    """Return the pause message shown when required validator checks fail."""
    short_task = task_id[:8]
    body = _with_why(message, why)
    return (
        f"**{loop_name} loop — paused (validation failed)** ⚠️\n\n"
        f"Job: `{job_id}` | Task: `{short_task}`\n\n"
        f"{body}\n\n"
        f"Before calling `loop_continue`: if any failing check depends on an artifact a "
        f"skill produced (e.g. `validate`'s report), re-run that skill via "
        f"`get_skill_definition` first to regenerate it. Calling `loop_continue` without "
        f"regenerating stale artifacts will re-grade the same output and fail again."
    )


def _iterating_message(
    loop_name: str,
    job_id: str,
    task_id: str,
    iteration: int,
    config: LoopConfig,
) -> str:
    """Return the message shown when repeat_until re-triggers the loop."""
    short_task = task_id[:8]
    header = (
        f"**{loop_name} loop — iterating (round {iteration})** 🔁\n\n"
        f"Job: `{job_id}` | Task: `{short_task}` | "
        f"`repeat_until: {config.repeat_until}` not yet met — restarting the skill sequence.\n\n"
    )
    return header + _skill_instruction(
        config.skills[0], loop_name, 0, len(config.skills), job_id, task_id
    )


# ---------------------------------------------------------------------------
# Loop startup (shared by run_loop and automatic on_complete chaining)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StartedLoop:
    """A ``_start_loop`` result: the agent message, plus the new run's ids.

    ``job_id`` is ``None`` when startup was refused and no state was written.
    Chaining publishes its outcome onto ``(job_id, loop_name, task_id)``
    and nowhere else.
    """

    message: str
    job_id: str | None = None
    loop_name: str | None = None
    task_id: str | None = None

    @property
    def chained(self) -> tuple[str, str, str] | None:
        if self.job_id is None or self.loop_name is None or self.task_id is None:
            return None
        return self.job_id, self.loop_name, self.task_id


def _validating_start_refusal(root: Path) -> str | None:
    """Refuse ``run_loop`` while this job already has a ``validating`` run.

    Validators no longer block the server, so a second ``run_loop`` can land
    before the worker finishes. The worker would then write its outcome onto
    whichever run is active, which would be the new one.
    """
    try:
        found = _find_active(root)
    except AmbiguousActiveRun as exc:
        return f"Error: {exc}"
    if not found:
        return None
    job_id, loop_name, task_id = found
    try:
        state = read_state(root, job_id, loop_name, task_id)
    except (json.JSONDecodeError, OSError):
        return None
    if state.get("status") != LoopStatus.validating.value:
        if _LEGACY_MOVE_NOTE.get() and state.get("status") != LoopStatus.complete.value:
            return (
                f"A `{loop_name}` loop is already in progress "
                f"(job `{job_id}`). Call `loop_continue`."
            )
        return None
    return (
        f"Cannot start a new loop: `{loop_name}` is still validating "
        f"(job `{job_id}`, task `{task_id[:8]}`). "
        "Call `loop_status`, or `loop_continue` if validation was interrupted."
    )


def _start_loop(
    root: Path,
    name: str,
    description: str | None = None,
    *,
    pending_finish: bool = False,
    extra: dict[str, Any] | None = None,
) -> _StartedLoop:
    """Load a loop config, initialise its state, and return the first-skill instruction.

    Shared by the ``run_loop`` tool and automatic ``on_complete`` chaining —
    chaining starts the next loop directly rather than asking the agent to
    make a second ``run_loop`` call.

    Reads persistent context from the job's ``activeContext.md`` (Open
    Learnings / Open Decisions) and surfaces it alongside the first-skill instruction —
    the "reads persistent context before running" half of the loop's memory
    property.

    *extra* is written with the first state, such as a rework run's
    ``rework_parent`` (GH-115).
    """
    if not pending_finish:
        refusal = _validating_start_refusal(root)
        if refusal:
            return _StartedLoop(refusal)
    refusal = scope_refusal(root, current_job(root), name)
    if refusal:
        return _StartedLoop(f"Error: {refusal}")
    try:
        config = _resolve_loop(name, root)
    except FileNotFoundError as exc:
        return _StartedLoop(f"Error: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _StartedLoop(f"Error loading loop config '{name}': {exc}")

    guard_error = _branch_guard_error(root, config)
    if guard_error:
        return _StartedLoop(guard_error)

    task_id = make_task_id()
    if config.require_branch is not None:
        # This loop establishes a brand new ticket identity — never trust
        # resolve_job_id() here. A pre-existing spec.md or branch name is
        # either the previous ticket's leftover state or not ticket-scoped
        # at all (see dmx.loop_state module docstring).
        try:
            existing_pending = find_pending_run(root)
        except AmbiguousActiveRun:
            existing_pending = None  # already ambiguous; let it surface below
        if existing_pending is not None:
            pending_job_id, pending_loop_name, pending_task_id = existing_pending
            return _StartedLoop(
                f"Cannot start a new `{name}` loop: a `{pending_loop_name}` run is already "
                f"in progress under a not-yet-identified job (`{pending_job_id}`, task "
                f"`{pending_task_id[:8]}`). Finish or resume it first with `loop_continue`, "
                "or remove its folder under `.dmx/jobs/` if it's a stale leftover."
            )
        job_id = make_pending_job_id(task_id)
    else:
        _repair_misplaced_ticket_files(root)
        try:
            job_id = resolve_job_id(root)
        except AmbiguousActiveRun as exc:
            return _StartedLoop(f"Error: {exc}")
        _move_root_ticket_files(root, job_id)

    # One write. A pending file, then a second write that marks the run in
    # progress, is visible to loop_status in between: the next loop looks
    # like it is waiting on its first skill, or no run exists at all.
    updates: dict[str, Any] = {"status": LoopStatus.running.value}
    if pending_finish:
        # The outcome message is published a moment later. Until then this
        # run must still look like validation in progress, or loop_status
        # reports a bare skill instruction instead of the chain result.
        updates["validation_started_at"] = _now_iso()
        updates["finish_message"] = None
    if extra:
        updates.update(extra)
    write_initial_state(
        workspace_root=root,
        loop_name=name,
        job_id=job_id,
        task_id=task_id,
        skills=config.skills,
        updates=updates,
    )

    logger.info("start_loop: name=%s job=%s task=%s", name, job_id, task_id)

    first_skill = config.skills[0]
    instruction = _skill_instruction(
        first_skill, name, 0, len(config.skills), job_id, task_id, description
    )

    memory_context = read_memory_context(root, job_id)
    if memory_context:
        instruction = (
            f"Memory context (from `.dmx/jobs/{job_id}/{ACTIVE_CONTEXT_FILE}`):\n"
            f"{memory_context}\n\n{instruction}"
        )
    return _StartedLoop(instruction, job_id, name, task_id)


# ---------------------------------------------------------------------------
# Validator execution + policy decision
# ---------------------------------------------------------------------------


_COMMIT_DMX_STATE_TIMEOUT_SECONDS = 15


def _commit_dmx_state(root: Path, message: str) -> str | None:
    """Best-effort commit of any uncommitted ``.dmx/`` changes.

    Called once a chain of loop runs has genuinely finished (a terminal
    outcome with no next loop, or a blocked chain — not ``paused``/
    ``iterating``, and not a step that chains on, GH-122) so its final
    state — the job's state JSON and the session-note breadcrumb
    ``_finish_loop`` just wrote to ``activeContext.md`` — isn't left as an
    uncommitted, local-only change. This matters most for a
    loop with no ``on_complete`` chain target (e.g. the bundled ``release``
    loop): nothing downstream runs to commit on its behalf, and
    ``close-ticket`` explicitly makes no ``.dmx/`` changes before
    force-deleting the branch, so an uncommitted final state here would be
    silently and permanently lost — see GH-23.

    Silently does nothing if *root* isn't a git repo or there's nothing to
    commit — this is a best-effort durability improvement (matching the
    README's "committed with the PR" claim for ``.dmx/jobs/``), not
    something that should ever break a loop response.

    Every subprocess call is timeout-bounded so a hung ``git`` invocation
    (e.g. a pre-commit hook prompting for input, or GPG signing waiting on
    a passphrase) can't hang the whole MCP tool call indefinitely.

    Does nothing on ``branch_base`` or ``production_branch`` — those branches
    receive this state through a merged PR, not a direct commit.     After a
    successful commit on any other branch, pushes that commit when it is the
    only commit ahead of an upstream that has an open PR. Other unpushed
    commits are left for the developer. Push failures are warnings; the
    local commit stands.

    Returns:
        ``None`` if there was nothing to commit or the commit succeeded.
        A short, user-facing warning string if a commit was *attempted*
        but failed (e.g. a pre-commit hook rejected it), or if the commit
        was skipped because the branch is protected, or if the follow-up
        push failed. Callers should surface it in the loop's response
        rather than only logging it server-side.
    """
    if _on_protected_branch(root):
        return (
            "⚠️ Did not commit `.dmx/` state because the current branch is the integration "
            "or production branch. Loop state stays local."
        )
    try:
        status = subprocess.run(
            ["git", "status", "--short", "--", ".dmx/"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001
        return None
    if status.returncode != 0 or not status.stdout.strip():
        return None  # not a git repo, or nothing under .dmx/ to commit

    try:
        subprocess.run(
            ["git", "add", ".dmx/"],
            capture_output=True,
            text=True,
            cwd=root,
            check=True,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            cwd=root,
            check=True,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not auto-commit .dmx/ state (%s): %s", message, exc)
        return (
            "⚠️ Could not auto-commit the loop's final `.dmx/` state (see server logs for "
            "details) — run `git status .dmx/` and commit manually if needed."
        )
    return _push_open_pr(root)


def _has_upstream(root: Path) -> bool:
    """True when HEAD tracks a remote branch."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _has_open_pr(root: Path) -> bool:
    """True when ``gh`` reports an open PR for HEAD.

    A missing ``gh``, no PR, or any error means there is nothing to push
    onto. The local commit from :func:`_commit_dmx_state` still stands.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "state", "-q", ".state"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001
        return False
    return result.returncode == 0 and result.stdout.strip() == "OPEN"


def _commits_ahead_of_upstream(root: Path) -> int | None:
    """How many commits HEAD is ahead of its upstream, or None if unknown."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "@{upstream}..HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _upstream_push_target(root: Path) -> tuple[str, str] | None:
    """Return ``(remote, ref)`` for HEAD's upstream, or None.

    ``ref`` is the remote branch name (``refs/heads/...`` stripped to the
    branch). Pushing ``HEAD:ref`` avoids depending on ``push.default``.
    """
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
        if branch.returncode != 0 or not branch.stdout.strip() or branch.stdout.strip() == "HEAD":
            return None
        name = branch.stdout.strip()
        remote = subprocess.run(
            ["git", "config", "--get", f"branch.{name}.remote"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
        merge = subprocess.run(
            ["git", "config", "--get", f"branch.{name}.merge"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001
        return None
    if remote.returncode != 0 or merge.returncode != 0:
        return None
    remote_name = remote.stdout.strip()
    ref = merge.stdout.strip().removeprefix("refs/heads/")
    if not remote_name or not ref:
        return None
    return remote_name, ref


def _push_open_pr(root: Path) -> str | None:
    """Push the loop-state commit when it is the only unpushed commit.

    A bare ``git push`` would also send earlier unpushed work and can
    dismiss review approvals. This pushes ``HEAD`` to the upstream branch
    only when exactly one commit is ahead. Otherwise it warns and leaves
    the push to the developer.

    Returns:
        ``None`` when there is nothing to push or the push succeeded.
        A short warning when a push was skipped or failed.
    """
    if not _has_upstream(root) or not _has_open_pr(root):
        return None
    ahead = _commits_ahead_of_upstream(root)
    if ahead != 1:
        return (
            "⚠️ Committed the loop's final `.dmx/` state but did not push it, because this "
            "branch has other unpushed commits. Push manually if the open PR should include "
            "this update."
        )
    target = _upstream_push_target(root)
    if target is None:
        return (
            "⚠️ Committed the loop's final `.dmx/` state but could not determine its upstream. "
            "Push this branch so the open PR picks up the update."
        )
    remote, ref = target
    try:
        result = subprocess.run(
            ["git", "push", remote, f"HEAD:{ref}"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=_COMMIT_DMX_STATE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not push loop state: %s", exc)
        return (
            "⚠️ Committed the loop's final `.dmx/` state but could not push it. "
            "Push this branch so the open PR picks up the update."
        )
    if result.returncode != 0:
        logger.warning(
            "could not push loop state: %s", result.stderr.strip() or result.stdout.strip()
        )
        return (
            "⚠️ Committed the loop's final `.dmx/` state but could not push it. "
            "Push this branch so the open PR picks up the update."
        )
    return None


def snapshot_loop_for_pr(root: Path) -> str:
    """Mark the active loop ``complete`` so a PR commit does not record ``running``.

    ``create-pr`` calls this before ``git add .dmx/``. ``outcome`` and
    ``validator_results`` stay empty; ``loop_continue`` fills them in after
    the PR is open. No active loop is a no-op so a standalone ``/dmx/create-pr``
    still works.
    """
    try:
        found = _find_active(root)
    except AmbiguousActiveRun as exc:
        return f"Error: {exc}"
    if not found:
        return "No active loop run. Nothing to snapshot."
    job_id, loop_name, task_id = found
    state = read_state(root, job_id, loop_name, task_id)
    if is_pr_snapshot(state):
        return f"Loop `{loop_name}` is already recorded as complete for the PR."
    skills = state.get("skills")
    index = state.get("current_skill_index")
    if (
        not isinstance(skills, list)
        or not isinstance(index, int)
        or index < 0
        or index >= len(skills)
        or skills[index] != "create-pr"
    ):
        return "The active loop is not on create-pr. Nothing to snapshot."
    supersede_pr_snapshots(root, job_id, keep_task_id=task_id)
    write_state(root, job_id, loop_name, task_id, {"status": LoopStatus.complete.value})
    return (
        f"Recorded `{loop_name}` as complete in "
        f"`.dmx/jobs/{job_id}/{loop_name}-{task_id}.json` so the PR commit does not "
        "leave it running. Validator results stay empty until `loop_continue`."
    )


# ---------------------------------------------------------------------------
# Rework after a failed run (GH-115)
# ---------------------------------------------------------------------------

_REWORK_AWAITING = "awaiting_approval"
_REWORK_RUNNING = "reworking"
_REWORK_RECHECKING = "rechecking"
_REWORK_RESOLVED = "resolved"
_REWORK_EXHAUSTED = "exhausted"
_REWORK_STOPPED = "stopped"


def _rework_of(state: dict[str, Any]) -> dict[str, Any]:
    rework = state.get("rework")
    return rework if isinstance(rework, dict) else {}


def _rounds(count: int) -> str:
    return f"{count} rework round{'' if count == 1 else 's'}"


def _rework_settings(config: LoopConfig, state: dict[str, Any]) -> dict[str, Any] | None:
    """Rework state to continue after a failure, or ``None`` to fail as before.

    A rework run never starts its own rework. A run already in rework keeps
    its round count; one that used up its rounds stops reworking.
    """
    if state.get("rework_parent"):
        return None
    current = state.get("rework")
    if isinstance(current, dict):
        if current.get("stage") == _REWORK_EXHAUSTED:
            return None
        return current
    configured = config.on_complete.on_failure.rework
    if configured is None:
        return None
    return {
        "loop": configured.loop,
        "max_rounds": configured.max_rounds,
        "approve": configured.approve,
        "round": 0,
        "history": [],
    }


def _rework_tasks_block(heading: str, tasks: list[str]) -> str:
    return f"**{heading}**\n\n" + "\n".join(tasks)


def _rework_approval_message(
    loop_name: str,
    job_id: str,
    task_id: str,
    rework: dict[str, Any],
    checks: list[ReworkCheck],
    why: str,
) -> str:
    short_task = task_id[:8]
    rework_loop = rework["loop"]
    tasks = rework_tasks(checks)
    names = ", ".join(sorted(check.name for check in checks))
    body = (
        f"**{loop_name} loop — paused for rework approval** ⚠️\n\n"
        f"Job: `{job_id}` | Task: `{short_task}` | "
        f"Rework round {rework['round']} of {rework['max_rounds']} ({rework_loop})\n\n"
        f"Required checks failed: {names}. dmx added these tasks to "
        f"`.dmx/jobs/{job_id}/{TASKS_FILE}`:\n\n"
        f"{_rework_tasks_block(rework['phase'], tasks)}\n\n"
        f"HUMAN GATE — your turn is done. Output this message to the developer and stop.\n"
        f"The developer can edit the tasks, then run `/loop-continue` (`loop_continue`) to "
        f"start the rework: the `{rework_loop}` loop implements this phase, then "
        f"`{loop_name}` runs again from its first skill. To skip the rework, check off or "
        f"delete the tasks first; `{loop_name}` then runs again at once."
    )
    return _with_why(body, why)


def _rework_history_lines(rework: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for entry in rework.get("history") or []:
        if not isinstance(entry, dict):
            continue
        checks = ", ".join(f"`{name}`" for name in entry.get("checks") or [])
        run = entry.get("rework_task_id")
        run_text = f" (rework run `{str(run)[:8]}`)" if run else ""
        lines.append(f"- Round {entry.get('round')}: {entry.get('phase')} — {checks}{run_text}")
    return lines


def _rework_exhausted_message(
    loop_name: str,
    job_id: str,
    task_id: str,
    rework: dict[str, Any],
    decision_message: str,
    why: str,
) -> str:
    rounds = int(rework.get("round") or 0)
    history = "\n".join(_rework_history_lines(rework))
    body = (
        f"**{loop_name} loop — paused after {_rounds(rounds)}** ⚠️\n\n"
        f"Job: `{job_id}` | Task: `{task_id[:8]}`\n\n"
        f"Required checks still fail after {_rounds(rounds)}, the limit for this run.\n\n"
        f"{history}\n\n"
        f"Now: {decision_message}\n\n"
        f"Fix it by hand, then run `/loop-continue` to re-run the validators, or run "
        f"`/rework` to allow more rounds."
    )
    return _with_why(body, why)


def _rework_on_failure(
    root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    config: LoopConfig,
    validator_results: list[dict[str, Any]],
    decision: dict[str, str],
    why: str,
    settings: dict[str, Any],
) -> str | None:
    """Start or stop rework after required checks failed.

    Returns ``None`` when the run should fail as it would without rework.
    """
    checks = failed_required_checks(config, validator_results)
    next_status = decision["next_status"]
    in_rework = bool(settings.get("history"))
    if not checks or any(not check.reworkable for check in checks):
        if in_rework:
            write_state(
                root,
                job_id,
                loop_name,
                task_id,
                {"rework": {**settings, "stage": _REWORK_STOPPED}},
            )
        if next_status != LoopStatus.paused.value:
            return None
        append_session_note(
            root,
            job_id,
            f"{loop_name} loop paused for validator review (job `{job_id}`, "
            f"task `{task_id[:8]}`): {decision['message']} Not reworked.",
        )
        message = _validator_failure_message(loop_name, job_id, task_id, decision["message"], why)
        note = not_reworkable_text(checks)
        return f"{message}\n\n{note}" if note else message

    if int(settings.get("round") or 0) >= int(settings["max_rounds"]):
        rework = {**settings, "stage": _REWORK_EXHAUSTED}
        write_state(root, job_id, loop_name, task_id, {"rework": rework})
        append_session_note(
            root,
            job_id,
            f"{loop_name} loop stopped reworking after {_rounds(int(rework['round']))} "
            f"(job `{job_id}`, task `{task_id[:8]}`): {decision['message']}",
        )
        message = _rework_exhausted_message(
            loop_name, job_id, task_id, rework, decision["message"], why
        )
        if next_status != LoopStatus.paused.value:
            return _store_outcome(root, job_id, loop_name, task_id, message)
        return message

    return _begin_rework_round(
        root, job_id, loop_name, task_id, settings, checks, why, pending_finish=True
    )


def _begin_rework_round(
    root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    settings: dict[str, Any],
    checks: list[ReworkCheck],
    why: str,
    *,
    pending_finish: bool,
) -> str:
    """Write the next rework phase, then wait for approval or start the rework run."""
    round_number = int(settings.get("round") or 0) + 1
    tasks = rework_tasks(checks)
    heading = append_rework_phase(
        ticket_file(root, job_id, TASKS_FILE), loop_name, round_number, task_id[:8], tasks
    )
    history = [
        *(settings.get("history") or []),
        {
            "round": round_number,
            "phase": heading,
            "checks": [check.name for check in checks],
            "rework_task_id": None,
        },
    ]
    rework = {
        "loop": settings["loop"],
        "max_rounds": int(settings["max_rounds"]),
        "approve": bool(settings.get("approve", True)),
        "round": round_number,
        "stage": _REWORK_AWAITING,
        "phase": heading,
        "rework_task_id": None,
        "history": history,
    }
    write_state(
        root,
        job_id,
        loop_name,
        task_id,
        {"status": LoopStatus.paused.value, "rework": rework},
    )
    append_session_note(
        root,
        job_id,
        f"{loop_name} loop failed required checks; dmx wrote {heading} "
        f"(job `{job_id}`, task `{task_id[:8]}`).",
    )
    if rework["approve"]:
        return _rework_approval_message(loop_name, job_id, task_id, rework, checks, why)
    header = (
        f"**{loop_name} loop — required checks failed; rework started**\n\n"
        f"dmx added these tasks to `.dmx/jobs/{job_id}/{TASKS_FILE}`:\n\n"
        f"{_rework_tasks_block(heading, tasks)}"
    )
    return _start_rework_run(
        root, job_id, loop_name, task_id, rework, _with_why(header, why), pending_finish
    )


def _start_rework_run(
    root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    rework: dict[str, Any],
    header: str,
    pending_finish: bool,
) -> str:
    """Mark the failed run ``reworking`` and start its rework loop."""
    write_state(
        root,
        job_id,
        loop_name,
        task_id,
        {
            "status": LoopStatus.reworking.value,
            "rework": {**rework, "stage": _REWORK_RUNNING},
        },
    )
    parent = {
        "loop_name": loop_name,
        "task_id": task_id,
        "round": rework["round"],
        "max_rounds": rework["max_rounds"],
    }
    started = _start_loop(
        root, rework["loop"], pending_finish=pending_finish, extra={"rework_parent": parent}
    )
    if started.task_id is None:
        stopped = {**rework, "stage": _REWORK_STOPPED}
        write_state(
            root,
            job_id,
            loop_name,
            task_id,
            {"status": LoopStatus.paused.value, "rework": stopped},
        )
        message = (
            f"{header}\n\nThe `{rework['loop']}` rework loop could not start: "
            f"{started.message}\n\nThe `{loop_name}` run is paused. Fix the cause and run "
            "`/rework`, or fix the failures by hand and run `/loop-continue`."
        )
        _publish_finish_message(root, job_id, loop_name, task_id, message)
        return message

    history = [
        {**entry, "rework_task_id": started.task_id}
        if isinstance(entry, dict) and entry.get("round") == rework["round"]
        else entry
        for entry in rework.get("history") or []
    ]
    write_state(
        root,
        job_id,
        loop_name,
        task_id,
        {
            "rework": {
                **rework,
                "stage": _REWORK_RUNNING,
                "rework_task_id": started.task_id,
                "history": history,
            }
        },
    )
    append_session_note(
        root,
        job_id,
        f"{loop_name} loop started rework round {rework['round']} of "
        f"{rework['max_rounds']} with {rework['loop']} (job `{job_id}`).",
    )
    line = rework_line(loop_name, rework["round"], rework["max_rounds"], rework["loop"])
    message = (
        f"{header}\n\n{line}\n\n{started.message}" if header else f"{line}\n\n{started.message}"
    )
    if pending_finish:
        _publish_finish_message(root, job_id, loop_name, task_id, message, chained=started.chained)
    return message


def _return_to_parent(
    root: Path,
    job_id: str,
    parent: dict[str, Any],
    header: str,
    *,
    finished: tuple[str, str] | None = None,
) -> str:
    """Restart the failed run at its first skill after its rework."""
    parent_loop = str(parent["loop_name"])
    parent_task = str(parent["task_id"])
    state = read_state(root, job_id, parent_loop, parent_task)
    rework = _rework_of(state)
    skills: list[str] = state["skills"]
    write_state(
        root,
        job_id,
        parent_loop,
        parent_task,
        {
            "status": LoopStatus.running.value,
            "current_skill_index": 0,
            "skills_completed": [],
            "skill_outputs": {},
            "finish_message": None,
            "validation_started_at": _now_iso() if finished else None,
            "rework": {**rework, "stage": _REWORK_RECHECKING},
        },
    )
    append_session_note(
        root,
        job_id,
        f"{parent_loop} loop runs again after rework round {rework.get('round')} (job `{job_id}`).",
    )
    instruction = _skill_instruction(skills[0], parent_loop, 0, len(skills), job_id, parent_task)
    message = (
        f"{header}"
        f"Back to **{parent_loop}** (rework round {rework.get('round')} of "
        f"{rework.get('max_rounds')}): it runs again from its first skill, then its "
        f"validators.\n\n{instruction}"
    )
    # Like chaining, returning to the parent leaves the state for the next
    # commit (GH-122).
    if finished is not None:
        _publish_finish_message(
            root,
            job_id,
            finished[0],
            finished[1],
            message,
            chained=(job_id, parent_loop, parent_task),
        )
    else:
        _publish_finish_message(root, job_id, parent_loop, parent_task, message)
    return message


def _finish_rework_run(
    root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    outcome: str,
    parent: dict[str, Any],
) -> str:
    """A rework run finished: go back to its parent, or stop it when the run failed."""
    if outcome == LoopOutcome.failure.value:
        return _stop_parent(
            root,
            job_id,
            parent,
            f"The `{loop_name}` rework run failed.",
            finished=(loop_name, task_id),
        )
    append_session_note(
        root,
        job_id,
        f"{loop_name} rework run completed (outcome: {outcome}) — back to "
        f"{parent['loop_name']} (job `{job_id}`).",
    )
    header = f"**{loop_name} loop — complete** (outcome: `{outcome}`)\n\n"
    return _return_to_parent(root, job_id, parent, header, finished=(loop_name, task_id))


def _stop_parent(
    root: Path,
    job_id: str,
    parent: dict[str, Any],
    reason: str,
    *,
    finished: tuple[str, str] | None = None,
) -> str:
    parent_loop = str(parent["loop_name"])
    parent_task = str(parent["task_id"])
    state = read_state(root, job_id, parent_loop, parent_task)
    rework = _rework_of(state)
    write_state(
        root,
        job_id,
        parent_loop,
        parent_task,
        {"status": LoopStatus.paused.value, "rework": {**rework, "stage": _REWORK_STOPPED}},
    )
    append_session_note(
        root, job_id, f"{parent_loop} loop rework stopped: {reason} (job `{job_id}`)."
    )
    message = (
        f"**{parent_loop} loop — rework stopped** ⚠️\n\n"
        f"Job: `{job_id}` | Task: `{parent_task[:8]}`\n\n"
        f"{reason} The `{parent_loop}` run is paused. Fix the failures by hand and run "
        "`/loop-continue` to re-run its validators, or run `/rework` to try again."
    )
    if finished is not None:
        _publish_finish_message(
            root,
            job_id,
            finished[0],
            finished[1],
            message,
            chained=(job_id, parent_loop, parent_task),
        )
    else:
        _publish_finish_message(root, job_id, parent_loop, parent_task, message)
    return message


def _continue_rework(
    root: Path, job_id: str, loop_name: str, task_id: str, state: dict[str, Any]
) -> str | None:
    """``loop_continue`` on a run waiting for rework approval or for its rework run."""
    rework = state.get("rework")
    if not isinstance(rework, dict):
        return None
    parent = {"loop_name": loop_name, "task_id": task_id}
    if state.get("status") == LoopStatus.reworking.value:
        child_id = rework.get("rework_task_id")
        child: dict[str, Any] = {}
        if isinstance(child_id, str):
            with contextlib.suppress(json.JSONDecodeError, OSError):
                child = read_state(root, job_id, str(rework.get("loop")), child_id)
        if child.get("status") == LoopStatus.complete.value and child.get("outcome") in {
            LoopOutcome.success.value,
            LoopOutcome.warning.value,
        }:
            return _return_to_parent(root, job_id, parent, "")
        return _stop_parent(
            root, job_id, parent, f"The `{rework.get('loop')}` rework run did not finish."
        )
    if state.get("status") != LoopStatus.paused.value or rework.get("stage") != _REWORK_AWAITING:
        return None
    phase = str(rework.get("phase") or "")
    if unchecked_in_phase(ticket_file(root, job_id, TASKS_FILE), phase) == 0:
        header = f"No open tasks under **{phase}**, so no rework run is needed.\n\n"
        return _return_to_parent(root, job_id, parent, header)
    return _start_rework_run(root, job_id, loop_name, task_id, rework, "", pending_finish=False)


def _rework_status_line(state: dict[str, Any]) -> str:
    parent = state.get("rework_parent")
    if not isinstance(parent, dict):
        return ""
    return rework_line(
        str(parent.get("loop_name")),
        int(parent.get("round") or 0),
        int(parent.get("max_rounds") or 0),
        str(state.get("loop_name")),
    )


def _manual_rework(
    root: Path,
    rework_loop: str,
    max_rounds: int,
    approve: bool,
) -> str:
    """``rework_loop``: start the rework flow on the active failed run."""
    if max_rounds < 1:
        return "Error: `max_rounds` must be at least 1."
    try:
        found = _find_active(root)
    except AmbiguousActiveRun as exc:
        return f"Error: {exc}"
    if not found:
        return (
            "No active loop run found. `/rework` works on a run that paused after a "
            "failed required check."
        )
    job_id, loop_name, task_id = found
    state = read_state(root, job_id, loop_name, task_id)
    current = _rework_of(state) or None
    if current and current.get("stage") == _REWORK_AWAITING:
        return (
            f"The `{loop_name}` run is already waiting for rework approval "
            f"({current.get('phase')}). Edit the tasks if needed, then run `/loop-continue`."
        )
    if (
        state.get("status") != LoopStatus.paused.value
        or state.get("outcome") != LoopOutcome.failure.value
    ):
        return (
            f"The `{loop_name}` run is {state.get('status')}, not paused after a failed "
            "required check. `/rework` works on a run that is."
        )
    if state.get("rework_parent"):
        return f"The `{loop_name}` run is itself a rework run; it can't start another rework."
    try:
        _resolve_loop(rework_loop, root)
    except Exception as exc:  # noqa: BLE001
        return f"Error loading the rework loop '{rework_loop}': {exc}"
    try:
        config = _resolve_loop(loop_name, root)
    except Exception as exc:  # noqa: BLE001
        return f"Error reloading loop config: {exc}"
    added = component_validators(root, job_id, loop_name)
    if added:
        config = config.model_copy(update={"validators": [*config.validators, *added]})
    results = state.get("validator_results") or []
    config = expand_reported_checks(config, results)
    checks = failed_required_checks(config, results)
    if not checks:
        return f"The `{loop_name}` run has no failed required checks to rework."
    if any(not check.reworkable for check in checks):
        return not_reworkable_text(checks)
    done = int(current.get("round") or 0) if current else 0
    settings = {
        "loop": rework_loop,
        "max_rounds": done + max_rounds,
        "approve": approve,
        "round": done,
        "history": (current.get("history") if current else None) or [],
    }
    decision = evaluate_validator_results(config, results)
    why = _validation_why(config, results, {**decision, "next_status": LoopStatus.paused.value})
    message = _begin_rework_round(
        root, job_id, loop_name, task_id, settings, checks, why, pending_finish=False
    )
    after = read_state(root, job_id, loop_name, task_id)
    if after.get("status") == LoopStatus.paused.value:
        _publish_finish_message(root, job_id, loop_name, task_id, message)
    return message


def _apply_loop_outcome(
    root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    config: LoopConfig,
    skill_outputs: dict[str, str],
) -> str:
    """Run validators, apply policy, persist the outcome, and return a message.

    Called once all skills in the loop have completed. Required check
    failures apply ``failure_handling``; optional check failures apply
    ``on_optional_failure``. On pause, the state file is left ``paused`` —
    the next ``loop_continue`` call re-runs validators (acting as a retry).

    If validators pass (success or warning) and the loop declares
    ``repeat_until``, the condition is evaluated before finishing. If not
    yet met, the loop restarts from the first skill with status ``running``
    and an incremented ``iteration_count``. ``iterating`` is not left on
    while those skills execute. This is not recorded as a failure.

    If ``on_complete`` declares a ``trigger_loop`` for this outcome
    (success, failure, or warning), the next loop starts automatically —
    no second ``run_loop`` call from the agent is required.

    Every branch appends a one-line breadcrumb to the job's ``activeContext.md``
    (Session Notes) — the "writes learnings back when it completes" half of
    the loop's memory property. This is a deterministic log entry, not
    judgment; promoting it to durable knowledge is still ``/dmx/update-memory``'s job.
    """
    short_task = task_id[:8]
    loop_context = {
        "job_id": job_id,
        "task_id": task_id,
        "loop_name": loop_name,
        "branch": current_branch(root),
        "ticket_ref": job_id if job_id != "unknown" else None,
        "job_dir": str(ticket_file(root, job_id, SPEC_FILE).parent),
        "spec_path": str(ticket_file(root, job_id, SPEC_FILE)),
        "tasks_path": str(ticket_file(root, job_id, TASKS_FILE)),
        "active_context_path": str(ticket_file(root, job_id, ACTIVE_CONTEXT_FILE)),
    }

    started_state = read_state(root, job_id, loop_name, task_id)
    added = component_validators(root, job_id, loop_name)
    if added:
        config = config.model_copy(update={"validators": [*config.validators, *added]})
    validator_results = run_validators(config, root, skill_outputs, loop_context)
    config = expand_reported_checks(config, validator_results)
    decision = evaluate_validator_results(config, validator_results)
    outcome = decision["outcome"]
    next_status = decision["next_status"]
    why = _validation_why(config, validator_results, decision)

    write_state(
        root,
        job_id,
        loop_name,
        task_id,
        {
            "validator_results": validator_results,
            "outcome": outcome,
            "status": next_status,
        },
    )

    if outcome == LoopOutcome.failure.value:
        settings = _rework_settings(config, started_state)
        if settings is not None:
            paused_why = _validation_why(
                config, validator_results, {**decision, "next_status": LoopStatus.paused.value}
            )
            handled = _rework_on_failure(
                root,
                job_id,
                loop_name,
                task_id,
                config,
                validator_results,
                decision,
                paused_why,
                settings,
            )
            if handled is not None:
                return handled

    if next_status == LoopStatus.paused.value:
        logger.info(
            "loop %s: validators failed, pausing for review — %s", loop_name, decision["message"]
        )
        append_session_note(
            root,
            job_id,
            f"{loop_name} loop paused for validator review (job `{job_id}`, "
            f"task `{short_task}`): {decision['message']}",
        )
        return _validator_failure_message(loop_name, job_id, task_id, decision["message"], why)

    if config.repeat_until and not evaluate_repeat_until(config.repeat_until, root, job_id):
        current_state = read_state(root, job_id, loop_name, task_id)
        iteration = current_state.get("iteration_count", 0) + 1
        write_state(
            root,
            job_id,
            loop_name,
            task_id,
            {
                "status": LoopStatus.running.value,
                "iteration_count": iteration,
                "current_skill_index": 0,
                "skills_completed": [],
            },
        )
        logger.info(
            "loop %s: repeat_until '%s' not met — iterating (round %d)",
            loop_name,
            config.repeat_until,
            iteration,
        )
        append_session_note(
            root,
            job_id,
            f"{loop_name} loop iterating (round {iteration}) — repeat_until "
            f"'{config.repeat_until}' not yet met (job `{job_id}`).",
        )
        return _iterating_message(loop_name, job_id, task_id, iteration, config)

    rework_parent = started_state.get("rework_parent")
    if isinstance(rework_parent, dict):
        return _finish_rework_run(root, job_id, loop_name, task_id, outcome, rework_parent)

    rework_state = started_state.get("rework")
    if (
        isinstance(rework_state, dict)
        and rework_state.get("stage") == _REWORK_RECHECKING
        and outcome != LoopOutcome.failure.value
    ):
        write_state(
            root,
            job_id,
            loop_name,
            task_id,
            {"rework": {**rework_state, "stage": _REWORK_RESOLVED}},
        )
        append_session_note(
            root,
            job_id,
            f"{loop_name} loop passed after {_rounds(int(rework_state.get('round') or 0))} "
            f"(job `{job_id}`).",
        )

    next_loop: str | None = None
    if outcome == LoopOutcome.success.value:
        next_loop = config.on_complete.on_success.trigger_loop
    elif outcome == LoopOutcome.failure.value:
        next_loop = config.on_complete.on_failure.trigger_loop
    else:
        next_loop = config.on_complete.on_warning.trigger_loop

    logger.info("loop %s complete — outcome=%s next_loop=%s", loop_name, outcome, next_loop)

    if next_loop:
        try:
            chain_guard_error = _branch_guard_error(root, _resolve_loop(next_loop, root))
        except Exception as exc:  # noqa: BLE001
            chain_guard_error = f"Error loading loop config '{next_loop}': {exc}"

        if chain_guard_error:
            # Don't claim we're chaining when the next loop can't actually
            # start (e.g. it require_branch's an integration branch this
            # loop is still running away from) — report the finished loop's
            # own outcome plainly instead of a misleading "chaining" message.
            append_session_note(
                root,
                job_id,
                f"{loop_name} loop completed (outcome: {outcome}) — chaining to "
                f"{next_loop} was configured but blocked: {chain_guard_error}",
            )
            message = _with_why(
                f"{_complete_message(loop_name, job_id, outcome)}\n\n"
                f"Configured to chain to **{next_loop}**, but it couldn't start: "
                f"{chain_guard_error}",
                why,
            )
            return _store_outcome(root, job_id, loop_name, task_id, message)

        append_session_note(
            root,
            job_id,
            f"{loop_name} loop completed (outcome: {outcome}) — "
            f"chained to {next_loop} (job `{job_id}`).",
        )
        # No commit here: the next loop's commit or create-pr picks this
        # state up, and the chain's last loop commits it (GH-122, GH-23).
        started = _start_loop(root, next_loop, pending_finish=True)
        chain_header = (
            f"**{loop_name} loop — complete** (outcome: `{outcome}`)\n\n"
            f"Chaining automatically to **{next_loop}** loop.\n\n"
        )
        message = _with_why(chain_header + started.message, why)
        _publish_finish_message(root, job_id, loop_name, task_id, message, chained=started.chained)
        return message

    append_session_note(
        root, job_id, f"{loop_name} loop completed (outcome: {outcome}) (job `{job_id}`)."
    )
    return _store_outcome(
        root,
        job_id,
        loop_name,
        task_id,
        _with_why(_complete_message(loop_name, job_id, outcome), why),
    )


_VALIDATORS_RUNNING = "Validators are running. Call `loop_status` to see the result."
_VALIDATION_INTERRUPTED = "Validation was interrupted; call `loop_continue` to re-run it."
_FINISH_PENDING = (
    "All skills for this run are complete; the finish step did not complete. "
    "Call `loop_continue` to re-run validators."
)
_STATUS_WAIT_SECONDS = 25.0
_VALIDATION_TASKS: dict[str, asyncio.Task[None]] = {}


def _finish_loop(
    root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    config: LoopConfig,
    skill_outputs: dict[str, str],
) -> str:
    """Run validators and remember the message ``loop_status`` should return."""
    message = _apply_loop_outcome(root, job_id, loop_name, task_id, config, skill_outputs)
    state = read_state(root, job_id, loop_name, task_id)
    if not state.get("finish_message"):
        _publish_finish_message(root, job_id, loop_name, task_id, message)
    return message


def _publish_finish_message(
    root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    message: str,
    *,
    chained: tuple[str, str, str] | None = None,
) -> None:
    """Store *message* on the finished run, and on the run chaining just started.

    *chained* is that new run's ids. Publishing onto whichever run happens
    to be active would stamp a "complete" message onto a ``run_loop`` the
    agent started while validators were still going.
    """
    write_state(root, job_id, loop_name, task_id, {"finish_message": message})
    if chained is None or chained == (job_id, loop_name, task_id):
        return
    write_state(root, chained[0], chained[1], chained[2], {"finish_message": message})


def _store_outcome(
    root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    message: str,
) -> str:
    """Store *message*, commit ``.dmx/``, and store again if the commit warned.

    The warning only exists after ``_commit_dmx_state`` returns. Publishing
    beforehand and then appending the warning to the discarded return value
    drops it: ``loop_status`` would never show a skipped push.
    """
    _publish_finish_message(root, job_id, loop_name, task_id, message)
    warning = _commit_dmx_state(root, f"chore: sync loop state for {loop_name} (job {job_id})")
    if warning:
        message = f"{message}\n\n{warning}"
        _publish_finish_message(root, job_id, loop_name, task_id, message)
    return message


def _finish_loop_guarded(
    root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    config: LoopConfig,
    skill_outputs: dict[str, str],
) -> None:
    try:
        _finish_loop(root, job_id, loop_name, task_id, config, skill_outputs)
    except Exception as exc:  # noqa: BLE001
        logger.exception("finish_loop failed for %s", loop_name)
        try:
            write_state(
                root,
                job_id,
                loop_name,
                task_id,
                {
                    "status": LoopStatus.failed.value,
                    "outcome": LoopOutcome.failure.value,
                    "finish_message": f"Error finishing the {loop_name} loop: {exc}",
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not record validation failure for %s", loop_name)


def _validation_worker_alive(task_id: str) -> bool:
    """True when this process has a validator worker for *task_id*."""
    task = _VALIDATION_TASKS.get(task_id)
    return task is not None and not task.done()


def _forget_validation_task(task: asyncio.Task[None], task_id: str) -> None:
    current = _VALIDATION_TASKS.get(task_id)
    if current is task:
        del _VALIDATION_TASKS[task_id]


def _schedule_finish_loop(
    root: Path,
    job_id: str,
    loop_name: str,
    task_id: str,
    config: LoopConfig,
    skill_outputs: dict[str, str],
) -> str:
    """Mark the run validating and finish it off the request path.

    A second call while this process still has a worker for *task_id* does
    not start another one. A ``validating`` file with no worker — the server
    restarted — is started again.
    """
    if _validation_worker_alive(task_id):
        return _VALIDATORS_RUNNING
    write_state(
        root,
        job_id,
        loop_name,
        task_id,
        {
            "status": LoopStatus.validating.value,
            "validation_started_at": _now_iso(),
            "finish_message": None,
        },
    )
    task = asyncio.get_running_loop().create_task(
        asyncio.to_thread(
            _finish_loop_guarded, root, job_id, loop_name, task_id, config, skill_outputs
        )
    )
    _VALIDATION_TASKS[task_id] = task

    def _drop(done: asyncio.Task[None]) -> None:
        _forget_validation_task(done, task_id)

    task.add_done_callback(_drop)
    return _VALIDATORS_RUNNING


async def _wait_for_validation(task_id: str) -> None:
    """Wait for the live worker, up to ``_STATUS_WAIT_SECONDS``.

    Returns as soon as the worker finishes. On timeout the worker keeps
    running; the caller reports that validation is still in progress.
    """
    task = _VALIDATION_TASKS.get(task_id)
    if task is None or task.done():
        # Chaining replaces the active run before the worker returns. The
        # worker is still keyed by the run that just finished.
        pending = [item for item in _VALIDATION_TASKS.values() if not item.done()]
        if len(pending) != 1:
            return
        task = pending[0]
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=_STATUS_WAIT_SECONDS)
    except TimeoutError:
        return


def _skills_are_complete(state: dict[str, object]) -> bool:
    """True when every skill is recorded and the finish step has not consumed them.

    ``current_skill_index`` is written before validators start. A restart in
    that gap leaves a non-terminal run with the index past the last skill.
    """
    skills = state.get("skills")
    index = state.get("current_skill_index")
    return isinstance(skills, list) and isinstance(index, int) and index >= len(skills)


def _rejected_advance(state: dict[str, object], skill: str) -> str | None:
    """Return a no-op message when *skill* is not the run's current skill.

    Matched by name. A loop that repeats a skill name can record a retry
    against the later occurrence; bundled loops use each name once.
    """
    skills = state.get("skills")
    if not isinstance(skills, list):
        skills = []
    completed = state.get("skills_completed")
    if not isinstance(completed, list):
        completed = []
    index = state.get("current_skill_index")
    if not isinstance(index, int):
        index = 0
    current = skills[index] if 0 <= index < len(skills) else None
    if current == skill:
        return None
    if skill in completed:
        if current:
            return (
                f"`{skill}` is already recorded. Nothing was advanced. "
                f"Current skill is `{current}`."
            )
        return (
            f"`{skill}` is already recorded. Nothing was advanced. "
            "Call `loop_status` for the result."
        )
    if current is None:
        return (
            f"`{skill}` is not the current skill. All skills are already recorded. "
            "Nothing was advanced."
        )
    return f"`{skill}` is not the current skill (`{current}`). Nothing was recorded."


def _latest_run_state(root: Path) -> dict[str, object] | None:
    """The newest loop-state file under the current job or a pending job."""
    jobs_dir = root / ".dmx" / "jobs"
    try:
        job_ids = [resolve_job_id(root)]
    except AmbiguousActiveRun:
        job_ids = []
    if jobs_dir.exists():
        job_ids.extend(path.name for path in jobs_dir.glob("_pending-*") if path.is_dir())
    best_at = ""
    best: dict[str, object] | None = None
    for job_id in job_ids:
        job_dir = jobs_dir / job_id
        if not job_dir.exists():
            continue
        for path in job_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict) or "status" not in data:
                continue
            updated = str(data.get("updated_at", ""))
            if updated >= best_at:
                best_at = updated
                best = data
    return best


def loop_status_message(root: Path) -> str:
    """Describe the active run, or the outcome of the run that just finished."""
    try:
        found = _find_active(root)
    except AmbiguousActiveRun as exc:
        return f"Error: {exc}"
    if found:
        job_id, loop_name, task_id = found
        state = read_state(root, job_id, loop_name, task_id)
        message = _active_status_message(root, job_id, loop_name, task_id, state)
        line = _rework_status_line(state)
        if line and line not in message:
            return f"{line}\n\n{message}"
        return message
    return _finished_status_message(root)


def _active_status_message(
    root: Path, job_id: str, loop_name: str, task_id: str, state: dict[str, Any]
) -> str:
    """``loop_status`` for the active run."""
    if state.get("status") == LoopStatus.validating.value and not _validation_worker_alive(task_id):
        return _VALIDATION_INTERRUPTED
    if state.get("status") == LoopStatus.validating.value or (
        state.get("validation_started_at") and not state.get("finish_message")
    ):
        started = state.get("validation_started_at") or "just now"
        return (
            f"Validators are running for `{loop_name}` (started {started}). "
            "Call `loop_status` to see the result."
        )
    if state.get("finish_message"):
        return str(state["finish_message"])
    if _skills_are_complete(state) and state.get("status") in {
        LoopStatus.running.value,
        LoopStatus.iterating.value,
    }:
        return _FINISH_PENDING
    skills = state.get("skills")
    index = state.get("current_skill_index")
    if isinstance(skills, list) and isinstance(index, int) and 0 <= index < len(skills):
        return (
            f"The {loop_name} loop is {state.get('status')} "
            f"on skill `{skills[index]}` ({index + 1}/{len(skills)}). "
            f"Job: `{job_id}`."
        )
    return f"The {loop_name} loop is {state.get('status')}. Job: `{job_id}`."


def _finished_status_message(root: Path) -> str:
    """``loop_status`` when no run is active."""
    latest = _latest_run_state(root)
    if isinstance(latest, dict):
        latest_task = latest.get("task_id")
        latest_alive = isinstance(latest_task, str) and _validation_worker_alive(latest_task)
        if latest.get("status") == LoopStatus.validating.value and not latest_alive:
            return _VALIDATION_INTERRUPTED
        if (
            latest_alive
            and latest.get("validation_started_at")
            and not latest.get("finish_message")
        ):
            latest_loop = latest.get("loop_name") or "loop"
            started = latest.get("validation_started_at") or "just now"
            return (
                f"Validators are running for `{latest_loop}` (started {started}). "
                "Call `loop_status` to see the result."
            )
        if latest.get("finish_message"):
            return str(latest["finish_message"])
    # The finishing run is already terminal and the next run does not exist
    # yet. A live worker is still between those two writes.
    if any(not task.done() for task in _VALIDATION_TASKS.values()):
        return _VALIDATORS_RUNNING
    return (
        "No active loop run found. "
        "Start a loop with `run_loop` or check if the previous loop completed."
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_loop_tools(app: FastMCP) -> None:
    """Register the loop runtime tools on *app*."""

    @app.tool
    async def run_loop(
        ctx: Context,
        name: str,
        description: str | None = None,
        workspace_root: str | None = None,
    ) -> str:
        """Start a dmx loop by name.

        Reads the loop config from ``.dmx/loops/{name}.yaml`` (app repo) or
        the bundled loops directory as fallback.  Initialises state and returns
        an instruction to run the first skill.

        Args:
            name: Loop name (e.g. ``spec``, ``dev``).
            description: Optional context passed to the first skill (e.g. feature description).
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            Plain-English instruction for the agent.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"
        return with_skipped_dmx_note(
            root,
            _with_rules_reminder(
                root,
                with_components_line(
                    root, _with_legacy_note(_start_loop(root, name, description).message)
                ),
            ),
        )

    @app.tool
    async def get_skill_definition(
        ctx: Context,
        name: str,
        workspace_root: str | None = None,
    ) -> str:
        """Fetch the full instruction set for a named dmx skill.

        Call this before executing a skill the loop runtime has scheduled.
        Returns the skill's complete step-by-step instructions with frontmatter
        stripped, ready to execute directly.

        Args:
            name: Skill name as returned by ``run_loop`` or ``loop_continue``
                  (e.g. ``create-ticket``, ``plan``, ``implement-next-phase``).
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            Full skill instructions, or an error message if the skill is not found.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"
        refusal = scope_refusal(root, current_job(root), name)
        if refusal and _resolve_skill(name, root) is not None:
            return refusal
        try:
            resolved = _resolve_skill(name, root)
        except SharedSourceError as exc:
            return f"Error reading .dmx/shared-sources.yaml: {exc}"
        if resolved is None:
            return f"Skill '{name}' not found. Check the skill name or add it to .dmx/skills/."

        body = _strip_frontmatter(resolved.raw)
        if resolved.root_path is None:
            return body

        # Folder-shaped skill (GH-27 phase 4): the body may reference
        # scripts/, references/, or assets/ relative to its own directory.
        # dmx never copies a shared source's content anywhere else (see
        # GH-27's "pass the root path, don't copy anything"), so that
        # directory is exactly where the source is cached (or, in a 0.5 repo,
        # vendored) — tell the agent so it can resolve those paths itself.
        prefix = (
            f"Skill root: `{resolved.root_path}/` — resolve any `scripts/`, `references/`, "
            "or `assets/` paths mentioned below relative to this directory.\n"
        )
        deps_note = _dependencies_note(resolved.raw)
        if deps_note:
            prefix += deps_note + "\n"
        return prefix + "\n" + body

    @app.tool(name="list_skills")
    async def list_skills_tool(
        ctx: Context,
        workspace_root: str | None = None,
    ) -> str:
        """List local and shared skills, not bundled slash commands.

        One line per skill: ``name (source): description``. The description
        is last. The name is what ``get_skill_definition`` accepts. A
        shadowed skill is omitted. A name that matches a bundled skill is
        marked as an override: loops and ``get_skill_definition`` load this
        copy, while ``/dmx/{name}`` stays the bundled prompt.

        This is not the ``dmx list-skills`` CLI command, which prints
        bundled skills only.

        Args:
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            The list, a line saying there are none, or the shared-sources error.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"
        return with_skipped_dmx_note(root, _with_rules_reminder(root, list_skills(root)))

    @app.tool(name="snapshot_loop_for_pr")
    async def snapshot_loop_for_pr_tool(
        ctx: Context,
        workspace_root: str | None = None,
    ) -> str:
        """Mark the active loop complete before ``create-pr`` commits ``.dmx/``.

        The release loop's state file is still ``running`` when that skill
        commits. This records it as ``complete`` with empty validator results
        so the merged PR does not show a live loop. ``loop_continue`` fills
        in ``check_pr_ready`` afterwards. No active loop is a no-op.

        Args:
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            Plain-English result for the agent.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"
        return snapshot_loop_for_pr(root)

    @app.tool
    async def loop_advance(
        ctx: Context,
        output: str,
        skill: str,
        workspace_root: str | None = None,
    ) -> str:
        """Advance the active loop after a skill completes.

        Call this after every skill run, passing the full skill output and
        the skill name you just finished. A repeat of a skill that is no
        longer the current one is ignored. Each skill name must appear once
        in the loop; a repeated name is matched by name, not by index.

        Args:
            output: Full output from the skill that just completed.
            skill: Name of the skill that just completed. Must be the run's
                current skill.
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            Plain-English instruction or status message for the agent.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"

        def finish(message: str) -> str:
            return with_skipped_dmx_note(
                root, with_components_line(root, _with_legacy_note(message))
            )

        _repair_misplaced_ticket_files(root)
        try:
            found = _find_active(root)
        except AmbiguousActiveRun as exc:
            return finish(f"Error: {exc}")
        if not found:
            return finish("No active loop run found. Start a loop with `run_loop` first.")
        job_id, loop_name, task_id = found
        try:
            job_id = _maybe_promote_pending_job(root, job_id)
        except PendingJobPromotionError as exc:
            return finish(f"Error: {exc}")
        _move_root_ticket_files(root, job_id)

        state = read_state(root, job_id, loop_name, task_id)
        if state.get("status") == LoopStatus.validating.value:
            if _validation_worker_alive(task_id):
                return finish(_VALIDATORS_RUNNING)
            return finish(_VALIDATION_INTERRUPTED)
        if state.get("status") == LoopStatus.reworking.value:
            return finish(
                f"The `{loop_name}` run is waiting on its rework run, which is no longer "
                "open. Nothing was recorded. Call `loop_continue`."
            )
        if _skills_are_complete(state) and state.get("status") in {
            LoopStatus.running.value,
            LoopStatus.iterating.value,
        }:
            return finish(_FINISH_PENDING)
        rejected = _rejected_advance(state, skill)
        if rejected:
            return finish(rejected)
        was_snapshot = is_pr_snapshot(state)
        skills: list[str] = state["skills"]
        idx: int = state["current_skill_index"]
        completed_skill = skills[idx]

        # Persist skill output.
        skill_outputs: dict[str, str] = state.get("skill_outputs", {})
        skill_outputs[completed_skill] = output
        skills_completed: list[str] = state.get("skills_completed", [])
        skills_completed.append(completed_skill)

        next_idx = idx + 1

        # Load config to check human_gate.
        try:
            config = _resolve_loop(loop_name, root)
        except Exception as exc:  # noqa: BLE001
            return finish(f"Error reloading loop config: {exc}")

        # Persist output and completed list regardless of branch taken below.
        write_state(
            root,
            job_id,
            loop_name,
            task_id,
            {
                "current_skill_index": next_idx,
                "skills_completed": skills_completed,
                "skill_outputs": skill_outputs,
                "finish_message": None,
                "validation_started_at": None,
            },
        )

        if next_idx < len(skills):
            # More skills remain.
            if config.human_gate and not was_snapshot:
                write_state(
                    root,
                    job_id,
                    loop_name,
                    task_id,
                    {
                        "status": LoopStatus.paused.value,
                    },
                )
                return finish(_pause_message(loop_name, job_id, task_id, next_idx, len(skills)))
            if config.human_gate:
                # PR snapshot stays complete so a later commit cannot put
                # `paused` back into the open PR.
                return finish(_pause_message(loop_name, job_id, task_id, next_idx, len(skills)))
            # human_gate: false — return next skill instruction immediately.
            next_skill = skills[next_idx]
            return finish(
                _skill_instruction(next_skill, loop_name, next_idx, len(skills), job_id, task_id)
            )
        elif config.human_gate:
            # All skills complete but human gate is on — pause for review before
            # running validators and chaining. loop_continue triggers the final step.
            # A PR snapshot is already complete; leave it that way.
            if not was_snapshot:
                write_state(
                    root,
                    job_id,
                    loop_name,
                    task_id,
                    {
                        "status": LoopStatus.paused.value,
                    },
                )
            return finish(_pause_message(loop_name, job_id, task_id, next_idx, len(skills)))
        else:
            # All skills complete — validators run off this request.
            return finish(
                _schedule_finish_loop(root, job_id, loop_name, task_id, config, skill_outputs)
            )

    @app.tool
    async def loop_continue(
        ctx: Context,
        workspace_root: str | None = None,
    ) -> str:
        """Resume a paused loop.

        Finds the active run and returns the next skill instruction. Call
        this after reviewing output at a human gate. Also re-runs validators
        when every skill is already recorded but the finish step never started.

        Args:
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            Plain-English instruction for the agent.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"

        def finish(message: str) -> str:
            return with_skipped_dmx_note(
                root, with_components_line(root, _with_legacy_note(message))
            )

        _repair_misplaced_ticket_files(root)
        try:
            found = _find_active(root)
        except AmbiguousActiveRun as exc:
            return finish(f"Error: {exc}")
        if not found:
            return finish(
                "No active loop run found. "
                "Start a loop with `run_loop` or check if the previous loop completed."
            )
        job_id, loop_name, task_id = found
        try:
            job_id = _maybe_promote_pending_job(root, job_id)
        except PendingJobPromotionError as exc:
            return finish(f"Error: {exc}")
        _move_root_ticket_files(root, job_id)

        state = read_state(root, job_id, loop_name, task_id)
        snapshot = is_pr_snapshot(state)

        reworked = _continue_rework(root, job_id, loop_name, task_id, state)
        if reworked is not None:
            return finish(reworked)

        if state.get("status") == LoopStatus.validating.value:
            if _validation_worker_alive(task_id):
                return finish(_VALIDATORS_RUNNING)
            try:
                config = _resolve_loop(loop_name, root)
            except Exception as exc:  # noqa: BLE001
                return finish(f"Error reloading loop config: {exc}")
            skill_outputs: dict[str, str] = state.get("skill_outputs", {})
            return finish(
                _schedule_finish_loop(root, job_id, loop_name, task_id, config, skill_outputs)
            )

        # Skills were recorded and the process stopped before ``validating``.
        # ``running`` and ``iterating`` would otherwise refuse to continue.
        finish_pending = _skills_are_complete(state) and state.get("status") in {
            LoopStatus.running.value,
            LoopStatus.iterating.value,
        }

        if not finish_pending and state["status"] != LoopStatus.paused.value and not snapshot:
            if state["status"] == LoopStatus.running.value:
                return finish(
                    f"The {loop_name} loop is currently running. "
                    "Wait for the skill to finish before calling loop_continue."
                )
            return finish(
                f"Loop '{loop_name}' is not paused (status: {state['status']}). "
                "Nothing to continue."
            )

        skills: list[str] = state["skills"]
        idx: int = state["current_skill_index"]

        if idx >= len(skills):
            # All skills already complete — human approved (or a previous
            # validator run paused for review). Validators run off this request.
            # Leave a PR snapshot ``complete``; do not flip it to ``running``.
            logger.info("loop_continue: %s all skills done, running validators", loop_name)

            try:
                config = _resolve_loop(loop_name, root)
            except Exception as exc:  # noqa: BLE001
                return finish(f"Error reloading loop config: {exc}")

            skill_outputs = state.get("skill_outputs", {})
            return finish(
                _schedule_finish_loop(root, job_id, loop_name, task_id, config, skill_outputs)
            )

        # A PR snapshot stays complete until validators fill in the outcome.
        # Flipping it to running here would be the next commit's status if
        # the finish step were interrupted.
        if not snapshot:
            write_state(
                root,
                job_id,
                loop_name,
                task_id,
                {
                    "status": LoopStatus.running.value,
                },
            )

        logger.info(
            "loop_continue: %s job=%s task=%s skill_index=%d", loop_name, job_id, task_id, idx
        )

        next_skill = skills[idx]
        return finish(_skill_instruction(next_skill, loop_name, idx, len(skills), job_id, task_id))

    @app.tool
    async def rework_loop(
        ctx: Context,
        loop: str = "dev",
        max_rounds: int = 2,
        approve: bool = True,
        workspace_root: str | None = None,
    ) -> str:
        """Turn a paused run's failed required checks into rework tasks (GH-115).

        Works on the active run when it paused after a failed required check,
        whether or not its loop config sets ``on_failure.rework``. Appends a
        rework phase to the ticket's ``tasks.md``. With ``approve`` (the
        default) the run waits for ``loop_continue``; otherwise the rework
        loop starts now. When the rework loop finishes, the failed loop runs
        again from its first skill. On a run that used up its rounds, this
        allows ``max_rounds`` more.

        Args:
            loop: The loop that does the fixing. Defaults to ``dev``.
            max_rounds: Rework rounds this call allows. At least 1.
            approve: Pause to show the tasks before the rework starts.
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            The tasks and what happens next, or why the run can't be reworked.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"
        _repair_misplaced_ticket_files(root)
        return with_skipped_dmx_note(
            root,
            with_components_line(
                root, _with_legacy_note(_manual_rework(root, loop, max_rounds, approve))
            ),
        )

    @app.tool
    async def loop_status(
        ctx: Context,
        workspace_root: str | None = None,
    ) -> str:
        """Report the active loop without changing it.

        One exception: a single in-progress run left in ``.dmx/jobs/none/``
        or ``unknown/`` by dmx 0.4.2 is moved into the branch folder. A file
        already committed on ``branch_base`` or its upstream is not moved.

        While validators run, this waits up to 25 seconds and returns as
        soon as they finish. If they are still running, it says so. If the
        server restarted mid-validation, it tells the agent to call
        ``loop_continue``. Once validation finishes, it returns the outcome
        message, including the next skill instruction when the loop chained.

        Args:
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            Plain-English status for the agent.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"
        try:
            found = _find_active(root)
        except AmbiguousActiveRun:
            found = None
        # Also wait when nothing is active yet: chaining marks the old run
        # terminal before the next run's file exists.
        await _wait_for_validation(found[2] if found else "")
        return with_skipped_dmx_note(
            root,
            _with_rules_reminder(
                root, with_components_line(root, _with_legacy_note(loop_status_message(root)))
            ),
        )
