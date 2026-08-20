"""Loop runtime MCP tools: run_loop, loop_advance, loop_continue.

These three tools expose the dmx loop runtime through the existing MCP server.
The agent is always the executor — dmx never runs skills directly.  Each tool
returns a plain-English instruction that the agent follows.

Tool contracts
--------------

``run_loop(name)``
    - Reads ``.dmx/loops/{name}.yaml`` (app repo first, bundled fallback).
    - Generates job_id (ticket ID or branch) and task_id (UUID4).
    - Writes initial state to ``.dmx/jobs/{job_id}/{name}-{task_id}.json``.
    - Updates ``.dmx/loop-state.json`` active pointer.
    - Returns: instruction to run the first skill, then call ``loop_advance``.

``loop_advance(output)``
    - Reads active state from ``.dmx/loop-state.json``.
    - Persists the skill output.
    - If more skills remain and ``human_gate: true``: pauses, returns pause msg.
    - If more skills remain and ``human_gate: false``: returns next instruction.
    - If all skills complete: runs validators, applies policy
      (``failure_handling`` / ``on_optional_failure``), writes outcome,
      returns completion message (and chains next loop if configured).

``loop_continue()``
    - Reads ``.dmx/loop-state.json``.
    - Advances ``current_skill_index`` past the last completed skill.
    - Returns the next skill instruction.
"""

from __future__ import annotations

import importlib.resources as pkg
import logging
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastmcp import Context, FastMCP

from dmx.loop_schema import LoopConfig, load_loop, load_loops_dir
from dmx.loop_state import (
    LoopOutcome,
    LoopStatus,
    clear_active_pointer,
    current_branch,
    make_task_id,
    read_active_pointer,
    read_state,
    resolve_job_id,
    write_initial_state,
    write_state,
)
from dmx.validator_runner import evaluate_validator_results, run_validators

