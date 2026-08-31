"""Loop runtime MCP tools: run_loop, loop_advance, loop_continue, get_skill_definition.

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

``loop_advance(output)``
    - Finds the active run by scanning ``.dmx/jobs/`` (no separate pointer
      file — see ``dmx.loop_state`` module docstring).
    - Persists the skill output.
    - If more skills remain and ``human_gate: true``: pauses, returns pause msg.
    - If more skills remain and ``human_gate: false``: returns next instruction.
    - If all skills complete: runs validators, applies policy
      (``failure_handling`` / ``on_optional_failure``), writes outcome, and —
      if ``on_complete`` declares a ``trigger_loop`` for that outcome —
      starts the next loop automatically and returns its first-skill
      instruction. Otherwise returns a terminal completion message.

``loop_continue()``
    - Finds the active run the same way as ``loop_advance``.
    - Advances ``current_skill_index`` past the last completed skill.
    - Returns the next skill instruction.
"""

from __future__ import annotations

import importlib.resources as pkg
import logging
import re
from pathlib import Path

from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.exceptions import AmbiguousActiveRun, WorkspaceRootInvalid
from dmx.loop_memory import append_session_note, read_memory_context
from dmx.loop_schema import LoopConfig, RequireBranch, load_loop, load_loops_dir
from dmx.loop_state import (
    LoopOutcome,
    LoopStatus,
    current_branch,
    find_active_run,
    find_pending_run,
    is_pending_job_id,
    make_pending_job_id,
    make_task_id,
    read_state,
    rename_job,
    resolve_job_id,
    write_initial_state,
    write_state,
)
from dmx.repeat_until import evaluate_repeat_until
from dmx.validator_runner import evaluate_validator_results, run_validators
from dmx.workspace import resolve_workspace_root

