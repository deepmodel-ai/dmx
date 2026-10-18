"""Turn failed required checks into rework tasks (GH-115).

A loop whose ``on_failure.rework`` is set, or the ``rework_loop`` tool, uses
these helpers: decide which failed checks can become code tasks, write them
as a phase at the end of the ticket's ``tasks.md``, and count what is still
open in that phase. The text is built from the validators' own results, so
the same failure always gives the same tasks.

The loop runtime that starts the rework run and returns to the failed loop
is in ``dmx.loop_tools``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from dmx.loop_schema import LoopConfig

__all__ = [
    "MAX_FINDINGS",
    "MAX_LINE",
    "ReworkCheck",
    "append_rework_phase",
    "failed_required_checks",
    "not_reworkable_text",
    "rework_line",
    "rework_tasks",
    "unchecked_in_phase",
]

MAX_FINDINGS = 10
MAX_LINE = 200

_PHASE_NUMBER_RE = re.compile(r"^##\s+Phase\s+(\d+)\s*:", re.MULTILINE)
_HEADING_RE = re.compile(r"^##\s", re.MULTILINE)
_UNCHECKED_RE = re.compile(r"^\s*-\s*\[\s?\]", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^(?:[-*]\s*)?\[[ xX]?\]\s*")
_BULLET_RE = re.compile(r"^[-*]\s+")
_VALIDATOR_ERROR = "Validator error:"


@dataclass(frozen=True)
class ReworkCheck:
    """One failed required check. ``reason`` is set when it can't become a task."""

    name: str
    tool: str
    message: str
    findings: list[str] = field(default_factory=list)
    reason: str | None = None

    @property
    def reworkable(self) -> bool:
        return self.reason is None


def _first_line(text: object) -> str:
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _one_line(text: object) -> str:
    """First line of *text*, without a leading bullet or checkbox, cut to ``MAX_LINE``."""
    line = _first_line(text)
    line = _CHECKBOX_RE.sub("", line)
    line = _BULLET_RE.sub("", line).strip()
    if len(line) > MAX_LINE:
        line = line[: MAX_LINE - 1].rstrip() + "…"
    return line


def failed_required_checks(
    config: LoopConfig, validator_results: list[dict[str, Any]]
) -> list[ReworkCheck]:
    """Every failed required check in *config*, in declaration order.

    A check is not reworkable when its validator marks it
    ``"reworkable": false``, when the validator errored, or when it was
    never reported.
    """
    reported: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for result in validator_results:
        for check in result.get("checks") or []:
            if isinstance(check, dict) and isinstance(check.get("name"), str):
                reported[check["name"]] = (result, check)

    found: list[ReworkCheck] = []
    for validator in config.validators:
        for declared in validator.checks:
            if not declared.required:
                continue
            entry = reported.get(declared.name)
            if entry is None:
                found.append(
                    ReworkCheck(
                        name=declared.name,
                        tool=validator.tool,
                        message="",
                        reason=f"the check was not reported by `{validator.tool}`",
                    )
                )
                continue
            result, check = entry
            if check.get("pass"):
                continue
            tool = str(result.get("tool") or validator.tool)
            message = _one_line(check.get("message")) or _one_line(result.get("message"))
            raw_findings = check.get("findings")
            findings = [
                line
                for line in (
                    _one_line(item)
                    for item in (raw_findings if isinstance(raw_findings, list) else [])
                )
                if line
            ][:MAX_FINDINGS]
            reason: str | None = None
            result_message = _first_line(result.get("message"))
            if result_message.startswith(_VALIDATOR_ERROR) or message.startswith(_VALIDATOR_ERROR):
                reason = result_message or message
            elif check.get("reworkable") is False:
                reason = message or "the validator marked it not reworkable"
            found.append(
                ReworkCheck(
                    name=declared.name,
                    tool=tool,
                    message=message,
                    findings=findings,
                    reason=reason,
                )
            )
    return found


def rework_tasks(checks: list[ReworkCheck]) -> list[str]:
    """One ``- [ ]`` task per check, with its findings as plain sub-bullets."""
    tasks: list[str] = []
    for check in checks:
        message = _one_line(check.message) or "failed"
        lines = [f"- [ ] Fix `{check.name}` ({check.tool}): {message}"]
        findings = [line for line in map(_one_line, check.findings) if line]
        lines.extend(f"  - {finding}" for finding in findings[:MAX_FINDINGS])
        tasks.append("\n".join(lines))
    return tasks


def not_reworkable_text(checks: list[ReworkCheck]) -> str:
    """The lines that say which failed checks can't be reworked, and why."""
    lines = [
        f"- `{check.name}` ({check.tool}): {check.reason}"
        for check in checks
        if not check.reworkable
    ]
    if not lines:
        return ""
    return (
        "Not reworked: these failures can't become code tasks, so the loop "
        "paused as usual.\n" + "\n".join(lines)
    )


def _phase_heading(tasks_text: str, loop_name: str, round_number: int) -> str:
    numbers = [int(match) for match in _PHASE_NUMBER_RE.findall(tasks_text)]
    number = max(numbers, default=0) + 1
    return f"Phase {number}: Rework — {loop_name}, round {round_number}"


def append_rework_phase(
    tasks_path: Path,
    loop_name: str,
    round_number: int,
    short_task: str,
    tasks: list[str],
) -> str:
    """Append a rework phase to *tasks_path* and return its heading text."""
    text = tasks_path.read_text(encoding="utf-8") if tasks_path.exists() else ""
    heading = _phase_heading(text, loop_name, round_number)
    block = (
        f"## {heading}\n\n"
        f"From the failed `{loop_name}` run `{short_task}`. "
        "dmx wrote these tasks from the failed checks; edit them before the rework starts.\n\n"
        + "\n".join(tasks)
        + "\n"
    )
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        text += "\n"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(text + block, encoding="utf-8")
    return heading


def unchecked_in_phase(tasks_path: Path, heading: str) -> int:
    """Unchecked tasks under ``## {heading}``; 0 when the file or heading is gone."""
    if not tasks_path.exists():
        return 0
    text = tasks_path.read_text(encoding="utf-8")
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.MULTILINE)
    if match is None:
        return 0
    rest = text[match.end() :]
    following = _HEADING_RE.search(rest)
    section = rest[: following.start()] if following else rest
    return len(_UNCHECKED_RE.findall(section))


def rework_line(parent_loop: str, round_number: int, max_rounds: int, rework_loop: str) -> str:
    """``validate — rework round 1 of 2 (dev), then back to validate``."""
    return (
        f"{parent_loop} — rework round {round_number} of {max_rounds} ({rework_loop}), "
        f"then back to {parent_loop}"
    )
