"""Learnings that wait in job folders for their owner (GH-88).

In a repo with components, each item under ``## Open Learnings`` or
``## Open Decisions`` in a ticket's ``activeContext.md`` starts with its
owner: ``- [ledger] ...`` or ``- [repo-wide] ...``. An untagged item counts
as ``repo-wide``. A PR writes the items it may (the memory of components
whose code it changed) and leaves the rest, which merge with it and wait.
When an owner's PR, or ``/dmx/update-memory <owner>``, folds one into
memory, it is marked with where it went::

    - [ledger] Amounts are int64 minor units → ledger, #418

An unmarked item in a job folder other than the current ticket's is
waiting. This module parses items and writes marks; deciding an item's
owner and editing memory are the agent's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.components import is_valid_id
from dmx.exceptions import WorkspaceRootInvalid
from dmx.loop_state import ACTIVE_CONTEXT_FILE
from dmx.workspace import resolve_workspace_root

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "REPO_WIDE",
    "InboxItem",
    "MarkResult",
    "mark_folded",
    "read_inbox",
    "register_learnings_tools",
    "waiting_counts",
    "waiting_learnings",
]

REPO_WIDE = "repo-wide"
SECTIONS = ("Open Learnings", "Open Decisions")

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^[-*]\s+")
_TAG_RE = re.compile(r"^\[([a-z0-9][a-z0-9_-]*)\]\s*")
_MARK_RE = re.compile(r"\s+→\s+([^,\n]+?)(?:,\s*(\S+))?\s*$")


@dataclass(frozen=True)
class InboxItem:
    """One open learning or decision."""

    job: str
    section: str
    owner: str
    tagged: bool
    text: str
    folded_to: str | None
    ref: str | None
    # Line numbers in the file, for marking: first and last line of the item.
    start: int
    end: int

    @property
    def first_line(self) -> str:
        return self.text.split("\n", 1)[0]

    def as_dict(self) -> dict[str, Any]:
        return {"job": self.job, "owner": self.owner, "section": self.section, "text": self.text}


def _parse(lines: list[str], job: str) -> list[InboxItem]:
    items: list[InboxItem] = []
    section: str | None = None
    current: list[tuple[int, str]] = []

    def flush() -> None:
        if section is None or not current:
            current.clear()
            return
        start, first = current[0]
        body = [_BULLET_RE.sub("", first, count=1).strip()]
        body += [line.strip() for _n, line in current[1:]]
        text = "\n".join(body)
        folded_to = ref = None
        mark = _MARK_RE.search(text)
        if mark:
            folded_to, ref = mark.group(1).strip(), mark.group(2)
            text = text[: mark.start()]
        tag = _TAG_RE.match(text)
        owner = tag.group(1) if tag else REPO_WIDE
        if tag:
            text = text[tag.end() :]
        items.append(
            InboxItem(
                job=job,
                section=section,
                owner=owner,
                tagged=tag is not None,
                text=text.strip(),
                folded_to=folded_to,
                ref=ref,
                start=start,
                end=current[-1][0],
            )
        )
        current.clear()

    for number, line in enumerate(lines):
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            section = heading.group(1) if heading.group(1) in SECTIONS else None
            continue
        if section is None:
            continue
        if not line.strip():
            flush()
        elif line[:1] in (" ", "\t") and current:
            current.append((number, line))
        else:
            flush()
            current.append((number, line))
    flush()
    return items


def read_inbox(path: Path, *, job: str | None = None) -> list[InboxItem]:
    """The open learnings and decisions in one ``activeContext.md``."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    return _parse(lines, job or path.parent.name)


def _inboxes(root: Path, exclude_job: str | None) -> list[InboxItem]:
    jobs = root / ".dmx" / "jobs"
    if not jobs.is_dir():
        return []
    items: list[InboxItem] = []
    for folder in sorted(jobs.iterdir()):
        if folder.is_dir() and folder.name != exclude_job:
            items.extend(read_inbox(folder / ACTIVE_CONTEXT_FILE, job=folder.name))
    return items