__all__ = ["register_loop_tools"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bundled loops directory
# ---------------------------------------------------------------------------


def _bundled_loops_dir() -> Path:
    return Path(str(pkg.files("dmx") / "loops"))


def _bundled_skills_dir() -> Path:
    return Path(str(pkg.files("dmx") / "skills"))


def _resolve_skill(name: str, workspace_root: Path) -> str | None:
    """Find and return the raw content of a skill markdown file.

    Search order:
    1. ``{workspace_root}/.dmx/skills/{name}.md`` (project-specific, exact)
    2. ``{workspace_root}/.dmx/skills/dmx-{name}.md`` (project-specific, prefixed)
    3. Recursive glob in the bundled skills directory for ``{name}.md``
    4. Recursive glob in the bundled skills directory for ``dmx-{name}.md``

    Returns ``None`` if the skill is not found in any location.
    """
    candidates = [name, f"dmx-{name}"]

    project_skills = workspace_root / ".dmx" / "skills"
    for candidate in candidates:
        path = project_skills / f"{candidate}.md"
        if path.exists():
            return path.read_text()

    bundled = _bundled_skills_dir()
    for candidate in candidates:
        matches = list(bundled.rglob(f"{candidate}.md"))
        if matches:
            return matches[0].read_text()

    return None


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

_BRANCH_BASE_RE = re.compile(r"^branch_base\s*:\s*([^\s#]+)", re.MULTILINE)


def _read_branch_base(workspace_root: Path) -> str | None:
    """Read ``branch_base`` from ``.dmx/config.md``, or None if unavailable.

    Mirrors the "fall back to reading .dmx/config.md" convention every
    skill uses when project config isn't injected into agent context —
    this runs in the deterministic MCP tool layer, which never has agent
    context, so config.md is the only source available here.
    """
    config_path = workspace_root / ".dmx" / "config.md"
    if not config_path.exists():
        return None
    match = _BRANCH_BASE_RE.search(config_path.read_text(encoding="utf-8"))
    if not match:
        return None
    value = match.group(1).strip()
    return value if value and value != "{REQUIRED}" else None


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

    Returns:
        ``(job_id, loop_name, task_id)``, or ``None`` if nothing is active.
    """
    job_id = resolve_job_id(root)
    active = find_active_run(root, job_id)
    if active is not None:
        loop_name, task_id = active
        return job_id, loop_name, task_id
    return find_pending_run(root)


def _maybe_promote_pending_job(root: Path, job_id: str) -> str:
    """Rename a temp/pending job folder to its real id once resolvable.

    Called right after a skill completes (``loop_advance``) or a paused
    loop resumes (``loop_continue``) — the natural points where an
    identity-creating skill (e.g. ``create-ticket``) may have just made the
    real ticket/branch resolvable. A no-op once the job is already real, or
    if the real identity still isn't resolvable yet.

    ``resolve_job_id``'s own fallbacks are deliberately *not* treated as a
    real identity here: ``"unknown"`` is a catch-all that unrelated pending
    jobs could collide under, and ``branch_base`` means ``create-ticket``
    hasn't actually switched to the new feature branch yet.
    """
    if not is_pending_job_id(job_id):
        return job_id
    real_job_id = resolve_job_id(root)
    if (
        real_job_id == job_id
        or is_pending_job_id(real_job_id)
        or real_job_id == "unknown"
        or real_job_id == _read_branch_base(root)
    ):
        return job_id
    rename_job(root, job_id, real_job_id)
    logger.info("promoted pending job %s -> %s", job_id, real_job_id)
    return real_job_id


# ---------------------------------------------------------------------------
# Loop config resolution
# ---------------------------------------------------------------------------


def _resolve_loop(name: str, workspace_root: Path) -> LoopConfig:
    """Load a loop config: app repo first, bundled fallback.

    Args:
        name: Loop name (must match filename stem).
        workspace_root: Repo root.

    Returns:
        Validated :class:`LoopConfig`.

    Raises:
        FileNotFoundError: If the loop is not found in either location.
    """
    app_path = workspace_root / ".dmx" / "loops" / f"{name}.yaml"
    if app_path.exists():
        return load_loop(app_path)

    bundled_path = _bundled_loops_dir() / f"{name}.yaml"
    if bundled_path.exists():
        return load_loop(bundled_path)

    # List available loops for a helpful error.
    app_loops = load_loops_dir(workspace_root / ".dmx" / "loops")
    bundled_loops = load_loops_dir(_bundled_loops_dir())
    available = sorted(set(app_loops) | set(bundled_loops))
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
        f"Job: `{job_id}` | Task: `{short_task}`{desc_block}\n\n"
        f"REQUIRED: Call `get_skill_definition` with name=`{skill_name}` to fetch the skill "
        f"instructions, then execute them exactly as written.\n\n"
        f"REQUIRED: When the skill finishes, you MUST immediately call the "
        f"`loop_advance` MCP tool with the skill's full output as the `output` argument. "
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


def _validator_failure_message(
    loop_name: str,
    job_id: str,
    task_id: str,
    message: str,
) -> str:
    """Return the pause message shown when required validator checks fail."""
    short_task = task_id[:8]
    return (
        f"**{loop_name} loop — paused (validation failed)** ⚠️\n\n"
        f"Job: `{job_id}` | Task: `{short_task}`\n\n"
        f"{message}\n\n"
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


def _start_loop(root: Path, name: str, description: str | None = None) -> str:
    """Load a loop config, initialise its state, and return the first-skill instruction.

    Shared by the ``run_loop`` tool and automatic ``on_complete`` chaining —
    chaining starts the next loop directly rather than asking the agent to
    make a second ``run_loop`` call.

    Reads persistent context from ``.dmx/activeContext.md`` (Open Learnings /
    Open Decisions) and surfaces it alongside the first-skill instruction —
    the "reads persistent context before running" half of the loop's memory
    property.
    """
    try:
        config = _resolve_loop(name, root)
    except FileNotFoundError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error loading loop config '{name}': {exc}"

    guard_error = _branch_guard_error(root, config)
    if guard_error:
        return guard_error

    task_id = make_task_id()
    if config.require_branch is not None:
        # This loop establishes a brand new ticket identity — never trust
        # resolve_job_id() here. A pre-existing spec.md or branch name is
        # either the previous ticket's leftover state or not ticket-scoped
        # at all (see dmx.loop_state module docstring).
        job_id = make_pending_job_id(task_id)
    else:
        job_id = resolve_job_id(root)

    write_initial_state(
        workspace_root=root,
        loop_name=name,
        job_id=job_id,
        task_id=task_id,
        skills=config.skills,
    )
    write_state(root, job_id, name, task_id, {"status": LoopStatus.running.value})

    logger.info("start_loop: name=%s job=%s task=%s", name, job_id, task_id)

    first_skill = config.skills[0]
    instruction = _skill_instruction(
        first_skill, name, 0, len(config.skills), job_id, task_id, description
    )

    memory_context = read_memory_context(root)
    if memory_context:
        instruction = (
            f"Memory context (from `.dmx/activeContext.md`):\n{memory_context}\n\n{instruction}"
        )
    return instruction


# ---------------------------------------------------------------------------
# Validator execution + policy decision
# ---------------------------------------------------------------------------


def _finish_loop(
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
    yet met, the loop transitions to ``iterating`` and restarts from the
    first skill — this is not recorded as a failure.

    If ``on_complete`` declares a ``trigger_loop`` for this outcome
    (success, failure, or warning), the next loop starts automatically —
    no second ``run_loop`` call from the agent is required.

    Every branch appends a one-line breadcrumb to ``.dmx/activeContext.md``
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
    }

    validator_results = run_validators(config, root, skill_outputs, loop_context)
    decision = evaluate_validator_results(config, validator_results)
    outcome = decision["outcome"]
    next_status = decision["next_status"]

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

    if next_status == LoopStatus.paused.value:
        logger.info(
            "loop %s: validators failed, pausing for review — %s", loop_name, decision["message"]
        )
        append_session_note(
            root,
            f"{loop_name} loop paused for validator review (job `{job_id}`, "
            f"task `{short_task}`): {decision['message']}",
        )
        return _validator_failure_message(loop_name, job_id, task_id, decision["message"])

    if config.repeat_until and not evaluate_repeat_until(config.repeat_until, root):
        current_state = read_state(root, job_id, loop_name, task_id)
        iteration = current_state.get("iteration_count", 0) + 1
        write_state(
            root,
            job_id,
            loop_name,
            task_id,
            {
                "status": LoopStatus.iterating.value,
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
            f"{loop_name} loop iterating (round {iteration}) — repeat_until "
            f"'{config.repeat_until}' not yet met (job `{job_id}`).",
        )
        return _iterating_message(loop_name, job_id, task_id, iteration, config)

    next_loop: str | None = None
    if outcome == LoopOutcome.success.value:
        next_loop = config.on_complete.on_success.trigger_loop
    elif outcome == LoopOutcome.failure.value:
        next_loop = config.on_complete.on_failure.trigger_loop
    else:
        next_loop = config.on_complete.on_warning.trigger_loop

    logger.info("loop %s complete — outcome=%s next_loop=%s", loop_name, outcome, next_loop)

    if next_loop:
        append_session_note(
            root,
            f"{loop_name} loop completed (outcome: {outcome}) — "
            f"chained to {next_loop} (job `{job_id}`).",
        )
        chain_header = (
            f"**{loop_name} loop — complete** (outcome: `{outcome}`)\n\n"
            f"Chaining automatically to **{next_loop}** loop.\n\n"
        )
        return chain_header + _start_loop(root, next_loop)

    append_session_note(root, f"{loop_name} loop completed (outcome: {outcome}) (job `{job_id}`).")
    return _complete_message(loop_name, job_id, outcome)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_loop_tools(app: FastMCP) -> None:
    """Register run_loop, get_skill_definition, loop_advance, and loop_continue on *app*."""

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
        return _start_loop(root, name, description)

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
        raw = _resolve_skill(name, root)
        if raw is None:
            return f"Skill '{name}' not found. Check the skill name or add it to .dmx/skills/."
        return _strip_frontmatter(raw)

    @app.tool
    async def loop_advance(
        ctx: Context,
        output: str,
        workspace_root: str | None = None,
    ) -> str:
        """Advance the active loop after a skill completes.

        Call this after every skill run, passing the full skill output.
        The orchestrator persists state, applies the human gate policy, and
        returns either the next skill instruction or a pause/completion message.

        Args:
            output: Full output from the skill that just completed.
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            Plain-English instruction or status message for the agent.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"

        try:
            found = _find_active(root)
        except AmbiguousActiveRun as exc:
            return f"Error: {exc}"
        if not found:
            return "No active loop run found. Start a loop with `run_loop` first."
        job_id, loop_name, task_id = found
        job_id = _maybe_promote_pending_job(root, job_id)

        state = read_state(root, job_id, loop_name, task_id)
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
            return f"Error reloading loop config: {exc}"

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
            },
        )

        if next_idx < len(skills):
            # More skills remain.
            if config.human_gate:
                write_state(
                    root,
                    job_id,
                    loop_name,
                    task_id,
                    {
                        "status": LoopStatus.paused.value,
                    },
                )
                return _pause_message(loop_name, job_id, task_id, next_idx, len(skills))
            else:
                # human_gate: false — return next skill instruction immediately.
                next_skill = skills[next_idx]
                return _skill_instruction(
                    next_skill, loop_name, next_idx, len(skills), job_id, task_id
                )
        elif config.human_gate:
            # All skills complete but human gate is on — pause for review before
            # running validators and chaining. loop_continue triggers the final step.
            write_state(
                root,
                job_id,
                loop_name,
                task_id,
                {
                    "status": LoopStatus.paused.value,
                },
            )
            return _pause_message(loop_name, job_id, task_id, next_idx, len(skills))
        else:
            # All skills complete — run validators and apply policy.
            return _finish_loop(root, job_id, loop_name, task_id, config, skill_outputs)

    @app.tool
    async def loop_continue(
        ctx: Context,
        workspace_root: str | None = None,
    ) -> str:
        """Resume a paused loop.

        Finds the active run and returns the next skill instruction. Call
        this after reviewing output at a human gate.

        Args:
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            Plain-English instruction for the agent.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"

        try:
            found = _find_active(root)
        except AmbiguousActiveRun as exc:
            return f"Error: {exc}"
        if not found:
            return (
                "No active loop run found. "
                "Start a loop with `run_loop` or check if the previous loop completed."
            )
        job_id, loop_name, task_id = found
        job_id = _maybe_promote_pending_job(root, job_id)

        state = read_state(root, job_id, loop_name, task_id)

        if state["status"] != LoopStatus.paused.value:
            if state["status"] == LoopStatus.running.value:
                return (
                    f"The {loop_name} loop is currently running. "
                    "Wait for the skill to finish before calling loop_continue."
                )
            return (
                f"Loop '{loop_name}' is not paused (status: {state['status']}). "
                "Nothing to continue."
            )

        skills: list[str] = state["skills"]
        idx: int = state["current_skill_index"]

        write_state(
            root,
            job_id,
            loop_name,
            task_id,
            {
                "status": LoopStatus.running.value,
            },
        )

        if idx >= len(skills):
            # All skills already complete — human approved (or a previous
            # validator run paused for review). Run validators and finish.
            logger.info("loop_continue: %s all skills done, running validators", loop_name)

            try:
                config = _resolve_loop(loop_name, root)
            except Exception as exc:  # noqa: BLE001
                return f"Error reloading loop config: {exc}"

            skill_outputs: dict[str, str] = state.get("skill_outputs", {})
            return _finish_loop(root, job_id, loop_name, task_id, config, skill_outputs)

        logger.info(
            "loop_continue: %s job=%s task=%s skill_index=%d", loop_name, job_id, task_id, idx
        )

        next_skill = skills[idx]
        return _skill_instruction(next_skill, loop_name, idx, len(skills), job_id, task_id)
