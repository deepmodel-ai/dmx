"""Loop-level memory hooks.

Property 3 of a loop: "the loop reads persistent context before running and
writes learnings back when it completes." This module implements that
deterministically at the runtime level — distinct from the coding-agent
skills (``update-memory``, ``commit``, ``create-pr``) that do deeper,
judgment-based memory bank reconciliation.

The loop runtime only touches ``.dmx/activeContext.md``, and only in the
lightweight ways the file already supports (see ``dmx-update-memory``):

- Before running: surface the current "Open Learnings" / "Open Decisions"
  sections to the agent, so the first skill starts with context instead of
  a blank slate.
- After finishing: append a one-line breadcrumb to "## Session Notes"
  recording what the loop did. This is a log entry, not judgment — promoting
  it into durable knowledge is still the job of ``/dmx/update-memory``.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["read_memory_context", "append_session_note"]

_ACTIVE_CONTEXT_REL = Path(".dmx") / "activeContext.md"

_SKELETON = "## Open Learnings\n\n## Open Decisions\n\n## Session Notes\n"

_MAX_SESSION_NOTES = 10

_SECTION_RE = r"^##\s+{heading}\s*\n(.*?)(?=^##\s|\Z)"


def _active_context_path(workspace_root: Path) -> Path:
    return workspace_root / _ACTIVE_CONTEXT_REL


def _section_body(content: str, heading: str) -> str:
    pattern = _SECTION_RE.format(heading=re.escape(heading))
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def read_memory_context(workspace_root: Path) -> str:
    """Return the current Open Learnings + Open Decisions, formatted for display.

    Returns an empty string if ``.dmx/activeContext.md`` doesn't exist or
    both sections are empty — nothing worth surfacing to the agent.
    """
    path = _active_context_path(workspace_root)
    if not path.exists():
        return ""

    content = path.read_text(encoding="utf-8")
    learnings = _section_body(content, "Open Learnings")
    decisions = _section_body(content, "Open Decisions")

    blocks = []
    if learnings:
        blocks.append(f"Open Learnings:\n{learnings}")
    if decisions:
        blocks.append(f"Open Decisions:\n{decisions}")

    return "\n\n".join(blocks)


def append_session_note(workspace_root: Path, note: str) -> None:
    """Append a one-line breadcrumb to the Session Notes section.

    Creates ``.dmx/activeContext.md`` with the standard learning-inbox
    skeleton if it doesn't exist yet. Keeps only the most recent
    ``_MAX_SESSION_NOTES`` entries, matching the trimming behaviour
    described for ``/dmx/update-memory``.
    """
    path = _active_context_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    content = path.read_text(encoding="utf-8") if path.exists() else _SKELETON

    if not re.search(r"^##\s+Session Notes\s*$", content, re.MULTILINE):
        content = content.rstrip() + "\n\n## Session Notes\n"

    pattern = _SECTION_RE.format(heading=re.escape("Session Notes"))
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)

    existing_body = match.group(1) if match else ""
    notes = [line.strip() for line in existing_body.splitlines() if line.strip()]
    notes.append(f"- {note}")
    notes = notes[-_MAX_SESSION_NOTES:]
    new_body = "\n".join(notes) + "\n"

    if match:
        new_content = content[: match.start(1)] + new_body + content[match.end(1) :]
    else:
        new_content = content + new_body

    path.write_text(new_content, encoding="utf-8")