def waiting_learnings(
    root: Path, owners: set[str] | None, *, exclude_job: str | None
) -> list[InboxItem]:
    """Unmarked items for *owners* (every owner when None) outside *exclude_job*."""
    return [
        item
        for item in _inboxes(root, exclude_job)
        if item.folded_to is None and (owners is None or item.owner in owners)
    ]


def waiting_counts(root: Path, *, exclude_job: str | None) -> dict[str, int]:
    """How many learnings wait for each owner, sorted by owner."""
    counts: dict[str, int] = {}
    for item in waiting_learnings(root, None, exclude_job=exclude_job):
        counts[item.owner] = counts.get(item.owner, 0) + 1
    return dict(sorted(counts.items()))


def has_waiting(path: Path) -> bool:
    """True when one ``activeContext.md`` has an unmarked open item."""
    return any(item.folded_to is None for item in read_inbox(path))


@dataclass(frozen=True)
class MarkResult:
    marked: tuple[str, ...]
    already: tuple[str, ...]
    not_found: tuple[str, ...]


def mark_folded(path: Path, items: list[str], *, target: str, ref: str) -> MarkResult:
    """Append ``→ target, ref`` to each item in *items*, matched by text or first line."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return MarkResult((), (), tuple(items))
    parsed = _parse([line.rstrip("\n") for line in lines], path.parent.name)
    marked: list[str] = []
    already: list[str] = []
    not_found: list[str] = []
    for wanted in items:
        key = wanted.strip()
        match = next((i for i in parsed if key in (i.text, i.first_line)), None)
        if match is None:
            not_found.append(wanted)
        elif match.folded_to is not None:
            already.append(wanted)
        else:
            line = lines[match.end]
            ending = "\n" if line.endswith("\n") else ""
            lines[match.end] = f"{line.rstrip()} → {target}, {ref}{ending}"
            marked.append(wanted)
    if marked:
        path.write_text("".join(lines), encoding="utf-8")
    return MarkResult(tuple(marked), tuple(already), tuple(not_found))


def register_learnings_tools(app: FastMCP) -> None:
    """Register ``mark_learnings_folded`` on *app*."""

    @app.tool
    async def mark_learnings_folded(
        ctx: Context,
        job: str,
        items: list[str],
        target: str,
        ref: str,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        """Mark waiting learnings as folded into *target*'s memory.

        Called by ``create-pr`` and ``/dmx/update-memory`` after writing
        learnings from another ticket's ``activeContext.md`` into memory, so
        they stop counting as waiting. Appends ``→ target, ref`` to each
        item, matched by its text or first line, as ``get_context``'s
        ``waiting_learnings`` returns it.

        Args:
            job: The job folder the items are in, e.g. ``GH-412``.
            items: The items' text.
            target: The component id, or ``repo-wide``, they went to.
            ref: The PR (``#418``), or the branch name before the PR exists.
            workspace_root: Repo root path override. Auto-detected if omitted.

        Returns:
            ``marked``, ``already`` (marked before) and ``not_found``.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return {"error": f"Could not resolve a valid workspace root: {exc}"}
        if not job or "/" in job or "\\" in job or job in (".", ".."):
            return {"error": f"`{job}` is not a job folder name."}
        if target != REPO_WIDE and not is_valid_id(target):
            return {"error": f"`{target}` is not a component id or `repo-wide`."}
        path = root / ".dmx" / "jobs" / job / ACTIVE_CONTEXT_FILE
        if not path.is_file():
            return {"error": f"`{job}` is not a job with an activeContext.md."}
        result = mark_folded(path, items, target=target, ref=ref.strip())
        return {
            "marked": list(result.marked),
            "already": list(result.already),
            "not_found": list(result.not_found),
        }
