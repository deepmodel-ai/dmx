"""Evaluation for loop config ``repeat_until`` conditions.

Known conditions are evaluated deterministically by the orchestrator — the
same principle as validator resolution: no guessing, no LLM judgment call.
An unknown condition is treated as met (the loop completes rather than
iterating) with a warning logged, so a misconfigured loop doesn't spin
forever on a condition nobody can evaluate.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from dmx.loop_state import TASKS_FILE, ticket_file

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["evaluate_repeat_until"]

logger = logging.getLogger(__name__)

_UNCHECKED_TASK_RE = re.compile(r"^\s*-\s*\[\s?\]", re.MULTILINE)


def _all_phases_complete(workspace_root: Path, job_id: str) -> bool:
    """True when the job's ``tasks.md`` has no unchecked ``- [ ]`` items.

    If ``tasks.md`` doesn't exist, there is nothing to iterate on — treat
    the condition as met.
    """
    tasks = ticket_file(workspace_root, job_id, TASKS_FILE)
    if not tasks.exists():
        return True
    content = tasks.read_text(encoding="utf-8")
    return not _UNCHECKED_TASK_RE.search(content)


_EVALUATORS = {
    "all_phases_complete": _all_phases_complete,
}


def evaluate_repeat_until(condition: str, workspace_root: Path, job_id: str) -> bool:
    """Return True if *condition* is met (the loop should stop iterating).

    Unknown conditions are treated as met, with a warning logged.
    """
    evaluator = _EVALUATORS.get(condition)
    if evaluator is None:
        logger.warning("unknown repeat_until condition '%s' — treating as met", condition)
        return True
    return evaluator(workspace_root, job_id)