__all__ = ["register_loop_tools"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bundled loops directory
# ---------------------------------------------------------------------------


def _bundled_loops_dir() -> Path:
    return Path(str(pkg.files("dmx") / "loops"))


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
    raise FileNotFoundError(
        f"Loop '{name}' not found. "
        f"Available loops: {available or ['(none)']}"
    )


# ---------------------------------------------------------------------------
# Workspace root resolution (mirrors tools.py)
# ---------------------------------------------------------------------------


async def _resolve_workspace_root(ctx: Context, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    try:
        roots = await ctx.list_roots()
        if roots:
            first_root = roots[0]
            uri = getattr(first_root, "uri", None) or str(first_root)
            parsed = urlparse(uri)
            if parsed.scheme == "file":
                return Path(unquote(parsed.path))
            return Path(unquote(uri))
    except Exception:  # noqa: BLE001
        pass
    import os
    return Path(os.getcwd())


# ---------------------------------------------------------------------------
# Skill instruction builder
# ---------------------------------------------------------------------------


def _skill_instruction(
    skill_name: str,
    loop_name: str,
    index: int,
    total: int,
    description: str | None = None,
) -> str:
    """Return the agent instruction for running a single skill."""
    desc_block = (
        f"\nContext for this skill: {description.strip().splitlines()[0]}\n"
        if description and index == 0
        else ""
    )
    return (
        f"LOOP RUNTIME — do not suggest workflow commands or alternative paths.\n\n"
        f"Run /{skill_name} now. (Loop: {loop_name} | Skill {index + 1}/{total}){desc_block}\n\n"
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
        f"Review the output above. When ready, call `loop_continue` to proceed."
    )


def _complete_message(
    loop_name: str,
    job_id: str,
    outcome: str,
    next_loop: str | None,
) -> str:
    icon = {"success": "✓", "failure": "✗", "warning": "⚠"}.get(outcome, "?")
    msg = f"**{loop_name} loop — complete {icon}**\n\nJob: `{job_id}` | Outcome: `{outcome}`"
    if next_loop:
        msg += f"\n\nChaining to: **{next_loop}** loop. Call `run_loop` with name=`{next_loop}`."
    return msg


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
        f"{message}"
    )


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
    ``on_optional_failure``. On pause, the active pointer is left in place —
    the next ``loop_continue`` call re-runs validators (acting as a retry).
    On complete/failed, the active pointer is cleared and the configured
    ``on_complete`` loop is surfaced.
    """
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

    write_state(root, job_id, loop_name, task_id, {
        "validator_results": validator_results,
        "outcome": outcome,
        "status": next_status,
    })

    if next_status == LoopStatus.paused.value:
        logger.info(
            "loop %s: validators failed, pausing for review — %s", loop_name, decision["message"]
        )
        return _validator_failure_message(loop_name, job_id, task_id, decision["message"])

    clear_active_pointer(root)

    next_loop: str | None = None
    if outcome == LoopOutcome.success.value:
        next_loop = config.on_complete.on_success.trigger_loop
    elif outcome == LoopOutcome.failure.value:
        next_loop = config.on_complete.on_failure.trigger_loop
    else:
        next_loop = config.on_complete.on_warning.trigger_loop

    logger.info("loop %s complete — outcome=%s next_loop=%s", loop_name, outcome, next_loop)
    return _complete_message(loop_name, job_id, outcome, next_loop)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_loop_tools(app: FastMCP) -> None:
    """Register run_loop, loop_advance, and loop_continue on *app*."""

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
        root = await _resolve_workspace_root(ctx, workspace_root)

        try:
            config = _resolve_loop(name, root)
        except FileNotFoundError as exc:
            return f"Error: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"Error loading loop config '{name}': {exc}"

        job_id = resolve_job_id(root)
        task_id = make_task_id()

        write_initial_state(
            workspace_root=root,
            loop_name=name,
            job_id=job_id,
            task_id=task_id,
            skills=config.skills,
        )

        write_state(root, job_id, name, task_id, {"status": LoopStatus.running.value})

        logger.info("run_loop: name=%s job=%s task=%s", name, job_id, task_id)

        first_skill = config.skills[0]
        return _skill_instruction(first_skill, name, 0, len(config.skills), description)

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
        root = await _resolve_workspace_root(ctx, workspace_root)

        pointer = read_active_pointer(root)
        if not pointer:
            return (
                "No active loop run found. Start a loop with `run_loop` first."
            )

        job_id = pointer["active_job_id"]
        task_id = pointer["active_task_id"]
        loop_name = pointer["active_loop_name"]

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
        write_state(root, job_id, loop_name, task_id, {
            "current_skill_index": next_idx,
            "skills_completed": skills_completed,
            "skill_outputs": skill_outputs,
        })

        if next_idx < len(skills):
            # More skills remain.
            if config.human_gate:
                write_state(root, job_id, loop_name, task_id, {
                    "status": LoopStatus.paused.value,
                })
                return _pause_message(loop_name, job_id, task_id, next_idx, len(skills))
            else:
                # human_gate: false — return next skill instruction immediately.
                next_skill = skills[next_idx]
                return _skill_instruction(next_skill, loop_name, next_idx, len(skills))
        elif config.human_gate:
            # All skills complete but human gate is on — pause for review before
            # running validators and chaining. loop_continue triggers the final step.
            write_state(root, job_id, loop_name, task_id, {
                "status": LoopStatus.paused.value,
            })
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

        Reads the active run pointer and returns the next skill instruction.
        Call this after reviewing output at a human gate.

        Args:
            workspace_root: Repo root path override.  Auto-detected if omitted.

        Returns:
            Plain-English instruction for the agent.
        """
        root = await _resolve_workspace_root(ctx, workspace_root)

        pointer = read_active_pointer(root)
        if not pointer:
            return (
                "No active loop run found. "
                "Start a loop with `run_loop` or check if the previous loop completed."
            )

        job_id = pointer["active_job_id"]
        task_id = pointer["active_task_id"]
        loop_name = pointer["active_loop_name"]

        state = read_state(root, job_id, loop_name, task_id)

        if state["status"] != LoopStatus.paused.value:
            return (
                f"Loop '{loop_name}' is not paused (status: {state['status']}). "
                "Nothing to continue."
            )

        skills: list[str] = state["skills"]
        idx: int = state["current_skill_index"]

        write_state(root, job_id, loop_name, task_id, {
            "status": LoopStatus.running.value,
        })

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

        logger.info("loop_continue: %s job=%s task=%s skill_index=%d", loop_name, job_id, task_id, idx)

        next_skill = skills[idx]
        return _skill_instruction(next_skill, loop_name, idx, len(skills))
