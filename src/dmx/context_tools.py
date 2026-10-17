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

from dmx.components import has_components
from dmx.exceptions import WorkspaceRootInvalid
from dmx.loop_schema import load_loops_dir
from dmx.loop_state import TICKET_FILES, ticket_file
from dmx.loop_tools import (  # noqa: PLC2701 — same package, no public API split
    _bundled_loops_dir,
    _resolve_loop,
    _skill_catalog,
)
from dmx.shared_sources import SharedSourceError, read_shared_sources, source_root
from dmx.ticket_components import TicketComponents, current_job, ticket_components
from dmx.validator_runner import ValidatorRunError, resolve_validator_path
from dmx.workspace import ide_folder, resolve_workspace_root

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["CORE_MEMORY_FILES", "build_context", "register_context_tools"]

# The repo-wide memory files, in the order the rule lists them.
CORE_MEMORY_FILES = ("projectbrief.md", "productContext.md", "systemPatterns.md", "techContext.md")


def _shown(root: Path, path: Path) -> str:
    """*path* relative to *root* when it is inside it, else absolute."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _memory_files(root: Path, found: TicketComponents) -> list[str]:
    dmx = root / ".dmx"
    core = [dmx / name for name in CORE_MEMORY_FILES if (dmx / name).is_file()]
    others = sorted(
        path
        for path in dmx.glob("*.md")
        if path.name != "config.md" and path.name not in CORE_MEMORY_FILES
    )
    files = [_shown(root, path) for path in (*core, *others)]
    for component in found.components:
        folder = root / component.folder
        files.extend(_shown(root, path) for path in sorted(folder.glob("*.md")))
    return files


def _skills(root: Path) -> list[dict[str, Any]]:
    try:
        catalog = _skill_catalog(root)
    except SharedSourceError:
        return []
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


def build_context(root: Path, *, folder: Path | None = None) -> dict[str, Any]:
    """Everything ``get_context`` returns, for *root*."""
    job_id = current_job(root)
    with_components = has_components(root)
    found = (
        ticket_components(root, job_id, folder=folder) if with_components else TicketComponents()
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
            }
            for entry in found.entries
        ],
        "unknown_components": [entry.id for entry in found.entries if entry.component is None],
        "skipped_components": [
            {"path": skipped.path, "reason": skipped.reason} for skipped in found.skipped
        ],
        "repo_wide_files": list(found.repo_wide_files),
        "memory_files": _memory_files(root, found),
        "ticket_files": (
            [_shown(root, ticket_file(root, job_id, name)) for name in TICKET_FILES]
            if job_id
            else []
        ),
        "skills": _skills(root),
        "validators": _validators(root),
        "waiting_learnings": [],
    }


def register_context_tools(app: FastMCP) -> None:
    """Register ``get_context`` on *app*."""

    @app.tool
    async def get_context(ctx: Context, workspace_root: str | None = None) -> dict[str, Any]:
        """What the current ticket should read, and what applies to it.

        In a repo with ``.dmx/components/``, call this before a task and read
        every file in ``memory_files``, plus ``ticket_files``, instead of a
        fixed list. Without components it returns the repo-wide memory
        files, so the result is the same as the fixed list.

        Args:
            workspace_root: Repo root path override. Auto-detected if omitted.

        Returns:
            ``job_id``, ``has_components``, ``components_line``,
            ``components`` (each with ``id``, ``reason``, ``folder``,
            ``description`` and ``depends_on``), ``unknown_components``,
            ``skipped_components``, ``repo_wide_files``, ``memory_files``,
            ``ticket_files``, ``skills``, ``validators`` and
            ``waiting_learnings``. Paths are relative to the repo root, except
            bundled files, which are absolute.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return {"error": f"Could not resolve a valid workspace root: {exc}"}
        return build_context(root, folder=ide_folder())
