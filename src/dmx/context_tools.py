"""``get_context``: what the current ticket should read and what applies to it (GH-86).

In a repo with components, the rule calls this before a task instead of
reading a fixed list of memory files. It returns the ticket's components,
the memory files to read, the ticket's own files, and the skills and
validators that apply, with their paths. Later 0.7 tickets fill in more:
dependency memory and waiting learnings (#88), component skills and
validators (#90).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.component_config import component_skill_catalog, parse_validator_entry
from dmx.component_tests import component_test
from dmx.components import ComponentSet, has_components, load_components
from dmx.exceptions import WorkspaceRootInvalid
from dmx.learnings import REPO_WIDE, waiting_counts, waiting_learnings
from dmx.loop_schema import load_loops_dir
from dmx.loop_state import TICKET_FILES, ticket_file
from dmx.loop_tools import (  # noqa: PLC2701 — same package, no public API split
    _bundled_loops_dir,
    _resolve_loop,
    _skill_catalog,
)
from dmx.shared_sources import SharedSourceError, read_shared_sources, source_root
from dmx.ticket_components import (
    TicketComponents,
    changed_files,
    current_job,
    ticket_components,
)
from dmx.validator_runner import ValidatorRunError, resolve_validator_path
from dmx.workspace import ide_folder, resolve_workspace_root

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["CORE_MEMORY_FILES", "build_context", "editable_memory", "register_context_tools"]

# The repo-wide memory files, in the order the rule lists them.
CORE_MEMORY_FILES = ("projectbrief.md", "productContext.md", "systemPatterns.md", "techContext.md")

# Changing any of these is a change to the repo-wide dmx setup, which lets a
# PR edit repo-wide memory too (GH-88). Memory edits themselves don't count.
_REPO_WIDE_SETUP = (
    ".dmx/config.md",
    ".dmx/loops/",
    ".dmx/skills/",
    "validators/",
    ".dmx/shared-sources.yaml",
    ".dmx/shared-sources.lock",
)


def _shown(root: Path, path: Path) -> str:
    """*path* relative to *root* when it is inside it, else absolute."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _with_dependencies(found: TicketComponents, components: ComponentSet) -> list[str]:
    """The ticket's component ids, then the ids they directly depend on, each once."""
    ids = [component.id for component in found.components]
    for component in found.components:
        for dep in component.depends_on:
            if dep not in ids and components.get(dep) is not None:
                ids.append(dep)
    return ids


def _memory_files(root: Path, ids: list[str]) -> list[str]:
    dmx = root / ".dmx"
    core = [dmx / name for name in CORE_MEMORY_FILES if (dmx / name).is_file()]
    others = sorted(
        path
        for path in dmx.glob("*.md")
        if path.name != "config.md" and path.name not in CORE_MEMORY_FILES
    )
    files = [_shown(root, path) for path in (*core, *others)]
    for component_id in ids:
        folder = root / ".dmx" / "components" / component_id
        files.extend(_shown(root, path) for path in sorted(folder.glob("*.md")))
    return files


def editable_memory(root: Path) -> list[str]:
    """The memory a PR from this branch may edit (GH-88).

    The components whose files the branch changed, plus ``repo-wide`` when
    it also changes the repo-wide dmx setup. Without components, all memory
    is repo-wide, as before.
    """
    if not has_components(root):
        return [REPO_WIDE]
    owners, _repo_wide = load_components(root).components_for(changed_files(root))
    editable = [owner.id for owner in owners]
    if any(path.startswith(_REPO_WIDE_SETUP) for path in changed_files(root, include_dmx=True)):
        editable.append(REPO_WIDE)
    return editable


def _skills(root: Path, component_ids: list[str]) -> list[dict[str, Any]]:
    try:
        catalog = _skill_catalog(root)
    except SharedSourceError:
        catalog = []
    catalog = component_skill_catalog(root, component_ids) + catalog
    return [
        {
            "name": name,
            "source": source,
            "path": _shown(root, path) if path else None,
            "description": description,
        }
        for name, description, source, path in catalog
    ]


def _loop_names(root: Path) -> list[str]:
    names = set(load_loops_dir(_bundled_loops_dir())) | set(load_loops_dir(root / ".dmx" / "loops"))
    try:
        for source in read_shared_sources(root):
            names |= set(load_loops_dir(source_root(root, source) / "loops"))
    except SharedSourceError:
        pass
    return sorted(names)


def _component_validators(root: Path, component_ids: list[str]) -> list[dict[str, Any]]:
    components = load_components(root)
    used: dict[str, tuple[str, str, list[str]]] = {}
    for component_id in component_ids:
        component = components.get(component_id)
        for loop_name, entries in sorted(
            ((component.validators if component else None) or {}).items()
        ):
            if not isinstance(entries, list):
                continue
            for index, raw in enumerate(entries):
                validator = parse_validator_entry(component_id, index, raw)
                name = validator.tool.split("/", 1)[1]
                used.setdefault(validator.tool, (component_id, name, []))[2].append(loop_name)
    entries_out: list[dict[str, Any]] = []
    for tool, (component_id, name, loops) in used.items():
        entry: dict[str, Any] = {"name": tool, "loops": loops}
        try:
            entry["path"] = _shown(root, resolve_validator_path(name, root, component=component_id))
        except ValidatorRunError as exc:
            entry["path"] = None
            entry["error"] = str(exc)
        entries_out.append(entry)
    return entries_out


