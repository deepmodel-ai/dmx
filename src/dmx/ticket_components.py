"""Which components the current ticket covers (GH-86).

A ticket's components are, in this order of reasons:

- ``spec``: the ``components`` list in its ``spec.md`` frontmatter, which
  ``create-ticket`` writes and the developer corrects by editing it;
- ``changed files``: every component whose ``paths`` match a file the
  branch changed since it left ``branch_base``, committed or not. These
  can't be removed by editing the list, because the change does touch them;
- ``IDE folder``: when the spec names no components (no spec, or no
  ``components`` key), the component holding the folder the IDE opened
  (#83).

Files under ``.dmx/`` are dmx's own bookkeeping, not code, so they never
add a component and are not repo-wide changes.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from dmx.components import (
    Component,
    ComponentSet,
    SkippedComponent,
    has_components,
    load_components,
)
from dmx.exceptions import AmbiguousActiveRun
from dmx.loop_state import SPEC_FILE, find_pending_run, resolve_job_id, ticket_file
from dmx.workspace import ide_folder

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "TicketComponent",
    "TicketComponents",
    "changed_files",
    "spec_components",
    "ticket_components",
    "with_components_line",
]

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

REASON_SPEC = "spec"
REASON_CHANGED = "changed files"
REASON_IDE_FOLDER = "IDE folder"


@dataclass(frozen=True)
class TicketComponent:
    """One of the ticket's components. ``component`` is None for an unknown id."""

    id: str
    reason: str
    component: Component | None


@dataclass(frozen=True)
class TicketComponents:
    """The ticket's components, the repo-wide files it changed, and skipped files."""

    entries: tuple[TicketComponent, ...] = ()
    repo_wide_files: tuple[str, ...] = ()
    skipped: tuple[SkippedComponent, ...] = ()

    @property
    def components(self) -> tuple[Component, ...]:
        return tuple(entry.component for entry in self.entries if entry.component is not None)

    def line(self) -> str:
        """``Components: ledger (.dmx/components/ledger/), ...``."""
        if not self.entries:
            return "Components: none (repo-wide)"
        parts = [
            f"{entry.id} ({entry.component.folder})"
            if entry.component is not None
            else f"{entry.id} (not a component)"
            for entry in self.entries
        ]
        return f"Components: {', '.join(parts)}"


def spec_components(spec: Path) -> tuple[str, ...] | None:
    """The ``components`` list in *spec*'s frontmatter, or None when it has none.

    An empty list is kept: the ticket says it covers no component.
    """
    try:
        content = spec.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return None
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or "components" not in data:
        return None
    value = data["components"]
    if value is None:
        return ()
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return None
    return tuple(str(item).strip() for item in value if str(item).strip())


def _git(root: Path, *args: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return result.stdout.splitlines() if result.returncode == 0 else []


def _base_ref(root: Path) -> str | None:
    # Imported here: loop_tools imports this module.
    from dmx.loop_tools import _read_branch_base  # noqa: PLC0415, PLC2701

    base = _read_branch_base(root)
    if base is None:
        return None
    for ref in (f"origin/{base}", base):
        if _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            return ref
    return None


def changed_files(root: Path, *, include_dmx: bool = False) -> tuple[str, ...]:
    """Files the branch changed since it left ``branch_base``, sorted.

    Committed, staged, unstaged and untracked changes, relative to *root*
    (the folder holding ``.dmx/``) and limited to it. Both sides of a
    rename count. Files under ``.dmx/`` are left out unless *include_dmx*.
    """
    found: set[str] = set()
    ref = _base_ref(root)
    if ref is not None:
        merge_base = _git(root, "merge-base", "HEAD", ref)
        if merge_base:
            found.update(
                _git(
                    root, "diff", "--name-only", "--no-renames", "--relative", merge_base[0], "HEAD"
                )
            )
    found.update(_git(root, "diff", "--name-only", "--no-renames", "--relative", "HEAD"))
    found.update(_git(root, "ls-files", "--others", "--exclude-standard"))
    return tuple(
        sorted(path for path in found if path and (include_dmx or not path.startswith(".dmx/")))
    )


def _folder_component(root: Path, folder: Path | None, components: ComponentSet) -> str | None:
    if folder is None:
        return None
    try:
        rel = folder.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    if rel in ("", "."):
        return None
    owner = components.component_for(rel)
    return owner.id if owner else None


def ticket_components(
    root: Path, job_id: str | None, *, folder: Path | None = None
) -> TicketComponents:
    """The components job *job_id* covers, with the reason for each.

    Args:
        root: The folder holding ``.dmx/``.
        job_id: The current job, or None when there is none.
        folder: The folder the IDE opened, for a ticket whose spec names no
            components.
    """
    components = load_components(root)
    reasons: dict[str, str] = {}

    recorded = spec_components(ticket_file(root, job_id, SPEC_FILE)) if job_id else None
    for component_id in recorded or ():
        reasons.setdefault(component_id, REASON_SPEC)

    owners, repo_wide = components.components_for(changed_files(root))
    for owner in owners:
        reasons.setdefault(owner.id, REASON_CHANGED)

    if recorded is None:
        from_folder = _folder_component(root, folder, components)
        if from_folder is not None:
            reasons.setdefault(from_folder, REASON_IDE_FOLDER)

    known = sorted(key for key in reasons if components.get(key) is not None)
    unknown = sorted(key for key in reasons if components.get(key) is None)
    entries = tuple(
        TicketComponent(id=key, reason=reasons[key], component=components.get(key))
        for key in (*known, *unknown)
    )
    return TicketComponents(entries=entries, repo_wide_files=repo_wide, skipped=components.skipped)


# Skipped component files already named, so each is named once per server.
_REPORTED_SKIPPED: set[tuple[Path, str]] = set()


def current_job(root: Path) -> str | None:
    """The job a loop message is about: the branch's, or a spec loop's pending one."""
    try:
        job_id = resolve_job_id(root)
    except AmbiguousActiveRun:
        return None
    if not ticket_file(root, job_id, SPEC_FILE).is_file():
        with contextlib.suppress(AmbiguousActiveRun):
            pending = find_pending_run(root)
            if pending is not None:
                return pending[0]
    return job_id


def with_components_line(root: Path, message: str) -> str:
    """Put the ticket's components line first, in a repo with components.

    Also names each skipped ``component.yaml`` once per server, at the end.
    A repo without ``.dmx/components/`` gets *message* unchanged.
    """
    if not has_components(root):
        return message
    found = ticket_components(root, current_job(root), folder=ide_folder())
    notes = []
    for skipped in found.skipped:
        key = (root.resolve(), skipped.path)
        if key in _REPORTED_SKIPPED:
            continue
        _REPORTED_SKIPPED.add(key)
        notes.append(f"Skipped `{skipped.path}`: {skipped.reason}. Fix it to load that component.")
    message = f"{found.line()}\n\n{message}"
    if notes:
        message = "\n\n".join([message, *notes])
    return message