def _validators(root: Path) -> list[dict[str, Any]]:
    used: dict[str, list[str]] = {}
    for loop_name in _loop_names(root):
        try:
            loop = _resolve_loop(loop_name, root)
        except (FileNotFoundError, SharedSourceError, ValueError):
            continue
        for validator in loop.validators:
            used.setdefault(validator.tool, []).append(loop_name)
    entries: list[dict[str, Any]] = []
    for name in sorted(used):
        entry: dict[str, Any] = {"name": name, "loops": used[name]}
        try:
            entry["path"] = _shown(root, resolve_validator_path(name, root))
        except ValidatorRunError as exc:
            entry["path"] = None
            entry["error"] = str(exc)
        entries.append(entry)
    return entries


def build_context(
    root: Path, *, folder: Path | None = None, waiting_for: list[str] | None = None
) -> dict[str, Any]:
    """Everything ``get_context`` returns, for *root*.

    ``waiting_for`` replaces whose waiting learnings are listed, for
    ``/dmx/update-memory <owner>``.
    """
    job_id = current_job(root)
    with_components = has_components(root)
    found = (
        ticket_components(root, job_id, folder=folder) if with_components else TicketComponents()
    )
    read_ids = _with_dependencies(found, load_components(root)) if with_components else []
    waiting = (
        waiting_learnings(
            root,
            set(waiting_for) if waiting_for is not None else {*read_ids, REPO_WIDE},
            exclude_job=job_id,
        )
        if with_components
        else []
    )
    return {
        "job_id": job_id,
        "has_components": with_components,
        "components_line": found.line() if with_components else None,
        "components": [
            {
                "id": entry.id,
                "reason": entry.reason,
                "folder": entry.component.folder if entry.component else None,
                "description": entry.component.description if entry.component else None,
                "depends_on": list(entry.component.depends_on) if entry.component else [],
                "test": component_test(root, entry.component) if entry.component else None,
            }
            for entry in found.entries
        ],
        "unknown_components": [entry.id for entry in found.entries if entry.component is None],
        "skipped_components": [
            {"path": skipped.path, "reason": skipped.reason} for skipped in found.skipped
        ],
        "repo_wide_files": list(found.repo_wide_files),
        "memory_files": _memory_files(root, read_ids),
        "editable_memory": editable_memory(root),
        "ticket_files": (
            [_shown(root, ticket_file(root, job_id, name)) for name in TICKET_FILES]
            if job_id
            else []
        ),
        "skills": _skills(root, [c.id for c in found.components]),
        "validators": _validators(root)
        + _component_validators(root, [c.id for c in found.components]),
        "waiting_learnings": [item.as_dict() for item in waiting],
        "waiting_counts": waiting_counts(root, exclude_job=job_id) if with_components else {},
    }


def register_context_tools(app: FastMCP) -> None:
    """Register ``get_context`` on *app*."""

    @app.tool
    async def get_context(
        ctx: Context, waiting_for: list[str] | None = None, workspace_root: str | None = None
    ) -> dict[str, Any]:
        """What the current ticket should read, and what applies to it.

        In a repo with ``.dmx/components/``, call this before a task and read
        every file in ``memory_files``, plus ``ticket_files``, instead of a
        fixed list. Without components it returns the repo-wide memory
        files, so the result is the same as the fixed list.

        ``memory_files`` covers the ticket's components and the ones they
        directly depend on. ``editable_memory`` is the memory a PR from this
        branch may edit: the components whose files it changed, plus
        ``repo-wide`` when it changes the repo-wide dmx setup.
        ``waiting_learnings`` are other tickets' unmarked learnings for those
        components and ``repo-wide``; fold one in, then mark it with
        ``mark_learnings_folded``.

        Args:
            waiting_for: List the waiting learnings of these owners (component
                ids or ``repo-wide``) instead, for ``/dmx/update-memory <owner>``.
            workspace_root: Repo root path override. Auto-detected if omitted.

        Returns:
            ``job_id``, ``has_components``, ``components_line``,
            ``components`` (each with ``id``, ``reason``, ``folder``,
            ``description`` and ``depends_on``), ``unknown_components``,
            ``skipped_components``, ``repo_wide_files``, ``memory_files``,
            ``editable_memory``, ``ticket_files``, ``skills``, ``validators``,
            ``waiting_learnings`` and ``waiting_counts``. Paths are relative
            to the repo root, except bundled files, which are absolute.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return {"error": f"Could not resolve a valid workspace root: {exc}"}
        return build_context(root, folder=ide_folder(), waiting_for=waiting_for)
